from __future__ import annotations

from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest

from codebase_atlas.providers.serena import SerenaSemanticProvider, normalize_serena_rows


class SerenaNormalizationTests(unittest.TestCase):
    def test_child_exit_error_includes_bounded_private_stderr_tail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stderr_path = Path(directory) / "runner-private.stderr.log"
            stderr_path.write_text("x" * 5000 + "\nroot cause\n", encoding="utf-8")
            provider = object.__new__(SerenaSemanticProvider)
            provider.timeout_seconds = 1.0
            provider._stderr_path = stderr_path
            provider._process = SimpleNamespace(poll=lambda: 1)
            provider._responses = queue.Queue()
            provider._responses.put(("eof", None))

            with self.assertRaises(RuntimeError) as raised:
                provider._read()

            self.assertIn("exit=1", str(raised.exception))
            self.assertIn("stderr_tail=", str(raised.exception))
            self.assertIn("root cause", str(raised.exception))
            self.assertLess(len(str(raised.exception)), 4200)

    def test_response_queue_timeout_is_cross_platform(self) -> None:
        provider = object.__new__(SerenaSemanticProvider)
        provider.timeout_seconds = 0.001
        provider._process = SimpleNamespace(poll=lambda: None)
        provider._responses = queue.Queue()
        with self.assertRaisesRegex(TimeoutError, "exceeded"):
            provider._read()

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

    def test_recovers_repository_relative_path_from_textual_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            (repository / "baseline.test.ts").write_text("test", encoding="utf-8")
            nodes = normalize_serena_rows(
                [{
                    "path": "../repository/baseline.test.ts",
                    "start_line": 1,
                    "provider_id": "baseline",
                }],
                query_type="references",
                symbol="baseline",
                repository=repository,
            )
        self.assertEqual(nodes[0].location.path, "baseline.test.ts")

    def test_rejects_resolved_path_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            (root / "outside.ts").write_text("outside", encoding="utf-8")
            with self.assertRaises(ValueError):
                normalize_serena_rows(
                    [{
                        "path": "../outside.ts",
                        "start_line": 1,
                        "provider_id": "outside",
                    }],
                    query_type="references",
                    symbol="outside",
                    repository=repository,
                )


if __name__ == "__main__":
    unittest.main()
