from __future__ import annotations

import asyncio
import re
from collections import Counter
from time import perf_counter
from urllib.parse import unquote, urlparse

from .models import (
    AlignmentCandidate,
    AnswerDetail,
    AskDiagnostics,
    AskRequest,
    AskResponse,
    AskTimings,
    Citation,
    GraphEdge,
    GraphNode,
    GraphResult,
    ReasoningStep,
    RelatedTerm,
    SelectionDecision,
    SenseCandidate,
    SourceSupport,
)
from .repository import GraphRepository, source_from_graph
from .retrieval import CandidateRetriever
from .selector import (
    HeuristicSenseSelector,
    SenseSelector,
    ThaiLLMChatSenseSelector,
    ThaiLLMStructuredOutputError,
    validate_selection,
)


_OTHER_SENSE_MARKERS = (
    "ความหมายอื่น", "อีกความหมาย", "หมายถึงอะไรอีก", "sense อื่น",
)
_RETAIN_SENSE_MARKERS = ("คำนี้", "คำนั้น", "คำที่เกี่ยวข้อง")
_COMPARE_MARKERS = (
    "เปรียบเทียบ", "ต่างกัน", "ทุกความหมาย", "ความหมายทั้งหมด", "มีกี่ความหมาย",
)
_RELATED_MARKERS = (
    "คำที่เกี่ยวข้อง", "คำพ้อง", "คำตรงข้าม", "คำกว้างกว่า", "คำเฉพาะกว่า",
    "ความสัมพันธ์", "เชื่อมโยง", "กราฟ",
)
_GENERAL_DEFINITION_ENDINGS = (
    "มีความหมายว่าอะไร", "หมายความว่าอะไร", "หมายถึงอะไร", "แปลว่าอะไร", "คืออะไร",
)
_GENERIC_CUE_PHRASES = (
    "หมายความว่าอะไร", "มีความหมายว่าอะไร", "หมายถึงอะไร", "แปลว่าอะไร", "คืออะไร", "คำว่า",
)
_LEXICAL_RELATIONS = {
    "synonym", "antonym", "hypernym", "hyponym", "similar", "relatedTerm",
    "holonym", "meronym", "entails", "causes", "domainTopic", "attribute",
    "coordinateTerm", "derivedTerm",
}
_RELATION_LABELS = {
    "synonym": "คำพ้อง", "antonym": "คำตรงข้าม", "hypernym": "คำกว้างกว่า",
    "hyponym": "คำเฉพาะกว่า", "similar": "ความหมายใกล้เคียง",
    "relatedTerm": "คำที่เกี่ยวข้อง", "holonym": "องค์รวม",
    "meronym": "ส่วนประกอบ", "entails": "การกระทำที่ตามมา",
    "causes": "เป็นเหตุให้", "domainTopic": "หมวดความรู้",
    "attribute": "คุณสมบัติ", "coordinateTerm": "คำระดับเดียวกัน",
    "derivedTerm": "คำที่สืบเนื่อง",
}
_SOURCE_LABELS = {
    "organizer": "ชุดข้อมูลนำเข้า", "lexitron": "LEXiTRON",
    "thai-wordnet": "Thai WordNet · เชื่อม English OMW", "omw-en": "English OMW",
    "wiktionary": "English Wiktionary · รายการคำภาษาไทย", "demo": "ข้อมูลสาธิต",
    "en-wiktionary-thai-entries": "English Wiktionary · รายการคำภาษาไทย",
    "th-wiktionary": "วิกิพจนานุกรมภาษาไทย",
}
_SOURCE_ORDER = {
    "organizer": 0, "lexitron": 1, "thai-wordnet": 2, "th-wiktionary": 3,
    "en-wiktionary-thai-entries": 4, "wiktionary": 4, "demo": 9,
}
_POS_LABELS = {
    "noun": "คำนาม", "verb": "คำกริยา", "adjective": "คำคุณศัพท์",
    "adverb": "คำวิเศษณ์", "pronoun": "คำสรรพนาม",
    "preposition": "คำบุพบท", "conjunction": "คำสันธาน",
    "interjection": "คำอุทาน", "classifier": "คำลักษณนาม",
}

def _is_general_definition_query(query: str) -> bool:
    normalized = query.casefold().strip().rstrip("?？").strip()
    ending = next(
        (item for item in _GENERAL_DEFINITION_ENDINGS if normalized.endswith(item)),
        None,
    )
    if not ending:
        return False

    subject = normalized[:-len(ending)].strip()
    explicitly_names_word = subject.startswith("คำว่า")
    if explicitly_names_word:
        subject = subject[len("คำว่า"):].strip()
    subject = subject.strip("“”\"' ")
    if not subject:
        return False

    # Without the explicit "คำว่า" marker, whitespace usually means the user
    # supplied a sentence/context rather than only a lexical item.
    return explicitly_names_word or not bool(re.search(r"\s", subject))


def detect_intent(query: str) -> str:
    normalized = query.casefold()
    if any(marker in normalized for marker in _COMPARE_MARKERS):
        return "compare"
    if any(marker in normalized for marker in _RELATED_MARKERS):
        return "related"
    if _is_general_definition_query(normalized):
        return "define_all"
    return "define"


def _tail(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.fragment:
        return unquote(parsed.fragment)
    return unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1] or uri)


def _source_label(source: str) -> str:
    return _SOURCE_LABELS.get(source, source)


