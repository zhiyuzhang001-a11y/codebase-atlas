from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from codebase_atlas.codex_integration import (
    PROJECT_RULE,
    codex_apply,
    codex_plan,
    codex_remove,
)


class FakeRunner:
    def __init__(self, existing=None, fail_adds=0) -> None:
        self.existing = existing
        self.fail_adds = fail_adds
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
            if self.fail_adds:
                self.fail_adds -= 1
                return subprocess.CompletedProcess(argv, 1, "", "simulated add failure")
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
    def test_project_rule_requires_on_demand_refresh_without_user_prompt(self) -> None:
        self.assertIn("on-query", PROJECT_RULE)
        self.assertIn("creating, modifying, renaming, or deleting", PROJECT_RULE)
        self.assertIn("automatically", PROJECT_RULE)

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

    def test_fallback_preserves_virtualenv_interpreter_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config, codex, _atlas = self.paths(root)
            target = root / "python-real"
            target.write_text("")
            virtualenv_python = root / "venv-python"
            virtualenv_python.symlink_to(target)

            def executable(candidate):
                return str(codex) if candidate == str(codex) else None

            with (
                patch(
                    "codebase_atlas.codex_integration.shutil.which",
                    side_effect=executable,
                ),
                patch(
                    "codebase_atlas.codex_integration.sys.executable",
                    str(virtualenv_python),
                ),
            ):
                plan = codex_plan(config, codex_binary=codex, runner=FakeRunner())
        self.assertEqual(plan["transport"]["command"], str(virtualenv_python.absolute()))

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

    def project_paths(self, root: Path):
        repository = root / "项目 with spaces"
        repository.mkdir()
        config = repository / ".codebase-atlas.toml"
        config.write_text(
            "schema_version = 1\n\n[project]\n"
            f'repository = {json.dumps(str(repository))}\n'
            'language = "python"\n'
            f'data_dir = {json.dumps(str(root / "data"))}\n'
            'cbm_project = "project"\n'
            'tsconfig = ""\n\n[runtime]\n'
            f'node = {json.dumps(str(root / "node"))}\n'
            f'node_bin_dir = {json.dumps(str(root))}\n'
            f'cbm_binary = {json.dumps(str(root / "cbm"))}\n'
            f'serena_python = {json.dumps(str(root / "python"))}\n',
            encoding="utf-8",
        )
        atlas = root / "atlas executable"
        atlas.write_text("")
        return repository, config, atlas

    def test_project_scope_plan_apply_remove_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository, config, atlas = self.project_paths(Path(raw))
            plan = codex_plan(config, scope="project", atlas_executable=atlas)
            self.assertEqual(plan["existing"], "absent")
            self.assertFalse(plan["global_config_mutation"])
            applied = codex_apply(config, scope="project", atlas_executable=atlas)
            target = repository / ".codex/config.toml"
            parsed = tomllib.loads(target.read_text(encoding="utf-8"))
            entry = parsed["mcp_servers"]["codebase_atlas"]
            self.assertEqual(entry["command"], str(atlas.resolve()))
            self.assertEqual(entry["args"][:3], [
                "mcp-auto", "--root", str(repository.resolve())
            ])
            self.assertNotIn("--config", entry["args"])
            self.assertIn("on-query", entry["args"])
            self.assertEqual(applied["existing"], "matching")
            repeated = codex_apply(config, scope="project", atlas_executable=atlas)
            self.assertFalse(repeated["mutates"])
            removed = codex_remove(config, scope="project", atlas_executable=atlas)
            self.assertTrue(removed["mutates"])
            self.assertFalse(target.exists())

    def test_project_scope_preserves_foreign_config_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository, config, atlas = self.project_paths(Path(raw))
            target = repository / ".codex/config.toml"
            target.parent.mkdir()
            original = 'model = "gpt-test"\n[features]\nexample = true\n'
            target.write_text(original, encoding="utf-8")
            codex_apply(config, scope="project", atlas_executable=atlas)
            self.assertTrue(target.read_text(encoding="utf-8").startswith(original))
            codex_remove(config, scope="project", atlas_executable=atlas)
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_project_scope_updates_only_owned_managed_block(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository, config, old_atlas = self.project_paths(root)
            new_atlas = root / "new atlas"
            new_atlas.write_text("")
            codex_apply(config, scope="project", atlas_executable=old_atlas)
            target = repository / ".codex/config.toml"
            original = target.read_text(encoding="utf-8")
            target.write_text('model = "preserved"\n' + original, encoding="utf-8")
            plan = codex_plan(config, scope="project", atlas_executable=new_atlas)
            self.assertEqual(plan["existing"], "managed_different")
            applied = codex_apply(
                config, scope="project", atlas_executable=new_atlas
            )
            updated = target.read_text(encoding="utf-8")
            self.assertTrue(applied["mutates"])
            self.assertTrue(updated.startswith('model = "preserved"\n'))
            self.assertIn(str(new_atlas.resolve()), updated)
            self.assertNotIn(str(old_atlas.resolve()), updated)

    def test_project_scope_refuses_foreign_or_invalid_atlas_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository, config, atlas = self.project_paths(Path(raw))
            target = repository / ".codex/config.toml"
            target.parent.mkdir()
            foreign = (
                '[mcp_servers.codebase_atlas]\ncommand = "/other"\nargs = []\n'
            )
            target.write_text(foreign, encoding="utf-8")
            plan = codex_plan(config, scope="project", atlas_executable=atlas)
            self.assertEqual(plan["status"], "blocked")
            with self.assertRaisesRegex(RuntimeError, "overwrite"):
                codex_apply(config, scope="project", atlas_executable=atlas)
            self.assertEqual(target.read_text(encoding="utf-8"), foreign)
            target.write_text("not = [valid", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid TOML"):
                codex_plan(config, scope="project", atlas_executable=atlas)

    def test_project_scope_refuses_symlinks_and_outside_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository, config, atlas = self.project_paths(root)
            foreign_dir = root / "foreign-codex"
            foreign_dir.mkdir()
            (repository / ".codex").symlink_to(foreign_dir)
            with self.assertRaisesRegex(RuntimeError, "real directory"):
                codex_plan(config, scope="project", atlas_executable=atlas)
            (repository / ".codex").unlink()
            outside = root / ".codebase-atlas.toml"
            outside.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
            plan = codex_plan(outside, scope="project", atlas_executable=atlas)
            self.assertEqual(plan["repository"], str(repository.resolve()))

    def test_project_scope_can_target_an_explicit_ancestor_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            repository, config, atlas = self.project_paths(workspace)
            plan = codex_plan(
                config, scope="project", atlas_executable=atlas,
                codex_project_root=workspace,
            )
            self.assertEqual(plan["codex_project_root"], str(workspace.resolve()))
            self.assertEqual(
                plan["target"], str((workspace / ".codex/config.toml").resolve())
            )
            codex_apply(
                config, scope="project", atlas_executable=atlas,
                codex_project_root=workspace,
            )
            self.assertTrue((workspace / ".codex/config.toml").is_file())
            codex_remove(
                config, scope="project", atlas_executable=atlas,
                codex_project_root=workspace,
            )
            self.assertFalse((workspace / ".codex/config.toml").exists())

    def test_project_scope_refuses_non_ancestor_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _repository, config, atlas = self.project_paths(root)
            foreign = root / "foreign"
            foreign.mkdir()
            with self.assertRaisesRegex(RuntimeError, "ancestor"):
                codex_plan(
                    config, scope="project", atlas_executable=atlas,
                    codex_project_root=foreign,
                )

    def legacy_entry(self, config: Path, command: Path):
        return {
            "transport": {
                "type": "stdio",
                "command": str(command),
                "args": [
                    "-m", "codebase_atlas.cli", "mcp", "--config", str(config)
                ],
                "env": None,
            }
        }

    def test_global_auto_plan_and_legacy_migration(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _repository, config, atlas = self.project_paths(root)
            codex = root / "codex"
            codex.write_text("")
            old_python = root / "old-python"
            old_python.write_text("")
            runner = FakeRunner(self.legacy_entry(config, old_python))
            plan = codex_plan(
                config, scope="global-auto", codex_binary=codex,
                atlas_executable=atlas, runner=runner,
            )
            self.assertEqual(plan["existing"], "legacy_fixed_atlas")
            self.assertNotIn("--config", plan["transport"]["args"])
            applied = codex_apply(
                config, scope="global-auto", codex_binary=codex,
                atlas_executable=atlas, runner=runner,
            )
            self.assertEqual(applied["existing"], "matching")
            self.assertEqual(runner.existing["transport"]["args"][0], "mcp-auto")

    def test_global_auto_rolls_back_exact_legacy_after_add_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _repository, config, atlas = self.project_paths(root)
            codex = root / "codex"
            codex.write_text("")
            old_python = root / "old-python"
            old_python.write_text("")
            legacy = self.legacy_entry(config, old_python)
            runner = FakeRunner(legacy, fail_adds=1)
            with self.assertRaisesRegex(RuntimeError, "exact legacy transport restored"):
                codex_apply(
                    config, scope="global-auto", codex_binary=codex,
                    atlas_executable=atlas, runner=runner,
                )
            self.assertEqual(
                runner.existing["transport"]["command"],
                legacy["transport"]["command"],
            )
            self.assertEqual(
                runner.existing["transport"]["args"], legacy["transport"]["args"]
            )

    def test_global_auto_refuses_foreign_entry_and_legacy_remove(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _repository, config, atlas = self.project_paths(root)
            codex = root / "codex"
            codex.write_text("")
            foreign = FakeRunner({
                "transport": {"type": "stdio", "command": "/other", "args": []}
            })
            plan = codex_plan(
                config, scope="global-auto", codex_binary=codex,
                atlas_executable=atlas, runner=foreign,
            )
            self.assertEqual(plan["status"], "blocked")
            legacy = FakeRunner(self.legacy_entry(config, root / "old-python"))
            with self.assertRaisesRegex(RuntimeError, "apply migration first"):
                codex_remove(
                    config, scope="global-auto", codex_binary=codex,
                    atlas_executable=atlas, runner=legacy,
                )

    def test_global_auto_matching_remove_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _repository, config, atlas = self.project_paths(root)
            codex = root / "codex"
            codex.write_text("")
            runner = FakeRunner()
            codex_apply(
                config, scope="global-auto", codex_binary=codex,
                atlas_executable=atlas, runner=runner,
            )
            removed = codex_remove(
                config, scope="global-auto", codex_binary=codex,
                atlas_executable=atlas, runner=runner,
            )
            self.assertEqual(removed["existing"], "absent")


if __name__ == "__main__":
    unittest.main()
