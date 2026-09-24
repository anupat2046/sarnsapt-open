from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from time import perf_counter
from urllib.parse import unquote, urlparse

import httpx

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
from .retrieval import CandidateRetriever, display_term, explicit_term
from .selector import (
    HeuristicSenseSelector,
    QueryAnalysis,
    SenseSelector,
    ThaiLLMChatSenseSelector,
    ThaiLLMStructuredOutputError,
    ThaiLLMUnavailableError,
    validate_selection,
)
from .thai_text import CONTENT_WEIGHT, context_tokens, normalize_typing


logger = logging.getLogger(__name__)

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

_CONVERSATION_REPLIES = {
    "สวัสดี": "สวัสดีครับ ผมสานศัพท์ ช่วยค้นความหมายคำไทย เลือกความหมายตามบริบท และดูข้อมูลจากหลายแหล่งได้ ถ้าอยากคุยเรื่องคำไหน พิมพ์มาได้เลยครับ",
    "สวัสดีครับ": "สวัสดีครับ ผมสานศัพท์ ช่วยค้นความหมายคำไทย เลือกความหมายตามบริบท และดูข้อมูลจากหลายแหล่งได้ ถ้าอยากคุยเรื่องคำไหน พิมพ์มาได้เลยครับ",
    "สวัสดีค่ะ": "สวัสดีครับ ผมสานศัพท์ ช่วยค้นความหมายคำไทย เลือกความหมายตามบริบท และดูข้อมูลจากหลายแหล่งได้ ถ้าอยากคุยเรื่องคำไหน พิมพ์มาได้เลยครับ",
    "หวัดดี": "สวัสดีครับ ผมสานศัพท์ ช่วยค้นความหมายคำไทย เลือกความหมายตามบริบท และดูข้อมูลจากหลายแหล่งได้ ถ้าอยากคุยเรื่องคำไหน พิมพ์มาได้เลยครับ",
    "hello": "สวัสดีครับ ผมสานศัพท์ ช่วยค้นความหมายคำไทย เลือกความหมายตามบริบท และดูข้อมูลจากหลายแหล่งได้ ถ้าอยากคุยเรื่องคำไหน พิมพ์มาได้เลยครับ",
    "hi": "สวัสดีครับ ผมสานศัพท์ ช่วยค้นความหมายคำไทย เลือกความหมายตามบริบท และดูข้อมูลจากหลายแหล่งได้ ถ้าอยากคุยเรื่องคำไหน พิมพ์มาได้เลยครับ",
    "ขอบคุณ": "ยินดีครับ ถ้ามีคำหรือประโยคที่อยากให้ช่วยดูความหมาย ถามต่อได้เลยครับ",
    "ขอบคุณครับ": "ยินดีครับ ถ้ามีคำหรือประโยคที่อยากให้ช่วยดูความหมาย ถามต่อได้เลยครับ",
    "ขอบคุณค่ะ": "ยินดีครับ ถ้ามีคำหรือประโยคที่อยากให้ช่วยดูความหมาย ถามต่อได้เลยครับ",
    "ช่วยอะไรได้บ้าง": "ผมช่วยค้นความหมายคำไทยตามบริบท เปรียบเทียบความหมายจากแหล่งข้อมูลที่นำเข้า และแสดงความสัมพันธ์พร้อมที่มาได้ครับ",
    "ทำอะไรได้บ้าง": "ผมช่วยค้นความหมายคำไทยตามบริบท เปรียบเทียบความหมายจากแหล่งข้อมูลที่นำเข้า และแสดงความสัมพันธ์พร้อมที่มาได้ครับ",
    "คุณทำอะไรได้บ้าง": "ผมช่วยค้นความหมายคำไทยตามบริบท เปรียบเทียบความหมายจากแหล่งข้อมูลที่นำเข้า และแสดงความสัมพันธ์พร้อมที่มาได้ครับ",
}

_SMALL_TALK_FALLBACK = {
    "ว่าไง": "สวัสดีครับ อยากคุยเรื่องคำไหนหรือมีประโยคที่อยากให้ช่วยดูความหมายไหมครับ",
    "เป็นไงบ้าง": "สบายดีครับ มีคำไทยหรือประโยคไหนที่อยากให้ช่วยดูไหมครับ",
    "อยู่ไหม": "อยู่ครับ ถามเรื่องคำไทยมาได้เลย",
}


