from __future__ import annotations

import json
import unittest

import httpx

from thailex_api.models import (
    ConversationTurn,
    EvidenceItem,
    SelectionDecision,
    SenseCandidate,
)
from thailex_api.selector import (
    HeuristicSenseSelector,
    OpenAIResponsesSenseSelector,
    ThaiLLMChatSenseSelector,
    ThaiLLMStructuredOutputError,
    validate_selection,
)


def candidate(uri: str, score: float, evidence_id: str) -> SenseCandidate:
    return SenseCandidate(
        sense_uri=uri,
        entry_uri="https://example.test/entry/khan",
        concept_uri="https://example.test/concept/khan",
        lemma="ขัน",
        pos="verb",
        source="demo",
        source_graph="https://w3id.org/thailex/graph/demo-edition-a",
        evidence=[
            EvidenceItem(
                evidence_id=evidence_id,
                kind="definition",
                text="หมุนสิ่งยึดให้แน่น",
                language="th",
                evidence_uri=f"https://example.test/{evidence_id}",
                source_graph="https://w3id.org/thailex/graph/demo-edition-a",
            )
        ],
        retrieval_score=score,
    )


class SelectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_heuristic_selects_first_ranked_candidate(self) -> None:
        candidates = [candidate("https://example.test/sense/1", 0.9, "ev-1")]
        result = await HeuristicSenseSelector().select("พ่อขันนอต", candidates)
        self.assertEqual(result.selected_sense_uri, candidates[0].sense_uri)
        self.assertEqual(result.evidence_ids, ["ev-1"])
        self.assertEqual(validate_selection(result, candidates), [])

    async def test_validator_rejects_evidence_from_another_sense(self) -> None:
        candidates = [
            candidate("https://example.test/sense/1", 0.9, "ev-1"),
            candidate("https://example.test/sense/2", 0.8, "ev-2"),
        ]
        result = SelectionDecision(
            selected_sense_uri=candidates[0].sense_uri,
            confidence=0.9,
            rationale="test",
            evidence_ids=["ev-2"],
            selector="openai",
        )
        self.assertEqual(
            validate_selection(result, candidates),
            ["evidence_not_owned_by_selected_sense:ev-2"],
        )

    async def test_openai_selector_uses_strict_schema_and_store_false(self) -> None:
        candidates = [candidate("https://example.test/sense/1", 0.9, "ev-1")]

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            self.assertIs(payload["store"], False)
            self.assertTrue(payload["text"]["format"]["strict"])
            self.assertEqual(
                payload["text"]["format"]["schema"]["properties"]
                ["selected_sense_uri"]["enum"],
                ["https://example.test/sense/1"],
            )
            return httpx.Response(
                200,
                json={
                    "output_text": json.dumps(
                        {
                            "selected_sense_uri": "https://example.test/sense/1",
                            "confidence": 0.91,
                            "rationale": "context matches",
                            "evidence_ids": ["ev-1"],
                        }
                    )
                },
            )

        selector = OpenAIResponsesSenseSelector(
            api_key="test-key", model="test-model", base_url="https://api.test/v1"
        )
        await selector._client.aclose()
        selector._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer test-key"},
        )
        try:
            result = await selector.select("พ่อขันนอต", candidates)
        finally:
            await selector.close()
        self.assertEqual(result.selector, "openai")
        self.assertEqual(validate_selection(result, candidates), [])

    async def test_thaillm_selector_uses_chat_completions_and_history(self) -> None:
        candidates = [candidate("https://example.test/sense/1", 0.9, "ev-1")]

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer test-key")
            payload = json.loads(request.content)
            self.assertEqual(
                payload["model"], "Pathumma-ThaiLLM-qwen3-8b-think-3.0.0"
            )
            task = json.loads(payload["messages"][1]["content"])
            self.assertEqual(task["conversation_history"][0]["role"], "user")
            self.assertEqual(task["candidates"][0]["candidate_id"], "S1")
            self.assertEqual(
                task["candidates"][0]["evidence"][0]["evidence_ref"], "S1-E1"
            )
            self.assertNotIn("sense_uri", task["candidates"][0])
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": (
                                    "```json\n"
                                    '{"intent":"define","selected_candidate_id":"S1",'
                                    '"confidence":0.94,"rationale":"ตรงกับบริบท",'
                                    '"cue_words":["ความหมาย"],'
                                    '"evidence_refs":["S1-E1"]}\n'
                                    "```"
                                ),
                            }
                        }
                    ]
                },
            )

        selector = ThaiLLMChatSenseSelector(
            api_key="test-key",
            model="Pathumma-ThaiLLM-qwen3-8b-think-3.0.0",
            base_url="https://api.test/api/v1",
        )
        await selector._client.aclose()
        selector._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer test-key"},
        )
        try:
            result = await selector.select(
                "แล้วความหมายนี้ล่ะ",
                candidates,
                history=[ConversationTurn(role="user", content="พ่อขันนอต")],
            )
        finally:
            await selector.close()
        self.assertEqual(result.selector, "thaillm")
        self.assertEqual(result.intent, "define")
        self.assertIsNone(result.grounded_answer)
        self.assertEqual(result.evidence_ids, ["ev-1"])
        self.assertEqual(validate_selection(result, candidates), [])

    def test_thaillm_parser_repairs_prose_around_one_json_object(self) -> None:
        parsed = ThaiLLMChatSenseSelector._parse_json_object(
            "คำตอบที่เลือกคือ\n"
            '{"selected_sense_uri":"https://example.test/sense/1",'
            '"confidence":0.9,"rationale":"ตรงบริบท",'
            '"evidence_ids":["ev-1"]}\nจบคำตอบ'
        )
        self.assertEqual(
            parsed["selected_sense_uri"], "https://example.test/sense/1"
        )

    async def test_thaillm_uses_one_call_and_rejects_invalid_json(self) -> None:
        candidates = [candidate("https://example.test/sense/1", 0.9, "ev-1")]
        calls: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            calls.append(payload)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "เลือกความหมายแรก"}}]},
            )

        selector = ThaiLLMChatSenseSelector(
            api_key="test-key",
            model="Pathumma-ThaiLLM-qwen3-8b-think-3.0.0",
            base_url="https://api.test/api/v1",
        )
        await selector._client.aclose()
        selector._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(ThaiLLMStructuredOutputError) as caught:
                await selector.select("พ่อขันนอต", candidates)
        finally:
            await selector.close()
        self.assertEqual(len(calls), 1)
        self.assertEqual(caught.exception.code, "missing_json_object")
        self.assertEqual(caught.exception.attempts, 1)

    async def test_thaillm_reports_safe_reason_without_retry(self) -> None:
        candidates = [candidate("https://example.test/sense/1", 0.9, "ev-1")]

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ไม่มี JSON"}}]},
            )

        selector = ThaiLLMChatSenseSelector(
            api_key="test-key",
            model="Pathumma-ThaiLLM-qwen3-8b-think-3.0.0",
            base_url="https://api.test/api/v1",
        )
        await selector._client.aclose()
        selector._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(ThaiLLMStructuredOutputError) as caught:
                await selector.select("พ่อขันนอต", candidates)
        finally:
            await selector.close()
        self.assertEqual(caught.exception.code, "missing_json_object")
        self.assertEqual(caught.exception.attempts, 1)

    def test_thaillm_compare_compacts_valid_evidence_with_candidate_diversity(self) -> None:
        candidates = {
            f"S{index}": candidate(
                f"https://example.test/sense/{index}", 0.9, f"ev-{index}-1"
            )
            for index in range(1, 5)
        }
        evidence_by_alias = {
            f"S{candidate_index}-E{evidence_index}": (
                f"S{candidate_index}", f"ev-{candidate_index}-{evidence_index}"
            )
            for candidate_index in range(1, 5)
            for evidence_index in range(1, 7)
        }
        result = ThaiLLMChatSenseSelector._validate_contract(
            {
                "intent": "compare",
                "selected_candidate_id": None,
                "confidence": 0.95,
                "cue_words": ["เปรียบเทียบ"],
                "rationale": "ต้องเปรียบเทียบทุกความหมาย",
                "evidence_refs": list(evidence_by_alias),
            },
            candidate_by_alias=candidates,
            evidence_by_alias=evidence_by_alias,
        )
        self.assertEqual(len(result.evidence_ids), 8)
        self.assertEqual(
            result.evidence_ids[:4],
            ["ev-1-1", "ev-2-1", "ev-3-1", "ev-4-1"],
        )

    def test_thaillm_define_all_does_not_select_one_sense(self) -> None:
        candidates = {
            "S1": candidate("https://example.test/sense/1", 0.9, "ev-1"),
            "S2": candidate("https://example.test/sense/2", 0.8, "ev-2"),
        }
        result = ThaiLLMChatSenseSelector._validate_contract(
            {
                "intent": "define_all",
                "selected_candidate_id": None,
                "confidence": 0.96,
                "cue_words": [],
                "rationale": "ไม่มีบริบทสำหรับเลือกความหมายเดียว",
                "evidence_refs": ["S1-E1"],
            },
            candidate_by_alias=candidates,
            evidence_by_alias={
                "S1-E1": ("S1", "ev-1"),
                "S2-E1": ("S2", "ev-2"),
            },
        )
        self.assertEqual(result.intent, "define_all")
        self.assertIsNone(result.selected_sense_uri)
        self.assertEqual(result.evidence_ids, ["ev-1", "ev-2"])
        self.assertEqual(validate_selection(result, list(candidates.values())), [])


if __name__ == "__main__":
    unittest.main()
