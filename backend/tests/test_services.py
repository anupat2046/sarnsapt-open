from __future__ import annotations

import json
import unittest

import httpx

from thailex_api.models import (
    AnswerDetail,
    AskRequest,
    GraphResult,
    RelatedTerm,
    SelectionDecision,
    SourceSupport,
)
from thailex_api.selector import HeuristicSenseSelector, ThaiLLMChatSenseSelector, ThaiLLMStructuredOutputError
from thailex_api.services import (
    AskService,
    AskServiceError,
    _grounded_cue_words,
    _context_lemma_for,
    detect_intent,
)
from test_selector import candidate


def reply(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})


def is_analysis(task: dict) -> bool:
    return "candidates" not in task


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


class ConversationalSelector:
    async def select(self, query: str, candidates, *, history=None):
        selected = candidates[0]
        return SelectionDecision(
            selected_sense_uri=selected.sense_uri,
            confidence=0.9,
            rationale="ตรงกับบริบท",
            evidence_ids=[selected.evidence[0].evidence_id],
            selector="thaillm",
            intent="define",
            grounded_answer="คำว่า ขัน ในประโยคนี้หมายถึงหมุนสิ่งยึดให้แน่นครับ",
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
        person = candidate(
            "https://example.test/sense/person", 0.8, "star-person"
        )
        person = person.model_copy(update={
            "evidence": [person.evidence[0].model_copy(update={
                "text": "บุคคลที่ได้รับความนิยมเป็นพิเศษ"
            })]
        })
        return "ดาว", [
            candidate(
                "https://example.test/sense/star",
                0.9,
                "star-body",
            ).model_copy(update={"source": "organizer", "source_graph": "https://w3id.org/thailex/graph/organizer/sample/v1"}),
            person.model_copy(update={"source": "organizer", "source_graph": "https://w3id.org/thailex/graph/organizer/sample/v2"}),
        ]


class AskServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_internal_refs_are_removed_instead_of_discarding_answer(self) -> None:
        from thailex_api.services import _strip_internal_refs

        cleaned = _strip_internal_refs("คำว่า อ้วน ออกเสียงว่า /ʔua̯n˥˩/ ตามวิกิพจนานุกรม (S4) และ (S5, S6) ดู S1-E2")
        self.assertEqual(cleaned, "คำว่า อ้วน ออกเสียงว่า /ʔua̯n˥˩/ ตามวิกิพจนานุกรม และ ดู")
        self.assertTrue(AskService._usable_model_answer(cleaned, lemma="อ้วน", min_length=12))

    def test_model_answer_must_not_expose_internal_evidence_refs(self) -> None:
        self.assertFalse(AskService._usable_model_answer(
            "คำว่า ดาว หมายถึงวัตถุบนท้องฟ้า ตามหลักฐาน S1-E1 ครับ",
            lemma="ดาว", required_definitions=["วัตถุบนท้องฟ้า"],
        ))

    async def test_small_talk_without_candidates_uses_one_model_call(self) -> None:
        class EmptyRetriever:
            def __init__(self):
                self.calls = []

            async def retrieve(self, query, *, lemma, max_candidates):
                self.calls.append((query, lemma))
                return None, []

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            task = json.loads(json.loads(request.content)["messages"][-1]["content"])
            calls.append(task)
            return reply({"kind": "conversation", "reply": "ว่าไงครับ วันนี้อยากคุยเรื่องคำไหน"})

        selector = ThaiLLMChatSenseSelector(api_key="test-key", model="test-model")
        await selector._client.aclose()
        selector._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        retriever = EmptyRetriever()
        try:
            service = AskService(
                repository=FakeRepository(), retriever=retriever,
                heuristic_selector=HeuristicSenseSelector(), llm_selector=selector,
                default_to_llm=True, provider="thaillm",
            )
            result = await service.ask(AskRequest(
                query="ว่าไง", context_lemma="ดาว",
                history=[{"role": "user", "content": "สวัสดี"}],
            ))
        finally:
            await selector.close()
        self.assertEqual(retriever.calls, [])  # small talk never searches the dictionary
        self.assertEqual(len(calls), 1)
        self.assertTrue(is_analysis(calls[0]))
        self.assertEqual(calls[0]["current_word"], "ดาว")
        self.assertEqual(result.detail.intent, "conversation")
        self.assertEqual(result.answer, "ว่าไงครับ วันนี้อยากคุยเรื่องคำไหน")
        self.assertEqual(result.diagnostics.answer_mode, "model")
        self.assertEqual(result.citations, [])

    async def test_unmatched_lexical_question_is_not_answered_as_small_talk(self) -> None:
        class EmptyRetriever:
            async def retrieve(self, query, *, lemma, max_candidates):
                return None, []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
                "intent": "lexical", "answer": ""
            })}}]})

        selector = ThaiLLMChatSenseSelector(api_key="test-key", model="test-model")
        await selector._client.aclose()
        selector._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            service = AskService(
                repository=FakeRepository(), retriever=EmptyRetriever(),
                heuristic_selector=HeuristicSenseSelector(), llm_selector=selector,
                default_to_llm=True, provider="thaillm",
            )
            result = await service.ask(AskRequest(query="คำว่า ไม่มีในคลัง หมายถึงอะไร"))
        finally:
            await selector.close()
        self.assertEqual(result.detail.intent, "define_all")
        self.assertEqual(result.citations, [])
        self.assertIn("ยังไม่พบคำที่ถาม", result.answer)

    async def test_lexical_question_without_match_never_becomes_small_talk(self) -> None:
        class EmptyRetriever:
            async def retrieve(self, query, *, lemma, max_candidates):
                return None, []

        class ChattySelector(ThaiLLMChatSenseSelector):
            def __init__(self):
                self.small_talk_calls = 0

            async def respond_without_candidates(self, query, *, history=None):
                self.small_talk_calls += 1
                return "สวัสดีครับ ผมสานศัพท์"

        selector = ChattySelector()
        service = AskService(
            repository=FakeRepository(), retriever=EmptyRetriever(),
            heuristic_selector=HeuristicSenseSelector(), llm_selector=selector,
            default_to_llm=True, provider="thaillm",
        )
        result = await service.ask(AskRequest(
            query="ผมอยากรู้ว่า คำว่าดาวอะมีความหมายว่าอะไรได้บ้างมีรายละเอียดยังไง",
            history=[
                {"role": "user", "content": "สวัสดี"},
                {"role": "assistant", "content": "สวัสดีครับ ผมสานศัพท์"},
            ],
        ))
        self.assertEqual(selector.small_talk_calls, 0)
        self.assertNotEqual(result.detail.intent, "conversation")
        self.assertIn("ยังไม่พบคำที่ถาม (“ดาวอะ”)", result.answer)
        self.assertNotIn("สวัสดี", result.answer)

    async def test_one_word_question_is_not_answered_as_a_word_inside_it(self) -> None:
        from thailex_api.selector import QueryAnalysis
        from thailex_api.thai_text import context_tokens

        class Lexicon:
            words = {"มีด": 2, "ตูก": 1}

            async def contains(self, lemma):
                return lemma in self.words

            async def longest_prefix(self, text):
                return next((text[:size] for size in range(len(text), 0, -1)
                             if text[:size] in self.words), None)

            async def mentions(self, text):
                return [word for word in self.words if word in text]

            async def context_tokens(self, query, lemma):
                return context_tokens(query, lemma, self.words)

        class Retriever:
            lexicon = Lexicon()

            async def retrieve(self, query, *, lemma, max_candidates):
                if lemma == "มีด":
                    return lemma, [candidate("https://example.test/sense/knife", 0.9, "knife-1")]
                return None, []

        class Analyzing(ThaiLLMChatSenseSelector):
            def __init__(self):
                pass

            async def analyze(self, query, *, history=None, current_word=None):
                return QueryAnalysis(kind="lexical", target_word="ตูกมีด",
                                     question_type="meaning", has_context=False)

        service = AskService(
            repository=FakeRepository(), retriever=Retriever(),
            heuristic_selector=HeuristicSenseSelector(), llm_selector=Analyzing(),
            default_to_llm=True, provider="thaillm",
        )
        result = await service.ask(AskRequest(query="ตูกมีดแปลว่าอะไร"))
        self.assertIn("ยังไม่พบคำที่ถาม (“ตูกมีด”)", result.answer)
        self.assertEqual(result.citations, [])

    async def test_not_found_define_all_does_not_claim_word_was_found(self) -> None:
        class EmptyRetriever:
            async def retrieve(self, query, *, lemma, max_candidates):
                return None, []

        service = AskService(
            repository=FakeRepository(), retriever=EmptyRetriever(),
            heuristic_selector=HeuristicSenseSelector(), default_to_llm=False,
        )
        result = await service.ask(AskRequest(query="คำว่าฟหกดหมายถึงอะไร"))
        self.assertNotIn("พบคำว่า", result.answer)
        self.assertIn("ยังไม่พบคำที่ถาม (“ฟหกด”)", result.answer)
        self.assertEqual(result.citations, [])

    async def test_conversation_opener_with_dictionary_words_is_not_a_lookup(self) -> None:
        class UnusedRetriever:
            lexicon = None

            async def retrieve(self, *_args, **_kwargs):
                raise AssertionError("An opener must not be looked up")

        service = AskService(
            repository=FakeRepository(), retriever=UnusedRetriever(),
            heuristic_selector=HeuristicSenseSelector(), default_to_llm=False,
        )
        result = await service.ask(AskRequest(query="ว่าไง ผมอยากถาม"))
        self.assertEqual(result.detail.intent, "conversation")
        self.assertIn("อยากรู้เรื่องคำไหน", result.answer)
        self.assertEqual(result.citations, [])

    async def test_sentence_with_content_word_is_still_looked_up(self) -> None:
        service = AskService(
            repository=FakeRepository(), retriever=FakeRetriever(),
            heuristic_selector=HeuristicSenseSelector(), default_to_llm=False,
        )
        result = await service.ask(AskRequest(query="ผมอยากขันนอต"))
        self.assertNotEqual(result.detail.intent, "conversation")

    async def test_greeting_is_conversation_without_graph_or_llm(self) -> None:
        class UnusedRetriever:
            async def retrieve(self, *_args, **_kwargs):
                raise AssertionError("Greeting must not search the dictionary")

        class UnusedSelector:
            async def select(self, *_args, **_kwargs):
                raise AssertionError("Greeting must not require ThaiLLM")

        service = AskService(
            repository=FakeRepository(), retriever=UnusedRetriever(),
            heuristic_selector=HeuristicSenseSelector(),
            llm_selector=UnusedSelector(), default_to_llm=True, provider="thaillm",
        )
        result = await service.ask(AskRequest(query="สวัสดีครับ!", context_lemma="ขัน"))
        self.assertEqual(result.detail.intent, "conversation")
        self.assertIn("สวัสดีครับ", result.answer)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.citations, [])
        self.assertEqual(result.graph.edges, [])
        self.assertFalse(result.evidence_validated)
        self.assertEqual(result.selection.selector, "none")

    async def test_lexical_question_starting_with_greeting_still_searches(self) -> None:
        service = AskService(
            repository=FakeRepository(), retriever=FakeRetriever(),
            heuristic_selector=HeuristicSenseSelector(), default_to_llm=False,
        )
        result = await service.ask(AskRequest(query="สวัสดี คำว่า ขัน หมายถึงอะไร"))
        self.assertEqual(result.detected_lemma, "ขัน")
        self.assertTrue(result.candidates)
        self.assertNotEqual(result.detail.intent, "conversation")

    def test_named_new_word_does_not_inherit_previous_chat_target(self) -> None:
        request = AskRequest(
            query="แล้วคำที่เกี่ยวข้องกับดาวมีอะไรบ้าง",
            context_lemma="ขัน",
        )
        self.assertIsNone(_context_lemma_for(request))

    async def test_valid_thaillm_answer_is_used_instead_of_fixed_template(self) -> None:
        service = AskService(
            repository=FakeRepository(),
            retriever=FakeRetriever(),
            heuristic_selector=HeuristicSenseSelector(),
            llm_selector=ConversationalSelector(),
            default_to_llm=True,
            provider="thaillm",
        )
        result = await service.ask(AskRequest(query="พ่อขันนอตให้แน่น"))
        self.assertTrue(
            result.answer.startswith("คำว่า ขัน ในประโยคนี้หมายถึงหมุนสิ่งยึดให้แน่นครับ")
        )
        self.assertIn("ที่มา: ข้อมูลสาธิต", result.answer)
        self.assertEqual(result.diagnostics.answer_mode, "model")
        self.assertTrue(result.evidence_validated)

    async def test_one_thaillm_call_receives_graph_relation_and_answers(self) -> None:
        from thailex_api.models import GraphEdge, GraphNode

        class RepositoryWithRelations(FakeRepository):
            async def graph(self, start_uris, *, hops, max_edges):
                return GraphResult(hops=2, nodes=[
                    GraphNode(uri="https://example.test/concept/khan", label="ขัน"),
                    GraphNode(uri="https://example.test/concept/khai", label="ไข"),
                ], edges=[
                    GraphEdge(source="https://example.test/sense/1",
                              predicate="http://www.w3.org/ns/lemon/ontolex#reference",
                              target="https://example.test/concept/khan",
                              source_graph="https://example.test/graph", hop=1),
                    GraphEdge(source="https://example.test/concept/khan",
                              predicate="https://w3id.org/thailex/ontology/relatedTerm",
                              target="https://example.test/concept/khai",
                              source_graph="https://example.test/graph", hop=2),
                ])

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            task = json.loads(json.loads(request.content)["messages"][-1]["content"])
            calls.append(task)
            if is_analysis(task):
                return reply({"kind": "lexical", "target_word": "ขัน", "intent": "related", "has_context": True})
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
                "intent": "related", "selected_candidate_id": "S1",
                "confidence": 0.9, "cue_words": ["นอต"],
                "rationale": "ตรงกับบริบท", "evidence_refs": ["S1-E1"],
                "grounded_answer": "คำว่า ขัน ในบริบทนี้มีคำที่เกี่ยวข้องคือ ไข ตามข้อมูลความสัมพันธ์ในกราฟครับ",
            }, ensure_ascii=False)}}]})

        selector = ThaiLLMChatSenseSelector(api_key="test-key", model="test-model")
        await selector._client.aclose()
        selector._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            service = AskService(
                repository=RepositoryWithRelations(), retriever=FakeRetriever(),
                heuristic_selector=HeuristicSenseSelector(), llm_selector=selector,
                default_to_llm=True, provider="thaillm",
            )
            result = await service.ask(AskRequest(query="ขันนอตแล้วมีคำที่เกี่ยวข้องไหม"))
        finally:
            await selector.close()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["question_analysis"], {"intent": "related"})
        self.assertEqual(calls[1]["candidates"][0]["relations"][0]["term"], "ไข")
        self.assertEqual(result.diagnostics.answer_mode, "model")
        self.assertIn("คำที่เกี่ยวข้องคือ ไข", result.answer)

    def test_relation_context_uses_only_graph_edges_for_each_sense(self) -> None:
        from thailex_api.models import GraphEdge, GraphNode

        candidate_item = candidate("https://example.test/sense/1", 0.9, "ev-1")
        graph = GraphResult(
            hops=2,
            nodes=[GraphNode(uri="https://example.test/concept/1", label="ขัน"),
                   GraphNode(uri="https://example.test/concept/2", label="ไข")],
            edges=[
                GraphEdge(source=candidate_item.sense_uri,
                          predicate="http://www.w3.org/ns/lemon/ontolex#reference",
                          target="https://example.test/concept/1", source_graph="urn:graph", hop=1),
                GraphEdge(source="https://example.test/concept/1",
                          predicate="https://w3id.org/thailex/ontology/relatedTerm",
                          target="https://example.test/concept/2", source_graph="urn:graph", hop=2),
            ],
        )
        facts = AskService._relation_context(graph, [candidate_item])
        self.assertEqual(facts[candidate_item.sense_uri][0]["term"], "ไข")

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
        self.assertIn("ความหมายในข้อมูลที่นำเข้า", result.answer)
        self.assertIn("- ที่มา: sample/v1", result.answer)
        self.assertIn("- ที่มา: sample/v2", result.answer)
        self.assertIn("บุคคลที่ได้รับความนิยมเป็นพิเศษ", result.answer)
        self.assertIn("หมุนสิ่งยึดให้แน่น", result.answer)
        self.assertIn("ไม่เลือกความหมายเดียวแทนผู้ใช้", result.answer)
        self.assertNotIn("ในบริบทนี้", result.answer)

    async def test_bare_word_answers_all_meanings_in_chat(self) -> None:
        retriever = MultiSenseRetriever()

        class RepositoryWithSenses(FakeRepository):
            async def get_senses(self, lemma: str, *, limit: int):
                _, senses = await retriever.retrieve("", lemma=lemma, max_candidates=limit)
                return senses

        service = AskService(
            repository=RepositoryWithSenses(), retriever=retriever,
            heuristic_selector=HeuristicSenseSelector(), default_to_llm=False,
        )
        result = await service.ask(AskRequest(query="ดาว"))
        self.assertEqual(result.detail.intent, "define_all")
        self.assertIsNone(result.selection.selected_sense_uri)
        self.assertIn("บุคคลที่ได้รับความนิยมเป็นพิเศษ", result.answer)
        self.assertIn("หมุนสิ่งยึดให้แน่น", result.answer)

    async def test_define_all_does_not_accept_answer_that_only_points_to_panel(self) -> None:
        retriever = MultiSenseRetriever()

        class RepositoryWithSenses(FakeRepository):
            async def get_senses(self, lemma: str, *, limit: int):
                _, senses = await retriever.retrieve("", lemma=lemma, max_candidates=limit)
                return senses

        class VagueSelector(DefineAllSelector):
            async def select(self, query, candidates, *, history=None):
                decision = await super().select(query, candidates, history=history)
                return decision.model_copy(update={
                    "grounded_answer": "คำว่า ดาว มีหลายความหมาย กรุณาเปิดดูข้อมูลในแผงหลักฐานครับ"
                })

        service = AskService(
            repository=RepositoryWithSenses(), retriever=retriever,
            heuristic_selector=HeuristicSenseSelector(), llm_selector=VagueSelector(),
            default_to_llm=True, provider="thaillm",
        )
        result = await service.ask(AskRequest(query="คำว่า ดาว หมายถึงอะไร"))
        self.assertEqual(result.diagnostics.answer_mode, "template")
        self.assertIn("บุคคลที่ได้รับความนิยมเป็นพิเศษ", result.answer)

    async def test_invalid_llm_result_falls_back_to_heuristic_and_says_so(self) -> None:
        service = AskService(
            repository=FakeRepository(),
            retriever=FakeRetriever(),
            heuristic_selector=HeuristicSenseSelector(),
            llm_selector=InvalidSelector(),
            default_to_llm=True,
            max_graph_edges=50,
        )
        result = await service.ask(
            AskRequest(query="พ่อขันนอตให้แน่น", lemma="ขัน", hops=2)
        )
        self.assertEqual(result.selection.selector, "heuristic")
        self.assertEqual(result.selection.selected_sense_uri, "https://example.test/sense/1")
        self.assertTrue(result.diagnostics.fallback_used)
        self.assertEqual(
            result.diagnostics.fallback_reason,
            "llm_evidence_validation_failed:selected_sense_not_in_candidates",
        )
        self.assertTrue(result.evidence_validated)

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

    async def test_structured_output_error_falls_back_with_specific_reason(self) -> None:
        service = AskService(
            repository=FakeRepository(),
            retriever=FakeRetriever(),
            heuristic_selector=HeuristicSenseSelector(),
            llm_selector=MalformedThaiLLMSelector(),
            default_to_llm=True,
            provider="thaillm",
        )
        result = await service.ask(AskRequest(query="พ่อขันนอตให้แน่น"))
        self.assertEqual(result.diagnostics.actual_selector, "heuristic")
        self.assertEqual(
            result.diagnostics.fallback_reason, "llm_structured_output_invalid:invalid_json"
        )
        self.assertEqual(result.diagnostics.answer_mode, "template")

    async def test_http_error_falls_back_and_reports_status_code(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "rate limited"})

        selector = ThaiLLMChatSenseSelector(api_key="test-key", model="test-model")
        await selector._client.aclose()
        selector._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            service = AskService(
                repository=FakeRepository(), retriever=FakeRetriever(),
                heuristic_selector=HeuristicSenseSelector(), llm_selector=selector,
                default_to_llm=True, provider="thaillm",
            )
            with self.assertLogs("thailex_api.services", level="WARNING"):
                result = await service.ask(AskRequest(query="พ่อขันนอตให้แน่น"))
        finally:
            await selector.close()
        self.assertTrue(result.diagnostics.fallback_used)
        self.assertEqual(
            result.diagnostics.fallback_reason, "llm_request_failed:HTTPStatusError:429"
        )
        self.assertIn("หมุนสิ่งยึดให้แน่น", result.answer)

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


