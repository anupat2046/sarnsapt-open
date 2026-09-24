from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .models import ConversationTurn, SelectionDecision, SenseCandidate


class SenseSelector(Protocol):
    async def select(
        self,
        query: str,
        candidates: list[SenseCandidate],
        *,
        history: list[ConversationTurn] | None = None,
    ) -> SelectionDecision: ...


class ThaiLLMUnavailableError(RuntimeError):
    """ThaiLLM failed with server errors recently; calls are paused briefly."""


class ThaiLLMStructuredOutputError(ValueError):
    """Safe, non-content-bearing reason for rejecting a ThaiLLM response."""

    def __init__(self, code: str, *, attempts: int = 1) -> None:
        self.code = code
        self.attempts = attempts
        super().__init__(f"{code} after {attempts} attempt(s)")


class _ThaiLLMSelectionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Literal["define", "define_all", "related", "compare", "word_info"]
    selected_candidate_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    cue_words: list[str] = Field(default_factory=list, max_length=8)
    rationale: str = Field(min_length=1, max_length=500)
    # ThaiLLM sometimes returns every valid Evidence Ref for compare requests even
    # when the prompt asks for at most eight.  Accept the bounded provider output
    # here, then normalize it deterministically below.  Unknown references still
    # fail validation and never reach the answer.
    evidence_refs: list[str] = Field(min_length=1, max_length=64)
    grounded_answer: str | None = Field(default=None, max_length=2200)


_ANALYSIS_EXAMPLES: list[tuple[dict[str, Any], dict[str, Any]]] = [
    ({"current_word": None, "question": "ขอถามอะไรหน่อยครับ"},
     {"kind": "conversation", "target_word": None, "question_type": None, "has_context": False,
      "reply": "ได้เลยครับ อยากรู้เรื่องคำไหน พิมพ์มาได้เลยครับ"}),
    ({"current_word": None, "question": "ผอม เป็นคำชนิดไหน"},
     {"kind": "lexical", "target_word": "ผอม", "question_type": "word_properties", "has_context": False, "reply": ""}),
    ({"current_word": "แมว", "question": "แมว"},
     {"kind": "lexical", "target_word": "แมว", "question_type": "meaning", "has_context": False, "reply": ""}),
    ({"current_word": None, "question": "แม่ตำน้ำพริกอยู่ในครัว"},
     {"kind": "lexical", "target_word": "ตำ", "question_type": "meaning", "has_context": True, "reply": ""}),
    ({"current_word": None, "question": "คำว่า น้ำ แปลว่าอะไร"},
     {"kind": "lexical", "target_word": "น้ำ", "question_type": "meaning", "has_context": False, "reply": ""}),
    ({"current_word": "หนาว", "question": "แล้วคำตรงข้ามล่ะ"},
     {"kind": "lexical", "target_word": "หนาว", "question_type": "relations", "has_context": False, "reply": ""}),
    ({"current_word": None, "question": "แต่ละพจนานุกรมให้ความหมายคำว่า เรือ ต่างกันยังไง"},
     {"kind": "lexical", "target_word": "เรือ", "question_type": "compare_sources", "has_context": False, "reply": ""}),
]

_QUESTION_TYPE_INTENT = {
    "relations": "related",
    "compare_sources": "compare",
    "word_properties": "word_info",
}


class QueryAnalysis(BaseModel):
    """What the user is asking, as read by the model before any lookup."""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["conversation", "lexical"]
    target_word: str | None = Field(default=None, max_length=100)
    question_type: Literal["meaning", "relations", "compare_sources", "word_properties"] | None = None
    intent: Literal["define", "define_all", "related", "compare", "word_info"] | None = None
    has_context: bool = False
    reply: str = Field(default="", max_length=600)

    @model_validator(mode="after")
    def _derive_intent(self) -> "QueryAnalysis":
        # The model answers two plain questions (what is asked, is there a
        # sentence); combining them here cannot produce a contradiction such
        # as "has context" together with "list every meaning".
        if self.question_type == "meaning":
            self.intent = "define" if self.has_context else "define_all"
        elif self.question_type is not None:
            self.intent = _QUESTION_TYPE_INTENT[self.question_type]
        elif self.intent is None:
            self.intent = "define_all"
        return self


class _ThaiLLMUnmatchedPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Literal["conversation", "lexical"]
    answer: str = Field(default="", max_length=600)


