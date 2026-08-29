from __future__ import annotations

import os
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from codebase_atlas.config import AtlasConfig, SHARED_PROVIDER_LAYOUT
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

    def test_legacy_repository_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            root = Path(raw)
            config = self._config(root)
            foreign = root / "foreign"
            foreign.mkdir()
            _database(config.legacy_cache_dir, config.project, foreign)
            plan = plan_provider_migration(config)
            self.assertEqual(plan.status, "blocked")
            self.assertIn("identity_mismatch", plan.reason)

    def test_partial_shared_generation_requires_explicit_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            config = self._config(Path(raw))
            config.shared_cache_dir.mkdir(parents=True, mode=0o700)
            if os.name != "nt":
                config.shared_cache_dir.chmod(0o700)
            residue = config.shared_cache_dir / f"{config.shared_project}.db.stage.interrupted"
            residue.write_bytes(b"partial")
            plan = plan_provider_migration(config)
            self.assertEqual(
                (plan.status, plan.action),
                ("blocked", "resume_or_quarantine_partial"),
            )
            self.assertEqual(plan.staging_residue, (str(residue),))

    def test_insufficient_disk_blocks_before_worker_start(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            config = self._config(Path(raw))
            _database(config.legacy_cache_dir, config.project, config.repository)
            plan = plan_provider_migration(
                config, disk_usage=lambda _path: SimpleNamespace(free=1)
            )
            self.assertEqual(
                (plan.status, plan.action), ("blocked", "free_space_before_rebuild")
            )
            self.assertFalse(plan.disk_preflight["ok"])

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
            self.assertIn("migrate-provider", payload["apply_command"])
            self.assertEqual(config_path.read_bytes(), before)
            self.assertFalse(config.shared_cache_dir.exists())

    def test_apply_rebuilds_validates_and_publishes_without_deleting_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            config = self._config(Path(raw))
            legacy = _database(config.legacy_cache_dir, config.project, config.repository)
            legacy_bytes = legacy.read_bytes()
            config_path = config.repository / ".codebase-atlas.toml"
            config.write(config_path)

            def indexer(candidate: AtlasConfig, _mode: str) -> dict[str, object]:
                _database(candidate.cache_dir, candidate.project, candidate.repository)
                return {"status": "indexed", "project": candidate.project}

            output = StringIO()
            with patch("codebase_atlas.cli._index_repository", side_effect=indexer) as called, redirect_stdout(output):
                code = main([
                    "migrate-provider", "--config", str(config_path), "--apply"
                ])
            payload = json.loads(output.getvalue())
            migrated = AtlasConfig.load(config_path)
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "migrated")
            self.assertEqual(migrated.provider_layout, SHARED_PROVIDER_LAYOUT)
            self.assertEqual(migrated.project, migrated.shared_project)
            self.assertEqual(migrated.legacy_project, config.project)
            self.assertEqual(migrated.cache_dir, migrated.shared_cache_dir)
            self.assertEqual(legacy.read_bytes(), legacy_bytes)
            called.assert_called_once()

            second = StringIO()
            with patch("codebase_atlas.cli._index_repository") as repeated, redirect_stdout(second):
                second_code = main([
                    "migrate-provider", "--config", str(config_path), "--apply"
                ])
            second_payload = json.loads(second.getvalue())
            self.assertEqual(second_code, 0)
            self.assertEqual(second_payload["action"], "already_active")
            repeated.assert_not_called()

    def test_worker_failure_preserves_exact_config_and_legacy_database(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            config = self._config(Path(raw))
            legacy = _database(config.legacy_cache_dir, config.project, config.repository)
            config_path = config.repository / ".codebase-atlas.toml"
            config.write(config_path)
            config_bytes = config_path.read_bytes()
            legacy_bytes = legacy.read_bytes()
            output = StringIO()
            with patch(
                "codebase_atlas.cli._index_repository", side_effect=RuntimeError("worker failed")
            ), redirect_stdout(output):
                code = main([
                    "migrate-provider", "--config", str(config_path), "--apply"
                ])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "failed")
            self.assertTrue(payload["legacy_preserved"])
            self.assertEqual(config_path.read_bytes(), config_bytes)
            self.assertEqual(legacy.read_bytes(), legacy_bytes)

    def test_publication_failure_restores_exact_legacy_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            config = self._config(Path(raw))
            _database(config.legacy_cache_dir, config.project, config.repository)
            config_path = config.repository / ".codebase-atlas.toml"
            config.write(config_path)
            original = config_path.read_bytes()

            def indexer(candidate: AtlasConfig, _mode: str) -> dict[str, object]:
                _database(candidate.cache_dir, candidate.project, candidate.repository)
                return {"status": "indexed", "project": candidate.project}

            output = StringIO()
            with patch("codebase_atlas.cli._index_repository", side_effect=indexer), patch(
                "codebase_atlas.cli.record_index_state", side_effect=OSError("publish failed")
            ), redirect_stdout(output):
                code = main([
                    "migrate-provider", "--config", str(config_path), "--apply"
                ])
            self.assertEqual(code, 2)
            self.assertEqual(config_path.read_bytes(), original)
            self.assertEqual(AtlasConfig.load(config_path).provider_layout, "legacy-project-v0")

    def test_interrupt_preserves_config_and_leaves_shared_unpublished(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"XDG_DATA_HOME": str(Path(raw) / "account")}, clear=False
        ):
            config = self._config(Path(raw))
            legacy = _database(config.legacy_cache_dir, config.project, config.repository)
            config_path = config.repository / ".codebase-atlas.toml"
            config.write(config_path)
            original = config_path.read_bytes()
            output = StringIO()
            with patch(
                "codebase_atlas.cli._index_repository", side_effect=KeyboardInterrupt
            ), redirect_stdout(output):
                code = main([
                    "migrate-provider", "--config", str(config_path), "--apply"
                ])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 130)
            self.assertEqual(payload["status"], "interrupted")
            self.assertEqual(config_path.read_bytes(), original)
            self.assertTrue(legacy.exists())


if __name__ == "__main__":
    unittest.main()
