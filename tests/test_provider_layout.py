from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.config import AtlasConfig, default_data_dir
from codebase_atlas.provider_layout import (
    configure_managed_provider_cache,
    inspect_provider_root,
    provider_environment,
    provider_project_identity,
    shared_provider_root,
)


class ProviderLayoutTests(unittest.TestCase):
    def test_managed_cache_disables_provider_watchers_without_global_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "repo"
            cache = root / "managed-cache"
            binary = root / "provider"
            repository.mkdir()
            binary.touch()
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            configure_managed_provider_cache(
                binary, cache, repository, runner=runner
            )
            self.assertEqual(
                [call[0][-3:] for call in calls],
                [
                    ["set", "auto_watch", "false"],
                    ["set", "watcher_enabled", "false"],
                ],
            )
            self.assertTrue(all(
                call[1]["env"]["CBM_CACHE_DIR"] == str(cache.resolve())
                and call[1]["env"]["CBM_ALLOWED_ROOT"] == str(repository.resolve())
                for call in calls
            ))
    def test_default_projects_share_provider_root_but_not_atlas_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "data")}, clear=False
        ):
            first = Path(raw) / "one" / "service"
            second = Path(raw) / "two" / "service"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            self.assertNotEqual(default_data_dir(first), default_data_dir(second))
            self.assertEqual(
                shared_provider_root(),
                (Path(raw) / "data/codebase-atlas/_shared/codebase-memory/v1").resolve(),
            )

    def test_project_identity_is_stable_and_disambiguates_same_basename(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            first = Path(raw) / "one" / "service"
            second = Path(raw) / "two" / "service"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            self.assertEqual(provider_project_identity(first), provider_project_identity(first / "."))
            self.assertNotEqual(provider_project_identity(first), provider_project_identity(second))
            self.assertRegex(provider_project_identity(first), r"^atlas-service-[0-9a-f]{24}$")

    def test_provider_environment_uses_exact_repository_not_parent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "workspace/repository"
            cache = root / "provider"
            repository.mkdir(parents=True)
            cache.mkdir()
            environment = provider_environment(cache, repository, {"KEEP": "yes"})
            self.assertEqual(environment["KEEP"], "yes")
            self.assertEqual(environment["CBM_CACHE_DIR"], str(cache.resolve()))
            self.assertEqual(environment["CBM_ALLOWED_ROOT"], str(repository.resolve()))

    def test_provider_root_inspection_is_read_only_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            missing = root / "missing"
            self.assertEqual(inspect_provider_root(missing).status, "missing")
            self.assertFalse(missing.exists())

            regular = root / "regular"
            regular.write_text("not a directory", encoding="utf-8")
            self.assertEqual(inspect_provider_root(regular).status, "not_directory")

            target = root / "target"
            target.mkdir(mode=0o700)
            link = root / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                pass
            else:
                self.assertEqual(inspect_provider_root(link).status, "unsafe_symlink")

            if os.name != "nt":
                broad = root / "broad"
                broad.mkdir(mode=0o755)
                broad.chmod(0o755)
                self.assertEqual(inspect_provider_root(broad).status, "permissions_too_broad")
                broad.chmod(0o700)
                self.assertTrue(inspect_provider_root(broad).ready)

    def test_config_exposes_shared_target_without_switching_legacy_cache(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            root = Path(raw)
            repository = root / "repo"
            repository.mkdir()
            config = AtlasConfig(
                repository,
                "python",
                root / "node",
                root / "cbm",
                root / "python",
                root / "project-data",
            )
            self.assertEqual(config.cache_dir, (root / "project-data/codebase-memory").resolve())
            self.assertEqual(config.legacy_cache_dir, config.cache_dir)
            self.assertEqual(config.shared_cache_dir, shared_provider_root())
            self.assertEqual(config.shared_project, provider_project_identity(repository))


if __name__ == "__main__":
    unittest.main()
