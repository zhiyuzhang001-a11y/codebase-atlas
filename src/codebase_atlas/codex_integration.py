"""Dry-run-first Codex MCP integration without direct config-file editing."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable


Runner = Callable[..., subprocess.CompletedProcess[str]]


PROJECT_RULE = """Before changing code, use ordinary source search to discover an exact candidate. Then call Codebase Atlas analyze_change with symbol plus path/owner. Treat unresolved or needs_disambiguation as a stop; never guess. Preserve stale, warning, partial, truncation, and continuation fields. Read the returned source regions before editing and run only evidence-backed tests. Fall back to direct source inspection whenever Atlas reports partial, error, or unsupported evidence."""


def _executable(value: str | Path | None, fallback: str) -> str:
    raw = str(value) if value is not None else fallback
    resolved = shutil.which(raw)
    if resolved:
        return str(Path(resolved).resolve())
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    raise RuntimeError(f"executable is not available: {raw}")


def _atlas_transport(
    config: Path, atlas_executable: str | Path | None
) -> tuple[str, list[str]]:
    if not config.is_file() or config.is_symlink():
        raise RuntimeError("Atlas config must be an existing regular non-symlink file")
    config = config.resolve()
    if atlas_executable is not None:
        return _executable(atlas_executable, "codebase-atlas"), [
            "mcp", "--config", str(config)
        ]
    discovered = shutil.which("codebase-atlas")
    if discovered:
        return str(Path(discovered).resolve()), ["mcp", "--config", str(config)]
    return str(Path(sys.executable).resolve()), [
        "-m", "codebase_atlas.cli", "mcp", "--config", str(config)
    ]


def _read_existing(
    codex: str, name: str, *, runner: Runner = subprocess.run
) -> dict[str, Any] | None:
    completed = runner(
        [codex, "mcp", "get", name, "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if "No MCP server named" in detail:
            return None
        raise RuntimeError(detail or "codex mcp get failed")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Codex returned invalid MCP JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Codex returned an invalid MCP record")
    return value


def _matches(existing: dict[str, Any], command: str, args: list[str]) -> bool:
    transport = existing.get("transport")
    return (
        isinstance(transport, dict)
        and transport.get("type") == "stdio"
        and transport.get("command") == command
        and transport.get("args") == args
    )


def codex_plan(
    config: Path,
    *,
    name: str = "codebase_atlas",
    codex_binary: str | Path | None = None,
    atlas_executable: str | Path | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    if not name or any(character.isspace() for character in name):
        raise ValueError("MCP name must be non-empty and contain no whitespace")
    codex = _executable(codex_binary, "codex")
    command, args = _atlas_transport(config, atlas_executable)
    existing = _read_existing(codex, name, runner=runner)
    state = "absent" if existing is None else (
        "matching" if _matches(existing, command, args) else "conflict"
    )
    return {
        "schema_version": 1,
        "status": "planned" if state != "conflict" else "blocked",
        "mode": "dry_run",
        "name": name,
        "existing": state,
        "transport": {"type": "stdio", "command": command, "args": args},
        "apply_argv": [codex, "mcp", "add", name, "--", command, *args],
        "remove_argv": [codex, "mcp", "remove", name],
        "mutates": False,
        "current_session_refresh_required": True,
        "verification": [
            "start a new Codex task or refresh the local client",
            "confirm analyze_change appears in the MCP tool list",
            "run one exact analyze_change query and inspect freshness/completeness",
        ],
        "project_rule": PROJECT_RULE,
    }


def codex_apply(
    config: Path,
    **kwargs: Any,
) -> dict[str, Any]:
    runner: Runner = kwargs.pop("runner", subprocess.run)
    plan = codex_plan(config, runner=runner, **kwargs)
    if plan["existing"] == "conflict":
        raise RuntimeError("refusing to overwrite an existing different MCP configuration")
    if plan["existing"] == "matching":
        return plan | {"status": "ready", "mode": "applied", "mutates": False}
    completed = runner(
        plan["apply_argv"], check=False, capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "codex mcp add failed")
    verified = codex_plan(config, runner=runner, **kwargs)
    if verified["existing"] != "matching":
        raise RuntimeError("Codex MCP verification did not match the requested transport")
    return verified | {"status": "ready", "mode": "applied", "mutates": True}


def codex_remove(
    config: Path,
    **kwargs: Any,
) -> dict[str, Any]:
    runner: Runner = kwargs.pop("runner", subprocess.run)
    plan = codex_plan(config, runner=runner, **kwargs)
    if plan["existing"] == "conflict":
        raise RuntimeError("refusing to remove a different MCP configuration")
    if plan["existing"] == "absent":
        return plan | {"status": "absent", "mode": "removed", "mutates": False}
    completed = runner(
        plan["remove_argv"], check=False, capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "codex mcp remove failed")
    verified = codex_plan(config, runner=runner, **kwargs)
    if verified["existing"] != "absent":
        raise RuntimeError("Codex MCP removal verification failed")
    return verified | {"status": "absent", "mode": "removed", "mutates": True}
