from __future__ import annotations

import os
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

    def test_verified_write_restores_original_after_partial_io_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "repo"
            repository.mkdir()
            path = repository / ".codebase-atlas.toml"
            original = AtlasConfig(
                repository, "python", root / "node", root / "cbm",
                root / "serena", root / "data",
            )
            original.write(path)
            original_bytes = path.read_bytes()
            identity = (path.stat().st_dev, path.stat().st_ino)
            updated = original.with_project("indexed")
            real_fdopen = os.fdopen

            class PartialFailureStream:
                def __init__(self, stream):
                    self.stream = stream
                    self.fail_next_write = True

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.stream.close()

                def fileno(self):
                    return self.stream.fileno()

                def read(self):
                    return self.stream.read()

                def seek(self, *args):
                    return self.stream.seek(*args)

                def truncate(self, *args):
                    return self.stream.truncate(*args)

                def flush(self):
                    return self.stream.flush()

                def write(self, value):
                    if self.fail_next_write:
                        self.fail_next_write = False
                        self.stream.write(value[: max(1, len(value) // 2)])
                        raise OSError("simulated partial publication failure")
                    return self.stream.write(value)

            with patch(
                "codebase_atlas.config.os.fdopen",
                side_effect=lambda descriptor, *args, **kwargs: PartialFailureStream(
                    real_fdopen(descriptor, *args, **kwargs)
                ),
            ):
                with self.assertRaisesRegex(OSError, "partial publication failure"):
                    updated.write_verified(path, identity)

            self.assertEqual(path.read_bytes(), original_bytes)
            self.assertEqual((path.stat().st_dev, path.stat().st_ino), identity)

    def test_verified_write_without_nofollow_preserves_approved_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "repo"
            repository.mkdir()
            path = repository / ".codebase-atlas.toml"
            original = AtlasConfig(
                repository, "python", root / "node", root / "cbm",
                root / "serena", root / "data",
            )
            original.write(path)
            identity = (path.stat().st_dev, path.stat().st_ino)

            with patch("codebase_atlas.config.os.O_NOFOLLOW", None, create=True):
                original.with_project("indexed").write_verified(path, identity)

            self.assertEqual(AtlasConfig.load(path).project, "indexed")
            self.assertEqual((path.stat().st_dev, path.stat().st_ino), identity)

    def test_verified_restore_preserves_exact_bytes_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "repo"
            repository.mkdir()
            path = repository / ".codebase-atlas.toml"
            original = AtlasConfig(
                repository, "python", root / "node", root / "cbm",
                root / "serena", root / "data",
            )
            original.write(path)
            custom = path.read_bytes() + b"\n# retained comment\n"
            path.write_bytes(custom)
            identity = (path.stat().st_dev, path.stat().st_ino)
            original.with_project("indexed").write_verified(path, identity)

            AtlasConfig.restore_verified(path, identity, custom)

            self.assertEqual(path.read_bytes(), custom)
            self.assertEqual((path.stat().st_dev, path.stat().st_ino), identity)

    def test_verified_write_without_nofollow_rejects_preopen_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "repo"
            repository.mkdir()
            path = repository / ".codebase-atlas.toml"
            replacement = repository / "replacement.toml"
            original = AtlasConfig(
                repository, "python", root / "node", root / "cbm",
                root / "serena", root / "data",
            )
            original.write(path)
            replacement.write_text("replacement", encoding="utf-8")
            identity = (path.stat().st_dev, path.stat().st_ino)
            real_open = os.open

            def replace_before_open(target, flags, *args):
                os.replace(replacement, path)
                return real_open(target, flags, *args)

            with patch("codebase_atlas.config.os.O_NOFOLLOW", None, create=True), patch(
                "codebase_atlas.config.os.open", side_effect=replace_before_open
            ):
                with self.assertRaisesRegex(ValueError, "identity changed"):
                    original.with_project("indexed").write_verified(path, identity)

            self.assertEqual(path.read_text(encoding="utf-8"), "replacement")


if __name__ == "__main__":
    unittest.main()
