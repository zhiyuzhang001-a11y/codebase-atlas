from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

from codebase_atlas.providers import TypeScriptTestProvider


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("ATLAS_NODE"), "ATLAS_NODE is required for TypeScript provider tests")
class TypeScriptTestProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = TypeScriptTestProvider(
            Path(os.environ["ATLAS_NODE"]), ROOT / "scripts/ts_test_analyzer.mjs"
        )

    def test_matches_exact_callbacks_and_rejects_same_name_decoy(self) -> None:
        expected = json.loads(
            (ROOT / "cases/ts-test-provider.v1.json").read_text(encoding="utf-8")
        )
        results = self.provider.related_tests(
            ROOT / "fixtures/ts-tests",
            "parseSize",
            target_path="src/size.ts",
        )
        actual = [
            {
                "path": node.location.path,
                "symbol": node.name,
                "start_line": node.location.start_line,
            }
            for node, _edge in results
        ]
        self.assertEqual(actual, expected["expected"])
        self.assertTrue(all(edge.resolution == "exact" for _node, edge in results))

    def test_requires_disambiguation_for_same_name_declarations(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "expected one declaration"):
            self.provider.related_tests(ROOT / "fixtures/ts-tests", "parseSize")

    def test_selected_production_tsconfig_still_includes_test_evidence(self) -> None:
        provider = TypeScriptTestProvider(
            Path(os.environ["ATLAS_NODE"]),
            ROOT / "scripts/ts_test_analyzer.mjs",
            Path("tsconfig.production.json"),
        )
        results = provider.related_tests(
            ROOT / "fixtures/ts-tests",
            "parseSize",
            target_path="src/size.ts",
        )
        self.assertEqual([node.name for node, _edge in results], [
            "parses a number",
            "handles whitespace",
        ])

    def test_owner_disambiguates_same_file_members(self) -> None:
        results = self.provider.related_tests(
            ROOT / "fixtures/ts-tests",
            "run",
            target_path="src/members.ts",
            target_owner="PrimaryWorker",
        )
        self.assertEqual([node.name for node, _edge in results], [
            "runs the primary worker",
        ])

    def test_owner_returns_exact_same_file_member_references(self) -> None:
        results = self.provider.references(
            ROOT / "fixtures/ts-tests",
            "run",
            target_path="src/members.ts",
            target_owner="PrimaryWorker",
        )
        self.assertEqual(
            [(node.location.path, node.location.start_line) for node in results],
            [("tests/members.test.ts", 4)],
        )


if __name__ == "__main__":
    unittest.main()
