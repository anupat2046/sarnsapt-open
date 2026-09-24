from __future__ import annotations

import unittest

from thailex_api.models import EvidenceItem, SenseCandidate
from thailex_api.selector import HeuristicSenseSelector
from thailex_api.retrieval import CandidateRetriever, display_term, explicit_term


class FakeRepository:
    def __init__(self) -> None:
        self.inventory_calls = 0
        self.requested_lemma = None

    async def lemma_inventory(self):
        self.inventory_calls += 1
        return [
            ("หมายถึง", 1), ("ขัน", 2), ("ดา", 1), ("ดาว", 3), ("ดาวเทียม", 1),
            ("เป็น", 9), ("ของ", 6), ("ห้อง", 2),
        ]

    async def get_senses(self, lemma: str, limit: int):
        self.requested_lemma = lemma
        return []


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_lemma_is_looked_up_without_mention_detection(self) -> None:
        repository = FakeRepository()
        detected, _ = await CandidateRetriever(repository).retrieve(
            "คืนนี้ดาวสว่าง",
            lemma="ดาว",
            max_candidates=4,
        )
        self.assertEqual(detected, "ดาว")
        self.assertEqual(repository.requested_lemma, "ดาว")

    async def test_explicit_kham_wa_pattern_wins_over_generic_mentions(self) -> None:
        repository = FakeRepository()
        detected, _ = await CandidateRetriever(repository).retrieve(
            "คำว่า “ขัน” ในประโยคนี้หมายถึงอะไร",
            lemma=None,
            max_candidates=8,
        )
        self.assertEqual(detected, "ขัน")
        self.assertEqual(repository.requested_lemma, "ขัน")

    async def test_unspaced_kham_wa_resolves_longest_known_lemma(self) -> None:
        for query, expected in [
            ("คำว่าดาวหมายถึงอะไร", "ดาว"),
            ("ผมอยากรู้ว่า คำว่าดาวอะมีความหมายว่าอะไรได้บ้างมีรายละเอียดยังไง", "ดาว"),
            ("คำว่าดาวเทียมคืออะไร", "ดาวเทียม"),
            ("คำว่า ขัน หมายถึงอะไร", "ขัน"),
        ]:
            with self.subTest(query=query):
                repository = FakeRepository()
                detected, _ = await CandidateRetriever(repository).retrieve(
                    query, lemma=None, max_candidates=8
                )
                self.assertEqual(detected, expected)
                self.assertEqual(repository.requested_lemma, expected)

    async def test_unknown_kham_wa_word_does_not_fall_back_to_words_in_question(self) -> None:
        repository = FakeRepository()
        detected, candidates = await CandidateRetriever(repository).retrieve(
            "คำว่าฟหกดหมายถึงอะไร", lemma=None, max_candidates=8
        )
        self.assertIsNone(detected)
        self.assertEqual(candidates, [])
        self.assertIsNone(repository.requested_lemma)

    async def test_sentence_mention_skips_function_words_and_keeps_compounds(self) -> None:
        for query, expected in [
            ("เขาเป็นดาวของห้อง", "ดาว"),
            ("ดาวเทียมโคจรรอบโลก", "ดาวเทียม"),
            ("เป็น", "เป็น"),
        ]:
            with self.subTest(query=query):
                repository = FakeRepository()
                detected, _ = await CandidateRetriever(repository).retrieve(
                    query, lemma=None, max_candidates=8
                )
                self.assertEqual(detected, expected)

    async def test_lexicon_is_loaded_once_and_reused(self) -> None:
        repository = FakeRepository()
        retriever = CandidateRetriever(repository)
        await retriever.retrieve("คำว่าดาวหมายถึงอะไร", lemma=None, max_candidates=8)
        await retriever.retrieve("เขาเป็นดาวของห้อง", lemma=None, max_candidates=8)
        self.assertEqual(repository.inventory_calls, 1)

    async def test_context_words_rank_the_sense_used_in_the_sentence(self) -> None:
        def sense(uri, definition, example):
            return SenseCandidate(
                sense_uri=uri, entry_uri="urn:entry", lemma="ดาว", pos="noun",
                source="organizer", source_graph="urn:graph:organizer/sample/v1",
                evidence=[
                    EvidenceItem(evidence_id=uri + "-d", kind="definition", text=definition,
                                 evidence_uri=uri + "-d", source_graph="urn:graph"),
                    EvidenceItem(evidence_id=uri + "-e", kind="example", text=example,
                                 evidence_uri=uri + "-e", source_graph="urn:graph"),
                ],
            )

        star = sense("urn:star", "วัตถุบนท้องฟ้าที่มองเห็นเป็นจุดสว่างในเวลากลางคืน", "คืนนี้มองเห็นดาวหลายดวง")
        person = sense("urn:person", "บุคคลที่ได้รับความสนใจเป็นพิเศษในกลุ่มหนึ่ง", "เธอเป็นดาวของงานแสดงครั้งนี้")

        class Repository(FakeRepository):
            async def get_senses(self, lemma, limit):
                return [person, star]

        retriever = CandidateRetriever(Repository())
        _, night = await retriever.retrieve("คืนนี้ดาวสว่างมาก", lemma=None, max_candidates=8)
        self.assertEqual(night[0].sense_uri, "urn:star")
        self.assertIn("สว่าง", night[0].context_cues)
        _, room = await retriever.retrieve("เขาเป็นดาวของห้อง", lemma=None, max_candidates=8)
        self.assertEqual(room[0].sense_uri, "urn:person")

        decision = await HeuristicSenseSelector().select("คืนนี้ดาวสว่างมาก", night)
        self.assertEqual(decision.selected_sense_uri, "urn:star")
        self.assertGreater(decision.confidence, 0.5)
        bare = await HeuristicSenseSelector().select("ดาว", (await retriever.retrieve("ดาว", lemma=None, max_candidates=8))[1])
        self.assertEqual(bare.confidence, 0.25)

    def test_bare_kham_is_not_treated_as_word_marker(self) -> None:
        self.assertEqual(explicit_term("คำนี้หมายถึงอะไร"), (None, False))
        self.assertEqual(explicit_term("คำตอบของคำถามนี้คืออะไร"), (None, False))
        self.assertEqual(explicit_term('คำ "ดาว" คืออะไร'), ("ดาว", True))
        self.assertEqual(display_term("คำว่าฟหกดหมายถึงอะไรครับ"), "ฟหกด")

    async def test_shortlist_reserves_slots_for_real_sources_before_demo(self) -> None:
        class MultiSourceRepository(FakeRepository):
            async def get_senses(self, lemma: str, limit: int):
                self.requested_lemma = lemma
                sources = [
                    "lexitron", "lexitron", "demo", "th-wiktionary",
                    "en-wiktionary-thai-entries", "thai-wordnet",
                ]
                return [
                    SenseCandidate(
                        sense_uri=f"urn:sense:{index}",
                        entry_uri=f"urn:entry:{index}",
                        lemma=lemma,
                        source=source,
                        source_graph=f"urn:graph:{source}",
                    )
                    for index, source in enumerate(sources)
                ]

        _, candidates = await CandidateRetriever(MultiSourceRepository()).retrieve(
            "คำว่า ขัน หมายถึงอะไร", lemma=None, max_candidates=4
        )

        self.assertEqual(
            {candidate.source for candidate in candidates},
            {"lexitron", "th-wiktionary", "en-wiktionary-thai-entries", "thai-wordnet"},
        )


if __name__ == "__main__":
    unittest.main()
