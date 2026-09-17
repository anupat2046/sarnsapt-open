from __future__ import annotations

import unittest

from thailex_api.models import GraphResult, SenseCandidate
from thailex_api.repository import GraphRepository


def binding(value: str, *, kind: str = "literal", language: str | None = None):
    result = {"type": kind, "value": value}
    if language:
        result["xml:lang"] = language
    return result


class RepositoryMappingTests(unittest.TestCase):
    def test_keeps_sense_and_evidence_provenance_separate(self) -> None:
        repository = object.__new__(GraphRepository)
        rows = [{
            "sourceGraph": binding("https://w3id.org/thailex/graph/thai-wordnet", kind="uri"),
            "entry": binding("https://w3id.org/thailex/entry/omw-th/khan", kind="uri"),
            "sense": binding("https://w3id.org/thailex/sense/omw-th/khan-1-v", kind="uri"),
            "concept": binding("https://w3id.org/thailex/concept/omw-th/1-v", kind="uri"),
            "lemma": binding("ขัน", language="th"),
            "pos": binding("https://lexinfo.net/ontology/3.0/lexinfo#verb", kind="uri"),
            "senseEdition": binding("Thai Wordnet 2.0"),
            "senseEditionUri": binding("https://w3id.org/thailex/edition/omw-th/2.0", kind="uri"),
            "senseSourceUrl": binding("https://github.com/omwn/omw-data", kind="uri"),
            "kind": binding("synset-definition"),
            "text": binding("tighten by screwing motions", language="en"),
            "evidenceUri": binding("https://w3id.org/thailex/evidence/omw-en/1", kind="uri"),
            "evidenceGraph": binding("https://w3id.org/thailex/graph/omw-en", kind="uri"),
            "evidenceEdition": binding("English Wordnet 2.0"),
            "evidenceEditionUri": binding("https://w3id.org/thailex/edition/omw-en/2.0", kind="uri"),
            "evidenceSourceUrl": binding("https://github.com/omwn/omw-data", kind="uri"),
            "evidenceLicense": binding("https://wordnet.princeton.edu/license-and-commercial-use", kind="uri"),
        }]

        candidate = repository._map_candidates(rows)[0]
        evidence = candidate.evidence[0]
        self.assertEqual(candidate.pos, "verb")
        self.assertEqual(candidate.sense_source, "thai-wordnet")
        self.assertEqual(evidence.sense_source, "thai-wordnet")
        self.assertEqual(evidence.evidence_source, "omw-en")
        self.assertEqual(evidence.evidence_language, "en")
        self.assertEqual(evidence.edition, "English Wordnet 2.0")


class SenseDetailsMappingTests(unittest.IsolatedAsyncioTestCase):
    async def test_deduplicates_display_values_from_multiple_source_resources(self) -> None:
        class Templates:
            def render(self, *_args, **_kwargs):
                return "query"

        class Client:
            async def select(self, _query):
                graph = binding("https://w3id.org/thailex/graph/th-wiktionary", kind="uri")
                return [
                    {"kind": binding("pronunciation"), "value": binding("/kʰan/"), "resource": binding("urn:form:1", kind="uri"), "sourceGraph": graph},
                    {"kind": binding("pronunciation"), "value": binding("/kʰan/"), "resource": binding("urn:form:2", kind="uri"), "sourceGraph": graph},
                    {"kind": binding("form"), "value": binding("การขัน", language="th"), "resource": binding("urn:form:3", kind="uri"), "tag": binding("abstract-noun"), "sourceGraph": graph},
                    {"kind": binding("form"), "value": binding("การขัน", language="th"), "resource": binding("urn:form:4", kind="uri"), "tag": binding("noun-form"), "sourceGraph": graph},
                    {"kind": binding("example"), "value": binding("ขันเกลียว", language="th"), "resource": binding("urn:example:1", kind="uri"), "sourceGraph": graph},
                    {"kind": binding("example"), "value": binding("ขันเกลียว", language="th"), "resource": binding("urn:example:2", kind="uri"), "sourceGraph": graph},
                ]

        repository = object.__new__(GraphRepository)
        repository.templates = Templates()
        repository.client = Client()
        candidate = SenseCandidate(
            sense_uri="https://w3id.org/thailex/sense/test/khan",
            entry_uri="https://w3id.org/thailex/entry/test/khan",
            lemma="ขัน",
            source="th-wiktionary",
            source_graph="https://w3id.org/thailex/graph/th-wiktionary",
        )

        details = await repository.get_sense_details(
            candidate, semantic_graph=GraphResult(hops=2)
        )

        self.assertEqual([item.value for item in details.pronunciations], ["/kʰan/"])
        self.assertEqual([item.value for item in details.forms], ["การขัน"])
        self.assertEqual(details.forms[0].tags, ["abstract-noun", "noun-form"])
        self.assertEqual([item.text for item in details.examples], ["ขันเกลียว"])


if __name__ == "__main__":
    unittest.main()
