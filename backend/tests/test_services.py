from __future__ import annotations

import unittest

from thailex_api.models import (
    AnswerDetail,
    AskRequest,
    GraphResult,
    RelatedTerm,
    SelectionDecision,
    SourceSupport,
)
from thailex_api.selector import HeuristicSenseSelector, ThaiLLMStructuredOutputError
from thailex_api.services import (
    AskService,
    AskServiceError,
    _grounded_cue_words,
    detect_intent,
)
from test_selector import candidate


class FakeRepository:
    async def graph(self, start_uris: list[str], *, hops: int, max_edges: int):
        self.graph_call = (start_uris, hops, max_edges)
        return GraphResult(hops=hops)

    async def get_senses(self, lemma: str, *, limit: int):
        return []

    async def get_alignments(self, lemma: str, *, limit: int):
        return []


class FakeRetriever:
    async def retrieve(self, query: str, *, lemma: str | None, max_candidates: int):
        return "ขัน", [candidate("https://example.test/sense/1", 0.9, "ev-1")]


class InvalidSelector:
    async def select(self, query: str, candidates, *, history=None):
        return SelectionDecision(
            selected_sense_uri="https://example.test/sense/not-retrieved",
            confidence=1,
            rationale="invalid",
            evidence_ids=[],
            selector="openai",
        )


class RecordingSelector:
    def __init__(self) -> None:
        self.candidates = []

    async def select(self, query: str, candidates, *, history=None):
        self.candidates = candidates
        selected = candidates[0]
        return SelectionDecision(
            selected_sense_uri=selected.sense_uri,
            confidence=0.9,
            rationale="selected real source",
            evidence_ids=[selected.evidence[0].evidence_id],
            selector="thaillm",
            grounded_answer="คำตอบจากหลักฐาน",
        )


class MalformedThaiLLMSelector:
    async def select(self, query: str, candidates, *, history=None):
        raise ThaiLLMStructuredOutputError("invalid_json", attempts=2)


class DefineAllSelector:
    async def select(self, query: str, candidates, *, history=None):
        return SelectionDecision(
            selected_sense_uri=None,
            confidence=0.96,
            rationale="เป็นคำถามความหมายทั่วไปโดยไม่มีบริบท",
            evidence_ids=[item.evidence[0].evidence_id for item in candidates],
            selector="thaillm",
            intent="define_all",
            cue_words=[],
        )


class MultiSenseRetriever:
    async def retrieve(self, query: str, *, lemma: str | None, max_candidates: int):
        return "ดาว", [
            candidate(
                "https://example.test/sense/star",
                0.9,
                "star-body",
            ).model_copy(update={"source": "organizer", "source_graph": "https://w3id.org/thailex/graph/organizer/sample/v1"}),
            candidate(
                "https://example.test/sense/person",
                0.8,
                "star-person",
            ).model_copy(update={"source": "organizer", "source_graph": "https://w3id.org/thailex/graph/organizer/sample/v2"}),
        ]


class AskServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_context_cues_do_not_echo_full_sentence(self) -> None:
        request = AskRequest(
            query="คำว่า ดาว ในประโยค ดาวสว่างในคืนนี้ หมายถึงอะไร"
        )
        cues = _grounded_cue_words(
            request,
            ["สว่าง", "ดาวสว่างในคืนนี้"],
            lemma="ดาว",
        )
        self.assertEqual(cues, ["สว่าง"])

    def test_intent_router_separates_definition_relations_and_comparison(self) -> None:
        self.assertEqual(detect_intent("คำว่า ขัน หมายถึงอะไร"), "define_all")
        self.assertEqual(detect_intent("กระดูกคืออะไร"), "define_all")
        self.assertEqual(detect_intent("พ่อขันนอตให้แน่น"), "define")
        self.assertEqual(detect_intent("แล้วคำที่เกี่ยวข้องมีอะไรบ้าง"), "related")
        self.assertEqual(detect_intent("เปรียบเทียบความหมายของคำว่า ขัน"), "compare")

    async def test_general_definition_lists_all_senses_without_selecting_one(self) -> None:
        retriever = MultiSenseRetriever()

        class RepositoryWithSenses(FakeRepository):
            async def get_senses(self, lemma: str, *, limit: int):
                _, senses = await retriever.retrieve("", lemma=lemma, max_candidates=limit)
                return senses

        service = AskService(
            repository=RepositoryWithSenses(),
            retriever=retriever,
            heuristic_selector=HeuristicSenseSelector(),
            llm_selector=DefineAllSelector(),
            default_to_llm=True,
            provider="thaillm",
        )
        result = await service.ask(AskRequest(query="ดาว หมายความว่าอะไร"))
        self.assertEqual(result.detail.intent, "define_all")
        self.assertIsNone(result.selection.selected_sense_uri)
        self.assertEqual(result.selection.cue_words, [])
        self.assertIn("มี 2 ความหมาย", result.answer)
        self.assertIn("ไม่เลือกแทนผู้ใช้", result.answer)
        self.assertNotIn("ในบริบทนี้", result.answer)

    async def test_invalid_llm_result_returns_error_without_fallback(self) -> None:
        repository = FakeRepository()
        service = AskService(
            repository=repository,
            retriever=FakeRetriever(),
            heuristic_selector=HeuristicSenseSelector(),
            llm_selector=InvalidSelector(),
            default_to_llm=True,
            max_graph_edges=50,
        )
        with self.assertRaises(AskServiceError) as caught:
            await service.ask(
                AskRequest(query="พ่อขันนอตให้แน่น", lemma="ขัน", hops=2)
            )
        self.assertEqual(caught.exception.code, "llm_evidence_validation_failed")

    async def test_llm_does_not_receive_demo_senses_when_real_sources_exist(self) -> None:
        real = candidate("https://example.test/sense/real", 0.8, "ev-real").model_copy(
            update={"source": "lexitron"}
        )
        demo = candidate("https://example.test/sense/demo", 0.9, "ev-demo")

        class MixedRetriever:
            async def retrieve(self, query, *, lemma, max_candidates):
                return "ขัน", [demo, real]

        selector = RecordingSelector()
        service = AskService(
            repository=FakeRepository(),
            retriever=MixedRetriever(),
            heuristic_selector=HeuristicSenseSelector(),
            llm_selector=selector,
            default_to_llm=True,
        )
        result = await service.ask(AskRequest(query="พ่อขันนอต"))
        self.assertEqual([item.source for item in selector.candidates], ["lexitron"])
        self.assertEqual(result.selection.selected_sense_uri, real.sense_uri)
        self.assertEqual(len(result.candidates), 2)

    async def test_structured_output_error_exposes_specific_safe_reason(self) -> None:
        service = AskService(
            repository=FakeRepository(),
            retriever=FakeRetriever(),
            heuristic_selector=HeuristicSenseSelector(),
            llm_selector=MalformedThaiLLMSelector(),
            default_to_llm=True,
            provider="thaillm",
        )
        with self.assertRaises(AskServiceError) as caught:
            await service.ask(AskRequest(query="พ่อขันนอตให้แน่น"))
        self.assertEqual(caught.exception.code, "llm_structured_output_invalid")
        self.assertIn("invalid_json", caught.exception.message)

    def test_answer_is_conversational_and_attributes_sources(self) -> None:
        central_uri = "https://example.test/concept/star"

        def support(source: str, edition: str, graph: str) -> SourceSupport:
            return SourceSupport(
                source=source,
                sense_uri=central_uri,
                pos="noun",
                definition="วัตถุท้องฟ้าที่เปล่งแสงได้ด้วยตนเอง.",
                match_relation="selected" if edition == "v1" else "exactMatch",
                review_status="selected" if edition == "v1" else "approved",
                source_graph=graph,
                edition=edition,
            )

        detail = AnswerDetail(
            intent="define",
            title="ความหมายของ “ดาว” ตามบริบท",
            summary="วัตถุท้องฟ้าที่เปล่งแสงได้ด้วยตนเอง.",
            explanation="",
            source_supports=[
                support(
                    "organizer", "พจนานุกรมตัวอย่าง ฉบับ v1",
                    "https://w3id.org/thailex/graph/organizer/sample-dictionary/v1",
                ),
                support(
                    "organizer", "พจนานุกรมตัวอย่าง ฉบับ v2",
                    "https://w3id.org/thailex/graph/organizer/sample-dictionary/v2",
                ),
                support(
                    "lexitron", "LEXiTRON 2.0",
                    "https://w3id.org/thailex/graph/lexitron",
                ),
            ],
            related_terms=[],
        )
        answer = AskService._compose_answer(
            detail, cue_words=["สว่าง", "คืนนี้"]
        )
        self.assertIn("วัตถุท้องฟ้าที่เปล่งแสงได้ด้วยตนเอง", answer)
        self.assertIn("คำว่า “สว่าง” และ “คืนนี้”", answer)
        self.assertIn("พจนานุกรมตัวอย่าง ฉบับ v1", answer)
        self.assertIn("พจนานุกรมตัวอย่าง ฉบับ v2", answer)
        self.assertEqual(len(answer.split("\n\n")), 3)


