from __future__ import annotations

import os
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.config import AtlasConfig
from codebase_atlas.cli import main
from codebase_atlas.provider_migration import plan_provider_migration


def _database(cache: Path, project: str, repository: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        cache.chmod(0o700)
    path = cache / f"{project}.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE projects(name TEXT PRIMARY KEY, root_path TEXT NOT NULL);"
        "CREATE TABLE nodes(id INTEGER PRIMARY KEY);"
        "CREATE TABLE edges(id INTEGER PRIMARY KEY);"
    )
    connection.execute(
        "INSERT INTO projects(name, root_path) VALUES (?, ?)",
        (project, str(repository.resolve())),
    )
    connection.commit()
    connection.close()
    return path


class ProviderMigrationPlanTests(unittest.TestCase):
    def _config(self, root: Path) -> AtlasConfig:
        repository = root / "repo"
        repository.mkdir()
        return AtlasConfig(
            repository, "python", root / "node", root / "cbm", root / "serena",
            root / "project-data", "legacy-project",
        )

    def test_missing_indexes_plan_fresh_shared_index_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            config = self._config(Path(raw))
            plan = plan_provider_migration(config)
            self.assertEqual(plan.status, "planned")
            self.assertEqual(plan.action, "fresh_shared_index")
            self.assertFalse(config.shared_cache_dir.exists())
            self.assertFalse(config.cache_dir.exists())

    def test_healthy_legacy_plans_rebuild_and_preserves_source_database(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            config = self._config(Path(raw))
            legacy = _database(config.cache_dir, config.project, config.repository)
            before = legacy.read_bytes()
            plan = plan_provider_migration(config)
            self.assertEqual((plan.status, plan.action), ("planned", "rebuild_into_shared"))
            self.assertEqual(plan.legacy["quick_check"], ["ok"])
            self.assertEqual(legacy.read_bytes(), before)
            self.assertFalse(config.shared_cache_dir.exists())

    def test_exact_healthy_shared_target_can_publish_without_reindex(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            config = self._config(Path(raw))
            _database(config.shared_cache_dir, config.shared_project, config.repository)
            plan = plan_provider_migration(config)
            self.assertEqual((plan.status, plan.action), ("ready", "verify_and_publish"))
            self.assertEqual(plan.shared["quick_check"], ["ok"])

    def test_shared_identity_collision_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            root = Path(raw)
            config = self._config(root)
            foreign = root / "foreign"
            foreign.mkdir()
            _database(config.shared_cache_dir, config.shared_project, foreign)
            plan = plan_provider_migration(config)
            self.assertEqual((plan.status, plan.action), ("blocked", "resolve_shared_conflict"))
            self.assertIn("identity_mismatch", plan.reason)

    def test_corrupt_legacy_is_not_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            config = self._config(Path(raw))
            config.cache_dir.mkdir(parents=True)
            (config.cache_dir / f"{config.project}.db").write_bytes(b"not sqlite")
            plan = plan_provider_migration(config)
            self.assertEqual(
                (plan.status, plan.action), ("blocked", "repair_legacy_before_migration")
            )
            self.assertIn("database_invalid", plan.reason)

    @unittest.skipIf(os.name == "nt", "POSIX permission contract")
    def test_unsafe_shared_root_blocks_before_any_migration(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            config = self._config(Path(raw))
            config.shared_cache_dir.mkdir(parents=True, mode=0o755)
            config.shared_cache_dir.chmod(0o755)
            plan = plan_provider_migration(config)
            self.assertEqual((plan.status, plan.action), ("blocked", "repair_shared_root"))
            self.assertEqual(plan.reason, "shared_root_permissions_too_broad")

    def test_cli_preview_is_read_only_and_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            config = self._config(Path(raw))
            config_path = config.repository / ".codebase-atlas.toml"
            config.write(config_path)
            before = config_path.read_bytes()
            output = StringIO()
            with redirect_stdout(output):
                code = main(["migrate-provider", "--config", str(config_path)])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["mode"], "read_only")
            self.assertEqual(payload["action"], "fresh_shared_index")
            self.assertEqual(payload["apply_command"], "")
            self.assertEqual(config_path.read_bytes(), before)
            self.assertFalse(config.shared_cache_dir.exists())


if __name__ == "__main__":
    unittest.main()
