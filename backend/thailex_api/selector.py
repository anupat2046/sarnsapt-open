from __future__ import annotations

import json
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import ConversationTurn, SelectionDecision, SenseCandidate


class SenseSelector(Protocol):
    async def select(
        self,
        query: str,
        candidates: list[SenseCandidate],
        *,
        history: list[ConversationTurn] | None = None,
    ) -> SelectionDecision: ...


class ThaiLLMStructuredOutputError(ValueError):
    """Safe, non-content-bearing reason for rejecting a ThaiLLM response."""

    def __init__(self, code: str, *, attempts: int = 1) -> None:
        self.code = code
        self.attempts = attempts
        super().__init__(f"{code} after {attempts} attempt(s)")


class _ThaiLLMSelectionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Literal["define", "define_all", "related", "compare"]
    selected_candidate_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    cue_words: list[str] = Field(default_factory=list, max_length=8)
    rationale: str = Field(min_length=1, max_length=500)
    # ThaiLLM sometimes returns every valid Evidence Ref for compare requests even
    # when the prompt asks for at most eight.  Accept the bounded provider output
    # here, then normalize it deterministically below.  Unknown references still
    # fail validation and never reach the answer.
    evidence_refs: list[str] = Field(min_length=1, max_length=64)


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
        confidence = min(0.82, 0.30 + selected.retrieval_score * 0.55)
        return SelectionDecision(
            selected_sense_uri=selected.sense_uri,
            confidence=round(confidence, 3),
            rationale=(
                "เลือกจากความสอดคล้องระหว่างบริบทกับนิยาม/ตัวอย่างในกราฟ "
                "โดยไม่สร้างข้อเท็จจริงใหม่"
            ),
            evidence_ids=[item.evidence_id for item in evidence[:3]],
            selector="heuristic",
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
                    "conversation_history": [
                        turn.model_dump() for turn in (history or [])[-8:]
                    ],
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
    """Selects one retrieved Sense through ThaiLLM's chat-completions API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://thaillm.or.th/api/v1",
        timeout: float = 30.0,
        max_tokens: int = 700,
        temperature: float = 0.1,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.temperature = temperature
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
                if len(diverse_evidence) >= 8:
                    break
            if len(diverse_evidence) < 8:
                selected_ids = {item.evidence_id for item in diverse_evidence}
                diverse_evidence.extend(
                    item for item in candidate.evidence
                    if item.evidence_id not in selected_ids
                )
            for evidence_index, item in enumerate(diverse_evidence[:8], start=1):
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
                    "evidence": evidence,
                }
            )
        task = {
            "conversation_history": [
                turn.model_dump() for turn in (history or [])[-8:]
            ],
            "question": query,
            "candidates": compact_candidates,
            "output_contract": {
                "intent": "define_all, define, related, or compare",
                "selected_candidate_id": (
                    "must be null for define_all/compare; otherwise must equal one supplied candidate_id"
                ),
                "confidence": "number from 0 to 1",
                "cue_words": "short words or phrases from the user question that support the decision",
                "rationale": "short Thai explanation grounded in context/evidence",
                "evidence_refs": (
                    "1-8 supplied evidence_ref values; for define/related they must belong "
                    "to the selected candidate"
                ),
            },
            "output_example": {
                "intent": "define_all",
                "selected_candidate_id": None,
                "confidence": 0.95,
                "cue_words": [],
                "rationale": "ผู้ใช้ถามความหมายทั่วไปโดยไม่มีบริบทให้เลือกความหมายเดียว",
                "evidence_refs": ["S1-E1", "S2-E1"],
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
                    "Sense เดียว ใช้ related เมื่อถามความสัมพันธ์ และ compare เมื่อขอเปรียบเทียบ "
                    "กรณี compare ต้องตั้ง selected_candidate_id=null และอ้าง evidence จากหลาย Candidate "
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
        response = await self._client.post(
            f"{self.base_url}/chat/completions", json=payload
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        try:
            content = self._extract_message_content(data)
            parsed = self._parse_json_object(content)
            return self._validate_contract(
                parsed,
                candidate_by_alias=candidate_by_alias,
                evidence_by_alias=evidence_by_alias,
            )
        except ThaiLLMStructuredOutputError as exc:
            raise ThaiLLMStructuredOutputError(exc.code, attempts=1) from exc

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
        multi_sense_intents = {"define_all", "compare"}
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
                raise ThaiLLMStructuredOutputError("evidence_not_in_candidates")
            if payload.intent not in multi_sense_intents and resolved[0] != payload.selected_candidate_id:
                raise ThaiLLMStructuredOutputError(
                    "evidence_not_owned_by_selected_sense"
                )
            resolved_evidence.append(resolved)

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
        )

    async def close(self) -> None:
        await self._client.aclose()


def validate_selection(
    decision: SelectionDecision, candidates: list[SenseCandidate]
) -> list[str]:
    if decision.selected_sense_uri is None:
        if not candidates:
            return []
        if decision.intent not in {"define_all", "compare"}:
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
