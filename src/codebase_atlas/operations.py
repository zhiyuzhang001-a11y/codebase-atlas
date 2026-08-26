"""Machine-readable daily-operation status and stale-query policy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .index_state import index_freshness, provider_database_health
from .languages import capability


STALE_POLICIES = ("warn", "error", "ignore")


def operational_index_status(
    data_dir: Path,
    repository: Path,
    cache_dir: Path,
    project: str,
    language: str = "python",
) -> dict[str, Any]:
    source = index_freshness(data_dir, repository, project)
    provider = (
        {"status": "live", "ok": True, "reason": "provider_is_live"}
        if capability(language).live_provider
        else provider_database_health(cache_dir, project)
    )
    if not provider["ok"]:
        return {
            "status": "rebuild_required",
            "ok": False,
            "reason": str(provider["reason"]),
            "source": source,
            "provider_database": provider,
        }
    return {
        "status": source["status"],
        "ok": source["ok"],
        "reason": source["reason"],
        "source": source,
        "provider_database": provider,
    }


def unknown_operational_status() -> dict[str, Any]:
    return {
        "status": "unknown",
        "ok": True,
        "reason": "project_configuration_not_loaded",
    }


def stale_policy_error(status: dict[str, Any], policy: str) -> str | None:
    if policy not in STALE_POLICIES:
        raise ValueError(f"unsupported stale policy: {policy}")
    if policy == "error" and not bool(status.get("ok")):
        return (
            f"index is {status.get('status', 'unknown')}: "
            f"{status.get('reason', 'index_not_ready')}; run codebase-atlas update"
        )
    return None


def index_warnings(status: dict[str, Any], policy: str) -> list[dict[str, str]]:
    if policy not in STALE_POLICIES:
        raise ValueError(f"unsupported stale policy: {policy}")
    if policy == "ignore":
        return []
    warnings: list[dict[str, str]] = []
    registrations = status.get("python_registrations")
    if isinstance(registrations, dict) and not bool(registrations.get("ok")):
        reason = str(registrations.get("reason", "registration_index_unavailable"))
        warnings.append({
            "code": "python_registration_index_unavailable",
            "status": str(registrations.get("status", "rebuild_required")),
            "reason": reason,
            "message": "Python registration evidence is unavailable. Run codebase-atlas update or repair.",
        })
    if bool(status.get("ok")):
        return warnings
    state = str(status.get("status", "unknown"))
    reason = str(status.get("reason", "index_not_ready"))
    warnings.append({
        "code": "index_not_current",
        "status": state,
        "reason": reason,
        "message": f"Index is {state}; results may be stale. Run codebase-atlas update.",
    })
    return warnings


def attach_operational_status(
    payload: dict[str, Any],
    status: dict[str, Any] | None,
    policy: str,
) -> dict[str, Any]:
    if status is None:
        return payload
    payload["index"] = status
    payload["warnings"] = index_warnings(status, policy)
    return payload
