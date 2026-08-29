"""Read-only planning for the M32 legacy-to-shared Provider migration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .config import AtlasConfig, SHARED_PROVIDER_LAYOUT
from .maintenance import inspect_provider_database_at
from .provider_layout import inspect_provider_root


@dataclass(frozen=True)
class ProviderMigrationPlan:
    schema_version: int
    status: str
    action: str
    writes_required: bool
    repository: str
    legacy_cache_dir: str
    legacy_project: str
    shared_cache_dir: str
    shared_project: str
    legacy: dict[str, object]
    shared: dict[str, object]
    shared_root: dict[str, object]
    reason: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def plan_provider_migration(
    config: AtlasConfig, *, deep: bool = True
) -> ProviderMigrationPlan:
    """Return a mutation-free migration decision for one exact repository."""
    root = inspect_provider_root(config.shared_cache_dir)
    legacy_project = config.legacy_project or config.project
    legacy = inspect_provider_database_at(
        config.legacy_cache_dir, legacy_project, config.repository, deep=deep
    )
    shared = inspect_provider_database_at(
        config.shared_cache_dir, config.shared_project, config.repository, deep=deep
    )

    if (
        config.provider_layout == SHARED_PROVIDER_LAYOUT
        and config.project == config.shared_project
        and shared["status"] == "healthy"
    ):
        status, action, writes, reason = (
            "ready", "already_active", False, "shared_layout_already_active"
        )
    elif not root.ready:
        status, action, writes, reason = (
            "blocked", "repair_shared_root", False, f"shared_root_{root.status}"
        )
    elif shared["status"] == "healthy":
        status, action, writes, reason = (
            "ready", "verify_and_publish", True, "exact_shared_database_verified"
        )
    elif shared["status"] != "missing":
        status, action, writes, reason = (
            "blocked", "resolve_shared_conflict", False,
            f"shared_target_{shared['reason']}",
        )
    elif legacy["status"] == "healthy":
        status, action, writes, reason = (
            "planned", "rebuild_into_shared", True, "healthy_legacy_requires_rebuild"
        )
    elif legacy["status"] == "missing":
        status, action, writes, reason = (
            "planned", "fresh_shared_index", True, "no_legacy_or_shared_database"
        )
    else:
        status, action, writes, reason = (
            "blocked", "repair_legacy_before_migration", False,
            f"legacy_{legacy['reason']}",
        )

    return ProviderMigrationPlan(
        schema_version=1,
        status=status,
        action=action,
        writes_required=writes,
        repository=str(config.repository),
        legacy_cache_dir=str(config.legacy_cache_dir),
        legacy_project=legacy_project,
        shared_cache_dir=str(config.shared_cache_dir),
        shared_project=config.shared_project,
        legacy=legacy,
        shared=shared,
        shared_root={
            "status": root.status,
            "path": str(root.path),
            "ready": root.ready,
            "detail": root.detail,
        },
        reason=reason,
    )


def shared_provider_config(config: AtlasConfig) -> AtlasConfig:
    """Build the publishable shared-layout config without writing it."""
    legacy_project = config.legacy_project or config.project
    return replace(
        config,
        project=config.shared_project,
        provider_layout=SHARED_PROVIDER_LAYOUT,
        legacy_project=legacy_project,
    )


def prepare_shared_provider_root(path: Path) -> bool:
    """Create only a missing final shared root and verify its exact safety."""
    before = inspect_provider_root(path)
    if before.status == "ready":
        return False
    if before.status != "missing":
        raise RuntimeError(f"unsafe shared Provider root: {before.status}")
    path.mkdir(parents=True, mode=0o700, exist_ok=False)
    after = inspect_provider_root(path)
    if after.status != "ready":
        raise RuntimeError(f"shared Provider root creation failed safety check: {after.status}")
    return True
