"""Single-process coordinator for same-connection transactional refresh."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import stat
import tempfile
import threading
from time import monotonic
from typing import Any, Callable

from .config import AtlasConfig
from .index_state import record_index_state, repository_snapshot, state_path
from .maintenance import inspect_provider_database_at
from .lifecycle import ProjectRefreshLease
from .operations import operational_index_status
from .provider_transport import CodebaseMemoryMcpTransport
from .python_registration_store import (
    StagedRegistrationIndex,
    load_registration_index_state,
    stage_registration_index,
)
from .refresh_planner import (
    RefreshPlanError,
    StagedGenerationManifest,
    build_generation_manifest,
    generation_artifact_identity,
    manifest_path,
    load_generation_manifest,
    plan_refresh,
    stage_generation_manifest_candidate,
)
from .refresh_recovery import (
    RefreshRecoveryJournal,
    journal_path,
    recover_refresh_transaction,
)
from .service import AtlasService


@contextmanager
def _measure_phase(timings_ms: dict[str, float], name: str):
    started = monotonic()
    try:
        yield
    finally:
        timings_ms[name] = timings_ms.get(name, 0.0) + (
            monotonic() - started
        ) * 1000.0


def _with_timings(
    payload: dict[str, Any], started: float, timings_ms: dict[str, float]
) -> dict[str, Any]:
    total = (monotonic() - started) * 1000.0
    payload["duration_ms"] = total
    payload["timings_ms"] = {**timings_ms, "total": total}
    return payload


def _snapshot_file(path: Path) -> bytes | None:
    if not path.exists():
        return None
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise RefreshPlanError(f"published state is not a safe regular file: {path.name}")
    return path.read_bytes()


def _restore_file(path: Path, payload: bytes | None) -> None:
    if payload is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{path.name}-restore-", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(raw_temporary, path)
    finally:
        if os.path.exists(raw_temporary):
            os.unlink(raw_temporary)


@dataclass
class ProviderDatabaseBackup:
    destination: Path
    backup: Path | None
    existed: bool

    @classmethod
    def create(cls, destination: Path) -> "ProviderDatabaseBackup":
        if not destination.exists():
            return cls(destination, None, False)
        metadata = os.lstat(destination)
        if not stat.S_ISREG(metadata.st_mode):
            raise RefreshPlanError("Provider database is not a safe regular file")
        descriptor, raw_backup = tempfile.mkstemp(
            prefix=".provider-generation-backup-",
            suffix=".db",
            dir=destination.parent,
        )
        os.close(descriptor)
        os.unlink(raw_backup)
        backup = Path(raw_backup)
        os.link(destination, backup)
        return cls(destination, backup, True)

    def rollback(self) -> None:
        if self.existed:
            if self.backup is None:
                raise OSError("Provider generation backup is unavailable")
            os.replace(self.backup, self.destination)
            self.backup = None
        else:
            self.destination.unlink(missing_ok=True)

    def commit(self) -> bool:
        try:
            if self.backup is not None:
                self.backup.unlink(missing_ok=True)
                self.backup = None
            return True
        except OSError:
            return False


class SnapshotWaitTimeout(TimeoutError):
    """A query could not bind to a published generation within its deadline."""

    def __init__(
        self, *, timeout_ms: int, waited_ms: float, owner: dict[str, Any]
    ) -> None:
        super().__init__("timed out waiting for the active project refresh")
        self.timeout_ms = timeout_ms
        self.waited_ms = waited_ms
        self.owner = owner

    def status(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": "refresh_wait_timeout",
            "ok": False,
            "reason": "active_project_refresh_did_not_publish_before_deadline",
            "coordination": {
                "waited_ms": self.waited_ms,
                "timeout_ms": self.timeout_ms,
                "owner": self.owner,
            },
            "next_action": "retry after the active refresh completes",
        }


class RefreshCoordinator:
    """Serialize one active MCP's explicit refresh transactions."""

    def __init__(
        self,
        config: AtlasConfig,
        transport: CodebaseMemoryMcpTransport,
        service: AtlasService,
        index_status: dict[str, Any],
        *,
        phase_observer: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.service = service
        self.index_status = index_status
        self._phase_observer = phase_observer
        self._lock = threading.Lock()
        self._lease = ProjectRefreshLease(
            config.data_dir, config.repository, config.project
        )
        # Recovery deletes recognized orphan staging. It must never run while a
        # different process owns a live transaction using those same prefixes.
        if self._lease.acquire():
            try:
                self.recovery_status = recover_refresh_transaction(config)
            finally:
                self._lease.release()
        else:
            self.recovery_status = {
                "status": "deferred",
                "action": "wait_for_active_refresh",
            }
        self._observed_manifest_identity: tuple[int, int, int] | None = None
        self._observed_generation_id: str | None = None

    def _observe_phase(self, phase: str) -> None:
        if self._phase_observer is not None:
            self._phase_observer(phase)

    def _advance_recovery(
        self, recovery: RefreshRecoveryJournal, phase: str
    ) -> None:
        recovery.advance(phase)
        self._observe_phase(phase)

    def plan(self) -> dict[str, Any]:
        return plan_refresh(
            self.config.data_dir,
            self.config.repository,
            self.config.project,
            self.config.language,
        )

    @contextmanager
    def query_snapshot(self, *, timeout_ms: int = 30_000):
        """Bind a query to one fully published cross-process generation."""
        started = monotonic()
        lease = ProjectRefreshLease(
            self.config.data_dir, self.config.repository, self.config.project
        )
        if not lease.acquire_shared(timeout_seconds=timeout_ms / 1000.0):
            raise SnapshotWaitTimeout(
                timeout_ms=timeout_ms,
                waited_ms=(monotonic() - started) * 1000.0,
                owner=lease.owner_status(),
            )
        try:
            if journal_path(self.config.data_dir).exists():
                raise RefreshPlanError("refresh_recovery_required")
            path = manifest_path(self.config.data_dir)
            try:
                metadata = path.stat()
                identity = (metadata.st_ino, metadata.st_mtime_ns, metadata.st_size)
            except FileNotFoundError:
                identity = None
            manifest = None
            if identity != self._observed_manifest_identity:
                manifest = load_generation_manifest(
                    self.config.data_dir, self.config.repository, self.config.project
                )
                self._observed_manifest_identity = identity
                self._observed_generation_id = (
                    str(manifest["generation_id"]) if manifest else None
                )
            generation_id = self._observed_generation_id
            if generation_id and generation_id != self.index_status.get("generation_id"):
                if manifest is None:
                    manifest = load_generation_manifest(
                        self.config.data_dir, self.config.repository, self.config.project
                    )
                self._replace_status(generation_id, reset_structural=True)
            snapshot = dict(self.index_status)
            snapshot["coordination"] = {
                "status": "generation_bound",
                "snapshot_wait_ms": (monotonic() - started) * 1000.0,
                "timeout_ms": timeout_ms,
            }
            yield snapshot
        finally:
            lease.release()

    def _replace_status(
        self, generation_id: str, *, reset_structural: bool = False
    ) -> None:
        preserved = {
            key: self.index_status[key]
            for key in ("identity", "auto_update", "software_update")
            if key in self.index_status
        }
        refreshed = operational_index_status(
            self.config.data_dir,
            self.config.repository,
            self.config.cache_dir,
            self.config.project,
        )
        refreshed.update(preserved)
        refreshed["generation_id"] = generation_id
        if self.config.language == "python":
            source_fingerprint = refreshed.get("source", {}).get("source_fingerprint")
            registration_index, registration_health = load_registration_index_state(
                self.config.data_dir,
                self.config.repository,
                self.config.project,
                source_fingerprint,
            )
            self.service.activate_generation(
                registration_index, reset_structural=reset_structural
            )
            refreshed["python_registrations"] = registration_health
        else:
            self.service.activate_generation(None, reset_structural=reset_structural)
        self.index_status.clear()
        self.index_status.update(refreshed)

    def _replace_failure_status(self, generation_id: str | None) -> None:
        preserved = {
            key: self.index_status[key]
            for key in ("identity", "auto_update", "software_update", "python_registrations")
            if key in self.index_status
        }
        refreshed = operational_index_status(
            self.config.data_dir,
            self.config.repository,
            self.config.cache_dir,
            self.config.project,
        )
        refreshed.update(preserved)
        if generation_id:
            refreshed["generation_id"] = generation_id
        self.index_status.clear()
        self.index_status.update(refreshed)

    def refresh(
        self,
        *,
        mode: str = "fast",
        timeout_ms: int = 300_000,
        force_provider: bool = False,
    ) -> dict[str, Any]:
        if mode not in {"fast", "moderate", "full"}:
            raise ValueError("mode must be fast, moderate, or full")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 1 <= timeout_ms <= 300_000:
            raise ValueError("timeout_ms must be an integer between 1 and 300000")
        started = monotonic()
        timings_ms: dict[str, float] = {}
        if not self._lock.acquire(blocking=False):
            return _with_timings({
                "schema_version": 1,
                "status": "refresh_in_progress",
                "route": "same_connection",
                "previous_generation_preserved": True,
                "next_action": "wait for the active refresh and call project_status",
            }, started, timings_ms)
        lease_acquired = False
        plan: dict[str, Any] = {}
        staged_registration: StagedRegistrationIndex | None = None
        staged_manifest: StagedGenerationManifest | None = None
        provider_backup: ProviderDatabaseBackup | None = None
        recovery: RefreshRecoveryJournal | None = None
        provider_called = False
        state_before: bytes | None = None
        manifest_before: bytes | None = None
        generation_before: str | None = None
        try:
            if not self._lease.acquire():
                return _with_timings({
                    "schema_version": 1,
                    "status": "refresh_owned_elsewhere",
                    "route": "same_project_lease",
                    "previous_generation_preserved": True,
                    "next_action": "wait for the project owner and retry project_status",
                }, started, timings_ms)
            lease_acquired = True
            if (
                self.recovery_status.get("status") == "deferred"
                or journal_path(self.config.data_dir).exists()
            ):
                with _measure_phase(timings_ms, "recovery"):
                    self.recovery_status = recover_refresh_transaction(self.config)
            with _measure_phase(timings_ms, "plan"):
                plan = self.plan()
            generation_before = plan.get("base_generation")
            provider_current = bool(
                self.index_status.get("provider_database", {}).get("ok")
            )
            if (
                plan.get("status") == "planned"
                and not plan.get("dirty_paths")
                and not plan.get("provider_inputs_changed")
                and provider_current
                and not force_provider
            ):
                if generation_before and (
                    self.index_status.get("generation_id") != generation_before
                    or self.index_status.get("status") != "fresh"
                    or not self.index_status.get("ok")
                ):
                    self._replace_status(
                        str(generation_before), reset_structural=True
                    )
                return _with_timings({
                    "schema_version": 1,
                    "status": "current",
                    "route": "same_connection_noop",
                    "generation_before": generation_before,
                    "generation_after": generation_before,
                    "dirty_paths": [],
                    "provider_inputs_changed": False,
                    "provider_called": False,
                    "previous_generation_preserved": True,
                    "next_action": "continue querying the current generation",
                }, started, timings_ms)

            with _measure_phase(timings_ms, "snapshot"):
                source_before = repository_snapshot(self.config.repository)
            if source_before.kind != "git" or not source_before.fingerprint:
                raise RefreshPlanError("repository snapshot is unavailable")
            observed = plan.get("observed_snapshot", {})
            if observed and (
                observed.get("source_fingerprint") != source_before.fingerprint
                or observed.get("source_head") != source_before.head
            ):
                raise RefreshPlanError("snapshot_changed_before_refresh")
            previous_fingerprint = (
                plan.get("base_source_fingerprint")
                or self.index_status.get("source", {}).get("source_fingerprint")
                or self.index_status.get("source", {}).get("indexed_source_fingerprint")
            )
            if self.config.language == "python":
                with _measure_phase(timings_ms, "registration"):
                    staged_registration = stage_registration_index(
                        self.config.data_dir,
                        self.config.repository,
                        self.config.project,
                        source_before.fingerprint,
                        previous_source_fingerprint=str(previous_fingerprint) if previous_fingerprint else None,
                    )

            database = self.config.cache_dir / f"{self.config.project}.db"
            if database.resolve().parent != self.config.cache_dir.resolve():
                raise RefreshPlanError("Provider project identity is not path-safe")
            database.parent.mkdir(parents=True, exist_ok=True)
            state_before = _snapshot_file(state_path(self.config.data_dir))
            manifest_before = _snapshot_file(manifest_path(self.config.data_dir))
            recovery = RefreshRecoveryJournal.begin(
                self.config, generation_before
            )
            self._observe_phase("prepared")
            provider_backup = ProviderDatabaseBackup.create(database)

            provider_called = True
            with _measure_phase(timings_ms, "provider"):
                provider = self.transport.call(
                    "index_repository",
                    {
                        "repo_path": str(self.config.repository),
                        "name": self.config.project,
                        "mode": mode,
                    },
                    timeout_ms=timeout_ms,
                )
            self._advance_recovery(recovery, "provider_indexed")
            self.service.mark_structural_started()
            if provider.get("status") != "indexed" or provider.get("project") != self.config.project:
                raise RefreshPlanError("Provider returned an invalid project generation")
            with _measure_phase(timings_ms, "provider_validation"):
                health = inspect_provider_database_at(
                    self.config.cache_dir,
                    self.config.project,
                    self.config.repository,
                    deep=True,
                )
            if not health.get("ok") or health.get("quick_check") != ["ok"]:
                raise RefreshPlanError(f"Provider generation validation failed: {health.get('reason')}")
            self._advance_recovery(recovery, "provider_validated")
            with _measure_phase(timings_ms, "snapshot"):
                source_after = repository_snapshot(self.config.repository)
            if (
                source_after.kind != "git"
                or source_after.fingerprint != source_before.fingerprint
                or source_after.head != source_before.head
            ):
                raise RefreshPlanError("snapshot_changed_during_refresh")

            generation_after = secrets.token_hex(16)
            recovery.set_candidate(generation_after)
            self._observe_phase("candidate_ready")
            provider_identity = generation_artifact_identity(database)
            sidecar_identity = (
                generation_artifact_identity(staged_registration.temporary)
                if staged_registration is not None
                else {"status": "not_applicable"}
            )
            candidate = build_generation_manifest(
                self.config.repository,
                self.config.project,
                self.config.language,
                generation_id=generation_after,
                provider_identity=provider_identity,
                sidecar_identity=sidecar_identity,
                created_at=f"generation:{generation_after}",
            )
            if candidate["source_fingerprint"] != source_before.fingerprint:
                raise RefreshPlanError("snapshot_changed_during_refresh")
            staged_manifest = stage_generation_manifest_candidate(
                self.config.data_dir,
                candidate,
                self.config.repository,
                self.config.project,
            )

            with _measure_phase(timings_ms, "publication"):
                if staged_registration is not None:
                    staged_registration.publish()
                    load_registration_index_state(
                        self.config.data_dir,
                        self.config.repository,
                        self.config.project,
                        source_before.fingerprint,
                    )
                    self._advance_recovery(recovery, "sidecar_published")
                staged_manifest.publish(manifest_path(self.config.data_dir))
                self._advance_recovery(recovery, "manifest_published")
                record_index_state(
                    self.config.data_dir,
                    self.config.repository,
                    self.config.project,
                    mode,
                    snapshot=source_after,
                )
                recovery.mark_state_published()
                self._observe_phase("state_published")
                self._replace_status(generation_after)
                self._advance_recovery(recovery, "generation_activated")

                self._advance_recovery(recovery, "committing")
                if staged_registration is not None:
                    staged_registration.commit()
                staged_manifest.commit()
                provider_backup.commit()
                recovery.commit()
            recovery = None
            return _with_timings({
                "schema_version": 1,
                "status": "refreshed",
                "route": "same_connection",
                "generation_before": generation_before,
                "generation_after": generation_after,
                "dirty_paths": plan.get("dirty_paths", []),
                "provider_inputs_changed": plan.get("provider_inputs_changed", True),
                "changes": plan.get("changes", {}),
                "full_fallback_reason": plan.get("full_fallback_reason", ""),
                "provider_called": True,
                "provider": {
                    "status": provider.get("status"),
                    "project": provider.get("project"),
                    "nodes": provider.get("nodes"),
                    "edges": provider.get("edges"),
                },
                "previous_generation_preserved": False,
                "next_action": "continue querying the new generation",
            }, started, timings_ms)
        except BaseException as exc:
            rollback_errors: list[str] = []
            with _measure_phase(timings_ms, "rollback"):
                for action in (
                    (lambda: staged_manifest.rollback()) if staged_manifest is not None else None,
                    (lambda: staged_registration.rollback()) if staged_registration is not None else None,
                    (lambda: _restore_file(state_path(self.config.data_dir), state_before)) if provider_called else None,
                    (lambda: _restore_file(manifest_path(self.config.data_dir), manifest_before)) if manifest_before is not None else None,
                    (lambda: provider_backup.rollback()) if provider_backup is not None and provider_called else None,
                ):
                    if action is None:
                        continue
                    try:
                        action()
                    except BaseException as rollback_exc:
                        rollback_errors.append(str(rollback_exc))
                if recovery is not None:
                    try:
                        recovery.rollback()
                        recovery = None
                    except BaseException as rollback_exc:
                        rollback_errors.append(str(rollback_exc))
            self._replace_failure_status(generation_before)
            if isinstance(exc, KeyboardInterrupt):
                raise
            return _with_timings({
                "schema_version": 1,
                "status": "failed",
                "route": "same_connection",
                "generation_before": generation_before,
                "generation_after": generation_before,
                "dirty_paths": plan.get("dirty_paths", []),
                "provider_inputs_changed": plan.get("provider_inputs_changed", True),
                "provider_called": provider_called,
                "error": str(exc),
                "provider_stderr_tail": getattr(self.transport, "stderr_text", "")[-2000:],
                "rollback_errors": rollback_errors,
                "previous_generation_preserved": not rollback_errors,
                "next_action": "fix the reported cause and retry refresh_index",
            }, started, timings_ms)
        finally:
            process = getattr(self.transport, "process", None)
            if process is not None and process.poll() is None:
                self.service.mark_structural_started()
            if staged_manifest is not None:
                staged_manifest.close()
            if staged_registration is not None:
                staged_registration.close()
            if provider_backup is not None:
                provider_backup.commit()
            if lease_acquired:
                self._lease.release()
            self._lock.release()


def refresh_with_retry(
    coordinator: RefreshCoordinator,
    *,
    mode: str = "fast",
    timeout_ms: int = 300_000,
    force_provider: bool = False,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    """Coalesce behind an owner and retry snapshot races within one deadline."""
    if max_attempts is not None and (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or max_attempts < 1
    ):
        raise ValueError("max_attempts must be a positive integer or None")
    started = monotonic()
    deadline = started + timeout_ms / 1000.0
    aggregate_timings: dict[str, float] = {}
    attempt = 0
    last_result: dict[str, Any] = {}
    while monotonic() < deadline and (max_attempts is None or attempt < max_attempts):
        attempt += 1
        coordinator_status = getattr(coordinator, "index_status", {})
        generation_before_attempt = (
            coordinator_status.get("generation_id")
            if isinstance(coordinator_status, dict) else None
        )
        remaining_ms = (
            timeout_ms
            if attempt == 1
            else max(1, int((deadline - monotonic()) * 1000.0))
        )
        refresh_arguments: dict[str, Any] = {
            "mode": mode,
            "timeout_ms": remaining_ms,
        }
        if force_provider:
            refresh_arguments["force_provider"] = True
        result = coordinator.refresh(**refresh_arguments)
        last_result = result
        result["attempts"] = attempt
        for name, value in result.get("timings_ms", {}).items():
            if name != "total" and isinstance(value, (int, float)):
                aggregate_timings[name] = aggregate_timings.get(name, 0.0) + float(value)
        if result.get("status") in {"refresh_in_progress", "refresh_owned_elsewhere"}:
            remaining_ms = max(1, int((deadline - monotonic()) * 1000.0))
            wait_started = monotonic()
            try:
                with coordinator.query_snapshot(timeout_ms=remaining_ms) as published:
                    published = dict(published or {})
            except SnapshotWaitTimeout as exc:
                aggregate_timings["wait_for_owner"] = (
                    aggregate_timings.get("wait_for_owner", 0.0) + exc.waited_ms
                )
                total = (monotonic() - started) * 1000.0
                return {
                    **exc.status(),
                    "route": "same_project_lease",
                    "attempts": attempt,
                    "previous_generation_preserved": True,
                    "duration_ms": total,
                    "timings_ms": {**aggregate_timings, "total": total},
                }
            aggregate_timings["wait_for_owner"] = (
                aggregate_timings.get("wait_for_owner", 0.0)
                + (monotonic() - wait_started) * 1000.0
            )
            published_generation = published.get("generation_id")
            if (
                published.get("ok")
                and published_generation
                and published_generation != generation_before_attempt
            ):
                total = (monotonic() - started) * 1000.0
                return {
                    "schema_version": 1,
                    "status": "current",
                    "route": "coalesced_after_owner",
                    "generation_before": published_generation,
                    "generation_after": published_generation,
                    "dirty_paths": [],
                    "provider_inputs_changed": False,
                    "provider_called": False,
                    "previous_generation_preserved": True,
                    "attempts": attempt,
                    "duration_ms": total,
                    "timings_ms": {**aggregate_timings, "total": total},
                    "next_action": "continue querying the published generation",
                }
            if (max_attempts is None or attempt < max_attempts) and monotonic() < deadline:
                continue
        elif result.get("error") in {
            "snapshot_changed_before_refresh",
            "snapshot_changed_during_refresh",
        } and (max_attempts is None or attempt < max_attempts) and monotonic() < deadline:
            continue
        total = (monotonic() - started) * 1000.0
        result["duration_ms"] = total
        result["timings_ms"] = {**aggregate_timings, "total": total}
        if aggregate_timings.get("wait_for_owner") and result.get("status") in {
            "current", "refreshed"
        }:
            result["route"] = "coalesced_after_owner"
        return result
    total = (monotonic() - started) * 1000.0
    exhausted = max_attempts is not None and attempt >= max_attempts
    return {
        "schema_version": 1,
        "status": "refresh_wait_timeout" if not exhausted else "refresh_retry_exhausted",
        "ok": False,
        "reason": (
            "refresh_retry_limit_reached"
            if exhausted else "refresh_deadline_exhausted"
        ),
        "route": "same_project_lease",
        "attempts": attempt,
        "generation_before": last_result.get("generation_before"),
        "generation_after": None,
        "previous_generation_preserved": True,
        "duration_ms": total,
        "timings_ms": {**aggregate_timings, "total": total},
        "next_action": "retry after the active refresh completes",
    }
