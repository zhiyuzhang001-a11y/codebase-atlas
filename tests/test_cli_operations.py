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

    def test_first_index_with_default_repository_config_is_immediately_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config, old_path = self.config(root)
            (config.repository / "sample.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(config.repository), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(config.repository), "config", "user.email", "atlas@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(config.repository), "config", "user.name", "Atlas Test"], check=True)
            subprocess.run(["git", "-C", str(config.repository), "add", "sample.py"], check=True)
            subprocess.run(["git", "-C", str(config.repository), "commit", "-qm", "initial"], check=True)
            config = config.with_project("")
            path = config.repository / ".codebase-atlas.toml"
            config.write(path)
            old_path.unlink()
            payload = {"project": "project", "status": "indexed", "nodes": 4, "edges": 7}
            with patch("codebase_atlas.cli._index_repository", return_value=payload):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["index", "--config", str(path)]), 0)
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["doctor", "--config", str(path)]), 0)
            self.assertEqual(json.loads(output.getvalue())["index"]["status"], "fresh")


if __name__ == "__main__":
    unittest.main()
