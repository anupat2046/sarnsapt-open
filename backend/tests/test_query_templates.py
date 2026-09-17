from __future__ import annotations

import unittest

from thailex_api.query_templates import sparql_int, sparql_iri, sparql_literal


class QueryValueTests(unittest.TestCase):
    def test_literal_keeps_query_syntax_inside_string(self) -> None:
        encoded = sparql_literal('ขัน" ) } UNION { ?s ?p ?o } #')
        self.assertTrue(encoded.startswith('"'))
        self.assertIn('\\"', encoded)
        self.assertEqual(encoded.count("UNION"), 1)

    def test_iri_rejects_sparql_breakout(self) -> None:
        with self.assertRaises(ValueError):
            sparql_iri("https://example.test/sense> } UNION { ?s ?p ?o }")

    def test_integer_is_bounded(self) -> None:
        self.assertEqual(sparql_int(20), "20")
        with self.assertRaises(ValueError):
            sparql_int(501)


if __name__ == "__main__":
    unittest.main()
