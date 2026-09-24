from __future__ import annotations

import unittest

from thailex_api.models import ReviewDecisionRequest
from thailex_api.repository import GraphRepository


class FakeClient:
    async def select(self, query: str):
        self.select_query = query
        return []

    async def update(self, query: str):
        self.update_query = query

    async def close(self):
        return None


class AlignmentReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_keeps_lemma_for_future_queries(self) -> None:
        class ReviewClient(FakeClient):
            def __init__(self):
                self.update_query = ""

            async def select(self, _query):
                def value(text):
                    return {"value": text}
                return [{
                    "assertion": value("https://w3id.org/thailex/alignment/assertion/" + "d" * 24),
                    "leftSense": value("https://w3id.org/thailex/sense/test/one"),
                    "rightSense": value("https://w3id.org/thailex/sense/test/two"),
                    "normalizedLemma": value("ขัน"),
                    "confidence": value("0.91"),
                    "semanticSimilarity": value("0.84"),
                    "posCompatibility": value("1"),
                    "method": value("lemma-exact+pos-exact+lexical-evidence"),
                    "recommendedRelation": value("closeMatch"),
                    "reviewStatus": value("https://w3id.org/thailex/ontology/PendingReviewStatus"),
                }]

        client = ReviewClient()
        repository = GraphRepository(client)
        await repository.save_review_decision(ReviewDecisionRequest(
            candidate_id="d" * 24,
            review_status="approved",
            relation="closeMatch",
            reviewer="linguist-01",
            note="checked source definitions",
        ))
        self.assertIn('tlkg:normalizedLemma "ขัน"@th', client.update_query)

    async def test_ai_reviewer_cannot_promote_alignment(self) -> None:
        repository = GraphRepository(FakeClient())
        with self.assertRaisesRegex(ValueError, "ผู้ตรวจต้องเป็นมนุษย์"):
            await repository.save_review_decision(
                ReviewDecisionRequest(
                    candidate_id="a" * 24,
                    review_status="approved",
                    relation="exactMatch",
                    reviewer="AI-PROVISIONAL",
                    note="machine suggestion",
                )
            )

    async def test_approved_decision_requires_relation(self) -> None:
        repository = GraphRepository(FakeClient())
        with self.assertRaisesRegex(ValueError, "ผลอนุมัติต้องเลือก"):
            await repository.save_review_decision(
                ReviewDecisionRequest(
                    candidate_id="b" * 24,
                    review_status="approved",
                    relation=None,
                    reviewer="linguist-01",
                    note="checked",
                )
            )

    async def test_rejected_decision_must_not_have_relation(self) -> None:
        repository = GraphRepository(FakeClient())
        with self.assertRaisesRegex(ValueError, "ผลปฏิเสธต้องไม่มี relation"):
            await repository.save_review_decision(
                ReviewDecisionRequest(
                    candidate_id="c" * 24,
                    review_status="rejected",
                    relation="closeMatch",
                    reviewer="linguist-01",
                    note="different senses",
                )
            )


if __name__ == "__main__":
    unittest.main()
