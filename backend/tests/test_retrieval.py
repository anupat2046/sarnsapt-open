from __future__ import annotations

import unittest

from thailex_api.models import SenseCandidate
from thailex_api.retrieval import CandidateRetriever


class FakeRepository:
    def __init__(self) -> None:
        self.detect_called = False

    async def detect_mentions(self, question: str, limit: int):
        self.detect_called = True
        return ["หมายถึง", "ขัน"]

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
        self.assertFalse(repository.detect_called)

    async def test_explicit_kham_wa_pattern_wins_over_generic_mentions(self) -> None:
        repository = FakeRepository()
        detected, _ = await CandidateRetriever(repository).retrieve(
            "คำว่า “ขัน” ในประโยคนี้หมายถึงอะไร",
            lemma=None,
            max_candidates=8,
        )
        self.assertEqual(detected, "ขัน")
        self.assertEqual(repository.requested_lemma, "ขัน")
        self.assertFalse(repository.detect_called)

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
