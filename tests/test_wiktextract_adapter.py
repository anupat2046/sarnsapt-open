import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from thailex_ingestion.wiktextract import (
    WiktextractAdapter,
    build_wiktextract,
    records_to_turtle,
)


THAI_ENTRY = {
    "word": "ขัน",
    "lang": "Thai",
    "lang_code": "th",
    "pos": "verb",
    "etymology_text": "From Proto-Tai.",
    "sounds": [{"ipa": "/kʰan˩˩˦/", "tags": ["Bangkok"]}],
    "forms": [{"form": "ขัน", "roman": "kǎn", "tags": ["romanization"]}],
    "synonyms": [{"word": "ขัด"}],
    "senses": [
        {
            "id": "en-ขัน-th-verb-demo",
            "glosses": ["to tighten"],
            "examples": [
                {"text": "ขันน็อตให้แน่น", "translation": "Tighten the nut."}
            ],
            "synonyms": [{"word": "ทำให้แน่น", "sense": "to make tight"}],
        }
    ],
}


class WiktextractAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "thai.jsonl"
        english = {"word": "test", "lang_code": "en", "pos": "noun", "senses": []}
        self.path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in (THAI_ENTRY, english))
            + "\n",
            encoding="utf-8",
        )
        self.adapter = WiktextractAdapter()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_audit_filters_language_and_separates_relation_levels(self) -> None:
        from thailex_ingestion import wiktextract

        digest = wiktextract._sha256(self.path)
        with patch.object(wiktextract, "WIKTEXTRACT_SHA256", digest):
            report = self.adapter.audit(self.path)
        self.assertEqual(report["counts"]["entry_records"], 2)
        self.assertEqual(report["counts"]["thai_entry_records"], 1)
        self.assertEqual(report["counts"]["filtered_non_thai_entries"], 1)
        self.assertEqual(report["sense_relation_counts"]["synonyms"], 1)
        self.assertEqual(report["skipped_ambiguous_relation_counts"]["synonyms"], 1)

    def test_normalize_imports_only_sense_level_relations(self) -> None:
        records, report = build_wiktextract(self.adapter, self.path, ["ขัน"])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["relations"][0]["term"], "ทำให้แน่น")
        self.assertNotIn("ขัด", [item["term"] for item in records[0]["relations"]])
        self.assertEqual(report["skipped_ambiguous_relation_count"], 1)
        self.assertEqual(records[0]["source_kind"], "community")

    def test_rdf_maps_enrichment_provenance_and_source_fact(self) -> None:
        records, _ = build_wiktextract(self.adapter, self.path, ["ขัน"])
        rdf = records_to_turtle(records)
        self.assertIn("tlkg:CommunitySource", rdf)
        self.assertIn("dcterms:license <https://creativecommons.org/licenses/by-sa/4.0/>", rdf)
        self.assertIn('ontolex:phoneticRep "/kʰan˩˩˦/"', rdf)
        self.assertIn('tlkg:etymologyText "From Proto-Tai."@en', rdf)
        self.assertIn('tlkg:definitionText "to tighten"@en', rdf)
        self.assertIn("lexinfo:partOfSpeech lexinfo:verb", rdf)
        self.assertIn(
            "tlkg:inEdition <https://w3id.org/thailex/edition/en-wiktionary-thai-entries/",
            rdf,
        )
        self.assertIn("a tlkg:ExampleAssertion, prov:Entity", rdf)
        self.assertIn("tlkg:synonym", rdf)
        self.assertIn("tlkg:assertionStatus tlkg:SourceFactStatus", rdf)


if __name__ == "__main__":
    unittest.main()
