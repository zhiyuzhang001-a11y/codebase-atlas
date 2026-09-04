"""Dry-run-first Codex MCP integration without direct config-file editing."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tomllib
from typing import Any, Callable

from .config import AtlasConfig


Runner = Callable[..., subprocess.CompletedProcess[str]]


PROJECT_RULE = """At task start, call project_status once. Stop if its repository is not the open project; tell the user if automatic refresh failed or a software update is available. The on-query policy automatically coalesces one safe refresh after creating, modifying, renaming, or deleting a batch of source files; inspect the returned auto_update status and never ask the user to remember a refresh command. Before changing code, use ordinary source search to discover an exact candidate. Then call Codebase Atlas analyze_change with symbol plus path/owner. Treat unresolved or needs_disambiguation as a stop; never guess. Preserve generation, stale, warning, partial, truncation, continuation, and auto_update fields. Read the returned source regions before editing and run only evidence-backed tests. Fall back to direct source inspection whenever Atlas reports partial, error, or unsupported evidence."""

PROJECT_SCOPE_BEGIN = "# >>> codebase-atlas managed project mcp v1 >>>"
PROJECT_SCOPE_END = "# <<< codebase-atlas managed project mcp v1 <<<"
PROJECT_CODEX_CONFIG = Path(".codex/config.toml")
GLOBAL_AUTO_SCOPE = "global-auto"


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
    # Preserve a virtualenv interpreter path. Resolving its symlink bypasses
    # pyvenv.cfg and can make the installed codebase_atlas package disappear.
    return str(Path(sys.executable).absolute()), [
        "-m", "codebase_atlas.cli", "mcp", "--config", str(config)
    ]


def _auto_transport(atlas_executable: str | Path | None) -> tuple[str, list[str]]:
    tail = [
        "mcp-auto",
        "--auto-update", "on-query",
        "--auto-update-timeout", "60",
        "--version-check", "notify",
    ]
    if atlas_executable is not None:
        return _executable(atlas_executable, "codebase-atlas"), tail
    discovered = shutil.which("codebase-atlas")
    if discovered:
        return str(Path(discovered).resolve()), tail
    return str(Path(sys.executable).absolute()), ["-m", "codebase_atlas.cli", *tail]


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


def _legacy_fixed_atlas(existing: dict[str, Any]) -> bool:
    transport = existing.get("transport")
    if not isinstance(transport, dict) or transport.get("type") != "stdio":
        return False
    command = transport.get("command")
    args = transport.get("args")
    environment = transport.get("env")
    if not isinstance(command, str) or not isinstance(args, list):
        return False
    if not all(isinstance(value, str) for value in args):
        return False
    if environment not in (None, {}):
        return False
    module_shape = args[:3] == ["-m", "codebase_atlas.cli", "mcp"]
    executable_shape = bool(args) and args[0] == "mcp"
    if not (module_shape or executable_shape) or args.count("--config") != 1:
        return False
    position = args.index("--config")
    if position + 1 >= len(args):
        return False
    config = Path(args[position + 1]).expanduser()
    try:
        if config.is_symlink() or not config.is_file():
            return False
        AtlasConfig.load(config)
    except (OSError, KeyError, TypeError, ValueError):
        return False
    return True


def _transport_add_argv(codex: str, name: str, transport: dict[str, Any]) -> list[str]:
    return [
        codex, "mcp", "add", name, "--",
        str(transport["command"]), *[str(value) for value in transport["args"]],
    ]


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _managed_project_block(name: str, command: str, args: list[str]) -> str:
    rendered_args = ", ".join(_toml_string(value) for value in args)
    return (
        f"{PROJECT_SCOPE_BEGIN}\n"
        f"[mcp_servers.{name}]\n"
        f"command = {_toml_string(command)}\n"
        f"args = [{rendered_args}]\n"
        "startup_timeout_sec = 65\n"
        f"{PROJECT_SCOPE_END}\n"
    )


def _project_paths(
    config: Path, codex_project_root: Path | None
) -> tuple[Path, Path, Path]:
    if not config.is_file() or config.is_symlink():
        raise RuntimeError("Atlas config must be an existing regular non-symlink file")
    configured = AtlasConfig.load(config)
    repository = configured.repository.resolve()
    project_root = (
        codex_project_root.resolve() if codex_project_root is not None else repository
    )
    if (
        not project_root.is_dir()
        or project_root.is_symlink()
        or not repository.is_relative_to(project_root)
    ):
        raise RuntimeError(
            "Codex project root must be a real ancestor directory of the Atlas repository"
        )
    codex_dir = project_root / PROJECT_CODEX_CONFIG.parent
    if codex_dir.exists():
        metadata = os.lstat(codex_dir)
        if not stat.S_ISDIR(metadata.st_mode) or codex_dir.is_symlink():
            raise RuntimeError("project .codex path must be a real directory")
    target = project_root / PROJECT_CODEX_CONFIG
    if target.exists() and (target.is_symlink() or not target.is_file()):
        raise RuntimeError("project Codex config must be a regular non-symlink file")
    return repository, project_root, target


def _project_state(
    target: Path, name: str, command: str, args: list[str]
) -> tuple[str, str, str]:
    block = _managed_project_block(name, command, args)
    if not target.exists():
        return "absent", "", block
    original = target.read_text(encoding="utf-8")
    try:
        value = tomllib.loads(original)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError("project Codex config is invalid TOML") from exc
    begin_count = original.count(PROJECT_SCOPE_BEGIN)
    end_count = original.count(PROJECT_SCOPE_END)
    servers = value.get("mcp_servers", {})
    existing = servers.get(name) if isinstance(servers, dict) else None
    if begin_count == end_count == 0:
        return ("conflict" if existing is not None else "absent"), original, block
    if begin_count != 1 or end_count != 1:
        raise RuntimeError("project Codex config has an invalid Atlas managed block")
    start = original.index(PROJECT_SCOPE_BEGIN)
    finish = original.index(PROJECT_SCOPE_END, start) + len(PROJECT_SCOPE_END)
    managed = original[start:finish].strip() + "\n"
    if managed == block:
        return "matching", original, block
    return "conflict", original, block


def _project_plan(
    config: Path,
    *,
    name: str,
    atlas_executable: str | Path | None,
    codex_project_root: Path | None,
) -> dict[str, Any]:
    repository, project_root, target = _project_paths(config, codex_project_root)
    command, transport_args = _atlas_transport(config, atlas_executable)
    args = [
        *transport_args,
        "--auto-update", "on-query",
        "--auto-update-timeout", "60",
        "--version-check", "notify",
    ]
    state, original, block = _project_state(target, name, command, args)
    return {
        "schema_version": 1,
        "status": "planned" if state != "conflict" else "blocked",
        "mode": "dry_run",
        "scope": "project",
        "name": name,
        "repository": str(repository),
        "codex_project_root": str(project_root),
        "target": str(target),
        "existing": state,
        "transport": {"type": "stdio", "command": command, "args": args},
        "managed_block": block,
        "preserved_bytes": len(original.encode("utf-8")),
        "mutates": False,
        "global_config_mutation": False,
        "current_session_refresh_required": True,
        "verification": [
            "start a new Codex task rooted at this repository",
            "confirm the initialize instructions name the exact repository",
            "run one exact analyze_change query and inspect auto_update/freshness",
        ],
        "project_rule": PROJECT_RULE,
    }


def _write_project_config(target: Path, original: str, block: str) -> None:
    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise RuntimeError("project .codex path must not be a symlink")
    separator = "" if not original or original.endswith("\n\n") else (
        "\n" if original.endswith("\n") else "\n\n"
    )
    payload = f"{original}{separator}{block}"
    if target.exists():
        before = os.lstat(target)
        if not stat.S_ISREG(before.st_mode) or target.is_symlink():
            raise RuntimeError("project Codex config changed before publication")
        identity = (before.st_dev, before.st_ino)
        descriptor = os.open(target, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != identity or not stat.S_ISREG(opened.st_mode):
                raise RuntimeError("project Codex config changed before publication")
            current = os.lstat(target)
            if (current.st_dev, current.st_ino) != identity:
                raise RuntimeError("project Codex config changed before publication")
            with os.fdopen(descriptor, "r+", encoding="utf-8") as stream:
                descriptor = -1
                try:
                    stream.seek(0)
                    stream.truncate()
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                except OSError:
                    stream.seek(0)
                    stream.truncate()
                    stream.write(original)
                    stream.flush()
                    os.fsync(stream.fileno())
                    raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    else:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            try:
                target.unlink()
            except OSError:
                pass
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def _remove_project_block(original: str) -> str:
    start = original.index(PROJECT_SCOPE_BEGIN)
    finish = original.index(PROJECT_SCOPE_END, start) + len(PROJECT_SCOPE_END)
    if finish < len(original) and original[finish] == "\n":
        finish += 1
    if start >= 2 and original[start - 2:start] == "\n\n":
        start -= 1
    return original[:start] + original[finish:]


def codex_plan(
    config: Path,
    *,
    name: str = "codebase_atlas",
    codex_binary: str | Path | None = None,
    atlas_executable: str | Path | None = None,
    scope: str = "global",
    codex_project_root: Path | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    if not name or any(character.isspace() for character in name):
        raise ValueError("MCP name must be non-empty and contain no whitespace")
    if scope == "project":
        return _project_plan(
            config, name=name, atlas_executable=atlas_executable,
            codex_project_root=codex_project_root,
        )
    if scope not in {"global", GLOBAL_AUTO_SCOPE}:
        raise ValueError(
            "Codex integration scope must be 'global', 'global-auto', or 'project'"
        )
    codex = _executable(codex_binary, "codex")
    command, args = (
        _auto_transport(atlas_executable)
        if scope == GLOBAL_AUTO_SCOPE
        else _atlas_transport(config, atlas_executable)
    )
    existing = _read_existing(codex, name, runner=runner)
    if existing is None:
        state = "absent"
    elif _matches(existing, command, args):
        state = "matching"
    elif scope == GLOBAL_AUTO_SCOPE and _legacy_fixed_atlas(existing):
        state = "legacy_fixed_atlas"
    else:
        state = "conflict"
    plan = {
        "schema_version": 1,
        "status": "planned" if state != "conflict" else "blocked",
        "mode": "dry_run",
        "scope": scope,
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
    if state == "legacy_fixed_atlas" and existing is not None:
        legacy = dict(existing["transport"])
        plan["legacy_transport"] = legacy
        plan["rollback_argv"] = _transport_add_argv(codex, name, legacy)
    return plan


def codex_apply(
    config: Path,
    **kwargs: Any,
) -> dict[str, Any]:
    runner: Runner = kwargs.pop("runner", subprocess.run)
    plan = codex_plan(config, runner=runner, **kwargs)
    if plan["scope"] == "project":
        if plan["existing"] == "conflict":
            raise RuntimeError("refusing to overwrite an existing different project MCP configuration")
        if plan["existing"] == "matching":
            return plan | {"status": "ready", "mode": "applied", "mutates": False}
        target = Path(plan["target"])
        original = target.read_text(encoding="utf-8") if target.exists() else ""
        _write_project_config(target, original, str(plan["managed_block"]))
        verified = codex_plan(config, runner=runner, **kwargs)
        if verified["existing"] != "matching":
            raise RuntimeError("project Codex MCP verification did not match the requested transport")
        return verified | {"status": "ready", "mode": "applied", "mutates": True}
    if plan["existing"] == "conflict":
        raise RuntimeError("refusing to overwrite an existing different MCP configuration")
    if plan["existing"] == "matching":
        return plan | {"status": "ready", "mode": "applied", "mutates": False}
    legacy = plan.get("legacy_transport")
    if plan["scope"] == GLOBAL_AUTO_SCOPE and legacy is not None:
        removed = runner(
            plan["remove_argv"], check=False, capture_output=True, text=True
        )
        if removed.returncode != 0:
            raise RuntimeError(removed.stderr.strip() or "codex mcp remove failed")
        completed = runner(
            plan["apply_argv"], check=False, capture_output=True, text=True
        )
        if completed.returncode != 0:
            rollback = runner(
                plan["rollback_argv"], check=False, capture_output=True, text=True
            )
            restored = _read_existing(
                str(plan["remove_argv"][0]), str(plan["name"]), runner=runner
            )
            if rollback.returncode != 0 or restored is None or not _matches(
                restored, str(legacy["command"]), list(legacy["args"])
            ):
                raise RuntimeError("global Atlas migration failed and exact rollback failed")
            raise RuntimeError(
                (completed.stderr.strip() or "codex mcp add failed")
                + "; exact legacy transport restored"
            )
        verified = codex_plan(config, runner=runner, **kwargs)
        if verified["existing"] != "matching":
            runner(
                verified["remove_argv"], check=False, capture_output=True, text=True
            )
            rollback = runner(
                plan["rollback_argv"], check=False, capture_output=True, text=True
            )
            restored = _read_existing(
                str(plan["remove_argv"][0]), str(plan["name"]), runner=runner
            )
            if rollback.returncode != 0 or restored is None or not _matches(
                restored, str(legacy["command"]), list(legacy["args"])
            ):
                raise RuntimeError("global Atlas verification failed and exact rollback failed")
            raise RuntimeError("global Atlas verification failed; exact legacy transport restored")
        return verified | {"status": "ready", "mode": "applied", "mutates": True}
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
    if plan["scope"] == "project":
        if plan["existing"] == "conflict":
            raise RuntimeError("refusing to remove a different project MCP configuration")
        if plan["existing"] == "absent":
            return plan | {"status": "absent", "mode": "removed", "mutates": False}
        target = Path(plan["target"])
        original = target.read_text(encoding="utf-8")
        remainder = _remove_project_block(original)
        if remainder.strip():
            before = os.lstat(target)
            identity = (before.st_dev, before.st_ino)
            descriptor = os.open(target, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != identity or not stat.S_ISREG(opened.st_mode):
                    raise RuntimeError("project Codex config changed before removal")
                with os.fdopen(descriptor, "r+", encoding="utf-8") as stream:
                    descriptor = -1
                    try:
                        stream.seek(0)
                        stream.truncate()
                        stream.write(remainder)
                        stream.flush()
                        os.fsync(stream.fileno())
                    except OSError:
                        stream.seek(0)
                        stream.truncate()
                        stream.write(original)
                        stream.flush()
                        os.fsync(stream.fileno())
                        raise
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        else:
            before = os.lstat(target)
            if not stat.S_ISREG(before.st_mode) or target.is_symlink():
                raise RuntimeError("project Codex config changed before removal")
            target.unlink()
        verified = codex_plan(config, runner=runner, **kwargs)
        if verified["existing"] != "absent":
            raise RuntimeError("project Codex MCP removal verification failed")
        return verified | {"status": "absent", "mode": "removed", "mutates": True}
    if plan["scope"] == GLOBAL_AUTO_SCOPE and plan["existing"] == "legacy_fixed_atlas":
        raise RuntimeError("refusing to remove a legacy fixed Atlas entry; apply migration first")
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
