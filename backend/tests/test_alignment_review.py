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
