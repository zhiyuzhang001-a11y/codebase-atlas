from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codebase_atlas.config import AtlasConfig, default_data_dir, diagnose
from codebase_atlas.index_state import record_index_state


class ConfigTests(unittest.TestCase):
    def test_round_trip_and_derived_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            (repo / "packages/app").mkdir(parents=True)
            (repo / "packages/app/tsconfig.json").touch()
            for name in ("node", "npm", "cbm", "serena-python"):
                (root / name).touch()
            (root / "npm").chmod(0o755)
            config = AtlasConfig(
                repo, "typescript", root / "node", root / "cbm",
                root / "serena-python", root / "data", "project-v1", root,
                Path("packages/app/tsconfig.json"),
            )
            path = repo / ".codebase-atlas.toml"
            config.write(path)
            loaded = AtlasConfig.load(path)
            record_index_state(loaded.data_dir, loaded.repository, loaded.project, "fast")
            loaded.cache_dir.mkdir(parents=True, exist_ok=True)
            (loaded.cache_dir / f"{loaded.project}.db").write_bytes(b"database")
            self.assertEqual(loaded, config)
            self.assertEqual(loaded.cache_dir, (root / "data/codebase-memory").resolve())

            def runner(command, **_kwargs):
                if command[0] == str(loaded.node):
                    output = "v20.0.0"
                elif command[0] == str(loaded.cbm_binary):
                    output = "codebase-memory-mcp 0.4.0"
                else:
                    output = "1.7.1"
                return SimpleNamespace(returncode=0, stdout=output, stderr="")

            with patch(
                "codebase_atlas.runtime.shutil.which",
                side_effect=lambda command, **_kwargs: str(root / "npm") if command == "npm" else None,
            ):
                checks = diagnose(loaded, runner=runner)
            self.assertTrue(all(item["ok"] for item in checks if item["required"]))

    def test_default_data_dir_is_stable_and_repository_specific(self) -> None:
        first = default_data_dir(Path("/tmp/example-a"))
        self.assertEqual(first, default_data_dir(Path("/tmp/example-a")))
        self.assertNotEqual(first, default_data_dir(Path("/tmp/example-b")))


if __name__ == "__main__":
    unittest.main()
