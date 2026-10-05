"""Generation-bound same-connection refresh for internal Rust candidates."""
from contextlib import contextmanager
import threading
import os
import stat
from time import monotonic

from .lifecycle import ProjectRefreshLease
from .operations import operational_index_status
from .refresh_coordinator import SnapshotWaitTimeout
from .refresh_planner import RefreshPlanError, plan_refresh
from .rust_project import _load_index, load_rust_service
from .rust_refresh import RustRefreshCoordinator
from .rust_refresh_recovery import rust_refresh_journal_path
from .rust_installation import runtime_from_receipt
from .rust_scanner_installation import verified_scanner


class RustMcpRefreshCoordinator:
    def __init__(self, config, service, index_status, *, config_path=None):
        self.config = config
        self.service = service
        self.index_status = index_status
        self._lock = threading.RLock()
        self._config_path = config_path
        self._config_snapshot = self._read_config_snapshot() if config_path is not None else None

    def _read_config_snapshot(self):
        metadata = os.lstat(self._config_path)
        if not stat.S_ISREG(metadata.st_mode):
            raise RefreshPlanError("rust_project_config_unsafe")
        return (metadata.st_dev, metadata.st_ino, self._config_path.read_bytes())

    def _check_config(self):
        if self._config_path is not None and self._read_config_snapshot() != self._config_snapshot:
            raise RefreshPlanError("rust_project_config_changed")

    def plan(self):
        self._check_config()
        return plan_refresh(self.config.data_dir, self.config.repository,
                            self.config.project, "rust")

    def _bind(self):
        self._check_config()
        generation, _syntax = _load_index(self.config.data_dir, self.config.repository, self.config.project)
        if generation["generation_id"] != self.service.rust_provider.generation["generation_id"]:
            # Construct/validate before replacing the live binding. Closing the
            # old owned child must succeed before the new generation is used.
            candidate = load_rust_service(self.config, session_continuations=self.service.session_continuations)
            started = self.service.started
            self.service.close()
            self.service.rust_provider = candidate.rust_provider
            self.service.rust_syntax_index = candidate.rust_syntax_index
            if started:
                self.service.start()
        preserved = {key: self.index_status[key] for key in (
            "identity", "auto_update", "software_update") if key in self.index_status}
        status = operational_index_status(self.config.data_dir, self.config.repository,
                                          self.config.cache_dir, self.config.project, language="rust")
        self.index_status.clear()
        self.index_status.update(status | preserved | {"generation_id": generation["generation_id"]})

    @contextmanager
    def query_snapshot(self, *, timeout_ms=30000):
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 1 <= timeout_ms <= 300000:
            raise ValueError("timeout_ms must be an integer between 1 and 300000")
        started = monotonic()
        lease = ProjectRefreshLease(self.config.data_dir, self.config.repository, self.config.project)
        if not self._lock.acquire(timeout=timeout_ms / 1000):
            raise SnapshotWaitTimeout(timeout_ms=timeout_ms, waited_ms=(monotonic() - started) * 1000,
                                      owner=lease.owner_status())
        try:
            budget = timeout_ms / 1000 - (monotonic() - started)
            if budget <= 0 or not lease.acquire_shared(timeout_seconds=budget):
                raise SnapshotWaitTimeout(timeout_ms=timeout_ms, waited_ms=(monotonic() - started) * 1000,
                                          owner=lease.owner_status())
            if rust_refresh_journal_path(self.config.data_dir).exists():
                raise RefreshPlanError("rust_refresh_recovery_required")
            self._bind()
            elapsed = (monotonic() - started) * 1000
            if elapsed >= timeout_ms:
                raise SnapshotWaitTimeout(timeout_ms=timeout_ms, waited_ms=elapsed, owner=lease.owner_status())
            yield dict(self.index_status) | {"coordination": {
                "status": "generation_bound", "snapshot_wait_ms": elapsed, "timeout_ms": timeout_ms}}
        finally:
            lease.release()
            self._lock.release()

    def refresh(self, *, mode="fast", timeout_ms=300000, force_provider=False):
        if mode not in {"fast", "moderate", "full"}:
            raise ValueError("mode must be fast, moderate, or full")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 1 <= timeout_ms <= 300000:
            raise ValueError("timeout_ms must be an integer between 1 and 300000")
        started = monotonic()
        if not self._lock.acquire(blocking=False):
            return {"status": "refresh_in_progress", "previous_generation_preserved": True}
        try:
            self._check_config()
            runtime = runtime_from_receipt(self.config.rust_runtime_receipt, repository=self.config.repository)
            scanner = verified_scanner(self.config.repository)
            def preflight():
                scanner.verify()
                return runtime.environment(self.config.repository)
            preflight()
            budget = timeout_ms / 1000 - (monotonic() - started)
            if budget <= 0:
                raise TimeoutError("Rust refresh preparation exhausted deadline")
            result = RustRefreshCoordinator(self.config, scanner.path, mode=mode,
                                             execution_preflight=preflight,
                                             publication_preflight=self._check_config).refresh(timeout_seconds=budget)
            result["route"] = "same_connection_rust"
            return result
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            return {"status": "failed", "error": str(exc), "previous_generation_preserved": True}
        finally:
            self._lock.release()
