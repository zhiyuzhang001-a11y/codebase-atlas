from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from threading import Barrier, Thread
from types import SimpleNamespace
from unittest.mock import patch

from codebase_atlas.cli import main
from codebase_atlas.config import AtlasConfig
from codebase_atlas.onboarding import OnboardingInputs, _shell_command, apply_plan, build_plan


class OnboardingTests(unittest.TestCase):
    def ready_checks(self) -> list[dict[str, object]]:
        return [{"name": "runtime", "ok": True, "required": True, "path": "", "version": "", "detail": "ready", "remediation": ""}]

    def planned(self, root: Path, *, language: str = "python") -> tuple[dict[str, object], AtlasConfig]:
        repo = root / "repo"
        repo.mkdir()
        config = AtlasConfig(repo, language, root / "node", root / "cbm", root / "serena", root / "data")
        with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
            plan, discovered = build_plan(OnboardingInputs(repo, repo / ".codebase-atlas.toml", language, config.node, config.cbm_binary, config.serena_python, None, None, config.data_dir, "fast"))
        self.assertIsNotNone(discovered)
        return plan, discovered  # type: ignore[return-value]

    def test_missing_runtime_blocks_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["onboard", "--repo", str(repo)]), 2)
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["mode"], "read_only")
            self.assertEqual(result["apply_argv"], [])
            self.assertEqual(result["apply_command"], "")
            self.assertEqual(result["command_shell"], "powershell" if os.name == "nt" else "posix")
            self.assertFalse((repo / ".codebase-atlas.toml").exists())

    def test_ready_plan_is_read_only_and_has_stable_actions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            config_path = repo / ".codebase-atlas.toml"
            config = AtlasConfig(repo, "python", root / "node", root / "cbm", root / "serena", root / "data")
            checks = self.ready_checks()
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=checks):
                plan, discovered = build_plan(OnboardingInputs(repo, config_path, "python", config.node, config.cbm_binary, config.serena_python, None, None, config.data_dir, "fast"))
            self.assertEqual(plan["status"], "planned")
            self.assertEqual([item["id"] for item in plan["actions"]], ["check_runtime", "create_config", "index", "doctor"])
            self.assertIsNotNone(discovered)
            self.assertFalse(config_path.exists())
            self.assertFalse(config.data_dir.exists())
            apply_argv = plan["apply_argv"]
            self.assertIsInstance(apply_argv, list)
            self.assertEqual(apply_argv[:7], ["codebase-atlas", "onboard", "--apply", "--repo", str(repo.resolve()), "--config", str(config_path.resolve())])
            self.assertIn("--node", apply_argv)
            self.assertIn(str(config.node), apply_argv)
            self.assertIn("--data-dir", apply_argv)
            self.assertIn(str(config.data_dir), apply_argv)
            if os.name != "nt":
                self.assertEqual(shlex.split(str(plan["apply_command"])), apply_argv)

    def test_windows_powershell_command_quotes_every_argument(self) -> None:
        values = ["codebase-atlas", "--config", r"C:\repo & tools\O'Brien\atlas.toml", "a|b", "(x)", "^", "%!", "$HOME", "tail\\"]
        with patch("codebase_atlas.onboarding.os.name", "nt"):
            command = _shell_command(values)
        self.assertEqual(command, "& 'codebase-atlas' '--config' 'C:\\repo & tools\\O''Brien\\atlas.toml' 'a|b' '(x)' '^' '%!' '$HOME' 'tail\\'")

    def test_windows_powershell_command_replays_metacharacters(self) -> None:
        if os.name != "nt":
            return
        payload = ["with space", "a&b", "a|b", "(group)", "caret^", "percent%bang!", "$HOME", "O'Brien", "tail\\"]
        values = [sys.executable, "-c", "import json, sys; print(json.dumps(sys.argv[1:]))", *payload]
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", _shell_command(values)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), payload)

    def test_symlinked_config_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            target = root / "target.toml"
            target.write_text("x", encoding="utf-8")
            config = repo / ".codebase-atlas.toml"
            config.symlink_to(target)
            plan, _ = build_plan(OnboardingInputs(repo, config, "python", None, None, None, None, None, None, "fast"))
            self.assertEqual(plan["status"], "blocked")
            self.assertIn("symlink", str(plan["error"]))

    def test_ancestor_symlink_and_data_symlink_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            outside = root / "outside"
            repo.mkdir()
            (outside / "nested").mkdir(parents=True)
            (repo / "linked").symlink_to(outside, target_is_directory=True)
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                plan, _ = build_plan(OnboardingInputs(repo, repo / "linked" / "nested" / "atlas.toml", "python", root / "node", root / "cbm", root / "serena", None, None, root / "data", "fast"))
            self.assertEqual(plan["status"], "blocked")
            self.assertIn("symlink", str(plan["error"]))
            (repo / "parent-linked").symlink_to(root, target_is_directory=True)
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                parent_plan, _ = build_plan(OnboardingInputs(repo, repo / "parent-linked" / "escaped.toml", "python", root / "node", root / "cbm", root / "serena", None, None, root / "data", "fast"))
            self.assertEqual(parent_plan["status"], "blocked")
            self.assertIn("symlink", str(parent_plan["error"]))
            (repo / "parent-data-linked").symlink_to(root, target_is_directory=True)
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                parent_data_plan, _ = build_plan(OnboardingInputs(repo, repo / "atlas.toml", "python", root / "node", root / "cbm", root / "serena", None, None, repo / "parent-data-linked" / "atlas-data", "fast"))
            self.assertEqual(parent_data_plan["status"], "blocked")
            self.assertIn("symlink", str(parent_data_plan["error"]))
            (repo / "data-linked").symlink_to(outside, target_is_directory=True)
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                data_plan, _ = build_plan(OnboardingInputs(repo, repo / "atlas.toml", "python", root / "node", root / "cbm", root / "serena", None, None, repo / "data-linked", "fast"))
            self.assertEqual(data_plan["status"], "blocked")
            self.assertIn("symlink", str(data_plan["error"]))
            external = root / "external"
            (external / "target").mkdir(parents=True)
            (external / "linked").symlink_to(external / "target", target_is_directory=True)
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                external_data_plan, _ = build_plan(OnboardingInputs(repo, repo / "atlas.toml", "python", root / "node", root / "cbm", root / "serena", None, None, external / "linked" / "atlas-data", "fast"))
            self.assertEqual(external_data_plan["status"], "blocked")
            self.assertIn("symlink", str(external_data_plan["error"]))

    def test_existing_invalid_config_is_preserved_and_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            config = repo / ".codebase-atlas.toml"
            original = "this is not a supported Atlas config\n"
            config.write_text(original, encoding="utf-8")
            plan, discovered = build_plan(OnboardingInputs(repo, config, "python", None, None, None, None, None, None, "fast"))
            self.assertEqual(plan["status"], "blocked")
            self.assertIsNone(discovered)
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_differing_usable_config_is_preserved_and_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original_repo = root / "original"
            requested_repo = root / "requested"
            original_repo.mkdir()
            requested_repo.mkdir()
            config_path = requested_repo / ".codebase-atlas.toml"
            existing = AtlasConfig(original_repo, "python", root / "node", root / "cbm", root / "serena", root / "data")
            existing.write(config_path)
            original = config_path.read_text(encoding="utf-8")
            plan, discovered = build_plan(OnboardingInputs(requested_repo, config_path, "python", None, None, None, None, None, None, "fast"))
            self.assertEqual(plan["status"], "blocked")
            self.assertIsNone(discovered)
            self.assertIn("differs for repository", str(plan["error"]))
            self.assertEqual(config_path.read_text(encoding="utf-8"), original)

    def test_apply_failure_keeps_config_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan, config = self.planned(root)
            config_path = Path(str(plan["config"]))
            with patch("codebase_atlas.onboarding.index_freshness", return_value={"status": "missing"}), patch("codebase_atlas.onboarding.provider_database_health", return_value={"ok": False}):
                result, exit_code = apply_plan(plan, config, indexer=lambda _config, _mode: (_ for _ in ()).throw(RuntimeError("index failed")), mode="fast")
            self.assertEqual(exit_code, 2)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(config_path.exists())
            self.assertEqual(result["resume"], plan["apply_command"])

    def test_interruption_after_config_creation_is_resumable_without_publishing_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            literal_config_path = root / "repo" / ".codebase-atlas.toml"
            plan, config = self.planned(root)
            config_path = Path(str(plan["config"]))
            self.assertEqual(config_path, literal_config_path.resolve())
            self.assertEqual(Path(str(plan["path_anchor"])), config.repository)
            state = config.data_dir / "index-state.json"
            barrier = Barrier(2)
            interrupted: list[BaseException] = []

            def interrupting_indexer(_config: AtlasConfig, _mode: str) -> dict[str, object]:
                barrier.wait(timeout=5)
                barrier.wait(timeout=5)
                raise KeyboardInterrupt()

            def run_interrupted_apply() -> None:
                try:
                    apply_plan(plan, config, indexer=interrupting_indexer, mode="fast")
                except BaseException as error:
                    interrupted.append(error)

            with patch("codebase_atlas.onboarding.index_freshness", return_value={"status": "missing"}), patch("codebase_atlas.onboarding.provider_database_health", return_value={"ok": False}):
                worker = Thread(target=run_interrupted_apply)
                worker.start()
                barrier.wait(timeout=5)
                self.assertTrue(config_path.is_file())
                self.assertFalse(state.exists())
                barrier.wait(timeout=5)
                worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertEqual(len(interrupted), 1)
            self.assertIsInstance(interrupted[0], KeyboardInterrupt)
            self.assertTrue(config_path.is_file())
            self.assertFalse(state.exists())

            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                resumed_plan, resumed_config = build_plan(OnboardingInputs(Path(str(plan["repository"])), Path(str(plan["config"])), None, None, None, None, None, None, None, "fast"))
            self.assertIsNotNone(resumed_config)
            with patch("codebase_atlas.onboarding.index_freshness", return_value={"status": "missing"}), patch("codebase_atlas.onboarding.provider_database_health", return_value={"ok": False}), patch("codebase_atlas.onboarding.diagnose", return_value=self.ready_checks()):
                result, exit_code = apply_plan(resumed_plan, resumed_config, indexer=lambda _config, _mode: {"project": "indexed"}, mode="fast")  # type: ignore[arg-type]
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["status"], "ready")
            self.assertTrue(state.is_file())

    def test_current_apply_does_not_start_provider_or_index_again(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            config_path = repo / ".codebase-atlas.toml"
            original = AtlasConfig(repo, "python", root / "node", root / "cbm", root / "serena", root / "data")
            original.write(config_path)
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                plan, config = build_plan(OnboardingInputs(repo, config_path, None, None, None, None, None, None, None, "fast"))
            self.assertIsNotNone(config)
            config = config  # type: ignore[assignment]
            with patch("codebase_atlas.onboarding.index_freshness", return_value={"status": "fresh", "mode": "fast"}), patch("codebase_atlas.onboarding.provider_database_health", return_value={"ok": True}):
                result, exit_code = apply_plan(plan, config, indexer=lambda _config, _mode: self.fail("indexer must not run"), mode="fast")
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["status"], "current")
            self.assertEqual(result["provider"], {"route": "atlas_source_current", "status": "not_started"})

    def test_apply_rejects_tampered_action_graph_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan, config = self.planned(root)
            plan["actions"] = []
            result, exit_code = apply_plan(plan, config, indexer=lambda _config, _mode: self.fail("indexer must not run"), mode="fast")
            self.assertEqual(exit_code, 2)
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(Path(str(plan["config"])).exists())

    def test_apply_preserves_config_replaced_during_index(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            config_path = repo / ".codebase-atlas.toml"
            original = AtlasConfig(repo, "python", root / "node", root / "cbm", root / "serena", root / "data")
            original.write(config_path)
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                plan, config = build_plan(OnboardingInputs(repo, config_path, None, None, None, None, None, None, None, "fast"))
            self.assertIsNotNone(config)
            config = config  # type: ignore[assignment]
            replacement = "user replacement during indexing\n"

            def indexer(_config: AtlasConfig, _mode: str) -> dict[str, object]:
                config_path.write_text(replacement, encoding="utf-8")
                return {"project": "indexed"}

            snapshot = SimpleNamespace(kind="plain", fingerprint="same")
            with patch("codebase_atlas.onboarding.index_freshness", return_value={"status": "missing"}), patch("codebase_atlas.onboarding.provider_database_health", return_value={"ok": False}), patch("codebase_atlas.onboarding.repository_snapshot", return_value=snapshot), patch("codebase_atlas.onboarding.record_index_state"), patch("codebase_atlas.onboarding.diagnose", return_value=self.ready_checks()):
                result, exit_code = apply_plan(plan, config, indexer=indexer, mode="fast")
            self.assertEqual(exit_code, 2)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(config_path.read_text(encoding="utf-8"), replacement)

    def test_apply_rejects_same_content_symlink_swap_during_index(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            config_path = repo / ".codebase-atlas.toml"
            original = AtlasConfig(repo, "python", root / "node", root / "cbm", root / "serena", root / "data")
            original.write(config_path)
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                plan, config = build_plan(OnboardingInputs(repo, config_path, None, None, None, None, None, None, None, "fast"))
            self.assertIsNotNone(config)
            outside = root / "outside.toml"
            original_text = config_path.read_text(encoding="utf-8")

            def indexer(_config: AtlasConfig, _mode: str) -> dict[str, object]:
                outside.write_text(original_text, encoding="utf-8")
                config_path.unlink()
                config_path.symlink_to(outside)
                return {"project": "indexed"}

            snapshot = SimpleNamespace(kind="plain", fingerprint="same")
            with patch("codebase_atlas.onboarding.index_freshness", return_value={"status": "missing"}), patch("codebase_atlas.onboarding.provider_database_health", return_value={"ok": False}), patch("codebase_atlas.onboarding.repository_snapshot", return_value=snapshot), patch("codebase_atlas.onboarding.record_index_state") as record_state, patch("codebase_atlas.onboarding.diagnose", return_value=self.ready_checks()):
                result, exit_code = apply_plan(plan, config, indexer=indexer, mode="fast")
            self.assertEqual(exit_code, 2)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(config_path.is_symlink())
            self.assertEqual(outside.read_text(encoding="utf-8"), original_text)
            record_state.assert_not_called()

    def test_publication_io_failure_preserves_config_and_does_not_record_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            config_path = repo / ".codebase-atlas.toml"
            original = AtlasConfig(repo, "python", root / "node", root / "cbm", root / "serena", root / "data")
            original.write(config_path)
            original_bytes = config_path.read_bytes()
            original_identity = (config_path.stat().st_dev, config_path.stat().st_ino)
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                plan, config = build_plan(OnboardingInputs(repo, config_path, None, None, None, None, None, None, None, "fast"))
            self.assertIsNotNone(config)
            snapshot = SimpleNamespace(kind="plain", fingerprint="same")
            with patch("codebase_atlas.onboarding.index_freshness", return_value={"status": "missing"}), patch("codebase_atlas.onboarding.provider_database_health", return_value={"ok": False}), patch("codebase_atlas.onboarding.repository_snapshot", return_value=snapshot), patch("codebase_atlas.onboarding.AtlasConfig.write_verified", side_effect=OSError("publication failed")), patch("codebase_atlas.onboarding.record_index_state") as record_state:
                result, exit_code = apply_plan(plan, config, indexer=lambda _config, _mode: {"project": "indexed"}, mode="fast")  # type: ignore[arg-type]

            self.assertEqual(exit_code, 2)
            self.assertEqual(result["status"], "failed")
            self.assertIn("publication failed", str(result["error"]))
            self.assertEqual(config_path.read_bytes(), original_bytes)
            self.assertEqual((config_path.stat().st_dev, config_path.stat().st_ino), original_identity)
            record_state.assert_not_called()

    def test_python_apply_reaches_ready_and_emits_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan, config = self.planned(root)
            snapshot = SimpleNamespace(kind="plain", fingerprint="same")
            with patch("codebase_atlas.onboarding.index_freshness", return_value={"status": "missing"}), patch("codebase_atlas.onboarding.provider_database_health", return_value={"ok": False}), patch("codebase_atlas.onboarding.repository_snapshot", return_value=snapshot), patch("codebase_atlas.onboarding.record_index_state"), patch("codebase_atlas.onboarding.diagnose", return_value=self.ready_checks()):
                result, exit_code = apply_plan(plan, config, indexer=lambda _config, _mode: {"project": "indexed"}, mode="fast")
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["status"], "ready")
            self.assertTrue(Path(str(plan["config"])).exists())
            self.assertIn("next_query", result["guidance"])
            self.assertIn("mcp", result["guidance"])
            self.assertIn("remove", result["guidance"])
            self.assertEqual(set(result["guidance"]), set(result["guidance_argv"]))
            for name, argv in result["guidance_argv"].items():
                self.assertEqual(result["guidance"][name], _shell_command(argv))

    def test_repository_local_custom_config_records_post_publication_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            config_path = repo / "atlas.toml"
            config = AtlasConfig(repo, "python", root / "node", root / "cbm", root / "serena", root / "data")
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                plan, discovered = build_plan(OnboardingInputs(repo, config_path, "python", config.node, config.cbm_binary, config.serena_python, None, None, config.data_dir, "fast"))
            self.assertIsNotNone(discovered)
            before = SimpleNamespace(kind="git", fingerprint="before")
            after_index = SimpleNamespace(kind="git", fingerprint="before")
            after_publication = SimpleNamespace(kind="git", fingerprint="before")
            with patch("codebase_atlas.onboarding.index_freshness", return_value={"status": "missing"}), patch("codebase_atlas.onboarding.provider_database_health", return_value={"ok": False}), patch("codebase_atlas.onboarding.repository_snapshot", side_effect=[before, after_index, after_publication]), patch("codebase_atlas.onboarding.record_index_state") as record_state, patch("codebase_atlas.onboarding.diagnose", return_value=self.ready_checks()):
                result, exit_code = apply_plan(plan, discovered, indexer=lambda _config, _mode: {"project": "indexed"}, mode="fast")
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(record_state.call_args.kwargs["snapshot"], after_publication)

            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                current_plan, current_config = build_plan(OnboardingInputs(repo, config_path, None, None, None, None, None, None, None, "fast"))
            self.assertIsNotNone(current_config)
            with patch("codebase_atlas.onboarding.index_freshness", return_value={"status": "fresh", "mode": "fast"}), patch("codebase_atlas.onboarding.provider_database_health", return_value={"ok": True}), patch("codebase_atlas.onboarding.diagnose", return_value=self.ready_checks()):
                current, current_exit = apply_plan(current_plan, current_config, indexer=lambda _config, _mode: self.fail("indexer must not run"), mode="fast")
            self.assertEqual(current_exit, 0)
            self.assertEqual(current["status"], "current")

    def test_late_source_change_is_not_recorded_as_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            config_path = repo / "atlas.toml"
            config = AtlasConfig(repo, "python", root / "node", root / "cbm", root / "serena", root / "data")
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=self.ready_checks()):
                plan, discovered = build_plan(OnboardingInputs(repo, config_path, "python", config.node, config.cbm_binary, config.serena_python, None, None, config.data_dir, "fast"))
            self.assertIsNotNone(discovered)
            indexed_source = SimpleNamespace(kind="git", fingerprint="indexed-source")
            late_source_change = SimpleNamespace(kind="git", fingerprint="unindexed-late-change")
            with patch("codebase_atlas.onboarding.index_freshness", return_value={"status": "missing"}), patch("codebase_atlas.onboarding.provider_database_health", return_value={"ok": False}), patch("codebase_atlas.onboarding.repository_snapshot", side_effect=[indexed_source, indexed_source, late_source_change]), patch("codebase_atlas.onboarding.record_index_state") as record_state, patch("codebase_atlas.onboarding.diagnose", return_value=self.ready_checks()):
                result, exit_code = apply_plan(plan, discovered, indexer=lambda _config, _mode: {"project": "indexed"}, mode="fast")
            self.assertEqual(exit_code, 2)
            self.assertEqual(result["status"], "failed")
            self.assertIn("repository changed while publishing", str(result["error"]))
            record_state.assert_not_called()

    def test_incomplete_doctor_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan, config = self.planned(root)
            snapshot = SimpleNamespace(kind="plain", fingerprint="same")
            incomplete = [{"name": "provider", "ok": False, "required": True, "path": "", "version": "", "detail": "not ready", "remediation": "repair"}]
            with patch("codebase_atlas.onboarding.index_freshness", return_value={"status": "missing"}), patch("codebase_atlas.onboarding.provider_database_health", return_value={"ok": False}), patch("codebase_atlas.onboarding.repository_snapshot", return_value=snapshot), patch("codebase_atlas.onboarding.record_index_state"), patch("codebase_atlas.onboarding.diagnose", return_value=incomplete):
                result, exit_code = apply_plan(plan, config, indexer=lambda _config, _mode: {"project": "indexed"}, mode="fast")
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(exit_code, 2)

    def test_typescript_prerequisite_failure_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            checks = [
                {"name": "tsconfig", "ok": False, "required": True, "path": "", "version": "", "detail": "missing", "remediation": "pass --tsconfig"},
                {"name": "typescript_semantic_runtime", "ok": False, "required": True, "path": "", "version": "", "detail": "missing", "remediation": "provide npm or language server"},
            ]
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=checks):
                plan, _ = build_plan(OnboardingInputs(repo, repo / ".codebase-atlas.toml", "typescript", root / "node", root / "cbm", root / "serena", None, None, root / "data", "fast"))
            self.assertEqual(plan["status"], "blocked")
            self.assertEqual(plan["apply_command"], "")
            self.assertEqual([check["name"] for check in plan["checks"]], ["tsconfig", "typescript_semantic_runtime"])

    def test_typescript_ready_plan_requires_the_checked_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            checks = [
                {"name": "tsconfig", "ok": True, "required": True, "path": "", "version": "", "detail": "ready", "remediation": ""},
                {"name": "typescript_semantic_runtime", "ok": True, "required": True, "path": "", "version": "", "detail": "npm ready", "remediation": ""},
            ]
            with patch("codebase_atlas.onboarding.runtime_checks", return_value=checks):
                plan, _ = build_plan(OnboardingInputs(repo, repo / ".codebase-atlas.toml", "typescript", root / "node", root / "cbm", root / "serena", root / "node-bin", Path("tsconfig.json"), root / "data", "fast"))
            self.assertEqual(plan["status"], "planned")
            apply_argv = plan["apply_argv"]
            tsconfig_index = apply_argv.index("--tsconfig")
            self.assertEqual(apply_argv[tsconfig_index + 1], "tsconfig.json")

    def test_existing_setup_remains_structured_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw) / "repo"
            repo.mkdir()
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["setup", "--repo", str(repo)]), 2)
            result = json.loads(output.getvalue())
            self.assertEqual(result["mode"], "read_only")
            self.assertIn("checks", result)
            self.assertFalse((repo / ".codebase-atlas.toml").exists())

    def test_existing_init_index_and_doctor_remain_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            config_path = repo / ".codebase-atlas.toml"
            config = AtlasConfig(repo, "python", root / "node", root / "cbm", root / "serena", root / "data")
            output = StringIO()
            with patch("codebase_atlas.cli.AtlasConfig.discover", return_value=config), redirect_stdout(output):
                self.assertEqual(main(["init", "--repo", str(repo), "--config", str(config_path)]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "initialized")
            output = StringIO()
            snapshot = SimpleNamespace(kind="plain", fingerprint="same")
            state = SimpleNamespace(source_fingerprint="state")
            with patch("codebase_atlas.cli._index_repository", return_value={"project": "indexed", "status": "indexed", "nodes": 1, "edges": 1}), patch("codebase_atlas.cli.repository_snapshot", return_value=snapshot), patch("codebase_atlas.cli.record_index_state", return_value=state), redirect_stdout(output):
                self.assertEqual(main(["index", "--config", str(config_path)]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "indexed")
            output = StringIO()
            checks = self.ready_checks()
            with patch("codebase_atlas.cli.diagnose", return_value=checks), patch("codebase_atlas.cli.index_freshness", return_value={"status": "fresh"}), patch("codebase_atlas.cli.provider_database_health", return_value={"status": "ready"}), redirect_stdout(output):
                self.assertEqual(main(["doctor", "--config", str(config_path)]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "ready")


if __name__ == "__main__":
    unittest.main()
