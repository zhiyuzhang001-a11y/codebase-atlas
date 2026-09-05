from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from time import monotonic, sleep
import statistics
import unittest
from unittest.mock import patch

from codebase_atlas.config import AtlasConfig
from codebase_atlas.index_state import record_index_state, state_path
from codebase_atlas import refresh_planner as refresh_planner_module
from codebase_atlas.operations import operational_index_status
from codebase_atlas.python_registration_store import (
    load_registration_index_state,
    registration_index_path,
    stage_registration_index,
)
from codebase_atlas.refresh_coordinator import (
    RefreshCoordinator,
    SnapshotWaitTimeout,
    refresh_with_retry,
)
from codebase_atlas.lifecycle import ProjectRefreshLease
from codebase_atlas.refresh_planner import build_generation_manifest, manifest_path
from codebase_atlas.refresh_recovery import (
    ACCEPT_PUBLISHED_PHASES,
    REFRESH_PHASES,
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


def external_refresh_until_phase(
    root_text: str, repository_text: str, phase: str, marker_text: str
) -> None:
    root = Path(root_text)
    repository = Path(repository_text)
    config = AtlasConfig(
        repository,
        "python",
        root / "node",
        root / "cbm",
        root / "serena",
        root / "data",
        "project",
    )
    database = config.cache_dir / "project.db"
    status = operational_index_status(
        config.data_dir, repository, config.cache_dir, config.project
    )
    service = AtlasService(repository=repository, session_continuations=True)
    service.start()

    def observe(observed: str) -> None:
        if observed != phase:
            return
        Path(marker_text).write_text(observed, encoding="utf-8")
        while True:
            sleep(1)

    try:
        coordinator = RefreshCoordinator(
            config,
            FakeTransport(database, repository, config.project),
            service,
            status,
            phase_observer=observe,
        )
        coordinator.refresh(force_provider=True)
    finally:
        service.close()


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
        with patch(
            "codebase_atlas.refresh_planner.repository_snapshot",
            wraps=refresh_planner_module.repository_snapshot,
        ) as snapshot:
            result = self.coordinator.refresh()
        elapsed_ms = (monotonic() - started) * 1000.0
        self.assertEqual(result["status"], "current")
        self.assertFalse(result["provider_called"])
        self.assertIn("plan", result["timings_ms"])
        self.assertNotIn("provider", result["timings_ms"])
        self.assertEqual(result["duration_ms"], result["timings_ms"]["total"])
        self.assertEqual(self.transport.calls, [])
        # The 250 ms performance gate was frozen on the macOS deployment
        # platform. Windows CI still proves the cross-platform no-Provider
        # fast path, but process startup timing is not comparable there.
        if os.name != "nt":
            self.assertLessEqual(elapsed_ms, 250.0)
            self.assertLessEqual(result["duration_ms"], 250.0)
        self.assertEqual(self.status["generation_id"], "generation-1")
        self.assertEqual(snapshot.call_count, 1)

    def test_force_provider_publishes_new_generation_even_when_source_is_current(self) -> None:
        result = self.coordinator.refresh(force_provider=True)

        self.assertEqual(result["status"], "refreshed")
        self.assertEqual(len(self.transport.calls), 1)
        self.assertNotEqual(result["generation_after"], "generation-1")
        self.assertEqual(self.status["generation_id"], result["generation_after"])

    def test_refresh_persists_every_publication_phase_in_order(self) -> None:
        from codebase_atlas import refresh_recovery as recovery_module

        phases = []
        writer = recovery_module._write_atomic

        def record(path, value):
            if path == journal_path(self.config.data_dir):
                phases.append(value["phase"])
            return writer(path, value)

        with patch("codebase_atlas.refresh_recovery._write_atomic", side_effect=record):
            result = self.coordinator.refresh(force_provider=True)
        self.assertEqual(result["status"], "refreshed")
        self.assertEqual(phases, list(REFRESH_PHASES))

    def test_provider_input_change_refreshes_even_without_python_source_delta(self) -> None:
        (self.repository / "settings.json").write_text('{"enabled": true}\n')
        result = self.coordinator.refresh()

        self.assertEqual(result["status"], "refreshed")
        self.assertEqual(result["dirty_paths"], [])
        self.assertEqual(len(self.transport.calls), 1)

    def test_changed_plan_snapshot_defers_before_calling_provider(self) -> None:
        before = self.hashes()
        plan = self.coordinator.plan()
        plan["observed_snapshot"]["source_fingerprint"] = "0" * 64
        with patch.object(self.coordinator, "plan", return_value=plan):
            result = self.coordinator.refresh(force_provider=True)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "snapshot_changed_before_refresh")
        self.assertFalse(result["provider_called"])
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.hashes(), before)

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
        self.assertEqual(
            set(result["timings_ms"]),
            {
                "plan", "snapshot", "registration", "provider",
                "provider_validation", "publication", "total",
            },
        )
        self.assertGreaterEqual(result["timings_ms"]["total"], 0.0)

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
        self.assertIn("rollback", result["timings_ms"])
        self.assertEqual(result["duration_ms"], result["timings_ms"]["total"])

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

    def test_snapshot_timeout_is_diagnostic_and_does_not_adopt_old_state(self) -> None:
        owner = ProjectRefreshLease(
            self.config.data_dir, self.repository, self.config.project
        )
        self.assertTrue(owner.acquire())
        try:
            with self.assertRaises(SnapshotWaitTimeout) as raised:
                with self.coordinator.query_snapshot(timeout_ms=20):
                    self.fail("snapshot must not be yielded while writer is active")
            status = raised.exception.status()
            self.assertEqual(status["status"], "refresh_wait_timeout")
            self.assertGreaterEqual(status["coordination"]["waited_ms"], 15)
            self.assertEqual(status["coordination"]["owner"]["project"], "project")
        finally:
            owner.release()

    def test_new_coordinator_does_not_delete_live_owner_staging(self) -> None:
        live = self.config.data_dir / ".python-registrations-live-owner.json"
        live.write_text("live")
        owner = ProjectRefreshLease(
            self.config.data_dir, self.repository, self.config.project
        )
        self.assertTrue(owner.acquire())
        other_service = AtlasService(repository=self.repository)
        other_service.start()
        try:
            other = RefreshCoordinator(
                self.config,
                FakeTransport(self.database, self.repository, self.config.project),
                other_service,
                dict(self.status),
            )
            self.assertEqual(other.recovery_status["status"], "deferred")
            self.assertTrue(live.exists())
        finally:
            owner.release()
        try:
            result = other.refresh()
            self.assertEqual(result["status"], "current")
            self.assertFalse(live.exists())
            self.assertIn("recovery", result["timings_ms"])
        finally:
            other_service.close()

    def test_refresh_retry_waits_for_owner_and_reports_wall_wait(self) -> None:
        class CoalescingCoordinator:
            def __init__(self):
                self.calls = 0

            def refresh(self, **_arguments):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "status": "refresh_owned_elsewhere",
                        "duration_ms": 0.1,
                        "timings_ms": {"total": 0.1},
                    }
                return {
                    "status": "current",
                    "generation_after": "generation-2",
                    "duration_ms": 0.1,
                    "timings_ms": {"plan": 0.05, "total": 0.1},
                }

            @contextmanager
            def query_snapshot(self, *, timeout_ms):
                self.assert_timeout = timeout_ms
                threading.Event().wait(0.02)
                yield {"generation_id": "generation-2"}

        import threading

        coordinator = CoalescingCoordinator()
        result = refresh_with_retry(coordinator, timeout_ms=1000)
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["route"], "coalesced_after_owner")
        self.assertEqual(result["attempts"], 2)
        self.assertGreaterEqual(result["timings_ms"]["wait_for_owner"], 15)
        self.assertGreaterEqual(result["duration_ms"], result["timings_ms"]["wait_for_owner"])

    def test_refresh_retry_does_not_coalesce_failed_owner_old_generation(self) -> None:
        class FailedOwnerCoordinator:
            def __init__(self):
                self.calls = 0
                self.index_status = {"generation_id": "generation-1"}

            def refresh(self, **_arguments):
                self.calls += 1
                if self.calls == 1:
                    return {"status": "refresh_owned_elsewhere", "timings_ms": {}}
                return {
                    "status": "failed",
                    "error": "provider failed",
                    "generation_before": "generation-1",
                    "generation_after": "generation-1",
                    "timings_ms": {},
                }

            @contextmanager
            def query_snapshot(self, *, timeout_ms):
                yield {
                    "status": "fresh",
                    "ok": True,
                    "generation_id": "generation-1",
                }

        coordinator = FailedOwnerCoordinator()
        result = refresh_with_retry(coordinator, timeout_ms=1000)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(coordinator.calls, 2)

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

    def test_every_durable_phase_has_deterministic_recovery_semantics(self) -> None:
        original = self.database.read_bytes()
        for target_phase in REFRESH_PHASES:
            with self.subTest(phase=target_phase):
                replace_bytes(self.database, original)
                journal = RefreshRecoveryJournal.begin(self.config, "generation-1")
                if target_phase != "prepared":
                    for phase in REFRESH_PHASES[1:]:
                        if phase == "candidate_ready":
                            journal.set_candidate("generation-2")
                        else:
                            journal.advance(phase)
                        if phase == target_phase:
                            break
                replace_bytes(self.database, f"candidate:{target_phase}".encode())
                result = recover_refresh_transaction(self.config)
                expected = (
                    "accepted_published_generation"
                    if target_phase in ACCEPT_PUBLISHED_PHASES
                    else "restored_previous_generation"
                )
                self.assertEqual(result["action"], expected)
                if target_phase in ACCEPT_PUBLISHED_PHASES:
                    self.assertEqual(
                        self.database.read_bytes(), f"candidate:{target_phase}".encode()
                    )
                else:
                    self.assertEqual(self.database.read_bytes(), original)
        replace_bytes(self.database, original)

    def test_external_process_termination_recovers_every_durable_phase(self) -> None:
        context = multiprocessing.get_context("spawn")
        marker = self.root / "observed-refresh-phase"
        for target_phase in REFRESH_PHASES:
            with self.subTest(phase=target_phase):
                before = self.hashes()
                marker.unlink(missing_ok=True)
                process = context.Process(
                    target=external_refresh_until_phase,
                    args=(
                        str(self.root),
                        str(self.repository),
                        target_phase,
                        str(marker),
                    ),
                )
                process.start()
                deadline = monotonic() + 20
                while (
                    not marker.exists()
                    and process.is_alive()
                    and monotonic() < deadline
                ):
                    sleep(0.02)
                if not marker.exists():
                    process.terminate()
                    process.join(timeout=5)
                    self.fail(
                        f"external refresh did not reach {target_phase}; "
                        f"exitcode={process.exitcode}"
                    )
                document = json.loads(
                    journal_path(self.config.data_dir).read_text(encoding="utf-8")
                )
                self.assertEqual(document["phase"], target_phase)
                candidate = document.get("generation_after")
                process.terminate()
                process.join(timeout=10)
                self.assertFalse(process.is_alive())

                recovered = recover_refresh_transaction(self.config)
                expected_action = (
                    "accepted_published_generation"
                    if target_phase in ACCEPT_PUBLISHED_PHASES
                    else "restored_previous_generation"
                )
                self.assertEqual(recovered["action"], expected_action)
                if target_phase in ACCEPT_PUBLISHED_PHASES:
                    published = json.loads(
                        manifest_path(self.config.data_dir).read_text(encoding="utf-8")
                    )
                    self.assertEqual(published["generation_id"], candidate)
                else:
                    self.assertEqual(self.hashes(), before)
                clean = recover_refresh_transaction(self.config)
                self.assertEqual(clean, {
                    "status": "clean",
                    "action": "removed_owned_staging",
                    "removed": 0,
                })

    def test_journal_rejects_non_monotonic_and_unknown_phases(self) -> None:
        journal = RefreshRecoveryJournal.begin(self.config, "generation-1")
        with self.assertRaisesRegex(ValueError, "did not advance"):
            journal.advance("prepared")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            journal.advance("not-a-phase")
        journal.rollback()

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
        orphan = self.config.data_dir / ".refresh-recovery-state-owned.bak"
        orphan.write_text("backup")
        provider_orphan = self.config.cache_dir / ".refresh-recovery-provider-owned.bak"
        provider_orphan.write_text("backup")
        unrelated = self.config.data_dir / "unrelated.tmp"
        unrelated.write_text("keep")
        result = recover_refresh_transaction(self.config)
        self.assertEqual(result["removed"], 3)
        self.assertFalse(candidate.exists())
        self.assertFalse(orphan.exists())
        self.assertFalse(provider_orphan.exists())
        self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