class SourceAttributionTests(unittest.IsolatedAsyncioTestCase):
    async def test_define_all_names_every_dataset_for_each_meaning(self) -> None:
        def star(uri, graph, dataset, definition, example=None):
            base = candidate(uri, 0.8, uri.rsplit("/", 1)[-1])
            evidence = [base.evidence[0].model_copy(update={
                "text": definition, "source_graph": graph,
            })]
            if example:
                evidence.append(base.evidence[0].model_copy(update={
                    "evidence_id": base.evidence[0].evidence_id + "-ex",
                    "kind": "example", "text": example, "source_graph": graph,
                }))
            return base.model_copy(update={
                "lemma": "ดาว", "pos": "noun", "source": "organizer",
                "source_graph": graph, "dataset": dataset,
                "edition": "ฉบับสังเคราะห์ 1", "evidence": evidence,
            })

        dictionary = "https://w3id.org/thailex/graph/organizer/sample-dictionary/v1"
        glossary = "https://w3id.org/thailex/graph/organizer/sample-glossary/v1"
        senses = [
            star("https://example.test/sense/d1", dictionary, "พจนานุกรมตัวอย่าง",
                 "วัตถุบนท้องฟ้า", "คืนนี้มองเห็นดาวหลายดวง"),
            star("https://example.test/sense/d2", dictionary, "พจนานุกรมตัวอย่าง",
                 "บุคคลที่ได้รับความสนใจ"),
            star("https://example.test/sense/g1", glossary, "อภิธานศัพท์สังเคราะห์",
                 "วัตถุบนท้องฟ้า"),
        ]

        class Retriever:
            async def retrieve(self, query, *, lemma, max_candidates):
                return "ดาว", senses

        class Repository(FakeRepository):
            async def get_senses(self, lemma, *, limit):
                return senses

        service = AskService(
            repository=Repository(), retriever=Retriever(),
            heuristic_selector=HeuristicSenseSelector(), default_to_llm=False,
        )
        result = await service.ask(AskRequest(query="ดาว"))
        self.assertIn("พบ 2 ความหมาย", result.answer)
        self.assertEqual(result.detail.summary, "พบ 2 ความหมาย จาก 2 แหล่งข้อมูล")
        self.assertIn(
            "1. วัตถุบนท้องฟ้า — คำนาม\n- ที่มา: พจนานุกรมตัวอย่าง, อภิธานศัพท์สังเคราะห์\n"
            "- ตัวอย่าง: “คืนนี้มองเห็นดาวหลายดวง”",
            result.answer,
        )
        self.assertIn("2. บุคคลที่ได้รับความสนใจ — คำนาม\n- ที่มา: พจนานุกรมตัวอย่าง", result.answer)
        self.assertEqual(
            {item.dataset for item in result.detail.source_supports},
            {"พจนานุกรมตัวอย่าง", "อภิธานศัพท์สังเคราะห์"},
        )
        self.assertTrue(all(item.dataset for item in result.citations))


class ContextIntentTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def star_senses():
        base = candidate("https://example.test/sense/star", 0.5, "star-d")
        star = base.model_copy(update={
            "lemma": "ดาว", "pos": "noun", "source": "organizer",
            "source_graph": "https://w3id.org/thailex/graph/organizer/sample/v1",
            "context_score": 0.8, "context_cues": ["สว่าง"],
            "evidence": [base.evidence[0].model_copy(update={
                "text": "วัตถุบนท้องฟ้าที่มองเห็นเป็นจุดสว่างในเวลากลางคืน",
            })],
        })
        person = star.model_copy(update={
            "sense_uri": "https://example.test/sense/person", "context_score": 0.1,
            "context_cues": [],
            "evidence": [star.evidence[0].model_copy(update={
                "evidence_id": "person-d", "text": "บุคคลที่ได้รับความสนใจเป็นพิเศษ",
            })],
        })
        return [star, person]

    async def test_analysed_context_sentence_selects_one_sense_with_the_model(self) -> None:
        senses = self.star_senses()

        class Retriever:
            async def retrieve(self, query, *, lemma, max_candidates):
                return "ดาว", senses

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            task = json.loads(json.loads(request.content)["messages"][-1]["content"])
            calls.append(task)
            if is_analysis(task):
                return reply({"kind": "lexical", "target_word": "ดาว", "intent": "define", "has_context": True})
            return reply({
                "intent": "define", "selected_candidate_id": "S1", "confidence": 0.9,
                "cue_words": ["สว่าง"], "rationale": "บริบทกลางคืน", "evidence_refs": ["S1-E1"],
                "grounded_answer": "ในประโยคนี้ คำว่า ดาว หมายถึงวัตถุบนท้องฟ้าที่เห็นเป็นจุดสว่างครับ",
            })

        selector = ThaiLLMChatSenseSelector(api_key="k", model="m")
        await selector._client.aclose()
        selector._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            service = AskService(
                repository=FakeRepository(), retriever=Retriever(),
                heuristic_selector=HeuristicSenseSelector(), llm_selector=selector,
                default_to_llm=True, provider="thaillm",
            )
            result = await service.ask(AskRequest(query="คืนนี้ดาวสว่างมาก"))
        finally:
            await selector.close()
        self.assertTrue(calls[1]["question_has_context"])
        self.assertEqual(calls[1]["question_analysis"], {"intent": "define"})
        self.assertEqual(result.detail.intent, "define")
        self.assertEqual(result.selection.selected_sense_uri, senses[0].sense_uri)
        self.assertEqual(result.diagnostics.actual_selector, "thaillm")
        self.assertEqual(result.diagnostics.answer_mode, "model")
        self.assertIsNone(result.diagnostics.fallback_reason)
        self.assertIn("วัตถุบนท้องฟ้า", result.answer)

    async def test_question_without_context_lists_all_senses(self) -> None:
        senses = self.star_senses()

        class Retriever:
            async def retrieve(self, query, *, lemma, max_candidates):
                return "ดาว", senses

        class Repository(FakeRepository):
            async def get_senses(self, lemma, *, limit):
                return senses

        service = AskService(
            repository=Repository(), retriever=Retriever(),
            heuristic_selector=HeuristicSenseSelector(), default_to_llm=False,
        )
        result = await service.ask(AskRequest(query="ดาว มีความหมายอะไรบ้าง"))
        self.assertEqual(result.detail.intent, "define_all")
        self.assertIsNone(result.selection.selected_sense_uri)


