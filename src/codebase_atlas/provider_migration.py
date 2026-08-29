"""Read-only planning for the M32 legacy-to-shared Provider migration."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import AtlasConfig
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
    legacy = inspect_provider_database_at(
        config.cache_dir, config.project, config.repository, deep=deep
    )
    shared = inspect_provider_database_at(
        config.shared_cache_dir, config.shared_project, config.repository, deep=deep
    )

    if not root.ready:
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
        legacy_cache_dir=str(config.cache_dir),
        legacy_project=config.project,
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
