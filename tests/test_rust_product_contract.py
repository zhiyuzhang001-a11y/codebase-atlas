"""Frozen acceptance inputs, not proof that the product gates have passed."""
from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "cases/rust-product-enablement.v1.json"
FIXTURES = ROOT / "tests/fixtures/rust-product"


class RustProductContractTests(unittest.TestCase):
    def test_positions_identify_frozen_symbols(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        for query in contract["fixture_queries"]:
            with self.subTest(query=query["id"]):
                base = FIXTURES / query["fixture"]
                if "source_path" in query:
                    line = (base / query["source_path"]).read_text(
                        encoding="utf-8"
                    ).splitlines()[query["source_line"] - 1]
                    start = query["source_column"] - 1
                    self.assertEqual(line[start:start + len(query["symbol"])], query["symbol"])
                if "target_range" in query:
                    span = query["target_range"]
                    line = (base / query["target_path"]).read_text(
                        encoding="utf-8"
                    ).splitlines()[span["start_line"] - 1]
                    self.assertEqual(
                        line[span["start_column"] - 1:span["end_column"] - 1], query["symbol"]
                    )
                for path in query.get("expected_paths", query.get("required_paths", [])):
                    self.assertTrue((base / path).is_file())
                if "expected_path" in query:
                    line = (base / query["expected_path"]).read_text(
                        encoding="utf-8"
                    ).splitlines()[query["expected_line"] - 1]
                    self.assertIn("fn " + query["symbol"], line)

    def test_budget_and_platform_contract_does_not_weaken_stage1(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        parent = json.loads((ROOT / "cases" / contract["parent_contract"]).read_text())
        budget = contract["budgets"]
        self.assertEqual(len(contract["platforms"]), 5)
        self.assertNotIn("macos-x64", contract["platforms"])
        self.assertFalse(contract["public_enablement_authorized"])
        for tier in ("t1", "t2"):
            self.assertEqual(budget[tier + "_process_tree_peak_rss_kib_max"],
                             parent["safety"][tier + "_peak_rss_kib_max"])
        self.assertEqual(budget["query_default_ms"], 30000)
        self.assertEqual(budget["query_max_ms"], 300000)
        self.assertEqual(budget["cold_query_end_to_end_ms"], 60000)
        self.assertGreaterEqual(budget["fresh_process_repeats_per_platform"], 3)


if __name__ == "__main__":
    unittest.main()