class WordInfoTests(unittest.IsolatedAsyncioTestCase):
    async def test_word_property_question_is_answered_by_model_from_word_facts(self) -> None:
        from thailex_api.models import LanguageDetailValue, SenseLanguageDetails

        chicken = candidate("https://example.test/sense/kai", 0.5, "kai-d").model_copy(update={
            "lemma": "ไก่", "pos": "noun", "source": "th-wiktionary",
            "source_graph": "https://w3id.org/thailex/graph/th-wiktionary",
        })

        class Retriever:
            async def retrieve(self, query, *, lemma, max_candidates):
                return "ไก่", [chicken]

        class Repository(FakeRepository):
            async def get_sense_details_batch(self, candidates):
                return {chicken.sense_uri: SenseLanguageDetails(
                    etymologies=[LanguageDetailValue(value="สืบทอดจากไทดั้งเดิม *kajᴮ", source_graph=chicken.source_graph)],
                    pronunciations=[LanguageDetailValue(value="/kaj˨˩/", source_graph=chicken.source_graph)],
                )}

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            task = json.loads(json.loads(request.content)["messages"][-1]["content"])
            calls.append(task)
            if is_analysis(task):
                return reply({"kind": "lexical", "target_word": "ไก่", "intent": "word_info", "has_context": False})
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
                "intent": "word_info", "selected_candidate_id": None, "confidence": 0.9,
                "cue_words": [], "rationale": "ถามรากศัพท์", "evidence_refs": ["S1-E1"],
                "grounded_answer": "คำว่า ไก่ สืบทอดจากไทดั้งเดิม *kajᴮ ตามวิกิพจนานุกรมภาษาไทยครับ",
            }, ensure_ascii=False)}}]})

        selector = ThaiLLMChatSenseSelector(api_key="k", model="m")
        await selector._client.aclose()
        selector._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            service = AskService(
                repository=Repository(), retriever=Retriever(),
                heuristic_selector=HeuristicSenseSelector(), llm_selector=selector,
                default_to_llm=True, provider="thaillm",
            )
            result = await service.ask(AskRequest(query="รากศัพท์ของคำว่าไก่คือ"))
        finally:
            await selector.close()
        self.assertEqual(calls[1]["candidates"][0]["word_info"]["etymology"], "สืบทอดจากไทดั้งเดิม *kajᴮ")
        self.assertFalse(calls[1]["question_has_context"])
        self.assertEqual(result.detail.intent, "word_info")
        self.assertEqual(result.diagnostics.actual_selector, "thaillm")
        self.assertIsNone(result.diagnostics.fallback_reason)
        self.assertEqual(result.diagnostics.answer_mode, "model")
        self.assertIn("ไทดั้งเดิม", result.answer)


