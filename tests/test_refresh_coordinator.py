from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from time import monotonic
import statistics
import unittest
from unittest.mock import patch

from codebase_atlas.config import AtlasConfig
from codebase_atlas.index_state import record_index_state, state_path
from codebase_atlas.operations import operational_index_status
from codebase_atlas.python_registration_store import (
    load_registration_index_state,
    registration_index_path,
    stage_registration_index,
)
from codebase_atlas.refresh_coordinator import RefreshCoordinator
from codebase_atlas.refresh_planner import build_generation_manifest, manifest_path
from codebase_atlas.refresh_recovery import (
    RefreshRecoveryJournal,
    journal_path,
    recover_refresh_transaction,
)
from codebase_atlas.service import AtlasService


def git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args], check=True, capture_output=True
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.replacement")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def write_provider_database(path: Path, project: str, repository: Path, marker: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.candidate")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            "CREATE TABLE projects(name TEXT, root_path TEXT);"
            "CREATE TABLE nodes(id INTEGER, marker INTEGER);"
            "CREATE TABLE edges(id INTEGER);"
        )
        connection.execute(
            "INSERT INTO projects(name, root_path) VALUES (?, ?)",
            (project, str(repository.resolve())),
        )
        connection.execute("INSERT INTO nodes(id, marker) VALUES (1, ?)", (marker,))
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary, path)


class FakeTransport:
    def __init__(self, database: Path, repository: Path, project: str) -> None:
        self.database = database
        self.repository = repository
        self.project = project
        self.calls: list[tuple[str, dict[str, object], int]] = []
        self.return_project = project
        self.fail = False
        self.exception: BaseException | None = None
        self.after_write = None

    def call(self, tool, arguments, *, timeout_ms):
        self.calls.append((tool, arguments, timeout_ms))
        if self.fail:
            raise RuntimeError("provider failed")
        if self.exception is not None:
            raise self.exception
        write_provider_database(
            self.database, self.return_project, self.repository, marker=len(self.calls) + 1
        )
        if self.after_write is not None:
            self.after_write()
        return {
            "status": "indexed",
            "project": self.return_project,
            "nodes": 2,
            "edges": 0,
        }


class RefreshCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repo"
        self.repository.mkdir()
        git(self.repository, "init", "-q")
        git(self.repository, "config", "user.email", "atlas@example.invalid")
        git(self.repository, "config", "user.name", "Atlas Test")
        (self.repository / "sample.py").write_text("def value():\n    return 1\n")
        git(self.repository, "add", "sample.py")
        git(self.repository, "commit", "-qm", "initial")
        for name in ("node", "cbm", "serena"):
            (self.root / name).touch()
        self.config = AtlasConfig(
            self.repository,
            "python",
            self.root / "node",
            self.root / "cbm",
            self.root / "serena",
            self.root / "data",
            "project",
        )
        self.database = self.config.cache_dir / "project.db"
        write_provider_database(self.database, self.config.project, self.repository, 1)
        state = record_index_state(
            self.config.data_dir, self.repository, self.config.project, "fast"
        )
        with stage_registration_index(
            self.config.data_dir,
            self.repository,
            self.config.project,
            state.source_fingerprint,
        ) as staged:
            staged.publish()
        registration, health = load_registration_index_state(
            self.config.data_dir,
            self.repository,
            self.config.project,
            state.source_fingerprint,
        )
        self.status = operational_index_status(
            self.config.data_dir,
            self.repository,
            self.config.cache_dir,
            self.config.project,
        )
        self.status["identity"] = {
            "repository": str(self.repository.resolve()), "project": self.config.project
        }
        self.status["python_registrations"] = health
        manifest = build_generation_manifest(
            self.repository,
            self.config.project,
            "python",
            generation_id="generation-1",
            provider_identity={"sha256": digest(self.database)},
            sidecar_identity={"sha256": digest(registration_index_path(self.config.data_dir))},
            created_at="generation:generation-1",
        )
        manifest_path(self.config.data_dir).write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        self.service = AtlasService(
            repository=self.repository,
            registration_index=registration,
            session_continuations=True,
        )
        self.service.start()
        self.transport = FakeTransport(
            self.database, self.repository, self.config.project
        )
        self.coordinator = RefreshCoordinator(
            self.config, self.transport, self.service, self.status
        )

    def tearDown(self) -> None:
        self.service.close()
        self.temporary.cleanup()

    def hashes(self) -> dict[str, str]:
        paths = (
            self.database,
            registration_index_path(self.config.data_dir),
            manifest_path(self.config.data_dir),
            state_path(self.config.data_dir),
        )
        return {path.name: digest(path) for path in paths}

    def test_noop_skips_provider_and_is_under_250ms(self) -> None:
        started = monotonic()
        result = self.coordinator.refresh()
        elapsed_ms = (monotonic() - started) * 1000.0
        self.assertEqual(result["status"], "current")
        self.assertFalse(result["provider_called"])
        self.assertEqual(self.transport.calls, [])
        # The 250 ms performance gate was frozen on the macOS deployment
        # platform. Windows CI still proves the cross-platform no-Provider
        # fast path, but process startup timing is not comparable there.
        if os.name != "nt":
            self.assertLessEqual(elapsed_ms, 250.0)
            self.assertLessEqual(result["duration_ms"], 250.0)
        self.assertEqual(self.status["generation_id"], "generation-1")

    def test_modify_refreshes_same_transport_and_invalidates_generation_caches(self) -> None:
        self.service._python_reference_cache[(Path("x"), "a", "b")] = ()
        self.service._python_complete_reference_cache[(Path("x"), "a", "b", "c")] = ()
        self.service._python_caller_cache[(Path("x"), "a", "b", "c", "d")] = object()
        (self.repository / "sample.py").write_text("def value():\n    return 2\n")

        result = self.coordinator.refresh(mode="fast", timeout_ms=12345)

        self.assertEqual(result["status"], "refreshed")
        self.assertEqual(result["generation_before"], "generation-1")
        self.assertNotEqual(result["generation_after"], "generation-1")
        self.assertEqual(result["dirty_paths"], ["sample.py"])
        self.assertEqual(len(self.transport.calls), 1)
        tool, arguments, timeout = self.transport.calls[0]
        self.assertEqual(tool, "index_repository")
        self.assertEqual(arguments["repo_path"], str(self.repository.resolve()))
        self.assertEqual(arguments["name"], self.config.project)
        self.assertEqual(timeout, 12345)
        self.assertEqual(self.status["status"], "fresh")
        self.assertEqual(self.status["generation_id"], result["generation_after"])
        self.assertFalse(self.service._python_reference_cache)
        self.assertFalse(self.service._python_complete_reference_cache)
        self.assertFalse(self.service._python_caller_cache)

    def test_provider_identity_failure_restores_all_published_artifacts(self) -> None:
        before = self.hashes()
        self.transport.return_project = "foreign"
        (self.repository / "sample.py").write_text("value = 2\n")

        result = self.coordinator.refresh()

        self.assertEqual(result["status"], "failed")
        self.assertIn("invalid project generation", result["error"])
        self.assertTrue(result["previous_generation_preserved"])
        self.assertEqual(self.hashes(), before)
        self.assertEqual(self.status.get("generation_id"), "generation-1")
        self.assertEqual(self.status["status"], "stale")

    def test_state_publication_failure_rolls_back_provider_sidecar_and_manifest(self) -> None:
        before = self.hashes()
        (self.repository / "sample.py").write_text("value = 3\n")
        with patch(
            "codebase_atlas.refresh_coordinator.record_index_state",
            side_effect=OSError("state publication failed"),
        ):
            result = self.coordinator.refresh()

        self.assertEqual(result["status"], "failed")
        self.assertIn("state publication failed", result["error"])
        self.assertTrue(result["previous_generation_preserved"])
        self.assertEqual(self.hashes(), before)

    def test_in_process_duplicate_refresh_gets_explicit_status(self) -> None:
        self.coordinator._lock.acquire()
        try:
            result = self.coordinator.refresh()
        finally:
            self.coordinator._lock.release()
        self.assertEqual(result["status"], "refresh_in_progress")
        self.assertNotIn("provider_busy", json.dumps(result))

    def test_cross_coordinator_duplicate_is_owned_elsewhere_under_two_seconds(self) -> None:
        other = RefreshCoordinator(
            self.config, FakeTransport(self.database, self.repository, self.config.project),
            self.service, self.status,
        )
        self.assertTrue(self.coordinator._lease.acquire())
        try:
            started = monotonic()
            result = other.refresh()
            elapsed_ms = (monotonic() - started) * 1000.0
        finally:
            self.coordinator._lease.release()
        self.assertEqual(result["status"], "refresh_owned_elsewhere")
        self.assertLess(elapsed_ms, 2000.0)
        self.assertNotIn("provider_busy", json.dumps(result))

    def test_sidecar_prepare_failure_never_calls_provider(self) -> None:
        before = self.hashes()
        (self.repository / "sample.py").write_text("value = 4\n")
        with patch(
            "codebase_atlas.refresh_coordinator.stage_registration_index",
            side_effect=OSError("sidecar stage failed"),
        ):
            result = self.coordinator.refresh()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.hashes(), before)
        self.assertEqual(self.status["status"], "stale")

    def test_provider_validation_failure_restores_previous(self) -> None:
        before = self.hashes()
        (self.repository / "sample.py").write_text("value = 5\n")
        with patch(
            "codebase_atlas.refresh_coordinator.inspect_provider_database_at",
            return_value={"ok": False, "reason": "provider_quick_check_failed"},
        ):
            result = self.coordinator.refresh()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.hashes(), before)
        self.assertTrue(result["previous_generation_preserved"])

    def test_activation_failure_restores_every_published_artifact(self) -> None:
        before = self.hashes()
        (self.repository / "sample.py").write_text("value = 6\n")
        with patch.object(
            self.service, "activate_generation", side_effect=RuntimeError("activation failed")
        ):
            result = self.coordinator.refresh()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.hashes(), before)
        self.assertEqual(self.status["generation_id"], "generation-1")
        self.assertEqual(self.status["status"], "stale")

    def test_change_during_provider_refresh_rolls_back_and_remains_stale(self) -> None:
        before = self.hashes()
        (self.repository / "sample.py").write_text("value = 7\n")
        self.transport.after_write = lambda: (
            self.repository / "late.py"
        ).write_text("value = 8\n")
        result = self.coordinator.refresh()
        self.assertEqual(result["status"], "failed")
        self.assertIn("snapshot_changed_during_refresh", result["error"])
        self.assertEqual(self.hashes(), before)
        self.assertEqual(self.status["status"], "stale")

    def test_timeout_and_cancellation_preserve_previous(self) -> None:
        for exception in (TimeoutError("provider timeout"), KeyboardInterrupt()):
            before = self.hashes()
            (self.repository / "sample.py").write_text("value = 9\n")
            self.transport.exception = exception
            if isinstance(exception, KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    self.coordinator.refresh()
            else:
                result = self.coordinator.refresh()
                self.assertEqual(result["status"], "failed")
            self.assertEqual(self.hashes(), before)
            self.transport.exception = None

    def test_cross_client_query_snapshot_adopts_published_generation(self) -> None:
        other_status = dict(self.status)
        other_service = AtlasService(repository=self.repository)
        other_service.start()
        other = RefreshCoordinator(
            self.config,
            FakeTransport(self.database, self.repository, self.config.project),
            other_service,
            other_status,
        )
        try:
            (self.repository / "sample.py").write_text("value = 10\n")
            refreshed = self.coordinator.refresh()
            self.assertEqual(refreshed["status"], "refreshed")
            with other.query_snapshot() as snapshot:
                self.assertEqual(
                    snapshot["generation_id"], refreshed["generation_after"]
                )
            self.assertIsNotNone(other_service.registration_index)
        finally:
            other_service.close()

    def test_warm_query_snapshot_has_no_repository_scan_and_p95_under_gate(self) -> None:
        with self.coordinator.query_snapshot():
            pass
        samples = []
        with patch(
            "codebase_atlas.refresh_coordinator.repository_snapshot",
            side_effect=AssertionError("query-time repository scan"),
        ):
            for _ in range(50):
                started = monotonic()
                with self.coordinator.query_snapshot() as snapshot:
                    self.assertEqual(snapshot["generation_id"], "generation-1")
                samples.append((monotonic() - started) * 1000.0)
        ordered = sorted(samples)
        p95 = ordered[int(len(ordered) * 0.95) - 1]
        self.assertLessEqual(p95, 2.022, (p95, statistics.median(samples)))

    def test_restart_journal_restores_previous_generation(self) -> None:
        before = self.hashes()
        journal = RefreshRecoveryJournal.begin(self.config, "generation-1")
        journal.set_candidate("generation-2")
        for path in (
            self.database,
            registration_index_path(self.config.data_dir),
            manifest_path(self.config.data_dir),
            state_path(self.config.data_dir),
        ):
            replace_bytes(path, b"candidate")
        result = recover_refresh_transaction(self.config)
        self.assertEqual(result["action"], "restored_previous_generation")
        self.assertEqual(self.hashes(), before)
        self.assertFalse(journal_path(self.config.data_dir).exists())

    def test_restart_accepts_fully_published_generation_and_cleans_backups(self) -> None:
        journal = RefreshRecoveryJournal.begin(self.config, "generation-1")
        journal.set_candidate("generation-2")
        candidate = b"candidate-provider"
        replace_bytes(self.database, candidate)
        journal.mark_state_published()
        result = recover_refresh_transaction(self.config)
        self.assertEqual(result["action"], "accepted_published_generation")
        self.assertEqual(self.database.read_bytes(), candidate)
        self.assertFalse(journal_path(self.config.data_dir).exists())

    def test_foreign_recovery_journal_fails_closed(self) -> None:
        journal = RefreshRecoveryJournal.begin(self.config, "generation-1")
        value = json.loads(journal.path.read_text())
        value["project"] = "foreign"
        journal.path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            recover_refresh_transaction(self.config)
        self.assertTrue(journal.path.exists())
        journal.document = value
        value["project"] = self.config.project
        journal.path.write_text(json.dumps(value))
        recover_refresh_transaction(self.config)

    def test_restart_removes_only_recognized_regular_staging(self) -> None:
        candidate = self.config.data_dir / ".generation-manifest-candidate-owned.json"
        candidate.write_text("candidate")
        unrelated = self.config.data_dir / "unrelated.tmp"
        unrelated.write_text("keep")
        result = recover_refresh_transaction(self.config)
        self.assertEqual(result["removed"], 1)
        self.assertFalse(candidate.exists())
        self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