def _pos_label(pos: str | None) -> str:
    if not pos:
        return "คำที่ยังไม่ระบุชนิดคำ"
    return _POS_LABELS.get(pos.casefold(), pos)


def _thai_join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} และ {items[1]}"
    return ", ".join(items[:-1]) + f" และ {items[-1]}"


def _chat_definition(definition: str) -> str:
    return definition.strip().rstrip(" .。")


def _context_lemma_for(request: AskRequest) -> str | None:
    if request.lemma or not request.context_lemma:
        return request.lemma
    normalized = request.query.casefold()
    explicitly_names_word = "คำว่า" in normalized
    if re.search(r"เกี่ยวข้องกับ\s*\S+", normalized):
        return None
    is_followup = (
        normalized.lstrip().startswith("แล้ว")
        or any(marker in normalized for marker in (*_OTHER_SENSE_MARKERS, "คำนี้", "คำนั้น"))
    )
    return request.context_lemma if is_followup and not explicitly_names_word else None


def _grounded_cue_words(
    request: AskRequest, cue_words: list[str], *, lemma: str | None = None
) -> list[str]:
    context = " ".join(
        [request.query, *(turn.content for turn in request.history)]
    ).casefold()
    result: list[str] = []
    for cue in cue_words:
        normalized_cue = cue.casefold().strip()
        if normalized_cue == (lemma or "").casefold().strip():
            continue
        if any(phrase in normalized_cue for phrase in _GENERIC_CUE_PHRASES):
            continue
        # A cue must be a readable fragment, not the full sentence echoed by
        # the model.  Long echoes are grounded but provide no explanation.
        if len(normalized_cue) > 28:
            continue
        if any(
            existing.casefold() in normalized_cue
            and len(normalized_cue) > len(existing) + 4
            for existing in result
        ):
            continue
        tokens = re.findall(r"[A-Za-z0-9\u0E00-\u0E7F]+", normalized_cue)
        if tokens and all(token in context for token in tokens) and cue not in result:
            result.append(cue)
    return result[:3]


def _apply_sense_context(
    request: AskRequest, candidates: list[SenseCandidate], *, detected_lemma: str | None = None
) -> list[SenseCandidate]:
    if not request.context_sense_uri:
        return candidates
    normalized = request.query.casefold()
    if detected_lemma != request.context_lemma:
        return candidates
    if any(marker in normalized for marker in _RETAIN_SENSE_MARKERS):
        return sorted(
            candidates,
            key=lambda item: item.sense_uri == request.context_sense_uri,
            reverse=True,
        )
    if any(marker in normalized for marker in _OTHER_SENSE_MARKERS):
        return sorted(
            candidates,
            key=lambda item: item.sense_uri == request.context_sense_uri,
        )
    if (
        request.history
        and detected_lemma == request.context_lemma
        and detect_intent(request.query) not in {"define_all", "compare"}
    ):
        return sorted(
            candidates,
            key=lambda item: item.sense_uri == request.context_sense_uri,
            reverse=True,
        )
    return candidates


def _best_definition_item(candidate: SenseCandidate):
    return next(
        (e for e in candidate.evidence if e.kind in {"definition", "synset-definition"}),
        None,
    )


def _best_definition(candidate: SenseCandidate) -> str | None:
    item = _best_definition_item(candidate)
    return item.text if item else None


def _best_example_item(candidate: SenseCandidate):
    return next(
        (e for e in candidate.evidence if e.kind in {"example", "synset-example"}),
        None,
    )


def _best_example(candidate: SenseCandidate) -> str | None:
    item = _best_example_item(candidate)
    return item.text if item else None


def _support(
    candidate: SenseCandidate,
    *,
    relation: str,
    confidence: float | None,
    review_status: str,
) -> SourceSupport:
    definition = _best_definition_item(candidate)
    example = _best_example_item(candidate)
    return SourceSupport(
        source=candidate.source,
        sense_uri=candidate.sense_uri,
        pos=candidate.pos,
        definition=definition.text if definition else None,
        example=example.text if example else None,
        match_relation=relation,
        confidence=confidence,
        review_status=review_status,
        evidence_ids=[item.evidence_id for item in candidate.evidence[:3]],
        sense_source=candidate.sense_source or candidate.source,
        evidence_source=(definition.evidence_source if definition else None),
        evidence_language=(definition.evidence_language if definition else None),
        source_graph=candidate.source_graph,
        edition=(definition.edition if definition else candidate.edition),
        source_url=(definition.source_url if definition else candidate.source_url),
        license=(definition.license if definition else candidate.license),
        attribution=(definition.attribution if definition else candidate.attribution),
    )


class AskServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 502,
        retryable: bool = True,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