class WordInfoFollowupTests(unittest.IsolatedAsyncioTestCase):
    async def test_bare_word_after_etymology_question_lists_meanings(self) -> None:
        chicken = candidate("https://example.test/sense/kai", 0.5, "kai-d").model_copy(update={
            "lemma": "ไก่", "pos": "noun", "source": "th-wiktionary",
            "source_graph": "https://w3id.org/thailex/graph/th-wiktionary",
        })

        class Retriever:
            async def retrieve(self, query, *, lemma, max_candidates):
                return "ไก่", [chicken]

        class Repository(FakeRepository):
            async def get_senses(self, lemma, *, limit):
                return [chicken]

        class CarryOverSelector(ThaiLLMChatSenseSelector):
            def __init__(self):
                pass

            async def select(self, query, candidates, **_kwargs):
                return SelectionDecision(
                    selected_sense_uri=None, confidence=0.9, rationale="ต่อจากคำถามก่อน",
                    evidence_ids=["kai-d"], selector="thaillm", intent="word_info",
                    grounded_answer="รากศัพท์ของคำว่า ไก่ มาจากไทดั้งเดิม",
                )

        service = AskService(
            repository=Repository(), retriever=Retriever(),
            heuristic_selector=HeuristicSenseSelector(), llm_selector=CarryOverSelector(),
            default_to_llm=True, provider="thaillm",
        )
        result = await service.ask(AskRequest(
            query="ไก่",
            history=[{"role": "user", "content": "รากศัพท์ของคำว่าไก่คือ"},
                     {"role": "assistant", "content": "รากศัพท์ของคำว่า ไก่ มาจากไทดั้งเดิม"}],
            context_lemma="ไก่",
        ))
        self.assertEqual(result.detail.intent, "define_all")
        self.assertNotIn("รากศัพท์", result.answer)

    def test_long_history_turn_is_shortened_not_rejected(self) -> None:
        request = AskRequest(query="ไก่แปลว่า", history=[{"role": "assistant", "content": "ก" * 5000}])
        self.assertLessEqual(len(request.history[0].content), 2000)

    def test_history_sent_to_model_is_recent_and_short(self) -> None:
        from thailex_api.models import ConversationTurn
        from thailex_api.selector import compact_history

        turns = [ConversationTurn(role="assistant", content="ก" * 1000) for _ in range(10)]
        compacted = compact_history(turns)
        self.assertEqual(len(compacted), 6)
        self.assertLessEqual(len(compacted[0]["content"]), 302)


class LLMAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_target_word_is_looked_up_and_meaning_list_is_model_written(self) -> None:
        father = candidate("https://example.test/sense/pho", 0.5, "pho-d").model_copy(update={
            "lemma": "พ่อ", "pos": "noun", "source": "lexitron",
            "source_graph": "https://w3id.org/thailex/graph/lexitron",
        })

        class Retriever:
            lexicon = None

            def __init__(self):
                self.lemmas = []

            async def retrieve(self, query, *, lemma, max_candidates):
                self.lemmas.append(lemma)
                return lemma, [father]

        def handler(request: httpx.Request) -> httpx.Response:
            task = json.loads(json.loads(request.content)["messages"][-1]["content"])
            if is_analysis(task):
                return reply({"kind": "lexical", "target_word": "พ่อ", "intent": "define_all", "has_context": False})
            return reply({
                "intent": "define_all", "selected_candidate_id": None, "confidence": 0.9,
                "cue_words": [], "rationale": "ถามความหมายทั่วไป", "evidence_refs": ["S1-E1"],
                "grounded_answer": "คำว่า พ่อ มีความหมายหลักดังนี้ครับ\n1. ชายผู้ให้กำเนิดแก่ลูก (ที่มา: LEXiTRON)",
            })

        retriever = Retriever()
        selector = ThaiLLMChatSenseSelector(api_key="k", model="m")
        await selector._client.aclose()
        selector._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            service = AskService(
                repository=FakeRepository(), retriever=retriever,
                heuristic_selector=HeuristicSenseSelector(), llm_selector=selector,
                default_to_llm=True, provider="thaillm",
            )
            result = await service.ask(AskRequest(query="ผมอยากรู้ว่าคำว่า พ่อ เเปลว่าอะไร"))
        finally:
            await selector.close()
        self.assertEqual(retriever.lemmas, ["พ่อ"])
        self.assertEqual(result.detail.intent, "define_all")
        self.assertEqual(result.diagnostics.answer_mode, "model")
        self.assertIn("ชายผู้ให้กำเนิด", result.answer)

    async def test_analysis_failure_falls_back_to_rules(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

        selector = ThaiLLMChatSenseSelector(api_key="k", model="m")
        await selector._client.aclose()
        selector._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            service = AskService(
                repository=FakeRepository(), retriever=FakeRetriever(),
                heuristic_selector=HeuristicSenseSelector(), llm_selector=selector,
                default_to_llm=True, provider="thaillm",
            )
            with self.assertLogs("thailex_api.services", level="WARNING"):
                result = await service.ask(AskRequest(query="ว่าไง ผมอยากถาม"))
        finally:
            await selector.close()
        self.assertEqual(result.detail.intent, "conversation")


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
    async def test_open_ended_followup_reuses_last_word_when_no_new_word_is_found(self) -> None:
        retriever = FollowupRetriever()
        service = AskService(
            repository=FakeRepository(),
            retriever=retriever,
            heuristic_selector=HeuristicSenseSelector(),
        )
        result = await service.ask(AskRequest(
            query="ช่วยอธิบายเพิ่มเติมได้ไหม",
            context_lemma="ขัน",
            context_sense_uri="https://example.test/sense/2",
            history=[{"role": "user", "content": "คำว่า ขัน หมายถึงอะไร"}],
        ))
        self.assertEqual(retriever.queries[-1][1], "ขัน")
        self.assertEqual(result.selection.selected_sense_uri, "https://example.test/sense/2")

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
        self.assertEqual(result.diagnostics.answer_mode, "template")
        self.assertEqual(result.selection.grounded_answer, result.answer)
        self.assertNotEqual(result.selection.rationale, "selected real source")
        self.assertIn("Backend ตรวจ", result.selection.rationale)
        self.assertGreaterEqual(result.timings.total_ms, result.timings.llm_ms)


if __name__ == "__main__":
    unittest.main()
