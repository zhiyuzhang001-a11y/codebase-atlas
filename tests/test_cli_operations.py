from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from codebase_atlas.cli import main
from codebase_atlas.config import AtlasConfig
from codebase_atlas.index_state import record_index_state, state_path


class CliOperationTests(unittest.TestCase):
    def config(self, root: Path) -> tuple[AtlasConfig, Path]:
        repository = root / "repo"
        repository.mkdir()
        for name in ("node", "cbm", "serena"):
            (root / name).touch()
        config = AtlasConfig(
            repository,
            "python",
            root / "node",
            root / "cbm",
            root / "serena",
            root / "data",
            "project",
            root,
        )
        path = root / "atlas.toml"
        config.write(path)
        return config, path

    def make_git_repository(self, repository: Path) -> None:
        (repository / "sample.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "atlas@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Atlas Test"], check=True)
        subprocess.run(["git", "-C", str(repository), "add", "sample.py"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "initial"], check=True)

    def prepare_fast_path(self, config: AtlasConfig) -> None:
        self.make_git_repository(config.repository)
        record_index_state(config.data_dir, config.repository, config.project, "fast")
        config.cache_dir.mkdir(parents=True, exist_ok=True)
        (config.cache_dir / f"{config.project}.db").write_bytes(b"database")

    def test_update_records_state_only_after_provider_success(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, path = self.config(Path(raw))
            output = StringIO()
            payload = {"project": "project", "status": "indexed", "nodes": 4, "edges": 7}
            with patch("codebase_atlas.cli._index_repository", return_value=payload):
                with redirect_stdout(output):
                    self.assertEqual(main(["update", "--config", str(path)]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "updated")
            self.assertEqual(result["provider"]["route"], "provider_managed")
            self.assertTrue(state_path(config.data_dir).is_file())

    def test_failed_update_preserves_previous_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, path = self.config(Path(raw))
            record_index_state(config.data_dir, config.repository, config.project, "fast")
            before = state_path(config.data_dir).read_bytes()
            with patch("codebase_atlas.cli._index_repository", side_effect=RuntimeError("failed")):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    main(["update", "--config", str(path)])
            self.assertEqual(state_path(config.data_dir).read_bytes(), before)

    def test_fresh_update_skips_provider_unless_forced(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, path = self.config(Path(raw))
            self.prepare_fast_path(config)
            output = StringIO()
            with patch("codebase_atlas.cli._index_repository") as provider:
                with redirect_stdout(output):
                    self.assertEqual(main(["update", "--config", str(path)]), 0)
            provider.assert_not_called()
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "current")
            self.assertEqual(result["provider"]["route"], "atlas_source_current")

            payload = {"project": "project", "status": "indexed", "nodes": 4, "edges": 7}
            with patch("codebase_atlas.cli._index_repository", return_value=payload) as provider:
                with redirect_stdout(StringIO()):
                    self.assertEqual(
                        main(["update", "--config", str(path), "--force-provider"]),
                        0,
                    )
            provider.assert_called_once()

    def test_missing_database_disables_fast_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, path = self.config(Path(raw))
            self.make_git_repository(config.repository)
            record_index_state(config.data_dir, config.repository, config.project, "fast")
            payload = {"project": "project", "status": "indexed", "nodes": 4, "edges": 7}
            with patch("codebase_atlas.cli._index_repository", return_value=payload) as provider:
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["update", "--config", str(path)]), 0)
            provider.assert_called_once()

    def test_first_index_with_default_repository_config_is_immediately_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config, old_path = self.config(root)
            self.make_git_repository(config.repository)
            config = config.with_project("")
            path = config.repository / ".codebase-atlas.toml"
            config.write(path)
            old_path.unlink()
            payload = {"project": "project", "status": "indexed", "nodes": 4, "edges": 7}
            with patch("codebase_atlas.cli._index_repository", return_value=payload):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["index", "--config", str(path)]), 0)
            config = AtlasConfig.load(path)
            config.cache_dir.mkdir(parents=True, exist_ok=True)
            (config.cache_dir / f"{config.project}.db").write_bytes(b"database")
            output = StringIO()
            runtime_ready = [{
                "name": "runtime", "ok": True, "required": True,
                "path": "", "version": "", "detail": "ready", "remediation": "",
            }]
            with patch("codebase_atlas.runtime.runtime_checks", return_value=runtime_ready):
                with redirect_stdout(output):
                    self.assertEqual(main(["doctor", "--config", str(path)]), 0)
            self.assertEqual(json.loads(output.getvalue())["index"]["status"], "fresh")

    def test_setup_reports_structured_read_only_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            _config, path = self.config(Path(raw))
            checks = [
                {
                    "name": "node", "ok": True, "required": True,
                    "path": "/node", "version": "20.0.0", "detail": "ready",
                    "remediation": "",
                }
            ]
            output = StringIO()
            with patch("codebase_atlas.cli.runtime_checks", return_value=checks):
                with redirect_stdout(output):
                    self.assertEqual(main(["setup", "--config", str(path)]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["mode"], "read_only")


if __name__ == "__main__":
    unittest.main()
