from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from codebase_atlas.config import AtlasConfig
from codebase_atlas.codex_integration import codex_apply
from codebase_atlas.index_state import (
    index_freshness,
    provider_database_health,
    record_index_state,
    repository_snapshot,
    state_path,
)


def git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)


class IndexStateTests(unittest.TestCase):
    def make_repository(self, root: Path) -> Path:
        repository = root / "repo"
        repository.mkdir()
        git(repository, "init", "-q")
        git(repository, "config", "user.email", "atlas@example.invalid")
        git(repository, "config", "user.name", "Atlas Test")
        (repository / "sample.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        git(repository, "add", "sample.py")
        git(repository, "commit", "-qm", "initial")
        return repository

    def test_fresh_then_modified_then_refreshed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.make_repository(root)
            data = root / "data"
            record_index_state(data, repository, "project", "fast")
            self.assertEqual(index_freshness(data, repository, "project")["status"], "fresh")
            (repository / "sample.py").write_text("def value():\n    return 3\n", encoding="utf-8")
            self.assertEqual(index_freshness(data, repository, "project")["status"], "stale")
            (repository / "sample.py").write_text("def value():\n    return 2\n", encoding="utf-8")
            self.assertEqual(index_freshness(data, repository, "project")["status"], "stale")
            record_index_state(data, repository, "project", "fast")
            self.assertEqual(index_freshness(data, repository, "project")["status"], "fresh")

    def test_add_delete_and_rename_are_stale(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.make_repository(root)
            data = root / "data"
            for mutate in (
                lambda: (repository / "new.py").write_text("x = 1\n", encoding="utf-8"),
                lambda: (repository / "sample.py").unlink(),
                lambda: (repository / "sample.py").rename(repository / "renamed.py"),
            ):
                git(repository, "reset", "--hard", "-q", "HEAD")
                (repository / "new.py").unlink(missing_ok=True)
                record_index_state(data, repository, "project", "fast")
                mutate()
                self.assertEqual(index_freshness(data, repository, "project")["status"], "stale")

    def test_custom_atlas_config_is_operational_but_other_toml_is_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.make_repository(root)
            before = repository_snapshot(repository)
            config = AtlasConfig(repository, "python", root / "node", root / "cbm", root / "serena", root / "data", project="indexed")
            config.write(repository / "atlas.toml")
            self.assertEqual(repository_snapshot(repository).fingerprint, before.fingerprint)
            (repository / "source.toml").write_text("value = 1\n", encoding="utf-8")
            self.assertNotEqual(repository_snapshot(repository).fingerprint, before.fingerprint)

    def test_foreign_atlas_config_is_source_even_at_default_name(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.make_repository(root)
            foreign = root / "foreign"
            foreign.mkdir()
            before = repository_snapshot(repository)
            config = AtlasConfig(foreign, "python", root / "node", root / "cbm", root / "serena", root / "data", project="foreign")
            config.write(repository / ".codebase-atlas.toml")
            after = repository_snapshot(repository)
            self.assertNotEqual(after.fingerprint, before.fingerprint)
            self.assertEqual(after.changed_paths, 1)

    def test_managed_project_codex_config_is_operational_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.make_repository(root)
            config = AtlasConfig(
                repository, "python", root / "node", root / "cbm",
                root / "serena", root / "data", project="indexed",
            )
            config_path = repository / ".codebase-atlas.toml"
            config.write(config_path)
            atlas = root / "codebase-atlas"
            atlas.write_text("")
            before = repository_snapshot(repository)
            codex_apply(config_path, scope="project", atlas_executable=atlas)
            after = repository_snapshot(repository)
            self.assertEqual(after.fingerprint, before.fingerprint)
            self.assertEqual(after.changed_paths, before.changed_paths)

    def test_unmanaged_project_codex_config_remains_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.make_repository(root)
            before = repository_snapshot(repository)
            target = repository / ".codex/config.toml"
            target.parent.mkdir()
            target.write_text('model = "custom"\n', encoding="utf-8")
            after = repository_snapshot(repository)
            self.assertNotEqual(after.fingerprint, before.fingerprint)

    def test_invalid_default_config_and_symlink_are_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.make_repository(root)
            before = repository_snapshot(repository)
            config_path = repository / ".codebase-atlas.toml"

            config_path.write_text("malformed = true\n", encoding="utf-8")
            malformed = repository_snapshot(repository)
            self.assertNotEqual(malformed.fingerprint, before.fingerprint)
            self.assertEqual(malformed.changed_paths, 1)

            config_path.unlink()
            config_path.symlink_to(repository / "sample.py")
            linked = repository_snapshot(repository)
            self.assertNotEqual(linked.fingerprint, before.fingerprint)
            self.assertEqual(linked.changed_paths, 1)

    def test_invalid_or_mismatched_state_requires_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = self.make_repository(root)
            data = root / "data"
            self.assertEqual(index_freshness(data, repository, "project")["status"], "rebuild_required")
            state_path(data).parent.mkdir(parents=True)
            state_path(data).write_text("not json", encoding="utf-8")
            self.assertEqual(index_freshness(data, repository, "project")["reason"], "index_state_invalid")
            record_index_state(data, repository, "project", "fast")
            value = json.loads(state_path(data).read_text(encoding="utf-8"))
            value["project"] = "other"
            state_path(data).write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(index_freshness(data, repository, "project")["reason"], "index_state_identity_mismatch")

    def test_non_git_state_is_unknown_but_usable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "repo"
            repository.mkdir()
            data = root / "data"
            record_index_state(data, repository, "project", "fast")
            result = index_freshness(data, repository, "project")
            self.assertEqual(result["status"], "unknown")
            self.assertTrue(result["ok"])

    def test_provider_database_health_requires_nonempty_safe_database(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cache = Path(raw)
            self.assertEqual(provider_database_health(cache, "project")["status"], "missing")
            (cache / "project.db").touch()
            self.assertEqual(provider_database_health(cache, "project")["reason"], "provider_database_empty")
            (cache / "project.db").write_bytes(b"database")
            self.assertTrue(provider_database_health(cache, "project")["ok"])
            self.assertEqual(provider_database_health(cache, "../outside")["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
