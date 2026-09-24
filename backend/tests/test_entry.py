from __future__ import annotations

import unittest

from thailex_api.entry import EntryBuilder
from thailex_api.lexicon import LexiconIndex
from thailex_api.models import (
    AlignmentCandidate,
    EvidenceItem,
    LanguageDetailValue,
    SearchItem,
    SenseCandidate,
    SenseLanguageDetails,
)
from thailex_api.retrieval import CandidateRetriever

DICTIONARY = "https://w3id.org/thailex/graph/organizer/sample-dictionary/v1"
GLOSSARY = "https://w3id.org/thailex/graph/organizer/sample-glossary/v1"


def sense(uri: str, graph: str, dataset: str, record: str, definition: str) -> SenseCandidate:
    return SenseCandidate(
        sense_uri=uri, entry_uri="urn:entry", lemma="ดาว", pos="noun",
        source="organizer", source_graph=graph, dataset=dataset,
        edition="ฉบับสังเคราะห์ 1", source_record_id=record,
        evidence=[EvidenceItem(
            evidence_id=uri + "-d", kind="definition", text=definition,
            evidence_uri=uri + "-d", source_graph=graph,
        )],
    )


class FakeRepository:
    def __init__(self) -> None:
        self.senses = [
            sense("urn:g1", GLOSSARY, "อภิธานศัพท์", "glossary-1", "วัตถุบนท้องฟ้า"),
            sense("urn:d10", DICTIONARY, "พจนานุกรมตัวอย่าง", "sample-10", "เครื่องหมายรูปดาว"),
            sense("urn:d2", DICTIONARY, "พจนานุกรมตัวอย่าง", "sample-2", "วัตถุบนท้องฟ้า"),
        ]

    async def lemma_inventory(self):
        return [("ดาว", 3), ("เป็น", 9)]

    async def get_senses(self, lemma, limit):
        return self.senses if lemma == "ดาว" else []

    async def get_sense_details_batch(self, candidates):
        return {
            item.sense_uri: SenseLanguageDetails(
                pronunciations=[LanguageDetailValue(value="daao", language="th-Latn", source_graph=item.source_graph)],
            )
            for item in candidates
        }

    async def get_alignments(self, lemma, limit):
        return [AlignmentCandidate(
            candidate_id="a" * 24, assertion_uri="urn:a", left_sense_uri="urn:d2",
            right_sense_uri="urn:g1", confidence=0.9, semantic_similarity=1.0,
            pos_compatibility=1.0, method="auto", recommended_relation="possiblySameSense",
            review_status="pending",
        )]

    async def search(self, query, limit):
        if query not in "ดาว":
            return []
        return [SearchItem(lemma="ดาว", sense_count=3, sources=["organizer"])]


class EntryBuilderTests(unittest.IsolatedAsyncioTestCase):
    def builder(self, repository: FakeRepository) -> EntryBuilder:
        lexicon = LexiconIndex(repository)
        return EntryBuilder(repository, CandidateRetriever(repository, lexicon), lexicon)

    async def test_entry_groups_senses_by_source_in_record_order_with_links(self) -> None:
        entry = await self.builder(FakeRepository()).build("ดาว")
        self.assertTrue(entry.found)
        self.assertEqual(entry.resolved_from, "exact")
        self.assertEqual((entry.sense_count, entry.source_count), (3, 2))
        self.assertEqual([group.name for group in entry.groups], ["พจนานุกรมตัวอย่าง", "อภิธานศัพท์"])
        dictionary = entry.groups[0]
        self.assertEqual([item.candidate.sense_uri for item in dictionary.senses], ["urn:d2", "urn:d10"])
        link = dictionary.senses[0].alignments[0]
        self.assertEqual((link.other_sense_uri, link.other_source_name, link.review_status), ("urn:g1", "อภิธานศัพท์", "pending"))
        self.assertEqual([item.value for item in entry.pronunciations], ["daao"])

    async def test_stale_lexicon_refreshes_in_background_and_keeps_serving(self) -> None:
        repository = FakeRepository()
        calls = []
        original = repository.lemma_inventory

        async def counted():
            calls.append(1)
            return await original()

        repository.lemma_inventory = counted
        lexicon = LexiconIndex(repository, ttl_seconds=0)
        self.assertTrue(await lexicon.contains("ดาว"))
        self.assertTrue(await lexicon.contains("ดาว"))  # stale: served, refresh scheduled
        await lexicon._refresh
        self.assertEqual(len(calls), 2)

    async def test_question_prefers_first_content_word_over_word_with_more_senses(self) -> None:
        class Repository(FakeRepository):
            async def lemma_inventory(self):
                return [("อ้วน", 2), ("ลักษณะ", 9), ("ดาว", 3), ("คืนนี้", 1), ("สว่าง", 5)]

        lexicon = LexiconIndex(Repository())
        self.assertEqual((await lexicon.mentions("อ้วน เป็นลักษณะคำแบบไหน"))[0], "อ้วน")
        # A plain sentence still prefers the ambiguous word.
        self.assertEqual((await lexicon.mentions("คืนนี้ดาวสว่างมาก"))[0], "สว่าง")

    async def test_short_graph_lemmas_do_not_hide_long_question_words(self) -> None:
        class ShortLemmaRepository(FakeRepository):
            async def lemma_inventory(self):
                return [("ดาว", 3), ("ตา", 1)]

        lexicon = LexiconIndex(ShortLemmaRepository())
        self.assertEqual(await lexicon.context_tokens("ดาว มีความหมายอะไรบ้าง", "ดาว"), [("มี", 0.25)])

    async def test_sentence_resolves_to_word_and_unknown_word_gets_suggestions(self) -> None:
        builder = self.builder(FakeRepository())
        sentence = await builder.build("เขาเป็นดาวของห้อง")
        self.assertEqual((sentence.lemma, sentence.resolved_from), ("ดาว", "sentence"))
        typo = await builder.build("ดาวว")
        self.assertFalse(typo.found)
        self.assertEqual([item.lemma for item in typo.suggestions], ["ดาว"])
        unknown = await builder.build("ฟหกด")
        self.assertFalse(unknown.found)
        self.assertEqual(unknown.suggestions, [])

    async def test_made_up_compound_is_not_answered_as_a_word_inside_it(self) -> None:
        class Repository(FakeRepository):
            async def lemma_inventory(self):
                return [("ดาว", 3), ("กรอบ", 2)]

        made_up = await self.builder(Repository()).build("กรอบดาว")
        self.assertFalse(made_up.found)
        self.assertIsNone(made_up.lemma)


if __name__ == "__main__":
    unittest.main()
