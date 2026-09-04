"""Bounded session-start index refresh for explicitly enabled MCP configs."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

from .config import AtlasConfig, SHARED_PROVIDER_LAYOUT
from .index_state import index_freshness, provider_database_health
from .lifecycle import GlobalCbmLock
from .python_registration_store import registration_index_health
from .refresh_planner import RefreshPlanError, plan_refresh


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _graceful_run(
    command: list[str], *, timeout: float, **_kwargs: Any
) -> subprocess.CompletedProcess[str]:
    """Give update ownership a chance to unwind before enforcing its deadline."""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            command, timeout, output=stdout, stderr=stderr
        ) from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def session_start_update(
    config: Path,
    *,
    timeout_seconds: float = 60.0,
    runner: Runner | None = None,
) -> dict[str, Any]:
    configured = AtlasConfig.load(config)
    freshness = index_freshness(
        configured.data_dir, configured.repository, configured.project
    )
    provider = provider_database_health(configured.cache_dir, configured.project)
    registrations = (
        registration_index_health(
            configured.data_dir,
            configured.repository,
            configured.project,
            freshness.get("source_fingerprint"),
        )
        if configured.language == "python"
        else {"status": "not_applicable", "ok": True}
    )
    generation_current = True
    if configured.provider_layout == SHARED_PROVIDER_LAYOUT:
        try:
            generation = plan_refresh(
                configured.data_dir,
                configured.repository,
                configured.project,
                configured.language,
            )
        except RefreshPlanError:
            generation_current = False
        else:
            generation_current = (
                generation.get("status") == "planned"
                and not generation.get("dirty_paths")
            )
    if (
        freshness.get("status") == "fresh"
        and freshness.get("mode") == "fast"
        and bool(provider.get("ok"))
        and bool(registrations.get("ok"))
        and generation_current
    ):
        return {
            "policy": "session-start",
            "status": "current",
            "ok": True,
            "attempted": True,
            "timeout_seconds": timeout_seconds,
            "reason": "index_current",
            "provider": {
                "route": "atlas_source_current",
                "status": "not_started",
                "database": provider,
            },
            "previous_index_preserved": False,
        }
    if (
        configured.provider_layout != SHARED_PROVIDER_LAYOUT
        and freshness.get("status") in {"stale", "rebuild_required"}
    ):
        probe = GlobalCbmLock(timeout_seconds=0.02)
        try:
            probe.acquire()
        except TimeoutError:
            return {
                "policy": "session-start",
                "status": "failed",
                "ok": False,
                "attempted": True,
                "timeout_seconds": timeout_seconds,
                "reason": "provider_busy",
                "provider": {"status": "busy"},
                "previous_index_preserved": True,
            }
        else:
            probe.release()
    command = [
        sys.executable,
        "-m",
        "codebase_atlas.cli",
        "update",
        "--config",
        str(config.resolve()),
        "--mode",
        "fast",
    ]
    try:
        completed = (runner or _graceful_run)(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "policy": "session-start",
            "status": "timeout",
            "ok": False,
            "attempted": True,
            "timeout_seconds": timeout_seconds,
            "reason": "update_time_budget_exceeded",
            "previous_index_preserved": True,
        }
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    result_status = str(payload.get("status", "failed"))
    ok = completed.returncode == 0 and result_status in {"current", "updated"}
    return {
        "policy": "session-start",
        "status": result_status if ok else "failed",
        "ok": ok,
        "attempted": True,
        "timeout_seconds": timeout_seconds,
        "reason": (
            "index_current" if result_status == "current" and ok
            else "index_updated" if result_status == "updated" and ok
            else str(payload.get("error") or completed.stderr.strip() or "update_failed")
        ),
        "provider": payload.get("provider", {}),
        "previous_index_preserved": not ok,
    }


def disabled_session_update() -> dict[str, Any]:
    return {
        "policy": "off",
        "status": "disabled",
        "ok": True,
        "attempted": False,
        "reason": "automatic_index_update_not_enabled",
    }
