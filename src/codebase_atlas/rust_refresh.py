"""Internal transactional refresh for the product-disabled Rust T0/T1 path."""

from __future__ import annotations

import secrets
import threading
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from .config import AtlasConfig
from .index_state import record_index_state, repository_snapshot
from .lifecycle import ProjectRefreshLease
from .providers.rust_syntax import (
    RustSyntaxProvider,
    StagedRustSyntaxPointer,
    StagedRustSyntaxShard,
    load_rust_syntax_pointer,
    rust_syntax_pointer_path,
    rust_syntax_shard_identity,
    stage_rust_syntax_pointer,
)
from .refresh_planner import (
    RefreshPlanError,
    StagedGenerationManifest,
    build_generation_manifest,
    manifest_path,
    plan_refresh,
    stage_generation_manifest_candidate,
)
from .rust_refresh_recovery import (
    RustRefreshRecoveryJournal,
    recover_rust_refresh_transaction,
)


PhaseObserver = Callable[[str], None]


class RustRefreshCoordinator:
    """Publish one exact Rust generation without exposing a product route."""

    def __init__(
        self,
        config: AtlasConfig,
        scanner: Path,
        *,
        runner=None,
        phase_observer: PhaseObserver | None = None,
    ) -> None:
        if config.language != "rust":
            raise ValueError("Rust refresh coordinator requires language=rust")
        if not config.project:
            raise ValueError("Rust refresh coordinator requires an exact project identity")
        self.config = config
        provider_arguments: dict[str, Any] = {}
        if runner is not None:
            provider_arguments["runner"] = runner
        self.provider = RustSyntaxProvider(
            scanner, config.repository, config.data_dir, config.project,
            **provider_arguments,
        )
        self._lease = ProjectRefreshLease(
            config.data_dir, config.repository, config.project
        )
        self._lock = threading.Lock()
        self._phase_observer = phase_observer

    def _observe(self, phase: str) -> None:
        if self._phase_observer is not None:
            self._phase_observer(phase)

    def refresh(self, *, timeout_seconds: float = 120.0) -> dict[str, Any]:
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 0 < timeout_seconds <= 300
        ):
            raise ValueError("timeout_seconds must be between 0 and 300")
        started = monotonic()
        if not self._lock.acquire(blocking=False):
            return {
                "schema_version": 1,
                "status": "refresh_in_progress",
                "previous_generation_preserved": True,
            }
        lease_acquired = False
        staged_shard: StagedRustSyntaxShard | None = None
        staged_manifest: StagedGenerationManifest | None = None
        staged_pointer: StagedRustSyntaxPointer | None = None
        recovery: RustRefreshRecoveryJournal | None = None
        generation_before: str | None = None
        generation_after: str | None = None
        plan: dict[str, Any] = {}
        accepted = False
        try:
            if not self._lease.acquire():
                return {
                    "schema_version": 1,
                    "status": "refresh_owned_elsewhere",
                    "previous_generation_preserved": True,
                }
            lease_acquired = True
            recovery_status = recover_rust_refresh_transaction(self.config)
            plan = plan_refresh(
                self.config.data_dir,
                self.config.repository,
                self.config.project,
                "rust",
            )
            generation_before = plan.get("base_generation")
            generation_after = secrets.token_hex(16)
            provisional = build_generation_manifest(
                self.config.repository,
                self.config.project,
                "rust",
                generation_id=generation_after,
                provider_identity={"status": "candidate", "fact_tier": "T1"},
                sidecar_identity={"status": "candidate", "kind": "rust_syntax_pointer"},
                created_at=f"generation:{generation_after}",
            )
            staged_shard = self.provider.stage(
                provisional, timeout_seconds=timeout_seconds
            )
            artifact = rust_syntax_shard_identity(staged_shard)
            provider_identity = {
                **staged_shard.document["provider_identity"],
                "fact_tier": "T1",
                "completeness": staged_shard.document["completeness"],
                "artifact": artifact,
            }
            pointer_identity = {
                "kind": "rust_syntax_pointer",
                "schema_version": 1,
                "path": str(rust_syntax_pointer_path(self.config.data_dir)),
            }
            candidate = build_generation_manifest(
                self.config.repository,
                self.config.project,
                "rust",
                generation_id=generation_after,
                provider_identity=provider_identity,
                sidecar_identity=pointer_identity,
                created_at=f"generation:{generation_after}",
            )
            for field in ("source_fingerprint", "source_head", "files", "source_scope"):
                if candidate[field] != provisional[field]:
                    raise RefreshPlanError("snapshot_changed_during_rust_scan")
            source_after = repository_snapshot(self.config.repository)
            if (
                source_after.kind != "git"
                or source_after.fingerprint != candidate["source_fingerprint"]
                or source_after.head != candidate["source_head"]
            ):
                raise RefreshPlanError("snapshot_changed_during_rust_scan")
            staged_manifest = stage_generation_manifest_candidate(
                self.config.data_dir,
                candidate,
                self.config.repository,
                self.config.project,
            )
            staged_pointer = stage_rust_syntax_pointer(
                self.config.data_dir,
                self.config.repository,
                self.config.project,
                generation_after,
                artifact,
            )
            recovery = RustRefreshRecoveryJournal.begin(
                self.config,
                generation_before,
                generation_after,
                staged_shard.destination,
            )
            self._observe("prepared")

            staged_shard.publish()
            recovery.advance("shard_published")
            self._observe("shard_published")
            staged_manifest.publish(manifest_path(self.config.data_dir))
            recovery.advance("manifest_published")
            self._observe("manifest_published")
            staged_pointer.publish()
            pointer = load_rust_syntax_pointer(
                self.config.data_dir,
                self.config.repository,
                self.config.project,
            )
            if pointer is None or pointer["generation_id"] != generation_after:
                raise RefreshPlanError("Rust syntax pointer publication validation failed")
            recovery.advance("pointer_published")
            self._observe("pointer_published")
            record_index_state(
                self.config.data_dir,
                self.config.repository,
                self.config.project,
                "rust-syntax-t1",
                snapshot=source_after,
            )
            self._observe("state_replaced")
            recovery.advance("state_published")
            accepted = True
            cleanup_errors: list[str] = []
            try:
                recovery.advance("committing")
            except OSError as cleanup_exc:
                cleanup_errors.append(str(cleanup_exc))
            if not staged_manifest.commit():
                cleanup_errors.append("generation manifest backup cleanup failed")
            try:
                recovery.commit()
                recovery = None
            except OSError as cleanup_exc:
                cleanup_errors.append(str(cleanup_exc))
            return {
                "schema_version": 1,
                "status": "refreshed",
                "route": "internal_rust_syntax",
                "generation_before": generation_before,
                "generation_after": generation_after,
                "dirty_paths": plan.get("dirty_paths", []),
                "source_scope_status": candidate["source_scope"]["status"],
                "files": len(candidate["files"]),
                "facts": len(staged_shard.document["facts"]),
                "identifier_candidates": staged_shard.document[
                    "identifier_candidate_count"
                ],
                "recovery": recovery_status,
                "cleanup_errors": cleanup_errors,
                "recovery_pending": recovery is not None,
                "previous_generation_preserved": False,
                "duration_ms": (monotonic() - started) * 1000.0,
            }
        except BaseException as exc:
            rollback_errors: list[str] = []
            if recovery is not None:
                try:
                    if accepted:
                        recovery.commit()
                    else:
                        recovery.rollback()
                    recovery = None
                except BaseException as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            if isinstance(exc, KeyboardInterrupt):
                raise
            return {
                "schema_version": 1,
                "status": "failed",
                "route": "internal_rust_syntax",
                "generation_before": generation_before,
                "generation_after": generation_after if accepted else generation_before,
                "error": str(exc),
                "rollback_errors": rollback_errors,
                "previous_generation_preserved": not accepted and not rollback_errors,
                "duration_ms": (monotonic() - started) * 1000.0,
            }
        finally:
            if staged_pointer is not None:
                staged_pointer.close()
            if staged_manifest is not None:
                staged_manifest.close()
            if staged_shard is not None:
                staged_shard.close()
            if lease_acquired:
                self._lease.release()
            self._lock.release()


def recover_pending_rust_refresh(config: AtlasConfig) -> dict[str, Any]:
    """Explicit internal recovery hook; public routing remains disabled."""
    if config.language != "rust":
        raise ValueError("Rust refresh recovery requires language=rust")
    lease = ProjectRefreshLease(config.data_dir, config.repository, config.project)
    if not lease.acquire():
        return {"status": "deferred", "action": "refresh_owned_elsewhere"}
    try:
        return recover_rust_refresh_transaction(config)
    finally:
        lease.release()