# Phrases that make a message a dictionary question. Such a message must never
# be answered as small talk, even when no word in it matched the graph.
_LEXICAL_SIGNALS = (
    "คำว่า", "ความหมาย", "หมายถึง", "หมายความ", "แปลว่า", "คืออะไร",
    "คำพ้อง", "คำตรงข้าม", "คำที่เกี่ยวข้อง", "ชนิดคำ", "นิยาม",
    "รากศัพท์", "ลักษณะคำ", "ประเภทคำ", "ออกเสียง", "อ่านว่า", "คำอ่าน",
)
# A word_info answer must be what the latest message asks for, not a topic
# carried over from the conversation history.
_WORD_INFO_MARKERS = (
    "รากศัพท์", "ที่มาของคำ", "มาจากภาษา", "ออกเสียง", "อ่านว่า", "อ่านยังไง",
    "อ่านอย่างไร", "คำอ่าน", "สะกด", "ชนิดคำ", "ลักษณะคำ", "ประเภทคำ", "คำแบบไหน",
    "คำชนิดไหน", "คำประเภทไหน", "เป็นคำอะไร", "หน้าที่ของคำ",
)
LLM_CANDIDATES = 8
# Intents that answer about the word as a whole rather than one chosen sense.
_MULTI_SENSE_INTENTS = {"define_all", "compare", "word_info"}


def _looks_lexical(query: str) -> bool:
    normalized = query.casefold()
    return any(signal in normalized for signal in _LEXICAL_SIGNALS)


_OPENER_REPLY = (
    "ได้เลยครับ อยากรู้เรื่องคำไหน พิมพ์คำนั้นมาได้เลย "
    "หรือส่งประโยคที่ใช้คำนั้นมาเพื่อให้ช่วยดูความหมายตามบริบทครับ"
)


def _conversation_reply(query: str) -> str | None:
    normalized = re.sub(r"[!?！？。\.\s]+$", "", query.casefold().strip())
    return _CONVERSATION_REPLIES.get(normalized)


def _small_talk_fallback(query: str) -> str | None:
    normalized = re.sub(r"[!?！？。\.\s]+$", "", query.casefold().strip())
    return _SMALL_TALK_FALLBACK.get(normalized)


def _is_bare_lemma_query(query: str, lemma: str | None) -> bool:
    if not lemma:
        return False
    normalized = query.casefold().strip().strip("“”\"' ").rstrip("!?！？。.").strip()
    return normalized == lemma.casefold().strip()

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


_INTERNAL_REF = re.compile(
    r"\s*[\(\[]\s*S\d+(?:-E\d+)?(?:\s*,\s*S\d+(?:-E\d+)?)*\s*[\)\]]|\bS\d+-E\d+\b"
)


def _strip_internal_refs(answer: str | None) -> str | None:
    """Remove candidate ids such as "(S4)" or "S1-E2" that the model sometimes
    copies from its input; the rest of a grounded answer is still usable."""
    if answer is None:
        return None
    return re.sub(r"[ 	]{2,}", " ", _INTERNAL_REF.sub("", answer)).strip()


