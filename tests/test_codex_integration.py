from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from codebase_atlas.codex_integration import codex_apply, codex_plan, codex_remove


class FakeRunner:
    def __init__(self, existing=None) -> None:
        self.existing = existing
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        if argv[1:3] == ["mcp", "get"]:
            if self.existing is None:
                return subprocess.CompletedProcess(
                    argv, 1, "", "Error: No MCP server named 'codebase_atlas' found."
                )
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.existing), "")
        if argv[1:3] == ["mcp", "add"]:
            separator = argv.index("--")
            self.existing = {
                "transport": {
                    "type": "stdio",
                    "command": argv[separator + 1],
                    "args": argv[separator + 2:],
                }
            }
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:3] == ["mcp", "remove"]:
            self.existing = None
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)


class CodexIntegrationTests(unittest.TestCase):
    def paths(self, root: Path):
        config = root / ".codebase-atlas.toml"
        config.write_text("schema_version = 1\n")
        codex = root / "codex"
        codex.write_text("")
        atlas = root / "codebase-atlas"
        atlas.write_text("")
        return config, codex, atlas

    def test_plan_is_read_only_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, codex, atlas = self.paths(Path(raw))
            runner = FakeRunner()
            plan = codex_plan(
                config, codex_binary=codex, atlas_executable=atlas, runner=runner
            )
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["existing"], "absent")
        self.assertFalse(plan["mutates"])
        self.assertEqual(plan["transport"]["args"][-2:], ["--config", str(config.resolve())])
        self.assertTrue(plan["current_session_refresh_required"])

    def test_apply_verifies_and_remove_only_deletes_matching_entry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, codex, atlas = self.paths(Path(raw))
            runner = FakeRunner()
            applied = codex_apply(
                config, codex_binary=codex, atlas_executable=atlas, runner=runner
            )
            removed = codex_remove(
                config, codex_binary=codex, atlas_executable=atlas, runner=runner
            )
        self.assertEqual(applied["status"], "ready")
        self.assertTrue(applied["mutates"])
        self.assertEqual(removed["status"], "absent")
        self.assertTrue(removed["mutates"])

    def test_conflict_is_never_overwritten_or_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, codex, atlas = self.paths(Path(raw))
            runner = FakeRunner({
                "transport": {"type": "stdio", "command": "/other", "args": []}
            })
            plan = codex_plan(
                config, codex_binary=codex, atlas_executable=atlas, runner=runner
            )
            self.assertEqual(plan["status"], "blocked")
            with self.assertRaisesRegex(RuntimeError, "overwrite"):
                codex_apply(
                    config, codex_binary=codex, atlas_executable=atlas, runner=runner
                )
            with self.assertRaisesRegex(RuntimeError, "remove"):
                codex_remove(
                    config, codex_binary=codex, atlas_executable=atlas, runner=runner
                )


if __name__ == "__main__":
    unittest.main()
