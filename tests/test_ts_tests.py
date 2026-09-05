from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from codebase_atlas.providers import TypeScriptTestProvider


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("ATLAS_NODE"), "ATLAS_NODE is required for TypeScript provider tests")
class TypeScriptPathNormalizationTests(unittest.TestCase):
    def run_normalizer(self, repository: Path, filename: Path) -> subprocess.CompletedProcess[str]:
        module = (ROOT / "scripts/ts_test_analyzer.mjs").as_uri()
        expression = (
            f"import {{ normalizedRelativePath }} from {json.dumps(module)};"
            f"console.log(normalizedRelativePath("
            f"{json.dumps(str(repository))}, {json.dumps(str(filename))}));"
        )
        return subprocess.run(
            [os.environ["ATLAS_NODE"], "--input-type=module", "--eval", expression],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_resolves_equivalent_directory_alias_before_relativizing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            source = repository / "baseline.test.ts"
            source.write_text("test", encoding="utf-8")
            alias = root / "alias"
            try:
                alias.symlink_to(repository, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory aliases are unavailable: {exc}")
            completed = self.run_normalizer(repository, alias / source.name)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "baseline.test.ts")

    def test_rejects_real_source_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            outside = root / "outside.ts"
            outside.write_text("outside", encoding="utf-8")
            completed = self.run_normalizer(repository, outside)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("TypeScript source escapes repository", completed.stderr)


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

    def test_returns_expression_assigned_arrow_caller_by_symbol_identity(self) -> None:
        results = self.provider.callers(
            ROOT / "fixtures/ts-tests",
            "target",
            target_path="src/relations.ts",
        )
        self.assertEqual([node.name for node, _edge in results], ["arrowCaller"])
        self.assertTrue(all(edge.resolution == "exact" for _node, edge in results))

    def test_returns_resolved_direct_callees(self) -> None:
        results = self.provider.callees(
            ROOT / "fixtures/ts-tests",
            "root",
            target_path="src/relations.ts",
        )
        self.assertEqual([node.name for node, _edge in results], ["first", "second"])

    def test_callees_prefer_overload_implementation_body(self) -> None:
        results = self.provider.callees(
            ROOT / "fixtures/ts-tests",
            "overloaded",
            target_path="src/relations.ts",
        )
        self.assertIn("first", [node.name for node, _edge in results])

    def test_returns_outer_suite_via_resolved_external_helper(self) -> None:
        results = self.provider.related_tests(
            ROOT / "fixtures/ts-tests",
            "root",
            target_path="src/relations.ts",
        )
        self.assertEqual([node.name for node, _edge in results], ["root helper suite"])
        self.assertEqual(results[0][0].location.start_line, 5)


if __name__ == "__main__":
    unittest.main()