class AskService:
    def __init__(
        self,
        *,
        repository: GraphRepository,
        retriever: CandidateRetriever,
        heuristic_selector: HeuristicSenseSelector,
        llm_selector: SenseSelector | None = None,
        default_to_llm: bool = False,
        max_graph_edges: int = 50,
        provider: str = "heuristic",
        model: str | None = None,
    ) -> None:
        self.repository = repository
        self.retriever = retriever
        self.heuristic_selector = heuristic_selector
        self.llm_selector = llm_selector
        self.default_to_llm = default_to_llm
        self.max_graph_edges = max_graph_edges
        self.provider = provider
        self.model = model

    async def ask(self, request: AskRequest) -> AskResponse:
        started = perf_counter()
        preliminary_intent = detect_intent(request.query)
        retrieval_lemma = _context_lemma_for(request)
        retrieval_started = perf_counter()
        detected, candidates = await self.retriever.retrieve(
            request.query, lemma=retrieval_lemma, max_candidates=request.max_candidates
        )
        if (
            not candidates and request.context_lemma and request.history
            and "คำว่า" not in request.query
        ):
            detected, candidates = await self.retriever.retrieve(
                request.query,
                lemma=request.context_lemma,
                max_candidates=request.max_candidates,
            )
        candidates = _apply_sense_context(request, candidates, detected_lemma=detected)
        selection_candidates = [c for c in candidates if c.source != "demo"] or candidates

        warnings: list[str] = []
        wants_llm = self.default_to_llm if request.use_llm is None else request.use_llm
        relation_facts: dict[str, list[dict[str, str]]] = {}
        if wants_llm and isinstance(self.llm_selector, ThaiLLMChatSenseSelector) and selection_candidates:
            context_graph = await self.repository.graph(
                [candidate.sense_uri for candidate in selection_candidates[:8]],
                hops=2,
                max_edges=min(100, max(40, self.max_graph_edges)),
            )
            relation_facts = self._relation_context(context_graph, selection_candidates)
        retrieval_ms = max(0, round((perf_counter() - retrieval_started) * 1000))
        llm_ms = 0
        if not selection_candidates:
            decision = SelectionDecision(
                selected_sense_uri=None,
                confidence=0.0,
                rationale="ไม่พบ Candidate Sense จาก GraphDB",
                evidence_ids=[],
                selector="none",
                intent=preliminary_intent,
            )
        elif wants_llm:
            if self.llm_selector is None:
                raise AskServiceError(
                    "llm_not_configured",
                    "ยังไม่ได้ตั้งค่า LLM สำหรับคำขอนี้",
                    status_code=503,
                )
            llm_started = perf_counter()
            try:
                if isinstance(self.llm_selector, ThaiLLMChatSenseSelector):
                    decision = await self.llm_selector.select(
                        request.query, selection_candidates, history=request.history,
                        relation_facts=relation_facts,
                    )
                else:
                    decision = await self.llm_selector.select(
                        request.query, selection_candidates, history=request.history
                    )
            except ThaiLLMStructuredOutputError as exc:
                raise AskServiceError(
                    "llm_structured_output_invalid",
                    f"ThaiLLM ส่งผลลัพธ์ไม่ครบตามรูปแบบ ({exc.code}) กรุณาลองใหม่",
                ) from exc
            except Exception as exc:
                raise AskServiceError(
                    "llm_request_failed",
                    f"ติดต่อ ThaiLLM ไม่สำเร็จ ({type(exc).__name__}) กรุณาลองใหม่",
                ) from exc
            finally:
                llm_ms = max(0, round((perf_counter() - llm_started) * 1000))
        else:
            decision = await self.heuristic_selector.select(
                request.query, selection_candidates, history=request.history
            )

        validation_started = perf_counter()
        validation_errors = validate_selection(decision, selection_candidates)
        if validation_errors:
            if wants_llm:
                raise AskServiceError(
                    "llm_evidence_validation_failed",
                    "Sense หรือ Evidence ที่ ThaiLLM ส่งกลับไม่อยู่ใน Candidate Set กรุณาลองใหม่",
                )
            warnings.extend(validation_errors)
        decision = decision.model_copy(
            update={
                "cue_words": _grounded_cue_words(
                    request, decision.cue_words, lemma=detected
                )
            }
        )
        validation_ms = max(0, round((perf_counter() - validation_started) * 1000))
        intent = decision.intent or preliminary_intent
        # A clearly context-free definition request must never be collapsed to
        # one arbitrary sense, even if a selector returns `define` or a sense ID.
        intent_corrected = preliminary_intent == "define_all" and intent == "define"
        if intent_corrected:
            intent = "define_all"
        if intent == "define_all":
            overview_evidence_ids: list[str] = []
            for candidate in selection_candidates:
                item = _best_definition_item(candidate)
                if item and item.evidence_id not in overview_evidence_ids:
                    overview_evidence_ids.append(item.evidence_id)
            decision = decision.model_copy(
                update={
                    "selected_sense_uri": None,
                    "cue_words": [],
                    "evidence_ids": overview_evidence_ids[:8],
                    "intent": "define_all",
                }
            )

        selected = next(
            (c for c in candidates if c.sense_uri == decision.selected_sense_uri), None
        )
        graph_started = perf_counter()
        if selected:
            semantic_graph = getattr(self.repository, "semantic_graph", None)
            graph = (
                await semantic_graph(
                    selected.sense_uri, max_edges=self.max_graph_edges
                )
                if callable(semantic_graph)
                else await self.repository.graph(
                    [selected.sense_uri],
                    hops=request.hops,
                    max_edges=self.max_graph_edges,
                )
            )
        elif intent in {"define_all", "compare"} and candidates:
            graph = await self.repository.graph(
                [candidate.sense_uri for candidate in candidates],
                hops=1,
                max_edges=self.max_graph_edges,
            )
        else:
            graph = GraphResult(hops=request.hops)

        all_senses: list[SenseCandidate] = candidates
        alignments: list[AlignmentCandidate] = []
        if detected:
            all_senses, alignments = await asyncio.gather(
                self.repository.get_senses(detected, limit=100),
                self.repository.get_alignments(detected, limit=300),
            )
        supports = self._build_supports(
            intent=intent, selected=selected, all_senses=all_senses, alignments=alignments
        )
        graph_ms = max(0, round((perf_counter() - graph_started) * 1000))
        citations = self._extend_citations(
            self._citations(candidates, selected, decision), supports, all_senses
        )
        related_terms = self._related_terms(graph)
        detail = self._build_detail(
            intent=intent, lemma=detected, selected=selected, candidates=candidates,
            all_senses=all_senses, alignments=alignments, supports=supports,
            related_terms=related_terms, decision=decision, citations=citations, graph=graph,
        )
        answer = self._compose_answer(detail, cue_words=decision.cue_words)
        answer_mode = "template"
        if (
            decision.selector == "thaillm"
            and not intent_corrected
            and self._usable_model_answer(decision.grounded_answer, lemma=detected)
        ):
            answer = decision.grounded_answer.strip()
            answer_mode = "model"
        safe_rationale = self._compose_selection_rationale(
            detail, cue_words=decision.cue_words
        )
        decision = decision.model_copy(
            update={"grounded_answer": answer, "rationale": safe_rationale}
        )
        explanation_graph = self._explanation_graph(
            query=request.query, detail=detail, selected=selected, citations=citations
        )
        requested_provider = self.provider if self.provider in {
            "heuristic", "openai", "thaillm"
        } else "heuristic"
        total_ms = max(0, round((perf_counter() - started) * 1000))
        return AskResponse(
            query=request.query,
            detected_lemma=detected,
            candidates=candidates,
            selection=decision,
            answer=answer,
            detail=detail,
            citations=citations,
            graph=graph,
            explanation_graph=explanation_graph,
            diagnostics=AskDiagnostics(
                requested_provider=requested_provider,
                actual_selector=decision.selector,
                model=self.model,
                answer_mode=answer_mode,
                fallback_used=False,
                fallback_reason=None,
                latency_ms=total_ms,
            ),
            timings=AskTimings(
                retrieval_ms=retrieval_ms,
                llm_ms=llm_ms,
                validation_ms=validation_ms,
                graph_ms=graph_ms,
                total_ms=total_ms,
            ),
            evidence_validated=bool(citations) and not validation_errors,
            validation_warnings=warnings,
        )

    @staticmethod
    def _citations(
        candidates: list[SenseCandidate],
        selected: SenseCandidate | None,
        decision: SelectionDecision,
    ) -> list[Citation]:
        owners = {
            item.evidence_id: (candidate, item)
            for candidate in candidates
            for item in candidate.evidence
        }
        requested = decision.evidence_ids
        if not requested and selected is not None:
            requested = [item.evidence_id for item in selected.evidence[:3]]
        return [AskService._citation(*owners[evidence_id]) for evidence_id in requested if evidence_id in owners]

    @staticmethod
    def _citation(candidate: SenseCandidate, item) -> Citation:
        return Citation(
            evidence_id=item.evidence_id, kind=item.kind, text=item.text,
            evidence_uri=item.evidence_uri, source=source_from_graph(item.source_graph),
            source_graph=item.source_graph, derived_from=item.derived_from,
            sense_source=item.sense_source or candidate.sense_source or candidate.source,
            evidence_source=item.evidence_source or source_from_graph(item.source_graph),
            evidence_language=item.evidence_language or item.language,
            edition=item.edition, source_url=item.source_url,
            license=item.license, attribution=item.attribution,
        )

    @staticmethod
    def _extend_citations(
        citations: list[Citation], supports: list[SourceSupport],
        all_senses: list[SenseCandidate],
    ) -> list[Citation]:
        seen = {item.evidence_id for item in citations}
        by_uri = {item.sense_uri: item for item in all_senses}
        result = list(citations)
        for support in supports:
            candidate = by_uri.get(support.sense_uri)
            if candidate is None:
                continue
            preferred = sorted(
                candidate.evidence,
                key=lambda item: (
                    item.kind not in {"definition", "synset-definition"}, item.kind
                ),
            )
            for evidence in preferred[:2]:
                if evidence.evidence_id not in seen:
                    result.append(AskService._citation(candidate, evidence))
                    seen.add(evidence.evidence_id)
        return result[:16]

    @staticmethod
    def _build_supports(
        *, intent: str, selected: SenseCandidate | None,
        all_senses: list[SenseCandidate], alignments: list[AlignmentCandidate],
    ) -> list[SourceSupport]:
        usable = [item for item in all_senses if item.source != "demo"] or all_senses
        if intent in {"define_all", "compare"}:
            result: list[SourceSupport] = []
            grouped: dict[str, list[SenseCandidate]] = {}
            for candidate in usable:
                grouped.setdefault(candidate.source_graph, []).append(candidate)
            for graph in sorted(grouped):
                ranked = sorted(
                    grouped[graph],
                    key=lambda item: (bool(_best_definition(item)), len(item.evidence)),
                    reverse=True,
                )
                for candidate in ranked[:4]:
                    result.append(_support(
                        candidate,
                        relation="unlinked",
                        confidence=None,
                        review_status="unlinked",
                    ))
            return result[:16]
        if selected is None:
            return []

        result = [_support(
            selected, relation="selected", confidence=1.0, review_status="selected"
        )]
        by_uri = {item.sense_uri: item for item in usable}
        linked: list[tuple[tuple[int, int, float], SenseCandidate, AlignmentCandidate]] = []
        for alignment in alignments:
            if alignment.review_status == "rejected":
                continue
            other_uri = (
                alignment.right_sense_uri if alignment.left_sense_uri == selected.sense_uri
                else alignment.left_sense_uri if alignment.right_sense_uri == selected.sense_uri
                else None
            )
            other = by_uri.get(other_uri or "")
            if other is None or other.source_graph == selected.source_graph:
                continue
            relation = alignment.decided_relation or alignment.recommended_relation
            linked.append(((
                1 if alignment.review_status == "approved" else 0,
                {"exactMatch": 2, "closeMatch": 1}.get(relation, 0),
                alignment.confidence,
            ), other, alignment))
        linked.sort(key=lambda item: item[0], reverse=True)
        used_sources = {selected.source_graph}
        for _, candidate, alignment in linked:
            if candidate.source_graph in used_sources:
                continue
            result.append(_support(
                candidate,
                relation=alignment.decided_relation or alignment.recommended_relation,
                confidence=alignment.confidence,
                review_status="approved" if alignment.review_status == "approved" else "pending",
            ))
            used_sources.add(candidate.source_graph)
        return result[:5]

    @staticmethod
    def _related_terms(graph: GraphResult) -> list[RelatedTerm]:
        labels = {node.uri: node.label for node in graph.nodes}
        result: list[RelatedTerm] = []
        seen: set[tuple[str, str]] = set()
        for edge in graph.edges:
            relation = _tail(edge.predicate)
            key = (relation, edge.target)
            if relation not in _LEXICAL_RELATIONS or key in seen:
                continue
            seen.add(key)
            relation_label = _RELATION_LABELS.get(relation, relation)
            result.append(RelatedTerm(
                term=labels.get(edge.target, _tail(edge.target)),
                relation=relation_label,
                target_uri=edge.target,
                source_graph=edge.source_graph,
            ))
        return result[:12]

    @staticmethod
    def _relation_context(
        graph: GraphResult, candidates: list[SenseCandidate]
    ) -> dict[str, list[dict[str, str]]]:
        labels = {node.uri: node.label for node in graph.nodes}
        references = {
            edge.source: edge.target
            for edge in graph.edges
            if _tail(edge.predicate) == "reference"
        }
        result: dict[str, list[dict[str, str]]] = {}
        for candidate in candidates:
            concept = references.get(candidate.sense_uri)
            if not concept:
                continue
            relations: list[dict[str, str]] = []
            for edge in graph.edges:
                relation = _tail(edge.predicate)
                if edge.source != concept or relation not in _LEXICAL_RELATIONS:
                    continue
                relations.append({
                    "relation": _RELATION_LABELS.get(relation, relation),
                    "term": labels.get(edge.target, _tail(edge.target)),
                    "source_graph": edge.source_graph,
                })
                if len(relations) >= 6:
                    break
            if relations:
                result[candidate.sense_uri] = relations
        return result

    @staticmethod
    def _usable_model_answer(answer: str | None, *, lemma: str | None) -> bool:
        if not answer or not lemma:
            return False
        cleaned = answer.strip()
        return (
            len(cleaned) >= 30
            and lemma in cleaned
            and "http://" not in cleaned
            and "https://" not in cleaned
            and not cleaned.startswith("{")
        )

    @staticmethod
    def _build_detail(
        *, intent: str, lemma: str | None, selected: SenseCandidate | None,
        candidates: list[SenseCandidate], all_senses: list[SenseCandidate],
        alignments: list[AlignmentCandidate], supports: list[SourceSupport],
        related_terms: list[RelatedTerm], decision: SelectionDecision,
        citations: list[Citation], graph: GraphResult,
    ) -> AnswerDetail:
        if not lemma or (
            selected is None and intent not in {"define_all", "compare"}
        ):
            return AnswerDetail(
                intent=intent, title="ยังไม่พบคำที่ต้องการ",
                summary="ไม่พบ Lexical Entry หรือ Candidate Sense ที่ตรงกับคำถาม",
                explanation="ลองระบุคำเป้าหมายให้ชัด เช่น “คำว่า ขัน หมายถึงอะไร”",
                reasoning_path=[ReasoningStep(
                    step=1, label="ตรวจคำถาม",
                    detail="ไม่พบคำที่เชื่อมกับ Lexical Entry ใน GraphDB",
                )],
            )
        source_counts = Counter(item.source for item in all_senses if item.source != "demo")
        graph_count = len({item.source_graph for item in all_senses})
        pending_count = sum(item.review_status == "pending" for item in alignments)
        approved_count = sum(item.review_status == "approved" for item in alignments)
        definition = _best_definition(selected) if selected else None
        definition = definition or "Sense นี้ยังไม่มีนิยาม"
        example = _best_example(selected) if selected else None
        source_names = ", ".join(
            f"{_source_label(source)} {count} Sense"
            for source, count in sorted(
                source_counts.items(), key=lambda item: _SOURCE_ORDER.get(item[0], 8)
            )
        )
        if intent == "define_all":
            title = f"ความหมายของ “{lemma}”"
            summary = f"พบ {len(candidates)} ความหมายในคลังคำ"
            explanation = (
                "คำถามนี้ไม่มีบริบทที่แยกความหมายได้ ระบบจึงไม่เลือก Sense เดียว "
                "และแสดงทุกความหมายที่ GraphDB ค้นพบ"
            )
        elif intent == "compare":
            title = f"เปรียบเทียบความหมายของ “{lemma}”"
            summary = f"พบ {sum(source_counts.values())} Source Senses จาก {graph_count} กราฟข้อมูล"
            explanation = (
                f"{source_names or 'ยังไม่มีข้อมูลจากแหล่งจริง'} โดยแสดงแต่ละแหล่งแยกกัน "
                f"เพราะ Alignment ที่อนุมัติแล้วมี {approved_count} คู่ และที่รอตรวจมี "
                f"{pending_count} คู่ ระบบจึงไม่รวม Sense ข้ามแหล่งเป็นข้อเท็จจริงเอง"
            )
        elif intent == "related":
            title = f"คำที่เกี่ยวข้องกับ “{lemma}” ใน Sense นี้"
            summary = definition
            if related_terms:
                relation_text = ", ".join(
                    f"{item.relation} “{item.term}”" for item in related_terms[:8]
                )
                explanation = f"GraphDB พบ {len(related_terms)} ความสัมพันธ์ทางภาษา: {relation_text}"
            else:
                explanation = (
                    "ยังไม่พบคำพ้อง คำตรงข้าม หรือลำดับชั้นที่ผูกกับ Sense นี้โดยตรง "
                    "แต่ยังเปิดดูแหล่งสนับสนุนและโครงสร้าง RDF ได้"
                )
        else:
            title = f"ความหมายของ “{lemma}” ตามบริบท"
            summary = definition
            example_text = f" ตัวอย่างที่พบ: “{example}”" if example else ""
            explanation = (
                f"ระบบเลือก Sense ชนิดคำ {selected.pos or 'ไม่ระบุ'} จาก "
                f"{_source_label(selected.source)} และพบข้อมูลเทียบเคียงจากอีก "
                f"{max(0, len(supports) - 1)} รายการ{example_text} แหล่งที่ยังเป็น "
                "pending จะแสดงเป็นข้อมูลประกอบ ไม่ถือว่าเป็น Sense เดียวกัน"
            )
        reasoning = [
            ReasoningStep(step=1, label="แยกเจตนาคำถาม", detail=f"จัดคำถามเป็น intent: {intent}"),
            ReasoningStep(
                step=2, label="ค้น Candidate Sense",
                detail=(
                    f"พบ {len(candidates)} Candidate ที่นำมาจัดอันดับ และ "
                    f"{sum(source_counts.values())} Source Senses จาก {len(source_counts)} แหล่ง"
                ),
            ),
            ReasoningStep(
                step=3,
                label=(
                    "แสดงทุกความหมาย"
                    if intent == "define_all"
                    else "จัดกลุ่ม Source Senses"
                    if intent == "compare"
                    else "เลือกความหมายตามบริบท"
                ),
                detail=(
                    "คำถามไม่มีบริบทเพียงพอ จึงแสดงทุกความหมายโดยไม่เลือก Sense เดียว"
                    if intent == "define_all"
                    else
                    "แสดง Sense ของแต่ละ Dataset แยกจากกัน โดยไม่เลือก Sense ใดเป็นคำตอบกลาง"
                    if intent == "compare"
                    else f"เลือก {selected.pos or 'Sense'} จาก {_source_label(selected.source)} "
                         f"ด้วย {decision.selector}; confidence {round(decision.confidence * 100)}%"
                ),
                evidence_ids=(
                    []
                    if intent in {"define_all", "compare"}
                    else decision.evidence_ids
                ),
            ),
            ReasoningStep(
                step=4, label="ตรวจหลักฐานและขยายกราฟ",
                detail=(
                    f"ตรวจ Citation {len(citations)} รายการ, กราฟ {len(graph.nodes)} โหนด/"
                    f"{len(graph.edges)} เส้น และ Alignment approved {approved_count}, pending {pending_count}"
                ),
                evidence_ids=[item.evidence_id for item in citations[:6]],
            ),
        ]
        return AnswerDetail(
            intent=intent, title=title, summary=summary, explanation=explanation,
            source_supports=supports, related_terms=related_terms, reasoning_path=reasoning,
        )

    @staticmethod
    def _compose_answer(
        detail: AnswerDetail, *, cue_words: list[str] | None = None
    ) -> str:
        if detail.title == "ยังไม่พบคำที่ต้องการ":
            return (
                "ยังไม่พบคำที่ถามในชุดข้อมูลที่นำเข้าครับ "
                "ลองระบุคำเป้าหมายให้ชัดขึ้น หรือเพิ่มชุดข้อมูลที่มีคำนี้ก่อน"
            )
        cues = list(dict.fromkeys(cue_words or []))[:3]
        primary = next(
            (
                support for support in detail.source_supports
                if support.review_status == "selected"
            ),
            detail.source_supports[0] if detail.source_supports else None,
        )
        if detail.intent == "define_all":
            lemma = detail.title.removeprefix("ความหมายของ ").strip("“”")
            meaning_rows: dict[str, SourceSupport] = {}
            for support in detail.source_supports:
                meaning_rows.setdefault(support.sense_uri, support)
            ordered_meanings = list(meaning_rows.values())
            meaning_lines = [
                f"{index}. {_chat_definition(support.definition or 'ยังไม่มีนิยาม')}"
                + (f" — {_pos_label(support.pos)}" if support.pos else "")
                for index, support in enumerate(ordered_meanings, start=1)
            ]
            return "\n\n".join([
                f"คำว่า “{lemma}” มี {len(ordered_meanings)} ความหมายในข้อมูลที่ค้นพบครับ",
                "\n".join(meaning_lines),
                "คำถามนี้ยังไม่มีบริบทที่ชี้ไปยังความหมายใดความหมายหนึ่ง "
                "ระบบจึงแสดงทุกความหมายโดยไม่เลือกแทนผู้ใช้ หากส่งประโยคที่มีคำนี้มา "
                "ระบบจะช่วยเลือกความหมายที่ตรงกับบริบทให้ได้ครับ",
            ])
        if detail.intent == "compare":
            sources = list(dict.fromkeys(
                _source_label(support.source) for support in detail.source_supports
            ))
            meaning_rows: dict[str, SourceSupport] = {}
            for support in detail.source_supports:
                meaning_rows.setdefault(support.sense_uri, support)
            ordered_meanings = list(meaning_rows.values())
            meaning_lines = [
                f"{index}. {support.definition or 'ยังไม่มีนิยาม'}"
                + (f" — {_pos_label(support.pos)}" if support.pos else "")
                for index, support in enumerate(ordered_meanings, start=1)
            ]
            return "\n\n".join([
                f"คำว่า “{detail.title.removeprefix('เปรียบเทียบความหมายของ ').strip('“”')}” "
                f"มี {len(ordered_meanings)} แนวความหมายในข้อมูลที่นำมาเปรียบเทียบครับ",
                "\n".join(meaning_lines),
                "ระบบแสดงข้อมูลแต่ละฉบับแยกจากกัน ได้แก่ "
                + ", ".join(sources)
                + " เพื่อให้เห็นว่าพจนานุกรมแต่ละแหล่งแบ่งความหมายและชนิดคำต่างกันอย่างไร "
                "โดยไม่รวมระเบียนต้นฉบับให้กลายเป็นข้อมูลเดียวกัน",
            ])
        if detail.intent == "related":
            intro = (
                f"สำหรับคำว่า “{detail.title.removeprefix('คำที่เกี่ยวข้องกับ ').removesuffix(' ใน Sense นี้').strip('“”')}” "
                f"ในความหมาย “{detail.summary}” ระบบพบคำที่เชื่อมอยู่ในกราฟดังนี้ครับ"
            )
            if detail.related_terms:
                relation_lines = [
                    f"• {item.relation}: {item.term}"
                    for item in detail.related_terms[:8]
                ]
            else:
                relation_lines = ["ยังไม่พบคำที่เชื่อมกับความหมายนี้โดยตรง"]
            source_line = None
            if primary:
                source_line = (
                    "ความหมายหลักอ้างอิงจาก "
                    + AskService._support_display_name(primary)
                    + " ส่วนรายการคำเกี่ยวข้องดึงจากความสัมพันธ์ที่บันทึกไว้ใน GraphDB"
                )
            return "\n\n".join(
                part for part in [intro, "\n".join(relation_lines), source_line] if part
            )

        lemma = detail.title.removeprefix("ความหมายของ ").removesuffix(" ตามบริบท").strip("“”")
        lines = [
            f"ในบริบทนี้ คำว่า “{lemma}” หมายถึง "
            f"“{_chat_definition(detail.summary)}” ครับ"
        ]
        if primary:
            if cues:
                cue_text = "คำว่า " + _thai_join([f"“{cue}”" for cue in cues])
                context_text = (
                    f"{cue_text} ช่วยชี้ว่าประโยคนี้ใช้คำว่า “{lemma}” "
                    "ในความหมายดังกล่าว"
                )
            else:
                context_text = ""
            if primary.pos:
                pos_text = f"คำว่า “{lemma}” ทำหน้าที่เป็น{_pos_label(primary.pos)}"
                if context_text:
                    lines.append(f"{context_text} โดย{pos_text}")
                else:
                    lines.append(pos_text + "ครับ")
            elif context_text:
                lines.append(context_text)
            if primary.example:
                lines.append(f"ตัวอย่างที่พบในคลังคือ “{primary.example}”")

            primary_name = AskService._support_chat_name(primary)
            other_names = list(dict.fromkeys(
                AskService._support_chat_name(support)
                for support in detail.source_supports
                if support is not primary
                and AskService._support_chat_name(support) != primary_name
            ))
            source_text = f"ความหมายหลักอ้างอิงจาก{primary_name}"
            if other_names:
                source_text += " และพบข้อมูลเทียบเคียงใน" + _thai_join(other_names)
            lines.append(source_text + " คุณสามารถเปิดดูหลักฐานของแต่ละแหล่งเพิ่มเติมได้")
        return "\n\n".join(lines)

    @staticmethod
    def _support_display_name(support: SourceSupport) -> str:
        source = _source_label(support.sense_source or support.source)
        if not support.edition:
            return source
        edition = support.edition.replace(
            " (ไฟล์ที่ได้รับยังไม่สมบูรณ์)", ""
        )
        if support.source == "organizer":
            return edition
        return f"{_source_label(support.source)} ({edition})"

    @staticmethod
    def _support_chat_name(support: SourceSupport) -> str:
        """Short source name for prose; detailed metadata stays in Evidence."""
        if support.source == "organizer" and support.edition:
            return support.edition
        return _source_label(
            support.evidence_source or support.source
        )

    @staticmethod
    def _compose_selection_rationale(
        detail: AnswerDetail, *, cue_words: list[str] | None = None
    ) -> str:
        cues = list(dict.fromkeys(cue_words or []))[:3]
        cue_text = ", ".join(f"“{cue}”" for cue in cues)
        if detail.intent == "define_all":
            return (
                "คำถามความหมายทั่วไปไม่มีบริบทพอเลือก Sense เดียว "
                "ระบบจึงตรวจและแสดงทุก Candidate จาก GraphDB"
            )
        if detail.intent == "compare":
            return (
                "คำถามต้องการเปรียบเทียบหลายความหมาย จึงไม่เลือก Sense เดียว "
                "Backend ตรวจแล้วว่า Evidence ทุกชิ้นอยู่ใน Candidate Set จาก GraphDB"
            )
        if detail.intent == "related":
            context = f" โดยใช้บริบท {cue_text}" if cue_text else ""
            return (
                "ระบบคงความหมายจากบทสนทนาปัจจุบัน"
                + context
                + " และ Backend ตรวจ Sense/Evidence ก่อนดึงความสัมพันธ์จาก GraphDB"
            )
        context = f" จากบริบท {cue_text}" if cue_text else ""
        return (
            "ระบบเลือก Candidate นี้"
            + context
            + " จากนั้น Backend ตรวจว่า Sense และ Evidence อยู่ในผลที่ GraphDB ส่งให้"
        )

    @staticmethod
    def _explanation_graph(
        *, query: str, detail: AnswerDetail, selected: SenseCandidate | None,
        citations: list[Citation],
    ) -> GraphResult:
        runtime_graph = "https://w3id.org/thailex/graph/runtime/explanation"
        question_uri = "urn:thailex:runtime:question"
        intent_uri = f"urn:thailex:runtime:intent:{detail.intent}"
        nodes = [
            GraphNode(uri=question_uri, label=query[:50], kind="resource"),
            GraphNode(uri=intent_uri, label=f"Intent: {detail.intent}", kind="concept"),
        ]
        edges = [GraphEdge(
            source=question_uri,
            predicate="https://w3id.org/thailex/ontology/classifiedAs",
            target=intent_uri, source_graph=runtime_graph, hop=1,
        )]
        evidence_owners: dict[str, str] = {}
        if detail.intent in {"define_all", "compare"}:
            support_nodes: set[str] = set()
            for support in detail.source_supports:
                if support.sense_uri not in support_nodes:
                    nodes.append(GraphNode(
                        uri=support.sense_uri,
                        label=(
                            f"{_source_label(support.source)}: "
                            f"{support.definition or 'Sense'}"
                        )[:70],
                        kind="sense",
                    ))
                    edges.append(GraphEdge(
                        source=intent_uri,
                        predicate=(
                            "https://w3id.org/thailex/ontology/listedSense"
                            if detail.intent == "define_all"
                            else "https://w3id.org/thailex/ontology/comparisonSense"
                        ),
                        target=support.sense_uri, source_graph=runtime_graph, hop=1,
                    ))
                    support_nodes.add(support.sense_uri)
                for evidence_id in support.evidence_ids:
                    evidence_owners[evidence_id] = support.sense_uri
        elif selected:
            nodes.append(GraphNode(
                uri=selected.sense_uri,
                label=f"{selected.lemma}: {_best_definition(selected) or 'Sense'}"[:70],
                kind="sense",
            ))
            edges.append(GraphEdge(
                source=intent_uri,
                predicate="https://w3id.org/thailex/ontology/selectedSense",
                target=selected.sense_uri, source_graph=runtime_graph, hop=1,
            ))
        source_nodes: set[str] = set()
        support_sense_uris = set(evidence_owners.values())
        for index, citation in enumerate(citations[:10]):
            evidence_uri = citation.evidence_uri
            if evidence_uri in support_sense_uris:
                evidence_uri = f"urn:thailex:runtime:evidence:{index}"
            source_uri = f"urn:thailex:source:{citation.source}"
            if not any(node.uri == evidence_uri for node in nodes):
                nodes.append(GraphNode(uri=evidence_uri, label=citation.text[:60], kind="resource"))
            if source_uri not in source_nodes:
                nodes.append(GraphNode(
                    uri=source_uri, label=_source_label(citation.source), kind="resource"
                ))
                source_nodes.add(source_uri)
            owner_uri = evidence_owners.get(citation.evidence_id)
            if owner_uri or selected:
                edges.append(GraphEdge(
                    source=owner_uri or selected.sense_uri,
                    predicate="https://w3id.org/thailex/ontology/supportedBy",
                    target=evidence_uri, source_graph=runtime_graph, hop=1,
                ))
            edges.append(GraphEdge(
                source=evidence_uri,
                predicate="http://www.w3.org/ns/prov#wasDerivedFrom",
                target=source_uri, source_graph=runtime_graph, hop=2,
            ))
        unique_edges: list[GraphEdge] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for edge in edges:
            key = (edge.source, edge.predicate, edge.target)
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(edge)
        return GraphResult(hops=2, nodes=nodes, edges=unique_edges, truncated=False)
