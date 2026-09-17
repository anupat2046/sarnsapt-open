import tempfile
import unittest
from pathlib import Path

from thailex_ingestion.wordnet_lmf import (
    WordNetLMFParser,
    build_omw_full_streaming,
    build_omw_subset,
    english_to_turtle,
    thai_to_turtle,
)


THAI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<LexicalResource>
  <Lexicon id="omw-th" label="Thai Wordnet" language="th" email="test@example.org"
           license="wordnet" version="2.0" url="https://github.com/omwn/omw-data"
           citation="Thai Wordnet Construction">
    <LexicalEntry id="omw-th-khan-v">
      <Lemma writtenForm="ขัน" partOfSpeech="v" />
      <Sense id="omw-th-khan-1-v" synset="omw-th-00000001-v" />
    </LexicalEntry>
    <LexicalEntry id="omw-th-other-n">
      <Lemma writtenForm="อื่น" partOfSpeech="n" />
      <Sense id="omw-th-other-1-n" synset="omw-th-00000003-n" />
    </LexicalEntry>
    <Synset id="omw-th-00000001-v" ili="i1" partOfSpeech="v" members="omw-th-khan-1-v" />
    <Synset id="omw-th-00000003-n" ili="i3" partOfSpeech="n" members="omw-th-other-1-n" />
  </Lexicon>
</LexicalResource>
"""

ENGLISH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<LexicalResource>
  <Lexicon id="omw-en" label="English Wordnet" language="en" email="test@example.org"
           license="https://wordnet.princeton.edu/license-and-commercial-use"
           version="2.0" url="https://github.com/omwn/omw-data" citation="Fellbaum 1998">
    <LexicalEntry id="omw-en-tighten-v">
      <Lemma writtenForm="tighten" partOfSpeech="v" />
      <Sense id="omw-en-tighten-1-v" synset="omw-en-00000001-v" />
    </LexicalEntry>
    <LexicalEntry id="omw-en-change-v">
      <Lemma writtenForm="change" partOfSpeech="v" />
      <Sense id="omw-en-change-1-v" synset="omw-en-00000002-v" />
    </LexicalEntry>
    <Synset id="omw-en-00000001-v" ili="i1" partOfSpeech="v" members="omw-en-tighten-1-v">
      <Definition>make tight or tighter</Definition>
      <Example>tighten the bolt</Example>
      <SynsetRelation target="omw-en-00000002-v" relType="hypernym" />
      <SynsetRelation target="omw-en-00000003-v" relType="domain_topic" />
    </Synset>
    <Synset id="omw-en-00000002-v" ili="i2" partOfSpeech="v" members="omw-en-change-1-v">
      <Definition>cause to change</Definition>
    </Synset>
  </Lexicon>
</LexicalResource>
"""


class WordNetLMFTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.thai_path = root / "omw-th.xml"
        self.english_path = root / "omw-en.xml"
        self.thai_path.write_text(THAI_XML, encoding="utf-8")
        self.english_path.write_text(ENGLISH_XML, encoding="utf-8")
        self.parser = WordNetLMFParser()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_audit_reads_wn_lmf_entities_and_metadata(self) -> None:
        audit = self.parser.audit(self.english_path)
        self.assertEqual(audit["metadata"]["id"], "omw-en")
        self.assertEqual(audit["counts"]["lexical_entries"], 2)
        self.assertEqual(audit["counts"]["synsets"], 2)
        self.assertEqual(audit["counts"]["definitions"], 2)
        self.assertEqual(audit["synset_relation_counts"]["hypernym"], 1)

    def test_build_aligns_thai_to_english_by_ili(self) -> None:
        thai, english, report = build_omw_subset(
            self.parser, self.thai_path, self.english_path, ["ขัน", "หาย"]
        )
        self.assertEqual(report["matched_lemma_count"], 1)
        self.assertEqual(report["missing_requested_lemmas"], ["หาย"])
        self.assertEqual(report["thai_sense_count"], 1)
        self.assertEqual(report["thai_entry_count"], 1)
        self.assertEqual(report["thai_source_entry_count"], 1)
        self.assertEqual(report["aligned_english_synset_count"], 1)
        self.assertEqual(report["english_context_synset_count"], 2)
        self.assertEqual(report["english_definition_count"], 1)
        self.assertEqual(
            report["selected_relation_counts"],
            {"domain_topic": 1, "hypernym": 1},
        )
        self.assertEqual(thai["synsets"][0]["english_synset_id"], "omw-en-00000001-v")
        self.assertEqual(len(english["synsets"]), 2)

    def test_rdf_preserves_graph_alignment_and_provenance(self) -> None:
        thai, english, _ = build_omw_subset(
            self.parser, self.thai_path, self.english_path, ["ขัน"]
        )
        thai_rdf = thai_to_turtle(thai)
        english_rdf = english_to_turtle(english)
        self.assertIn("skos:exactMatch <https://w3id.org/thailex/concept/omw-en/", thai_rdf)
        self.assertIn("tlkg:interlingualIndex <https://w3id.org/thailex/ili/i1>", thai_rdf)
        self.assertIn("lexinfo:partOfSpeech lexinfo:verb", thai_rdf)
        self.assertIn('tlkg:definitionText "make tight or tighter"@en', english_rdf)
        self.assertIn("tlkg:hypernym <https://w3id.org/thailex/concept/omw-en/", english_rdf)
        self.assertIn('tlkg:sourceRelationType "hypernym"', english_rdf)
        self.assertIn("dcterms:bibliographicCitation", english_rdf)

    def test_full_streaming_build_writes_bounded_outputs_and_reports_gaps(self) -> None:
        root = Path(self.temp.name)
        report = build_omw_full_streaming(
            self.parser,
            self.thai_path,
            self.english_path,
            root / "thai.jsonl",
            root / "english.jsonl",
            root / "thai.ttl",
            root / "english.ttl",
            batch_size=1,
        )
        self.assertEqual(report["selection_mode"], "full")
        self.assertEqual(report["thai_entry_count"], 2)
        self.assertEqual(report["thai_sense_count"], 2)
        self.assertEqual(report["aligned_english_synset_count"], 1)
        self.assertEqual(report["thai_synsets_without_english_match_count"], 1)
        self.assertEqual(report["status"], "warning")
        self.assertIn('"record_type":"entry"', (root / "thai.jsonl").read_text(encoding="utf-8"))
        self.assertIn("tlkg:hasSynsetDefinition", (root / "english.ttl").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
