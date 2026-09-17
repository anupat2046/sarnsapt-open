import csv
import json
import tempfile
import unittest
from pathlib import Path

from thailex_ingestion.organizer import (
    OrganizerCSVAdapter,
    OrganizerJSONAdapter,
    OrganizerXMLAdapter,
    audit_dataset,
    build_dataset,
    load_mapping,
    records_to_turtle,
    write_json,
)


def mapping_for(input_format: str) -> dict:
    input_config = {"format": input_format}
    if input_format == "csv":
        input_config.update({"encoding": "utf-8-sig", "delimiter": ","})
    elif input_format == "json":
        input_config.update({"encoding": "utf-8", "record_path": "records"})
    elif input_format == "xml":
        input_config.update({"record_path": ".//entry", "namespaces": {}})
    return {
        "mapping_version": "1.0",
        "source": {
            "id": "synthetic-organizer",
            "title": "Synthetic organizer adapter test",
            "edition_id": "edition-test",
            "edition_title": "Synthetic edition",
            "language": "th",
            "source_url": "https://example.invalid/source",
            "license": "Testing only",
            "citation": "Synthetic fixture; not organizer data",
        },
        "input": input_config,
        "fields": {
            "source_record_id": {"path": "id" if input_format != "xml" else "@id"},
            "lemma": {"path": "lemma"},
            "source_entry": {"path": "lemma"},
            "pos": {"path": "pos"},
            "sense_number": {"path": "sense"},
            "definition": {"path": "definition"},
            "examples": {"path": "examples", "separator": "|"},
            "synonyms": {
                "path": "synonyms",
                "separator": "|",
                "sense_disambiguated": True,
            },
            "antonyms": {
                "path": "antonyms",
                "separator": "|",
                "sense_disambiguated": False,
            },
        },
        "pos_mapping": {"V": "verb"},
    }


class OrganizerAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_mapping(self, value: dict, name: str = "mapping.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def _audit_and_build(self, data_path: Path, mapping_path: Path):
        audit, _, _, _ = audit_dataset(data_path, mapping_path)
        audit_path = self.root / "audit.json"
        write_json(audit_path, audit)
        records, validation, mapping = build_dataset(
            data_path, mapping_path, audit_path
        )
        return audit, records, validation, mapping

    def test_csv_adapter_audits_maps_and_skips_ambiguous_relation(self) -> None:
        data_path = self.root / "data.csv"
        with data_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=["id", "lemma", "pos", "sense", "definition", "examples", "synonyms", "antonyms"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "id": "r-1", "lemma": "ขัน", "pos": "V", "sense": "1",
                    "definition": "ทำให้แน่น", "examples": "ขันนอต|ขันสกรู",
                    "synonyms": "หมุนให้แน่น", "antonyms": "คลาย",
                }
            )
        mapping_path = self._write_mapping(mapping_for("csv"))
        audit, records, validation, _ = self._audit_and_build(data_path, mapping_path)
        self.assertIsInstance(load_mapping(mapping_path) and OrganizerCSVAdapter(load_mapping(mapping_path)), OrganizerCSVAdapter)
        self.assertEqual(audit["status"], "warning")
        self.assertEqual(audit["skipped_relation_counts"]["antonyms"], 1)
        self.assertEqual(records[0]["examples"], ["ขันนอต", "ขันสกรู"])
        self.assertEqual(records[0]["synonyms"][0]["term"], "หมุนให้แน่น")
        self.assertEqual(records[0]["antonyms"], [])
        self.assertEqual(validation["graph_iri"], "https://w3id.org/thailex/graph/organizer/synthetic-organizer/edition-test")

    def test_json_adapter_reads_configured_record_path(self) -> None:
        data_path = self.root / "data.json"
        data_path.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "id": "r-2", "lemma": "ตา", "pos": "noun",
                            "sense": "1", "definition": "อวัยวะสำหรับมอง",
                            "examples": ["มองด้วยตา"], "synonyms": [], "antonyms": [],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        mapping_path = self._write_mapping(mapping_for("json"))
        audit, records, _, _ = self._audit_and_build(data_path, mapping_path)
        self.assertIsInstance(OrganizerJSONAdapter(load_mapping(mapping_path)), OrganizerJSONAdapter)
        self.assertEqual(audit["counts"]["source_records"], 1)
        self.assertEqual(records[0]["lemma"], "ตา")
        self.assertEqual(records[0]["raw_record"]["definition"], "อวัยวะสำหรับมอง")

    def test_xml_adapter_preserves_serialized_source_record(self) -> None:
        data_path = self.root / "data.xml"
        data_path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<records><entry id="r-3"><lemma>หัว</lemma><pos>noun</pos><sense>1</sense>
<definition>ส่วนบนของร่างกาย</definition><examples>เขาส่ายหัว</examples>
<synonyms>ศีรษะ</synonyms><antonyms /></entry></records>""",
            encoding="utf-8",
        )
        mapping_path = self._write_mapping(mapping_for("xml"))
        audit, records, _, _ = self._audit_and_build(data_path, mapping_path)
        self.assertIsInstance(OrganizerXMLAdapter(load_mapping(mapping_path)), OrganizerXMLAdapter)
        self.assertEqual(audit["input_file"]["format"], "xml")
        self.assertIn("<lemma>หัว</lemma>", records[0]["raw_record"]["xml"])

    def test_build_refuses_data_changed_after_audit(self) -> None:
        data_path = self.root / "data.json"
        data_path.write_text(
            json.dumps({"records": [{"id": "r-1", "lemma": "คำ", "definition": "ข้อความ"}]}),
            encoding="utf-8",
        )
        config = mapping_for("json")
        config["fields"] = {
            "source_record_id": {"path": "id"},
            "lemma": {"path": "lemma"},
            "definition": {"path": "definition"},
        }
        mapping_path = self._write_mapping(config)
        audit, _, _, _ = audit_dataset(data_path, mapping_path)
        audit_path = self.root / "audit.json"
        write_json(audit_path, audit)
        data_path.write_text(
            json.dumps({"records": [{"id": "r-1", "lemma": "เปลี่ยน", "definition": "ข้อความ"}]}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "changed after audit"):
            build_dataset(data_path, mapping_path, audit_path)

    def test_rdf_uses_edition_graph_identity_and_preserves_raw_record(self) -> None:
        data_path = self.root / "data.json"
        data_path.write_text(
            json.dumps(
                {"records": [{"id": "r-4", "lemma": "ใจ", "pos": "noun", "sense": "1", "definition": "ศูนย์รวมความรู้สึก", "examples": [], "synonyms": [], "antonyms": []}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        mapping_path = self._write_mapping(mapping_for("json"))
        _, records, _, mapping = self._audit_and_build(data_path, mapping_path)
        rdf = records_to_turtle(records, mapping)
        self.assertIn("tlkg:OrganizerSource", rdf)
        self.assertIn("/edition/organizer/synthetic-organizer/edition-test", rdf)
        self.assertIn("tlkg:rawRecordText", rdf)
        self.assertIn("tlkg:SourceFactStatus", rdf)


if __name__ == "__main__":
    unittest.main()
