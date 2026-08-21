from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codebase_atlas.config import AtlasConfig, default_data_dir, diagnose


class ConfigTests(unittest.TestCase):
    def test_round_trip_and_derived_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            (repo / "packages/app").mkdir(parents=True)
            (repo / "packages/app/tsconfig.json").touch()
            for name in ("node", "cbm", "serena-python"):
                (root / name).touch()
            config = AtlasConfig(
                repo, "typescript", root / "node", root / "cbm",
                root / "serena-python", root / "data", "project-v1", root,
                Path("packages/app/tsconfig.json"),
            )
            path = repo / ".codebase-atlas.toml"
            config.write(path)
            loaded = AtlasConfig.load(path)
            self.assertEqual(loaded, config)
            self.assertEqual(loaded.cache_dir, (root / "data/codebase-memory").resolve())
            self.assertTrue(all(item["ok"] for item in diagnose(loaded)))

    def test_default_data_dir_is_stable_and_repository_specific(self) -> None:
        first = default_data_dir(Path("/tmp/example-a"))
        self.assertEqual(first, default_data_dir(Path("/tmp/example-a")))
        self.assertNotEqual(first, default_data_dir(Path("/tmp/example-b")))


if __name__ == "__main__":
    unittest.main()
