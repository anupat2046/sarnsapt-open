import json
import tempfile
import unittest
from pathlib import Path

from thailex_ingestion.alignment import (
    apply_decisions,
    candidates_to_turtle,
    generate_candidates,
    load_decisions,
    normalize_lemma,
    normalize_pos,
    score_candidate,
    select_review_batch,
)


def sense(source: str, suffix: str, pos: str, evidence: list[dict]):
    return {
        "sense_uri": f"https://example.invalid/{source}/{suffix}",
        "concept_uri": f"https://example.invalid/concept/{source}/{suffix}",
        "entry_uri": "https://example.invalid/entry/khan",
        "source_graph": f"https://example.invalid/graph/{source}",
        "source": source,
        "source_record_id": suffix,
        "lemma": "ขัน",
        "normalized_lemma": "ขัน",
        "source_pos": pos,
        "pos": pos,
        "evidence": evidence,
    }


class AlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.left = sense(
            "lexitron", "l1", "verb",
            [{"kind": "translation", "text": "tighten", "language": "en"}],
        )
        self.right = sense(
            "omw-th", "w1", "verb",
            [{"kind": "label", "text": "tighten", "language": "en"}],
        )

    def test_normalization_removes_zero_width_and_maps_source_pos(self) -> None:
        self.assertEqual(normalize_lemma("  ข\u200bัน  "), "ขัน")
        self.assertEqual(normalize_pos("V"), "verb")
        self.assertEqual(normalize_pos("adj"), "adjective")

    def test_high_score_is_only_a_pending_recommendation(self) -> None:
        candidate = score_candidate(self.left, self.right)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["recommended_relation"], "exactMatch")
        self.assertEqual(candidate["relation"], "possiblySameSense")
        self.assertEqual(candidate["review_status"], "pending")
        self.assertEqual(candidate["confidence"], 1.0)

    def test_incompatible_pos_does_not_create_candidate(self) -> None:
        noun = dict(self.right, pos="noun")
        self.assertIsNone(score_candidate(self.left, noun))

    def test_candidates_are_cross_source_and_deduplicated(self) -> None:
        same_source = dict(self.right, source="lexitron", sense_uri="https://example.invalid/lexitron/l2")
        candidates = generate_candidates([self.left, self.right, same_source])
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(item["left"]["source"] != item["right"]["source"] for item in candidates))

    def test_approved_decision_promotes_only_reviewed_graph(self) -> None:
        candidate = score_candidate(self.left, self.right)
        reviewed = apply_decisions(
            [candidate],
            {
                candidate["candidate_id"]: {
                    "candidate_id": candidate["candidate_id"],
                    "review_status": "approved",
                    "relation": "exactMatch",
                    "reviewer": "language-expert-1",
                    "note": "English labels and POS identify the same sense",
                }
            },
        )
        proposed_rdf = candidates_to_turtle(reviewed, "pending")
        reviewed_rdf = candidates_to_turtle(reviewed, "reviewed")
        self.assertNotIn("a tlkg:AlignmentAssertion", proposed_rdf)
        self.assertIn("skos:exactMatch", reviewed_rdf)
        self.assertIn("tlkg:ApprovedReviewStatus", reviewed_rdf)
        self.assertIn('tlkg:reviewedBy "language-expert-1"', reviewed_rdf)

    def test_rejected_decision_has_audit_assertion_but_no_direct_edge(self) -> None:
        candidate = score_candidate(self.left, self.right)
        rejected = apply_decisions(
            [candidate],
            {
                candidate["candidate_id"]: {
                    "candidate_id": candidate["candidate_id"],
                    "review_status": "rejected",
                    "relation": None,
                    "reviewer": "language-expert-1",
                    "note": "Different context",
                }
            },
        )
        rdf = candidates_to_turtle(rejected, "reviewed")
        self.assertIn("a tlkg:AlignmentAssertion", rdf)
        self.assertIn("tlkg:RejectedReviewStatus", rdf)
        direct_lines = [line for line in rdf.splitlines() if line.startswith("<https://example.invalid/lexitron/l1>")]
        self.assertEqual(direct_lines, [])

    def test_stale_decision_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown/stale"):
            apply_decisions([], {"stale": {"review_status": "approved"}})

    def test_decision_file_requires_consistent_relation_and_review_note(self) -> None:
        candidate = score_candidate(self.left, self.right)
        payload = {
            "version": "1.0",
            "decisions": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "review_status": "rejected",
                    "relation": "exactMatch",
                    "reviewer": "language-expert-1",
                    "note": "Different context",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must set relation to null"):
                load_decisions(path)

            payload["decisions"][0]["relation"] = None
            payload["decisions"][0]["note"] = ""
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requires a review note"):
                load_decisions(path)

            payload["decisions"][0]["note"] = "Different context"
            payload["decisions"][0]["reviewer"] = "AI-PROVISIONAL"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requires a human reviewer"):
                load_decisions(path)

    def test_review_batch_balances_lemma_coverage_and_source_pairs(self) -> None:
        candidates = []
        for lemma in ("ขัน", "กิน"):
            for source, suffix, evidence in (
                ("omw-th", "w1", "tighten"),
                ("wiktionary", "k1", "tighten"),
                ("wiktionary", "k2", "tighten securely"),
            ):
                left = dict(self.left, lemma=lemma, normalized_lemma=lemma)
                right = sense(source, f"{lemma}-{suffix}", "verb", [
                    {"kind": "label", "text": evidence, "language": "en"}
                ])
                right["lemma"] = lemma
                right["normalized_lemma"] = lemma
                candidate = score_candidate(left, right)
                self.assertIsNotNone(candidate)
                candidates.append(candidate)

        selected, report = select_review_batch(
            candidates, ["ขัน", "กิน"], size=4, per_lemma=2, min_confidence=0.85
        )
        self.assertEqual(len(selected), 4)
        self.assertEqual(report["lemma_counts"], {"ขัน": 2, "กิน": 2})
        self.assertEqual({item["selection_round"] for item in selected}, {1, 2})
        for lemma in ("ขัน", "กิน"):
            pairs = {
                "|".join(sorted((item["left"]["source"], item["right"]["source"])))
                for item in selected if item["normalized_lemma"] == lemma
            }
            self.assertEqual(len(pairs), 2)


if __name__ == "__main__":
    unittest.main()
