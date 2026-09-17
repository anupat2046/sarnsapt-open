import csv
import tempfile
import unittest
from pathlib import Path

from thailex_ingestion.lexitron import LexitronAdapter, build_lexitron_subset, records_to_turtle


class LexitronAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = LexitronAdapter()
        self.row = {
            "id": "10376",
            "t-search": "ขัน",
            "t-entry": "ขัน 2",
            "e-entry": "tighten",
            "t-cat": "V",
            "t-syn": "รัด, ทำให้แน่น",
            "t-sample": "ช่างขันนอตให้แน่น",
            "t-ant": "คลาย, ผ่อน",
            "t-def": "หมุนหรือบิดให้แน่น",
            "e-related": "",
            "t-num": "",
            "notes": "",
        }

    def test_normalize_preserves_source_and_maps_fields(self) -> None:
        record = self.adapter.normalize(self.row)
        self.assertEqual(record["source_record_id"], "10376")
        self.assertEqual(record["lemma"], "ขัน")
        self.assertEqual(record["written_form"], "ขัน")
        self.assertEqual(record["source_entry"], "ขัน 2")
        self.assertEqual(record["sense_number"], "2")
        self.assertEqual(record["pos"], "verb")
        self.assertEqual(record["source_pos"], "V")
        self.assertEqual(record["pos_status"], "mapped")
        self.assertEqual(record["record_kind"], "definition-bearing")
        self.assertEqual(record["policy_decision"], "accept")
        self.assertEqual(record["quality_flags"], [])
        self.assertEqual([item["term"] for item in record["synonyms"]], ["รัด", "ทำให้แน่น"])
        self.assertEqual([item["term"] for item in record["antonyms"]], ["คลาย", "ผ่อน"])
        self.assertEqual(record["translations"], [{"term": "tighten", "language": "en"}])
        self.assertEqual(record["raw_record"]["t-entry"], "ขัน 2")

    def test_unknown_pos_is_retained_as_warning(self) -> None:
        row = dict(self.row, **{"t-cat": "CUSTOM"})
        record = self.adapter.normalize(row)
        errors, warnings = self.adapter.validate(record)
        self.assertEqual(errors, [])
        self.assertEqual(record["pos"], "unknown")
        self.assertEqual(record["pos_status"], "unmapped")
        self.assertIn("unmapped-pos", warnings)

    def test_missing_definition_with_translation_is_retained(self) -> None:
        row = dict(self.row, **{"t-def": ""})
        record = self.adapter.normalize(row)
        errors, warnings = self.adapter.validate(record)
        self.assertEqual(errors, [])
        self.assertEqual(record["definition"], None)
        self.assertEqual(record["record_kind"], "translation-only")
        self.assertEqual(record["policy_decision"], "accept-with-warning")
        self.assertIn("missing-definition", warnings)
        turtle = records_to_turtle([record])
        self.assertNotIn("tlkg:hasDefinition", turtle)
        self.assertNotIn("tlkg:DefinitionAssertion", turtle)
        self.assertIn('tlkg:recordKind "translation-only"', turtle)

    def test_missing_definition_and_translation_is_rejected(self) -> None:
        row = dict(self.row, **{"t-def": "", "e-entry": ""})
        record = self.adapter.normalize(row)
        errors, _ = self.adapter.validate(record)
        self.assertEqual(record["record_kind"], "unusable")
        self.assertEqual(record["policy_decision"], "reject")
        self.assertTrue(errors)

    def test_build_and_rdf_keep_source_specific_sense(self) -> None:
        records, selected_rows, report = build_lexitron_subset(
            self.adapter, [self.row], requested_lemmas=["ขัน"], limit_lemmas=20
        )
        self.assertEqual(len(selected_rows), 1)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["definition_bearing_count"], 1)
        self.assertEqual(report["translation_only_count"], 0)
        turtle = records_to_turtle(records)
        self.assertIn("https://w3id.org/thailex/sense/lexitron/10376", turtle)
        self.assertIn('tlkg:sourceRecordId "10376"', turtle)
        self.assertIn("prov:wasDerivedFrom <https://w3id.org/thailex/source/lexitron/record/10376>", turtle)
        self.assertIn("tlkg:definitionText \"หมุนหรือบิดให้แน่น\"@th", turtle)
        self.assertIn("vartrans:Translation", turtle)

    def test_audit_reads_utf8_bom_and_headers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telex.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(self.row))
                writer.writeheader()
                writer.writerow(self.row)
            report = self.adapter.audit(path)
        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["row_count"], 1)
        self.assertTrue(report["file"]["utf8_bom"])
        self.assertEqual(report["data_policy"]["importable_record_count"], 1)


if __name__ == "__main__":
    unittest.main()
