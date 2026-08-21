from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from codebase_atlas.cli import main
from codebase_atlas.config import AtlasConfig
from codebase_atlas.index_state import record_index_state
from codebase_atlas.maintenance import (
    apply_cleanup,
    cleanup_plan,
    inspect_installation,
    inspect_provider_database,
    repair_plan,
)


class MaintenanceTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[AtlasConfig, Path]:
        repository = root / "repo"
        repository.mkdir()
        for executable in ("node", "cbm", "serena"):
            (root / executable).touch()
        config = AtlasConfig(
            repository, "python", root / "node", root / "cbm", root / "serena",
            root / "data", "fixture-project",
        )
        config.cache_dir.mkdir(parents=True)
        database = config.cache_dir / "fixture-project.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            "CREATE TABLE projects(name TEXT PRIMARY KEY, root_path TEXT NOT NULL);"
            "CREATE TABLE nodes(id INTEGER PRIMARY KEY);"
            "CREATE TABLE edges(id INTEGER PRIMARY KEY);"
        )
        connection.execute(
            "INSERT INTO projects(name, root_path) VALUES (?, ?)",
            (config.project, str(config.repository)),
        )
        connection.commit()
        connection.close()
        return config, database

    def test_shallow_and_deep_database_inspection_are_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, database = self._fixture(Path(raw))
            before = database.stat().st_mtime_ns
            shallow = inspect_provider_database(config)
            deep = inspect_provider_database(config, deep=True)
            self.assertEqual(shallow["status"], "healthy")
            self.assertEqual(deep["quick_check"], ["ok"])
            self.assertEqual(database.stat().st_mtime_ns, before)

    def test_known_provider_shadow_project_is_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, database = self._fixture(Path(raw))
            connection = sqlite3.connect(database)
            connection.execute(
                "INSERT INTO projects(name, root_path) VALUES (?, ?)",
                (config.project + "::missed", ""),
            )
            connection.commit()
            connection.close()
            result = inspect_provider_database(config)
            self.assertEqual(result["status"], "healthy")
            self.assertEqual(result["auxiliary_projects"], [config.project + "::missed"])

    def test_missing_empty_corrupt_and_incompatible_databases_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, database = self._fixture(Path(raw))
            database.unlink()
            self.assertEqual(inspect_provider_database(config)["status"], "missing")
            database.touch()
            self.assertEqual(inspect_provider_database(config)["status"], "invalid")
            database.write_bytes(b"not a sqlite database")
            self.assertEqual(inspect_provider_database(config)["status"], "corrupt")
            database.unlink()
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY)")
            connection.commit()
            connection.close()
            self.assertEqual(inspect_provider_database(config)["status"], "incompatible")

    def test_report_finds_staging_quarantine_and_temporary_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, database = self._fixture(Path(raw))
            record_index_state(config.data_dir, config.repository, config.project, "fast")
            Path(str(database) + ".stage.abcd").write_bytes(b"stage")
            Path(str(database) + ".corrupt.1").write_bytes(b"old")
            (config.data_dir / ".index-state-leftover.json").write_text("{}")
            report = inspect_installation(config)
            kinds = {item["kind"] for item in report["findings"]}
            self.assertTrue(report["ok"])
            self.assertEqual(report["mode"], "read_only")
            self.assertEqual(
                kinds,
                {"provider_staging", "provider_quarantine", "atlas_state_temporary"},
            )

    def test_cli_inspect_emits_json_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, _database = self._fixture(Path(raw))
            record_index_state(config.data_dir, config.repository, config.project, "fast")
            path = config.repository / ".codebase-atlas.toml"
            config.write(path)
            output = StringIO()
            with redirect_stdout(output):
                code = main(["inspect", "--config", str(path), "--deep"])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "healthy")
            self.assertEqual(payload["provider_database"]["quick_check"], ["ok"])

    def test_repair_plan_never_treats_transient_failure_as_corruption(self) -> None:
        report = {
            "provider_database": {"status": "unavailable", "ok": False},
            "index": {"status": "fresh", "ok": True},
        }
        plan = repair_plan(report)
        self.assertEqual(plan["action"], "wait_and_retry")
        self.assertFalse(plan["applicable"])

    def test_failed_applied_repair_preserves_database_and_atlas_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config, database = self._fixture(Path(raw))
            record_index_state(config.data_dir, config.repository, config.project, "fast")
            state_path = config.data_dir / "index-state.json"
            state_path.write_text("{}")
            config_path = config.repository / ".codebase-atlas.toml"
            config.write(config_path)
            database_before = database.read_bytes()
            state_before = state_path.read_bytes()
            output = StringIO()
            with patch("codebase_atlas.cli._index_repository", side_effect=RuntimeError("injected failure")):
                with redirect_stdout(output):
                    code = main(["repair", "--config", str(config_path), "--apply"])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "failed")
            self.assertFalse(payload["atlas_state_advanced"])
            self.assertEqual(database.read_bytes(), database_before)
            self.assertEqual(state_path.read_bytes(), state_before)

    def test_cleanup_is_dry_run_first_retains_newest_and_refuses_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config, database = self._fixture(root)
            first = Path(str(database) + ".corrupt.1")
            second = Path(str(database) + ".corrupt.2")
            first.write_bytes(b"old")
            second.write_bytes(b"newest")
            temporary = config.data_dir / ".index-state-abcd.json"
            temporary.write_bytes(b"temporary")
            outside = root / "outside"
            outside.write_bytes(b"keep")
            symlink = Path(str(database) + ".stage.escape")
            symlink.symlink_to(outside)
            plan = cleanup_plan(config)
            target_paths = {item["path"] for item in plan["targets"]}
            retained_paths = {item["path"] for item in plan["retained"]}
            self.assertIn(str(first), target_paths)
            self.assertIn(str(temporary), target_paths)
            self.assertIn(str(second), retained_paths)
            self.assertEqual(plan["refused"][0]["path"], str(symlink))
            self.assertTrue(first.exists())
            with self.assertRaisesRegex(ValueError, "refused targets"):
                apply_cleanup(config, plan)
            self.assertTrue(first.exists())
            symlink.unlink()
            plan = cleanup_plan(config)
            result = apply_cleanup(config, plan)
            self.assertEqual(result["removed_count"], 2)
            self.assertFalse(first.exists())
            self.assertFalse(temporary.exists())
            self.assertTrue(second.exists())
            self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