def _best_definition_text(candidate: SenseCandidate) -> str | None:
    return next(
        (item.text.strip() for item in candidate.evidence
         if item.kind in {"definition", "synset-definition"}),
        None,
    )


EVIDENCE_PER_CANDIDATE = 3
HISTORY_TURNS = 6
HISTORY_CHARS = 300


def compact_history(history: list[ConversationTurn] | None) -> list[dict[str, str]]:
    """Recent turns, each shortened.

    The model needs to know which word the conversation is about, not the full
    text of earlier answers; long answers crowd out the output budget and get
    copied into the next reply.
    """
    turns = []
    for turn in (history or [])[-HISTORY_TURNS:]:
        content = turn.content.strip()
        if len(content) > HISTORY_CHARS:
            content = content[:HISTORY_CHARS].rstrip() + " …"
        turns.append({"role": turn.role, "content": content})
    return turns


class HeuristicSenseSelector:
    async def select(
        self,
        query: str,
        candidates: list[SenseCandidate],
        *,
        history: list[ConversationTurn] | None = None,
    ) -> SelectionDecision:
        if not candidates:
            return SelectionDecision(
                selected_sense_uri=None,
                confidence=0.0,
                rationale="ไม่พบ Candidate Sense จากฐานข้อมูล",
                evidence_ids=[],
                selector="none",
            )
        selected = candidates[0]
        evidence = sorted(
            selected.evidence,
            key=lambda item: (item.kind not in {"definition", "synset-definition"}, item.kind),
        )
        # Confidence reflects how much of the context this sense explains and
        # how clearly it beats the next distinct sense, not a fixed prior.
        runner_up = max(
            (
                item.context_score for item in candidates[1:]
                if _best_definition_text(item) != _best_definition_text(selected)
            ),
            default=0.0,
        )
        top = selected.context_score
        margin = (top - runner_up) / top if top > 0 else 0.0
        confidence = min(0.85, 0.25 + 0.6 * top * max(0.0, margin))
        return SelectionDecision(
            selected_sense_uri=selected.sense_uri,
            confidence=round(confidence, 3),
            rationale=(
                "เลือกจากคำในบริบทที่ตรงกับนิยาม/ตัวอย่างในกราฟ "
                "โดยไม่สร้างข้อเท็จจริงใหม่"
                if top > 0 else
                "ไม่พบคำในบริบทที่ตรงกับนิยาม จึงเลือกความหมายที่มีข้อมูลครบที่สุด"
            ),
            evidence_ids=[item.evidence_id for item in evidence[:3]],
            selector="heuristic",
            cue_words=selected.context_cues[:3],
        )


