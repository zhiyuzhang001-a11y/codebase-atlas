from __future__ import annotations

import unittest

from codebase_atlas.providers.serena import normalize_serena_rows


class SerenaNormalizationTests(unittest.TestCase):
    def test_normalizes_exact_reference_occurrence(self) -> None:
        nodes = normalize_serena_rows(
            [
                {
                    "path": "src/x.py",
                    "symbol": "VALUE",
                    "start_line": 7,
                    "end_line": 7,
                    "start_column": 9,
                    "end_column": 14,
                    "provider_id": "VALUE",
                    "provenance": {"operation": "find_referencing_symbols"},
                }
            ],
            query_type="references",
            symbol="VALUE",
        )
        self.assertEqual((nodes[0].location.path, nodes[0].location.start_line), ("src/x.py", 7))
        self.assertEqual(nodes[0].kind, "reference")
        self.assertEqual((nodes[0].location.start_column, nodes[0].location.end_column), (9, 14))
        self.assertTrue(nodes[0].id.endswith(":7:9"))
        self.assertEqual(nodes[0].attributes["operation"], "find_referencing_symbols")


if __name__ == "__main__":
    unittest.main()