def _shorten_at_boundary(text: str, limit: int) -> str:
    """Cut long source text at a clause boundary so no gloss is left half-quoted."""
    if len(text) <= limit:
        return text
    cut = max(text.rfind(mark, 0, limit) for mark in (";", "。", ". ", "; "))
    return (text[: cut + 1] if cut > limit // 2 else text[:limit]).rstrip() + " …"


def _clean_edition(edition: str) -> str:
    return edition.replace(" (ไฟล์ที่ได้รับยังไม่สมบูรณ์)", "")


def _base_source_name(support: SourceSupport) -> str:
    """Dataset-level name; imported datasets are named by their own title."""
    if support.source != "organizer":
        return _source_label(support.evidence_source or support.source)
    if support.dataset:
        return support.dataset
    if support.edition:
        return _clean_edition(support.edition)
    graph = support.source_graph or ""
    return graph.removeprefix("https://w3id.org/thailex/graph/organizer/") or _source_label(support.source)


def _source_names(supports: list[SourceSupport]) -> dict[str, str]:
    """Readable name per source graph, adding the edition only when two graphs
    would otherwise share a name (for example v1 and v2 of one dataset)."""
    base: dict[str, tuple[SourceSupport, str]] = {}
    for support in supports:
        base.setdefault(support.source_graph or support.sense_uri, (support, _base_source_name(support)))
    counts = Counter(name for _, name in base.values())
    names: dict[str, str] = {}
    for key, (support, name) in base.items():
        edition = _clean_edition(support.edition) if support.edition else None
        if counts[name] > 1 and edition and edition not in name:
            name = f"{name} ({edition})"
        names[key] = name
    return names


def _support_key(support: SourceSupport) -> str:
    return support.source_graph or support.sense_uri


def _context_lemma_for(request: AskRequest) -> str | None:
    if request.lemma or not request.context_lemma:
        return request.lemma
    normalized = request.query.casefold()
    explicitly_names_word = "คำว่า" in normalized
    if re.search(r"เกี่ยวข้องกับ\s*\S+", normalized):
        return None
    is_followup = (
        normalized.lstrip().startswith("แล้ว")
        or any(marker in normalized for marker in (
            *_OTHER_SENSE_MARKERS, "คำนี้", "คำนั้น", "อธิบายเพิ่มเติม",
            "ขยายความ", "รายละเอียดเพิ่ม",
        ))
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
        target = (lemma or "").casefold().strip()
        # A cue that contains the word itself explains nothing ("ดาวสว่างมาก").
        if normalized_cue == target or (target and target in normalized_cue):
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
        dataset=candidate.dataset,
        edition=(definition.edition if definition else candidate.edition),
        source_url=(definition.source_url if definition else candidate.source_url),
        license=(definition.license if definition else candidate.license),
        attribution=(definition.attribution if definition else candidate.attribution),
    )


def _llm_failure_reason(exc: Exception) -> str:
    """Short, key-free reason that the UI can map to a readable label."""
    if isinstance(exc, ThaiLLMStructuredOutputError):
        return f"llm_structured_output_invalid:{exc.code}"
    if isinstance(exc, ThaiLLMUnavailableError):
        return "llm_unavailable:paused_after_server_errors"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"llm_request_failed:HTTPStatusError:{exc.response.status_code}"
    return f"llm_request_failed:{type(exc).__name__}"


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

    def _conversation_response(
        self, request: AskRequest, answer: str, *, started: float,
        retrieval_ms: int = 0, llm_ms: int = 0, from_model: bool = False,
        fallback_reason: str | None = None,
    ) -> AskResponse:
        total_ms = max(0, round((perf_counter() - started) * 1000))
        requested_provider = self.provider if self.provider in {
            "heuristic", "openai", "thaillm"
        } else "heuristic"
        selector = "thaillm" if from_model else "none"
        empty_graph = GraphResult(hops=request.hops)
        return AskResponse(
            query=request.query,
            detected_lemma=None,
            candidates=[],
            selection=SelectionDecision(
                selected_sense_uri=None,
                confidence=0.0,
                rationale="ข้อความสนทนาทั่วไป ไม่ต้องเลือกความหมายจากพจนานุกรม",
                evidence_ids=[],
                selector=selector,
                intent="conversation",
            ),
            answer=answer,
            detail=AnswerDetail(
                intent="conversation",
                title="สนทนากับสานศัพท์",
                summary=answer,
                explanation="คำตอบสนทนาทั่วไป ไม่ได้อ้างว่าเป็นข้อมูลจากพจนานุกรม",
            ),
            citations=[],
            graph=empty_graph,
            explanation_graph=empty_graph,
            diagnostics=AskDiagnostics(
                requested_provider=requested_provider,
                actual_selector=selector,
                model=self.model if from_model else None,
                answer_mode="model" if from_model else "template",
                fallback_used=fallback_reason is not None,
                fallback_reason=fallback_reason,
                latency_ms=total_ms,
            ),
            timings=AskTimings(
                retrieval_ms=retrieval_ms, llm_ms=llm_ms, validation_ms=0,
                graph_ms=0, total_ms=total_ms,
            ),
            evidence_validated=False,
        )

    async def _resolve_target(
        self, target: str, *, user_named: bool = False, query: str = ""
    ) -> str | None:
        """Map the model's target word onto a lemma that exists in the graph."""
        lexicon = getattr(self.retriever, "lexicon", None)
        if lexicon is None:
            return target
        if await lexicon.contains(target):
            return target
        if not user_named:
            # In a sentence the model may return a phrase ("ขันนอต"); the
            # dictionary word inside it is what the user wants explained.
            for text in (target, query):
                mentions = await lexicon.mentions(text)
                if mentions:
                    return mentions[0]
            return None
        # Only trim trailing question words or particles ("ดาวอะ" -> "ดาว").
        # Never swap in a different word: "ฟหกด" must stay not found rather
        # than become "กด" and receive an invented definition.
        prefix = await lexicon.longest_prefix(target)
        if prefix:
            rest = target[len(prefix):]
            tokens = await lexicon.context_tokens(rest, None) if rest.strip() else []
            if not any(weight == CONTENT_WEIGHT for _, weight in tokens):
                return prefix
        return None

    async def _is_conversation_opener(self, request: AskRequest) -> bool:
        """Greeting or "I want to ask" with no word, sentence or follow-up to look up."""
        query = request.query.strip()
        if (
            not query or request.lemma or _looks_lexical(query)
            or explicit_term(query)[0] is not None or _context_lemma_for(request)
        ):
            return False
        lexicon = getattr(self.retriever, "lexicon", None)
        if lexicon is not None and await lexicon.contains(query):
            return False  # a bare word such as "ถาม" is a dictionary lookup
        tokens = (
            await lexicon.context_tokens(query, None)
            if lexicon is not None else context_tokens(query, None)
        )
        return not any(weight == CONTENT_WEIGHT for _, weight in tokens)

    async def _has_context(self, query: str, lemma: str) -> bool:
        lexicon = getattr(self.retriever, "lexicon", None)
        tokens = (
            await lexicon.context_tokens(query, lemma)
            if lexicon is not None else context_tokens(query, lemma)
        )
        return any(weight == CONTENT_WEIGHT for _, weight in tokens)

    async def ask(self, request: AskRequest) -> AskResponse:
        started = perf_counter()
        request = request.model_copy(update={"query": normalize_typing(request.query)})
        conversation_reply = _conversation_reply(request.query)
        if conversation_reply is not None:
            # An exact greeting needs no model call.
            return self._conversation_response(request, conversation_reply, started=started)
        wants_llm = self.default_to_llm if request.use_llm is None else request.use_llm
        llm_first = wants_llm and isinstance(self.llm_selector, ThaiLLMChatSenseSelector)
        analysis: QueryAnalysis | None = None
        analysis_ms = 0
        analysis_failure: str | None = None
        if llm_first:
            analysis_started = perf_counter()
            try:
                analysis = await self.llm_selector.analyze(
                    request.query, history=request.history, current_word=request.context_lemma
                )
            except Exception as exc:
                analysis_failure = _llm_failure_reason(exc)
                logger.warning("ThaiLLM question analysis failed, using rules: %s", analysis_failure)
            analysis_ms = max(0, round((perf_counter() - analysis_started) * 1000))

        warnings: list[str] = []
        unmatched_llm_ms = analysis_ms
        requested_term: str | None = None
        if analysis is not None:
            if analysis.kind == "conversation":
                reply = analysis.reply.strip()
                return self._conversation_response(
                    request, reply or _OPENER_REPLY, started=started,
                    llm_ms=analysis_ms, from_model=bool(reply),
                )
            preliminary_intent = analysis.intent
            question_has_context = analysis.has_context
            requested_term = normalize_typing(analysis.target_word or "").strip().strip("“”\"'") or None
            retrieval_started = perf_counter()
            lemma = (
                await self._resolve_target(
                    requested_term,
                    # Without a context sentence the target is the word the user
                    # typed, so a different word must not be substituted for it:
                    # "ตูกมีดแปลว่าอะไร" is not a question about "มีด".
                    user_named=(
                        explicit_term(request.query)[0] is not None
                        or not analysis.has_context
                    ),
                    query=request.query,
                )
                if requested_term else None
            )
            detected, candidates = (
                await self.retriever.retrieve(
                    request.query, lemma=lemma, max_candidates=request.max_candidates
                )
                if lemma else (None, [])
            )
            candidates = _apply_sense_context(request, candidates, detected_lemma=detected)
            retrieval_ms = max(0, round((perf_counter() - retrieval_started) * 1000))
        else:
            if await self._is_conversation_opener(request):
                return self._conversation_response(
                    request, _small_talk_fallback(request.query) or _OPENER_REPLY,
                    started=started, llm_ms=analysis_ms, fallback_reason=analysis_failure,
                )
            preliminary_intent = detect_intent(request.query)
            retrieval_lemma = _context_lemma_for(request)
            retrieval_started = perf_counter()
            detected, candidates = await self.retriever.retrieve(
                request.query, lemma=retrieval_lemma, max_candidates=request.max_candidates
            )
            if (
                not candidates and request.context_lemma and request.history
                and "คำว่า" not in request.query
                and _context_lemma_for(request) == request.context_lemma
            ):
                detected, candidates = await self.retriever.retrieve(
                    request.query,
                    lemma=request.context_lemma,
                    max_candidates=request.max_candidates,
                )
            candidates = _apply_sense_context(request, candidates, detected_lemma=detected)
            retrieval_ms = max(0, round((perf_counter() - retrieval_started) * 1000))
            if not candidates and not _looks_lexical(request.query):
                fallback = _small_talk_fallback(request.query)
                if fallback is not None:
                    return self._conversation_response(
                        request, fallback, started=started, retrieval_ms=retrieval_ms,
                        fallback_reason=analysis_failure,
                    )
            if _is_bare_lemma_query(request.query, detected):
                preliminary_intent = "define_all"
            question_has_context = bool(detected) and await self._has_context(
                request.query, detected
            )
            if (
                preliminary_intent == "define" and detected and not question_has_context
                and (
                    (not request.history and not request.context_sense_uri)
                    # "ไก่แปลว่า" names the word again, so it is not a follow-up on
                    # the previously selected sense.
                    or detected.casefold() in request.query.casefold()
                )
            ):
                # e.g. "ดาว มีความหมายอะไรบ้าง": nothing in the message can pick
                # one sense, so list them instead of guessing.
                preliminary_intent = "define_all"
        selection_candidates = [c for c in candidates if c.source != "demo"] or candidates

        relation_facts: dict[str, list[dict[str, str]]] = {}
        word_facts: dict[str, dict[str, object]] = {}
        llm_candidates = selection_candidates[:LLM_CANDIDATES]
        if llm_first and llm_candidates:
            if preliminary_intent == "related":
                context_graph = await self.repository.graph(
                    [candidate.sense_uri for candidate in llm_candidates],
                    hops=2,
                    max_edges=min(100, max(40, self.max_graph_edges)),
                )
                relation_facts = self._relation_context(context_graph, llm_candidates)
            details_batch = getattr(self.repository, "get_sense_details_batch", None)
            needs_word_facts = preliminary_intent == "word_info" or any(
                marker in request.query for marker in _WORD_INFO_MARKERS
            )
            word_facts = self._word_facts(
                llm_candidates,
                await details_batch(llm_candidates) if needs_word_facts and callable(details_batch) else {},
            )
        llm_ms = unmatched_llm_ms
        fallback_reason: str | None = None
        analysis_intent = analysis.intent if analysis is not None else None
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
                        request.query, llm_candidates, history=request.history,
                        relation_facts=relation_facts,
                        question_has_context=question_has_context,
                        word_facts=word_facts,
                        required_intent=analysis_intent,
                    )
                else:
                    decision = await self.llm_selector.select(
                        request.query, selection_candidates, history=request.history
                    )
            except Exception as exc:
                # The graph still holds the facts; answer from it and say
                # plainly that the model was not used for this reply.
                fallback_reason = _llm_failure_reason(exc)
                logger.warning("ThaiLLM selection failed, using heuristic: %s", fallback_reason)
                decision = await self.heuristic_selector.select(
                    request.query, selection_candidates, history=request.history
                )
            finally:
                llm_ms = analysis_ms + max(0, round((perf_counter() - llm_started) * 1000))
        else:
            decision = await self.heuristic_selector.select(
                request.query, selection_candidates, history=request.history
            )

        validation_started = perf_counter()
        validation_errors = validate_selection(decision, selection_candidates)
        if validation_errors and wants_llm and fallback_reason is None:
            fallback_reason = f"llm_evidence_validation_failed:{validation_errors[0]}"
            logger.warning("ThaiLLM selection rejected, using heuristic: %s", fallback_reason)
            decision = await self.heuristic_selector.select(
                request.query, selection_candidates, history=request.history
            )
            validation_errors = validate_selection(decision, selection_candidates)
        if validation_errors:
            warnings.extend(validation_errors)
        if (
            analysis is None
            and fallback_reason is None
            and decision.selector in {"thaillm", "openai"}
            and decision.intent == "define_all"
            and preliminary_intent == "define"
            and question_has_context
            and detected and detected.casefold() in request.query.casefold()
            and explicit_term(request.query)[0] is None
        ):
            # The user wrote a sentence that uses the word; listing every sense
            # ignores it. Pick from the context and say the model was overridden.
            fallback_reason = "llm_intent_overridden:define_all_with_context"
            logger.info("ThaiLLM listed all senses despite context; using heuristic selection")
            decision = await self.heuristic_selector.select(
                request.query, selection_candidates, history=request.history
            )
            decision = decision.model_copy(update={"intent": "define"})
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
        intent_corrected = (
            analysis is None and preliminary_intent == "define_all" and intent == "define"
        )
        if analysis is not None and decision.selector == "thaillm" and intent != analysis.intent:
            # The analysis step already read the question; keep its intent unless
            # that would need a sense the answer step did not choose.
            if analysis.intent in _MULTI_SENSE_INTENTS or decision.selected_sense_uri:
                intent = analysis.intent
            else:
                intent = "define_all"
        if analysis is None and intent == "word_info" and not any(
            marker in request.query for marker in _WORD_INFO_MARKERS
        ):
            intent_corrected = True
            intent = (
                "define_all" if preliminary_intent in {"define", "define_all"}
                else preliminary_intent
            )
        if intent_corrected and intent == "define":
            intent = "define_all"
        if intent in {"compare", "word_info"} and decision.selected_sense_uri:
            decision = decision.model_copy(update={"selected_sense_uri": None, "intent": intent})
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
        elif intent in _MULTI_SENSE_INTENTS and candidates:
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
            requested_term=detected or requested_term or display_term(request.query),
            all_senses=all_senses, alignments=alignments, supports=supports,
            related_terms=related_terms, decision=decision, citations=citations, graph=graph,
        )
        answer = self._compose_answer(detail, cue_words=decision.cue_words)
        answer_mode = "template"
        decision = decision.model_copy(
            update={"grounded_answer": _strip_internal_refs(decision.grounded_answer)}
        )
        # Without the analysis step, define_all keeps the graph listing; with it,
        # the model writes the grouped, sourced answer and the listing is only
        # the fallback.
        if (
            decision.selector == "thaillm"
            and (intent != "define_all" or analysis is not None)
            and self._usable_model_answer(
                decision.grounded_answer, lemma=detected,
                # "อ้วน อ่านว่า /ʔua̯n˥˩/" is a complete answer to a word-info question.
                min_length=12 if intent == "word_info" else 30,
            )
        ):
            # A word_info answer comes from etymology/pronunciation data, so only
            # the sources that supplied such data are credited.
            fact_graphs = {
                candidate.source_graph for candidate in llm_candidates
                if {"etymology", "pronunciations"} & set(word_facts.get(candidate.sense_uri, {}))
            } if intent == "word_info" else None
            answer = self._with_source_attribution(
                decision.grounded_answer.strip(), detail, only_graphs=fact_graphs
            )
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
                fallback_used=(fallback_reason or analysis_failure) is not None,
                fallback_reason=fallback_reason or analysis_failure,
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
            dataset=(
                candidate.dataset if item.source_graph == candidate.source_graph else None
            ),
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
        if intent in _MULTI_SENSE_INTENTS:
            result: list[SourceSupport] = []
            grouped: dict[str, list[SenseCandidate]] = {}
            for candidate in usable:
                grouped.setdefault(candidate.source_graph, []).append(candidate)
            # Organizer dictionaries first, then the public sources, so a
            # listing does not open with an English gloss.
            for graph in sorted(
                grouped, key=lambda item: (_SOURCE_ORDER.get(grouped[item][0].source, 8), item)
            ):
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
    def _word_facts(
        candidates: list[SenseCandidate], details: dict[str, object]
    ) -> dict[str, dict[str, object]]:
        """Etymology, pronunciation and source POS for the model; values stay short."""
        facts: dict[str, dict[str, object]] = {}
        names = _source_names([
            _support(candidate, relation="unlinked", confidence=None, review_status="unlinked")
            for candidate in candidates
        ])
        for candidate in candidates:
            item = details.get(candidate.sense_uri)
            entry: dict[str, object] = {
                "source_name": names.get(candidate.source_graph, _source_label(candidate.source)),
            }
            if item is None:
                facts[candidate.sense_uri] = entry
                continue
            if candidate.original_pos:
                entry["source_pos"] = candidate.original_pos
            if item.etymologies:
                entry["etymology"] = _shorten_at_boundary(item.etymologies[0].value, 700)
            pronunciations = list(dict.fromkeys(
                value.value for value in (*item.pronunciations, *item.romanizations)
            ))[:3]
            if pronunciations:
                entry["pronunciations"] = pronunciations
            translations = list(dict.fromkeys(value.value for value in item.translations))[:5]
            if translations:
                entry["translations"] = translations
            facts[candidate.sense_uri] = entry
        return facts

    @staticmethod
    def _usable_model_answer(
        answer: str | None, *, lemma: str | None,
        required_definitions: list[str] | None = None,
        min_length: int = 30,
    ) -> bool:
        if not answer or not lemma:
            return False
        cleaned = answer.strip()
        valid_shape = (
            len(cleaned) >= min_length
            and lemma in cleaned
            and "http://" not in cleaned
            and "https://" not in cleaned
            and re.search(r"\bS\d+(?:-E\d+)?\b", cleaned) is None
            and not cleaned.startswith("{")
        )
        if not valid_shape:
            return False
        if required_definitions is not None:
            distinct = {_chat_definition(item) for item in required_definitions if item.strip()}
            return bool(distinct) and all(definition in cleaned for definition in distinct)
        return True

    @staticmethod
    def _build_detail(
        *, intent: str, lemma: str | None, selected: SenseCandidate | None,
        candidates: list[SenseCandidate], all_senses: list[SenseCandidate],
        alignments: list[AlignmentCandidate], supports: list[SourceSupport],
        related_terms: list[RelatedTerm], decision: SelectionDecision,
        citations: list[Citation], graph: GraphResult,
        requested_term: str | None = None,
    ) -> AnswerDetail:
        if not lemma or not candidates or (
            selected is None and intent not in _MULTI_SENSE_INTENTS
        ):
            return AnswerDetail(
                intent=intent, title="ยังไม่พบคำที่ต้องการ",
                summary=(
                    f"ไม่พบคำว่า “{requested_term}” ในชุดข้อมูลที่นำเข้า"
                    if requested_term and not candidates
                    else "ไม่พบ Lexical Entry หรือ Candidate Sense ที่ตรงกับคำถาม"
                ),
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
        if intent in {"define_all", "word_info"}:
            title = f"ความหมายของ “{lemma}”"
            distinct_meanings = {
                (_chat_definition(item.definition), item.pos or "")
                for item in supports if item.definition
            }
            summary = (
                f"พบ {len(distinct_meanings) or len(supports)} ความหมาย "
                f"จาก {len({_support_key(item) for item in supports})} แหล่งข้อมูล"
            )
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
                    if intent in {"define_all", "word_info"}
                    else "จัดกลุ่ม Source Senses"
                    if intent == "compare"
                    else "เลือกความหมายตามบริบท"
                ),
                detail=(
                    "คำถามไม่มีบริบทเพียงพอ จึงแสดงทุกความหมายโดยไม่เลือก Sense เดียว"
                    if intent in {"define_all", "word_info"}
                    else
                    "แสดง Sense ของแต่ละ Dataset แยกจากกัน โดยไม่เลือก Sense ใดเป็นคำตอบกลาง"
                    if intent == "compare"
                    else f"เลือก {selected.pos or 'Sense'} จาก {_source_label(selected.source)} "
                         f"ด้วย {decision.selector}; confidence {round(decision.confidence * 100)}%"
                ),
                evidence_ids=(
                    []
                    if intent in _MULTI_SENSE_INTENTS
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
            term = re.search(r"“(.+)”", detail.summary)
            asked = f" (“{term.group(1)}”)" if term else ""
            return (
                f"ยังไม่พบคำที่ถาม{asked} ในชุดข้อมูลที่นำเข้าครับ "
                "ลองพิมพ์คำเป้าหมายในเครื่องหมายคำพูด เช่น คำว่า “ดาว” หมายถึงอะไร "
                "หรือเพิ่มชุดข้อมูลที่มีคำนี้ก่อน"
            )
        cues = list(dict.fromkeys(cue_words or []))[:3]
        primary = next(
            (
                support for support in detail.source_supports
                if support.review_status == "selected"
            ),
            detail.source_supports[0] if detail.source_supports else None,
        )
        if detail.intent in {"define_all", "word_info"}:
            lemma = detail.title.removeprefix("ความหมายของ ").strip("“”")
            names = _source_names(detail.source_supports)
            # Identical definition text from different datasets is shown once,
            # but every dataset that states it stays visible on that line.
            meaning_rows: dict[tuple[str, str], list[SourceSupport]] = {}
            for support in detail.source_supports:
                if support.definition:
                    key = (_chat_definition(support.definition), support.pos or "")
                    meaning_rows.setdefault(key, []).append(support)
            meaning_lines: list[str] = []
            for index, ((definition, pos), rows) in enumerate(meaning_rows.items(), start=1):
                meaning_lines.append(
                    f"{index}. {definition}" + (f" — {_pos_label(pos)}" if pos else "")
                )
                sources = list(dict.fromkeys(names[_support_key(row)] for row in rows))
                meaning_lines.append("- ที่มา: " + ", ".join(sources))
                example = next((row.example for row in rows if row.example), None)
                if example:
                    meaning_lines.append(f"- ตัวอย่าง: “{example}”")
            if not meaning_lines:
                return f"พบคำว่า “{lemma}” แต่ยังไม่มีคำนิยามในข้อมูลที่นำเข้าครับ"
            return "\n\n".join([
                f"คำว่า “{lemma}” พบ {len(meaning_rows)} ความหมายในข้อมูลที่นำเข้าดังนี้ครับ",
                "\n".join(meaning_lines),
                "ยังไม่มีประโยคประกอบ จึงไม่เลือกความหมายเดียวแทนผู้ใช้ครับ "
                "ถ้าส่งประโยคที่ใช้คำนี้มา ผมจะช่วยดูว่าเข้ากับความหมายไหน",
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

            names = _source_names(detail.source_supports)
            primary_name = names[_support_key(primary)]
            other_names = list(dict.fromkeys(
                names[_support_key(support)]
                for support in detail.source_supports
                if _support_key(support) != _support_key(primary)
            ))
            source_text = f"ความหมายหลักอ้างอิงจาก{primary_name}"
            if other_names:
                source_text += " และพบข้อมูลเทียบเคียงใน" + _thai_join(other_names)
            lines.append(source_text + " คุณสามารถเปิดดูหลักฐานของแต่ละแหล่งเพิ่มเติมได้")
        return "\n\n".join(lines)

    @staticmethod
    def _support_display_name(support: SourceSupport) -> str:
        if support.source == "organizer":
            name = _base_source_name(support)
            edition = _clean_edition(support.edition) if support.edition else None
            return f"{name} ({edition})" if edition and edition not in name else name
        source = _source_label(support.sense_source or support.source)
        if not support.edition:
            return source
        return f"{_source_label(support.source)} ({_clean_edition(support.edition)})"

    @staticmethod
    def _with_source_attribution(
        answer: str, detail: AnswerDetail, *, only_graphs: set[str] | None = None
    ) -> str:
        """Model prose must still say where the facts came from."""
        supports = [
            item for item in detail.source_supports
            if not only_graphs or item.source_graph in only_graphs
        ] or detail.source_supports
        names = list(dict.fromkeys(_source_names(supports).values()))
        if not names or any(name in answer for name in names):
            return answer
        return f"{answer}\n\nที่มา: {', '.join(names)}"

    @staticmethod
    def _compose_selection_rationale(
        detail: AnswerDetail, *, cue_words: list[str] | None = None
    ) -> str:
        cues = list(dict.fromkeys(cue_words or []))[:3]
        cue_text = ", ".join(f"“{cue}”" for cue in cues)
        if detail.intent in {"define_all", "word_info"}:
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
        if detail.intent in _MULTI_SENSE_INTENTS:
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