class OpenAIResponsesSenseSelector:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def select(
        self,
        query: str,
        candidates: list[SenseCandidate],
        *,
        history: list[ConversationTurn] | None = None,
    ) -> SelectionDecision:
        if not candidates:
            return await HeuristicSenseSelector().select(
                query, candidates, history=history
            )
        sense_uris = [candidate.sense_uri for candidate in candidates]
        evidence_ids = [
            item.evidence_id for candidate in candidates for item in candidate.evidence
        ]
        schema = {
            "type": "object",
            "properties": {
                "selected_sense_uri": {"type": "string", "enum": sense_uris},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
                "evidence_ids": {
                    "type": "array",
                    "items": {"type": "string", "enum": evidence_ids},
                    "maxItems": 5,
                },
            },
            "required": [
                "selected_sense_uri",
                "confidence",
                "rationale",
                "evidence_ids",
            ],
            "additionalProperties": False,
        }
        compact_candidates = [
            {
                "sense_uri": candidate.sense_uri,
                "lemma": candidate.lemma,
                "pos": candidate.pos,
                "source": candidate.source,
                "evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "kind": item.kind,
                        "text": item.text,
                    }
                    for item in candidate.evidence[:8]
                ],
            }
            for candidate in candidates
        ]
        payload = {
            "model": self.model,
            "store": False,
            "instructions": (
                "Select exactly one supplied Thai lexical sense for the question. "
                "Use only the supplied sense_uri and evidence_id values. Do not invent facts."
            ),
            "input": json.dumps(
                {
                    "conversation_history": compact_history(history),
                    "question": query,
                    "candidates": compact_candidates,
                },
                ensure_ascii=False,
            ),
            "max_output_tokens": 500,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "thai_sense_selection",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        response = await self._client.post(f"{self.base_url}/responses", json=payload)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        output_text = data.get("output_text") or self._extract_output_text(data)
        parsed = json.loads(output_text)
        return SelectionDecision.model_validate({**parsed, "selector": "openai"})

    @staticmethod
    def _extract_output_text(data: dict[str, Any]) -> str:
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return content["text"]
        raise ValueError("OpenAI response did not contain output_text")

    async def close(self) -> None:
        await self._client.aclose()


class ThaiLLMChatSenseSelector:
    """Selects a retrieved Sense and drafts a grounded conversational answer."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://thaillm.or.th/api/v1",
        timeout: float = 30.0,
        max_tokens: int = 1200,
        temperature: float = 0.1,
        max_attempts: int = 2,
        retry_delay_seconds: float = 1.0,
        outage_cooldown_seconds: float = 20.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_attempts = max(1, max_attempts)
        self.retry_delay_seconds = retry_delay_seconds
        self.outage_cooldown_seconds = outage_cooldown_seconds
        self._paused_until = 0.0
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def select(
        self,
        query: str,
        candidates: list[SenseCandidate],
        *,
        history: list[ConversationTurn] | None = None,
        relation_facts: dict[str, list[dict[str, str]]] | None = None,
        question_has_context: bool = False,
        word_facts: dict[str, dict[str, Any]] | None = None,
        required_intent: str | None = None,
    ) -> SelectionDecision:
        if not candidates:
            return await HeuristicSenseSelector().select(
                query, candidates, history=history
            )

        candidate_by_alias: dict[str, SenseCandidate] = {}
        evidence_by_alias: dict[str, tuple[str, str]] = {}
        compact_candidates = []
        for candidate_index, candidate in enumerate(candidates, start=1):
            candidate_id = f"S{candidate_index}"
            candidate_by_alias[candidate_id] = candidate
            evidence = []
            diverse_evidence = []
            represented_graphs: set[str] = set()
            for item in candidate.evidence:
                if item.source_graph not in represented_graphs:
                    diverse_evidence.append(item)
                    represented_graphs.add(item.source_graph)
                if len(diverse_evidence) >= EVIDENCE_PER_CANDIDATE:
                    break
            if len(diverse_evidence) < EVIDENCE_PER_CANDIDATE:
                selected_ids = {item.evidence_id for item in diverse_evidence}
                diverse_evidence.extend(
                    item for item in candidate.evidence
                    if item.evidence_id not in selected_ids
                )
            for evidence_index, item in enumerate(diverse_evidence[:EVIDENCE_PER_CANDIDATE], start=1):
                evidence_ref = f"{candidate_id}-E{evidence_index}"
                evidence_by_alias[evidence_ref] = (candidate_id, item.evidence_id)
                evidence.append(
                    {
                        "evidence_ref": evidence_ref,
                        "kind": item.kind,
                        "text": item.text,
                        "source": item.evidence_source,
                        "edition": item.edition,
                    }
                )
            compact_candidates.append(
                {
                    "candidate_id": candidate_id,
                    "lemma": candidate.lemma,
                    "pos": candidate.pos,
                    "source": candidate.source,
                    "edition": candidate.edition,
                    "evidence": evidence,
                    "relations": (relation_facts or {}).get(candidate.sense_uri, [])[:6],
                    "word_info": (word_facts or {}).get(candidate.sense_uri, {}),
                }
            )
        task = {
            "conversation_history": compact_history(history),
            "question": query,
            "question_has_context": question_has_context,
            "question_analysis": {"intent": required_intent} if required_intent else None,
            "candidates": compact_candidates,
            "output_contract": {
                "intent": "define_all, define, related, compare, or word_info",
                "selected_candidate_id": (
                    "must be null for define_all/compare/word_info; otherwise must equal one supplied candidate_id"
                ),
                "confidence": "number from 0 to 1",
                "cue_words": "short words or phrases from the user question that support the decision",
                "rationale": "short Thai explanation grounded in context/evidence",
                "evidence_refs": (
                    "1-8 supplied evidence_ref values; for define/related they must belong "
                    "to the selected candidate"
                ),
                "grounded_answer": (
                    "Thai conversational answer that directly answers every part of the question. "
                    "For define_all: a short Thai lead sentence, then a numbered list with one line "
                    "per distinct meaning (merge candidates that state the same meaning, at most 6 "
                    "items), each line giving the meaning briefly in Thai followed by (ที่มา: "
                    "word_info.source_name, ...). Prefer Thai evidence; a meaning found only in English "
                    "evidence may be summarised in Thai and marked (คำอธิบายภาษาอังกฤษ). "
                    "For compare: a short comparison of how the sources divide the meanings. "
                    "For define, related and word_info: answer fully but concisely. "
                    "Use only supplied evidence, relations and word_info; if a requested fact is "
                    "missing, say so plainly. Do not invent agreement or relationships."
                ),
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "คุณเป็นตัวเลือกความหมายคำภาษาไทยในระบบสานศัพท์ "
                    "ให้จำแนก intent และเลือก Sense เฉพาะเมื่อมีบริบท ในคำขอเดียว "
                    "ใช้ข้อเท็จจริงจาก evidence ที่ให้เท่านั้น ห้ามใช้ความจำภายนอก "
                    "ห้ามสร้าง Candidate ID, Evidence Ref, ความหมาย หรือความสัมพันธ์ใหม่ "
                    "ใช้ intent=define_all เมื่อผู้ใช้ถามความหมายทั่วไป เช่น คำนี้หมายความว่าอะไร "
                    "คืออะไร หรือแปลว่าอะไร โดยไม่มีประโยคหรือคำแวดล้อมที่แยกความหมายได้ "
                    "กรณี define_all ต้องตั้ง selected_candidate_id=null, cue_words=[] และอ้าง "
                    "evidence อย่างน้อยหนึ่งรายการจากแต่ละ Candidate ห้ามถือคำเป้าหมายหรือ "
                    "วลีคำถามทั่วไปว่าเป็นคำใบ้ ใช้ intent=define เมื่อมีบริบทจริงที่ช่วยเลือก "
                    "Sense เดียว ถ้า question_has_context=true แปลว่าผู้ใช้ใส่ประโยคหรือคำแวดล้อมมาแล้ว "
                    "ห้ามตอบ define_all ต้องเลือก Sense ที่เข้ากับบริบทที่สุด (เว้นแต่ขอเปรียบเทียบหรือถามความสัมพันธ์) "
                    "ใช้ intent=word_info เมื่อผู้ใช้ถามข้อมูลของตัวคำ เช่น รากศัพท์ ที่มาของคำ การออกเสียง "
                    "หรือชนิดคำ/ลักษณะคำ (เช่น อ้วนเป็นคำแบบไหน) โดยตั้ง selected_candidate_id=null "
                    "ตอบจาก pos และ word_info (source_name, source_pos, etymology, pronunciations, translations) "
                    "ของ candidate เท่านั้น บอกว่าข้อมูลแต่ละส่วนมาจากแหล่งใด ถ้าแหล่งต่างกันให้แยกกล่าว "
                    "ถ้าไม่มีข้อมูลส่วนที่ถามให้บอกตรง ๆ ว่าไม่พบในข้อมูลที่นำเข้า "
                    "และอ้าง evidence_ref ของ candidate ที่ใช้ตอบ "
                    "คำที่ผู้ใช้ระบุด้วย คำว่า ไม่ถือเป็นบริบทของประโยค "
                    "ถ้ามี question_analysis ให้ใช้ intent ตามนั้น เพราะวิเคราะห์คำถามไว้แล้ว "
                    "ตัดสิน intent จาก question ล่าสุดเท่านั้น ประวัติสนทนาใช้เพียงเพื่อรู้ว่ากำลังคุยถึงคำใด "
                    "ห้ามตอบซ้ำหัวข้อของคำถามก่อนหน้าถ้า question ล่าสุดไม่ได้ถาม "
                    "ถ้า question เป็นเพียงคำเดียวให้ใช้ define_all "
                    "ใช้ related เมื่อถามความสัมพันธ์ และ compare เมื่อขอเปรียบเทียบ "
                    "กรณี compare ต้องตั้ง selected_candidate_id=null และอ้าง evidence จากหลาย Candidate "
                    "ตอบคำถามผู้ใช้ให้เหมือนกำลังสนทนา ไม่ต้องยึดรูปแบบคำตอบเดิมทุกครั้ง "
                    "หากเป็นคำถามต่อเนื่องให้ใช้ประวัติสนทนาและความหมายที่เลือกเป็นบริบท "
                    "grounded_answer ต้องตอบครบทุกส่วนของคำถาม ใช้เฉพาะข้อความใน evidence "
                    "กับ relations ที่ให้มาเท่านั้น ถ้าข้อมูลส่วนใดไม่มีให้บอกว่าไม่พบในข้อมูล "
                    "ถ้าถามคำเดี่ยวโดยไม่มีบริบท ให้ตอบความหมายที่พบทั้งหมดในข้อความแชต "
                    "โดยระบุนิยามจริงของแต่ละความหมาย ห้ามตอบแค่ว่ามีหลายความหมาย "
                    "หรือบอกให้ผู้ใช้เปิดดูที่อื่น "
                    "อย่าอ้างว่าแหล่งต่าง ๆ เห็นตรงกันหากไม่มีข้อมูลจับคู่ที่ยืนยันแล้ว "
                    "หลีกเลี่ยงรายการแหล่งข้อมูลยาว ๆ เพราะผู้ใช้เปิดดูในแผงหลักฐานได้ "
                    "ห้ามแสดง Candidate ID หรือ Evidence Ref เช่น S1 หรือ S1-E1 ในคำตอบผู้ใช้ "
                    "เมื่ออ้างแหล่งข้อมูลให้ใช้ word_info.source_name ตามที่ให้มาเท่านั้น "
                    "ข้อมูลรากศัพท์ให้ยกตามข้อความ etymology ห้ามตีความหรือเล่าประวัติเพิ่มเกินข้อความ "
                    "การออกเสียงให้ยกค่าใน pronunciations ตามที่ให้มา ห้ามอธิบายเสียงวรรณยุกต์ สระ "
                    "หรือพยัญชนะเองถ้าข้อมูลไม่ได้ระบุไว้ "
                    "ตอบเป็น JSON object ก้อนเดียวตาม output_contract ห้ามใช้ Markdown"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(task, ensure_ascii=False),
            },
        ]
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        last_error: ThaiLLMStructuredOutputError | None = None
        for attempt in range(1, self.max_attempts + 1):
            data = await self._post_chat(payload)
            try:
                content = self._extract_message_content(data)
                parsed = self._parse_json_object(content)
                return self._validate_contract(
                    parsed,
                    candidate_by_alias=candidate_by_alias,
                    evidence_by_alias=evidence_by_alias,
                )
            except ThaiLLMStructuredOutputError as exc:
                last_error = exc
                # Ask once more with the reason, without echoing the bad output.
                payload = {
                    **payload,
                    "messages": [
                        *messages,
                        {
                            "role": "user",
                            "content": (
                                f"ผลลัพธ์ก่อนหน้าใช้ไม่ได้ ({exc.code}) ตอบใหม่เป็น JSON object "
                                "ก้อนเดียวตาม output_contract โดยใช้เฉพาะ candidate_id และ "
                                "evidence_ref ที่ให้มาเท่านั้น ถ้าผลลัพธ์ถูกตัดเพราะยาวเกิน "
                                "ให้ย่อ grounded_answer และลดจำนวน evidence_refs"
                            ),
                        },
                    ],
                }
        assert last_error is not None
        raise ThaiLLMStructuredOutputError(
            last_error.code, attempts=self.max_attempts
        ) from last_error

    async def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST with short backoff for gateway or connection failures.

        thaillm.or.th has outages where every request returns Cloudflare
        "502: Bad gateway" for tens of seconds. Two retries (1 s, 2 s) ride out
        short blips; after that, calls pause for `outage_cooldown_seconds` so
        each question falls back at once instead of waiting through retries in
        both the analysis and the answer step. Timeouts and 4xx responses (auth,
        rate limit) are not retried: repeating them only adds wait or load.
        """
        if monotonic() < self._paused_until:
            raise ThaiLLMUnavailableError("ThaiLLM is paused after recent server errors")
        url = f"{self.base_url}/chat/completions"
        delays = (self.retry_delay_seconds, self.retry_delay_seconds * 2)
        for attempt in range(len(delays) + 1):
            last_attempt = attempt == len(delays)
            try:
                response = await self._client.post(url, json=payload)
            except (httpx.ConnectError, httpx.RemoteProtocolError):
                if not last_attempt:
                    await asyncio.sleep(delays[attempt])
                    continue
                self._paused_until = monotonic() + self.outage_cooldown_seconds
                raise
            if response.status_code in {500, 502, 503, 504}:
                if not last_attempt:
                    await asyncio.sleep(delays[attempt])
                    continue
                self._paused_until = monotonic() + self.outage_cooldown_seconds
            response.raise_for_status()
            return response.json()
        raise AssertionError("unreachable")

    async def analyze(
        self,
        query: str,
        *,
        history: list[ConversationTurn] | None = None,
        current_word: str | None = None,
    ) -> QueryAnalysis:
        """Read the question before any dictionary lookup.

        Deciding the target word and the kind of question is language
        understanding; rules over Thai phrasing kept missing new wordings.
        """
        # Only the word under discussion is passed on. Earlier messages, even
        # the user's own, made the model repeat the previous kind of question
        # ("รากศัพท์…" then "ไก่" was read as another etymology question).
        task = {
            "current_word": current_word,
            "question": query,
            "output_contract": {
                "kind": "conversation or lexical",
                "target_word": "the Thai word the user asks about, exactly as written, without คำว่า or quotes; null for conversation",
                "question_type": (
                    "meaning (ความหมาย แปลว่าอะไร หรือความหมายในประโยค), "
                    "relations (คำพ้อง คำตรงข้าม คำที่เกี่ยวข้อง), "
                    "compare_sources (เปรียบเทียบว่าแต่ละพจนานุกรมให้ความหมายต่างกันอย่างไร), "
                    "word_properties (รากศัพท์ ที่มา การออกเสียง การสะกด ชนิดคำ); null for conversation"
                ),
                "has_context": "true only when the user supplied a sentence or situation that uses the word",
                "reply": "short Thai reply ending with ครับ for conversation; empty string for lexical",
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "คุณวิเคราะห์คำถามของผู้ใช้ระบบพจนานุกรมภาษาไทยสานศัพท์ ยังไม่ต้องตอบความหมาย "
                    "kind=conversation เฉพาะเมื่อผู้ใช้ทักทาย ขอบคุณ ถามว่าระบบทำอะไรได้ "
                    "หรือบอกว่าจะถามแต่ยังไม่ได้ระบุคำ "
                    "kind=lexical เมื่อถามเกี่ยวกับคำ หรือส่งประโยคบอกเล่าใด ๆ มา "
                    "ในระบบนี้ประโยคบอกเล่าที่ไม่ได้ทักทาย เช่น 'คืนนี้ดาวสว่างมาก' คือประโยคที่ผู้ใช้ส่งมาให้ตีความคำ "
                    "จึงเป็น lexical question_type=meaning has_context=true ไม่ใช่การชวนคุย "
                    "target_word คือคำที่ถูกถาม ไม่ใช่คำที่ใช้ตั้งคำถาม เช่น ใน 'อ้วน เป็นลักษณะคำแบบไหน' "
                    "target_word=อ้วน ใน 'รากศัพท์ของคำว่าไก่คือ' target_word=ไก่ "
                    "ถ้าผู้ใช้อ้างถึงคำเดิม เช่น คำนี้ หรือถามต่อโดยไม่ระบุคำ ให้ใช้ current_word "
                    "ถ้าผู้ใช้ส่งประโยคมาให้ตีความ ให้เลือกคำที่มีหลายความหมายและน่าจะเป็นคำที่ถามถึง "
                    "question_type=meaning เมื่อถามความหมาย ไม่ว่าจะมีประโยคหรือไม่ "
                    "has_context=true เมื่อ question เป็นประโยคที่ใช้คำนั้นอยู่ "
                    "question_type=relations เมื่อถามคำพ้อง คำตรงข้าม หรือคำที่เกี่ยวข้อง "
                    "question_type=compare_sources เฉพาะเมื่อขอเปรียบเทียบระหว่างแหล่งหรือพจนานุกรม "
                    "question_type=word_properties เมื่อถามรากศัพท์ ที่มา การออกเสียง การสะกด หรือชนิดคำ "
                    "ตัดสินจาก question เท่านั้น current_word คือคำที่กำลังคุยกันอยู่ ใช้เมื่อ question อ้างถึงคำเดิม "
                    "ห้ามสืบทอด question_type จากข้อความก่อนหน้า "
                    "target_word ต้องเป็นคำที่ปรากฏใน question หรือ current_word เท่านั้น ห้ามแก้คำสะกดของผู้ใช้ "
                    "ตอบเป็น JSON object ก้อนเดียวตาม output_contract เท่านั้น"
                ),
            },
            # Worked examples as prior turns: a small model follows these more
            # reliably than rules written in the system prompt.
            *[
                message
                for example_task, example_answer in _ANALYSIS_EXAMPLES
                for message in (
                    {"role": "user", "content": json.dumps(
                        {**example_task, "output_contract": task["output_contract"]}, ensure_ascii=False
                    )},
                    {"role": "assistant", "content": json.dumps(example_answer, ensure_ascii=False)},
                )
            ],
            {"role": "user", "content": json.dumps(task, ensure_ascii=False)},
        ]
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": min(self.max_tokens, 300),
            "temperature": self.temperature,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        last_error: ThaiLLMStructuredOutputError | None = None
        for _ in range(self.max_attempts):
            data = await self._post_chat(payload)
            try:
                parsed = self._parse_json_object(self._extract_message_content(data))
                try:
                    analysis = QueryAnalysis.model_validate(parsed)
                except ValidationError as exc:
                    raise ThaiLLMStructuredOutputError("analysis_schema_invalid") from exc
                if analysis.kind == "lexical" and not (analysis.target_word or "").strip():
                    raise ThaiLLMStructuredOutputError("analysis_target_missing")
                return analysis
            except ThaiLLMStructuredOutputError as exc:
                last_error = exc
        assert last_error is not None
        raise ThaiLLMStructuredOutputError(last_error.code, attempts=self.max_attempts) from last_error

    async def respond_without_candidates(
        self, query: str, *, history: list[ConversationTurn] | None = None
    ) -> str | None:
        """Handle small talk without pretending that a missing word has evidence."""
        task = {
            "conversation_history": compact_history(history),
            "question": query,
            "available_lexical_evidence": [],
            "output_contract": {
                "intent": "conversation or lexical",
                "answer": "Short natural Thai reply for conversation; empty for lexical",
            },
        }
        data = await self._post_chat(
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "คุณเป็นผู้ช่วยสานศัพท์ แยกข้อความสนทนาทั่วไป เช่น ทักทาย "
                            "ถามสารทุกข์สุกดิบ ขอบคุณ หรือถามความสามารถของระบบ "
                            "ออกจากคำถามเกี่ยวกับคำศัพท์หรือข้อมูลอื่น ๆ "
                            "ถ้าเป็นสนทนาทั่วไป ให้ intent=conversation และตอบสั้น ๆ อย่างเป็นธรรมชาติ "
                            "ในน้ำเสียงของสานศัพท์ซึ่งลงท้ายด้วย ครับ และชวนให้ถามเรื่องคำหรือส่งประโยคมา "
                            "ถ้าถามความหมาย คำสัมพันธ์ คำก่อนหน้า หรือข้อเท็จจริงอื่น "
                            "ให้ intent=lexical และ answer เป็นสตริงว่าง "
                            "ไม่มีหลักฐานพจนานุกรมให้ใช้ ห้ามแต่งนิยามหรืออ้างแหล่งข้อมูล "
                            "ตอบเป็น JSON object เท่านั้น"
                        ),
                    },
                    {"role": "user", "content": json.dumps(task, ensure_ascii=False)},
                ],
                "max_tokens": min(self.max_tokens, 300),
                "temperature": self.temperature,
                "stream": False,
                "response_format": {"type": "json_object"},
            },
        )
        parsed = self._parse_json_object(self._extract_message_content(data))
        try:
            payload = _ThaiLLMUnmatchedPayload.model_validate(parsed)
        except ValidationError as exc:
            raise ThaiLLMStructuredOutputError("unmatched_schema_invalid") from exc
        if payload.intent == "lexical":
            return None
        answer = payload.answer.strip()
        if not answer:
            raise ThaiLLMStructuredOutputError("conversation_answer_missing")
        return answer

    @staticmethod
    def _extract_message_content(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ThaiLLMStructuredOutputError("missing_choices")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ThaiLLMStructuredOutputError("missing_message_content")
        return message["content"]

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            if start < 0:
                raise ThaiLLMStructuredOutputError("missing_json_object")
            try:
                parsed, _ = json.JSONDecoder().raw_decode(text[start:])
            except json.JSONDecodeError as exc:
                raise ThaiLLMStructuredOutputError("invalid_json") from exc
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError as exc:
                raise ThaiLLMStructuredOutputError("invalid_json") from exc
        if not isinstance(parsed, dict):
            raise ThaiLLMStructuredOutputError("json_not_object")
        return parsed

    @staticmethod
    def _validate_contract(
        parsed: dict[str, Any],
        *,
        candidate_by_alias: dict[str, SenseCandidate],
        evidence_by_alias: dict[str, tuple[str, str]],
    ) -> SelectionDecision:
        try:
            payload = _ThaiLLMSelectionPayload.model_validate(parsed)
        except ValidationError as exc:
            missing = any(error.get("type") == "missing" for error in exc.errors())
            code = "missing_required_fields" if missing else "schema_validation_failed"
            raise ThaiLLMStructuredOutputError(code) from exc
        multi_sense_intents = {"define_all", "compare", "word_info"}
        candidate = (
            candidate_by_alias.get(payload.selected_candidate_id)
            if payload.selected_candidate_id is not None
            else None
        )
        if payload.selected_candidate_id is not None and candidate is None:
            raise ThaiLLMStructuredOutputError("selected_sense_not_in_candidates")
        if payload.intent in multi_sense_intents:
            candidate = None
        elif candidate is None:
            raise ThaiLLMStructuredOutputError("selected_sense_required_for_intent")
        resolved_evidence: list[tuple[str, str]] = []
        for evidence_ref in payload.evidence_refs:
            resolved = evidence_by_alias.get(evidence_ref)
            if resolved is None:
                # An invented ref is a citation that cannot be shown, not a
                # claim about which sense applies; drop it. The chosen sense is
                # still checked, and real evidence is attached below.
                continue
            if payload.intent not in multi_sense_intents and resolved[0] != payload.selected_candidate_id:
                raise ThaiLLMStructuredOutputError(
                    "evidence_not_owned_by_selected_sense"
                )
            resolved_evidence.append(resolved)
        if not resolved_evidence and candidate is not None and payload.intent not in multi_sense_intents:
            selected_alias = payload.selected_candidate_id
            first_own = next(
                (value for value in evidence_by_alias.values() if value[0] == selected_alias), None
            )
            if first_own is None:
                raise ThaiLLMStructuredOutputError("evidence_not_in_candidates")
            resolved_evidence.append(first_own)

        # Keep the response compact. Multi-sense answers preserve at least one
        # cited fact per Candidate before filling the remaining slots.
        selected_evidence: list[tuple[str, str]] = []
        if payload.intent in multi_sense_intents:
            represented_candidates: set[str] = set()
            for candidate_id in candidate_by_alias:
                first = next(
                    (
                        resolved
                        for resolved in resolved_evidence
                        if resolved[0] == candidate_id
                    ),
                    next(
                        (
                            value
                            for value in evidence_by_alias.values()
                            if value[0] == candidate_id
                        ),
                        None,
                    ),
                )
                if first is not None:
                    selected_evidence.append(first)
                    represented_candidates.add(candidate_id)
                if len(selected_evidence) >= 8:
                    break
            for resolved in resolved_evidence:
                if resolved[0] not in represented_candidates:
                    selected_evidence.append(resolved)
                    represented_candidates.add(resolved[0])
                if len(selected_evidence) >= 8:
                    break
        selected_evidence_ids = {item[1] for item in selected_evidence}
        for resolved in resolved_evidence:
            if resolved[1] not in selected_evidence_ids:
                selected_evidence.append(resolved)
                selected_evidence_ids.add(resolved[1])
            if len(selected_evidence) >= 8:
                break
        return SelectionDecision(
            selected_sense_uri=candidate.sense_uri if candidate else None,
            confidence=payload.confidence,
            rationale=payload.rationale,
            evidence_ids=[item[1] for item in selected_evidence],
            selector="thaillm",
            intent=payload.intent,
            cue_words=payload.cue_words,
            grounded_answer=payload.grounded_answer.strip() if payload.grounded_answer else None,
        )

    async def close(self) -> None:
        await self._client.aclose()


def validate_selection(
    decision: SelectionDecision, candidates: list[SenseCandidate]
) -> list[str]:
    if decision.selected_sense_uri is None:
        if not candidates:
            return []
        if decision.intent not in {"define_all", "compare", "word_info"}:
            return ["selector_returned_no_sense"]
        allowed = {
            item.evidence_id for candidate in candidates for item in candidate.evidence
        }
        return [
            f"evidence_not_in_candidates:{item}"
            for item in decision.evidence_ids
            if item not in allowed
        ]
    by_uri = {candidate.sense_uri: candidate for candidate in candidates}
    selected = by_uri.get(decision.selected_sense_uri)
    if selected is None:
        return ["selected_sense_not_in_candidates"]
    allowed = {item.evidence_id for item in selected.evidence}
    invalid = [item for item in decision.evidence_ids if item not in allowed]
    return [f"evidence_not_owned_by_selected_sense:{item}" for item in invalid]