class FollowupRetriever:
    def __init__(self) -> None:
        self.queries: list[tuple[str, str | None]] = []

    async def retrieve(self, query: str, *, lemma: str | None, max_candidates: int):
        self.queries.append((query, lemma))
        if lemma == "ขัน":
            return "ขัน", [
                candidate("https://example.test/sense/1", 0.9, "ev-1"),
                candidate("https://example.test/sense/2", 0.7, "ev-2"),
            ]
        return None, []


class FollowupServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_followup_uses_confirmed_context_lemma_for_retrieval(self) -> None:
        repository = FakeRepository()
        retriever = FollowupRetriever()
        service = AskService(
            repository=repository,
            retriever=retriever,
            heuristic_selector=HeuristicSenseSelector(),
        )
        result = await service.ask(
            AskRequest(
                query="แล้วความหมายอื่นล่ะ",
                context_lemma="ขัน",
                history=[
                    {"role": "user", "content": "คำว่า ขัน หมายถึงอะไร"},
                    {"role": "assistant", "content": "พบความหมายจากฐานข้อมูล"},
                ],
            )
        )
        self.assertEqual(result.detected_lemma, "ขัน")
        self.assertEqual(retriever.queries, [("แล้วความหมายอื่นล่ะ", "ขัน")])
        self.assertEqual(result.selection.selector, "heuristic")

    async def test_followup_does_not_guess_context_from_history(self) -> None:
        repository = FakeRepository()
        retriever = FollowupRetriever()
        service = AskService(
            repository=repository,
            retriever=retriever,
            heuristic_selector=HeuristicSenseSelector(),
        )
        result = await service.ask(
            AskRequest(
                query="แล้วความหมายอื่นล่ะ",
                history=[{"role": "user", "content": "คำว่า กิน หมายถึงอะไร"}],
            )
        )
        self.assertIsNone(result.detected_lemma)
        self.assertEqual(len(retriever.queries), 1)

    async def test_explicit_new_word_does_not_reuse_previous_context(self) -> None:
        repository = FakeRepository()
        retriever = FollowupRetriever()
        service = AskService(
            repository=repository,
            retriever=retriever,
            heuristic_selector=HeuristicSenseSelector(),
        )
        await service.ask(
            AskRequest(query="แล้วคำว่า ตา ล่ะ", context_lemma="ขัน")
        )
        self.assertEqual(retriever.queries, [("แล้วคำว่า ตา ล่ะ", None)])

    async def test_related_words_followup_keeps_current_sense(self) -> None:
        service = AskService(
            repository=FakeRepository(),
            retriever=FollowupRetriever(),
            heuristic_selector=HeuristicSenseSelector(),
        )
        result = await service.ask(
            AskRequest(
                query="แล้วคำที่เกี่ยวข้องมีอะไรบ้าง",
                context_lemma="ขัน",
                context_sense_uri="https://example.test/sense/2",
            )
        )
        self.assertEqual(
            result.selection.selected_sense_uri, "https://example.test/sense/2"
        )
        self.assertEqual(result.detail.intent, "related")

    async def test_other_meaning_followup_moves_away_from_current_sense(self) -> None:
        service = AskService(
            repository=FakeRepository(),
            retriever=FollowupRetriever(),
            heuristic_selector=HeuristicSenseSelector(),
        )
        result = await service.ask(
            AskRequest(
                query="แล้วความหมายอื่นล่ะ",
                context_lemma="ขัน",
                context_sense_uri="https://example.test/sense/1",
            )
        )
        self.assertEqual(
            result.selection.selected_sense_uri, "https://example.test/sense/2"
        )

    async def test_compare_intent_is_handled_in_same_llm_call(self) -> None:
        selector = RecordingSelector()
        service = AskService(
            repository=FakeRepository(),
            retriever=FakeRetriever(),
            heuristic_selector=HeuristicSenseSelector(),
            llm_selector=selector,
            default_to_llm=True,
            provider="thaillm",
            model="test-model",
        )
        result = await service.ask(AskRequest(query="เปรียบเทียบความหมายของคำว่า ขัน"))
        self.assertEqual(len(selector.candidates), 1)
        self.assertEqual(result.detail.intent, "compare")
        self.assertFalse(result.diagnostics.fallback_used)
        self.assertIn("ข้อมูลที่นำมาเปรียบเทียบ", result.answer)
        self.assertEqual(result.selection.grounded_answer, result.answer)
        self.assertNotEqual(result.selection.rationale, "selected real source")
        self.assertIn("Backend ตรวจ", result.selection.rationale)
        self.assertGreaterEqual(result.timings.total_ms, result.timings.llm_ms)


if __name__ == "__main__":
    unittest.main()
