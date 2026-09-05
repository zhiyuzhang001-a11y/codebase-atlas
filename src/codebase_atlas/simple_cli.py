"""Four-command project lifecycle interface for Codebase Atlas."""

from __future__ import annotations

import argparse
import ast
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time
from typing import Any

from . import __version__
from .cli import _index_repository, main as advanced_main
from .codex_integration import codex_apply, codex_plan
from .config import AtlasConfig, CONFIG_NAME, diagnose
from .index_state import index_freshness
from .lifecycle import ProjectOperationLease, ProjectRefreshLease
from .maintenance import inspect_installation
from .onboarding import OnboardingInputs, apply_plan, build_plan
from .project_discovery import ProjectResolution, resolve_project
from .project_lifecycle import (
    ProjectLifecycleState,
    lifecycle_state_path,
    load_lifecycle_state,
    publish_lifecycle_state,
)
from .runtime import required_checks_ok


def _result(
    operation: str,
    status: str,
    repository: Path,
    *,
    mutates: bool,
    **fields: Any,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "operation": operation,
        "status": status,
        "repository": str(repository.resolve()),
        "atlas_version": __version__,
        "mutates": mutates,
        **fields,
    }


def _repository_root(start: Path) -> tuple[Path, ProjectResolution]:
    resolution = resolve_project(start)
    if resolution.status in {
        "invalid_project_root",
        "ambiguous_project",
        "invalid_config",
        "repository_mismatch",
    }:
        raise RuntimeError(f"{resolution.status}: {resolution.reason}")
    completed = subprocess.run(
        ["git", "-C", str(resolution.root), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("target must belong to exactly one Git repository")
    root = Path(completed.stdout.strip()).resolve()
    if root != resolution.root.resolve():
        raise RuntimeError("resolved Git repository changed during discovery")
    return root, resolution


def _operation_project(repository: Path) -> str:
    from .provider_layout import provider_project_identity

    return provider_project_identity(repository)


def _acquire_refresh(
    lease: ProjectRefreshLease, *, timeout_seconds: float
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while not lease.acquire():
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return True


def _tracked_sources(repository: Path, language: str) -> list[Path]:
    completed = subprocess.run(
        ["git", "-C", str(repository), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        return []
    suffixes = {".py"} if language == "python" else {
        ".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"
    }
    paths: list[Path] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        candidate = repository / relative
        try:
            metadata = os.lstat(candidate)
        except OSError:
            continue
        if (
            relative.suffix.lower() in suffixes
            and stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
        ):
            paths.append(relative)
    return paths


def _verification_candidate(config: AtlasConfig) -> tuple[str, str, bytes]:
    for relative in _tracked_sources(config.repository, config.language):
        path = config.repository / relative
        try:
            payload = path.read_bytes()
            text = payload.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if config.language == "python":
            try:
                module = ast.parse(text)
            except SyntaxError:
                continue
            symbol = next(
                (
                    node.name
                    for node in module.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ),
                "",
            )
        else:
            match = re.search(
                r"(?:^|\n)\s*(?:export\s+)?(?:async\s+)?"
                r"(?:function|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)",
                text,
            )
            if match is None:
                match = re.search(
                    r"(?:^|\n)\s*(?:export\s+)?(?:const|let|var)\s+"
                    r"([A-Za-z_$][\w$]*)",
                    text,
                )
            symbol = match.group(1) if match else ""
        if symbol:
            return symbol, relative.as_posix(), payload
    raise RuntimeError("no tracked source symbol is available for the verification query")


def _query_payload(
    config: AtlasConfig, config_path: Path, symbol: str, target_path: str
) -> dict[str, Any]:
    output = StringIO()
    with redirect_stdout(output):
        code = advanced_main([
            "query", "definition", symbol,
            "--config", str(config_path),
            "--target-path", target_path,
            "--stale-policy", "error",
        ])
    try:
        payload = json.loads(output.getvalue())
    except json.JSONDecodeError as exc:
        raise RuntimeError("verification query returned invalid JSON") from exc
    if code != 0:
        raise RuntimeError(str(payload.get("message") or payload.get("error") or "query failed"))
    return payload


def _verification_query(config: AtlasConfig, config_path: Path) -> dict[str, Any]:
    symbol, target_path, before = _verification_candidate(config)
    positive = _query_payload(config, config_path, symbol, target_path)
    nodes = positive.get("nodes")
    if not isinstance(nodes, list) or not any(
        isinstance(node, dict)
        and isinstance(node.get("source"), dict)
        and node["source"].get("path") == target_path
        for node in nodes
    ):
        raise RuntimeError("verification symbol did not resolve to the target source file")
    negative_symbol = "__atlas_wrong_project_" + secrets.token_hex(12)
    negative = _query_payload(config, config_path, negative_symbol, "")
    if negative.get("nodes"):
        raise RuntimeError("cross-project negative verification returned unexpected facts")
    if (config.repository / target_path).read_bytes() != before:
        raise RuntimeError("target source changed during Atlas verification")
    return {
        "symbol": symbol,
        "target_path": target_path,
        "matched_nodes": len(nodes),
        "cross_project_negative": "pass",
    }


def enable_project(
    repository: Path,
    *,
    config_path: Path | None = None,
    language: str | None = None,
    node: Path | None = None,
    cbm_binary: Path | None = None,
    serena_python: Path | None = None,
    node_bin_dir: Path | None = None,
    tsconfig: Path | None = None,
    data_dir: Path | None = None,
    mode: str = "fast",
) -> tuple[dict[str, Any], int]:
    root, resolution = _repository_root(repository)
    selected_config = (
        config_path.resolve()
        if config_path is not None
        else (resolution.config or root / CONFIG_NAME).resolve()
    )
    inputs = OnboardingInputs(
        root, selected_config, language, node, cbm_binary, serena_python,
        node_bin_dir, tsconfig, data_dir, mode,
    )
    plan, candidate = build_plan(inputs)
    if plan["status"] != "planned" or candidate is None:
        return _result(
            "enable", "blocked", root, mutates=False,
            project_state="not_enabled", index_status="unknown",
            connection_status="not_configured", error=plan.get("error", ""),
            onboarding=plan,
        ), 2
    operation_lock = ProjectOperationLease(
        candidate.data_dir, root, _operation_project(root)
    )
    if not operation_lock.acquire():
        return _result(
            "enable", "blocked", root, mutates=False,
            project_state="busy", index_status="unknown",
            connection_status="unchanged",
            error="another lifecycle operation owns this project",
        ), 2
    operation_id = secrets.token_hex(16)
    state_existed = lifecycle_state_path(candidate.data_dir).exists()
    was_operational = resolution.status == "configured"
    previous: ProjectLifecycleState | None = None
    state_mutated = False
    try:
        plan, candidate = build_plan(inputs)
        if plan["status"] != "planned" or candidate is None:
            return _result(
                "enable", "blocked", root, mutates=False,
                project_state="not_enabled", index_status="unknown",
                connection_status="unchanged", error=plan.get("error", ""),
            ), 2
        lifecycle_project = candidate.project
        previous = (
            load_lifecycle_state(
                candidate.data_dir, root, lifecycle_project,
                missing_status="ready",
            )
            if lifecycle_project
            else None
        )
        if previous is not None and previous.status == "removed":
            return _result(
                "enable", "blocked", root, mutates=False,
                project_state="removed", index_status="unavailable",
                connection_status="removed",
                error="restore from the recorded recovery receipt before enabling",
            ), 2
        if previous is not None and (previous.status != "ready" or not state_existed):
            publish_lifecycle_state(
                candidate.data_dir,
                previous.transition("enabling", operation_id=operation_id),
            )
            state_mutated = True
        applied, code = apply_plan(
            plan, candidate, indexer=_index_repository, mode=mode
        )
        if code != 0:
            raise RuntimeError(str(applied.get("error") or applied["status"]))
        configured = AtlasConfig.load(selected_config)
        codex_preview = codex_plan(
            selected_config, scope="project", codex_project_root=root
        )
        if codex_preview["status"] == "blocked":
            raise RuntimeError("project Codex MCP configuration conflicts with Atlas")
        codex_result = codex_apply(
            selected_config, scope="project", codex_project_root=root
        )
        checks = diagnose(configured)
        freshness = index_freshness(
            configured.data_dir, configured.repository, configured.project
        )
        inspection = inspect_installation(configured, deep=True)
        verification = _verification_query(configured, selected_config)
        if (
            not required_checks_ok(checks)
            or freshness.get("status") != "fresh"
            or not inspection.get("ok")
        ):
            raise RuntimeError("Atlas acceptance checks did not reach ready/fresh/healthy")
        current = load_lifecycle_state(
            configured.data_dir, configured.repository, configured.project,
            missing_status="ready",
        )
        desired_generation = str(freshness.get("source_fingerprint") or "")
        final = (
            current
            if (
                current.status == "ready"
                and current.atlas_version == __version__
                and current.index_generation == desired_generation
            )
            else current.transition(
                "ready",
                atlas_version=__version__,
                index_generation=desired_generation,
            )
        )
        if state_mutated or not state_existed or current != final:
            publish_lifecycle_state(configured.data_dir, final)
            state_mutated = True
        return _result(
            "enable", "ready", root,
            mutates=bool(applied.get("config_created"))
            or bool(codex_result.get("mutates")) or state_mutated,
            project_state="ready", index_status="fresh",
            connection_status="configured_task_start_required",
            config=str(selected_config), project=configured.project,
            verification=verification,
            current_session_refresh_required=True,
        ), 0
    except (OSError, RuntimeError, ValueError) as exc:
        if previous is not None and was_operational:
            try:
                if state_mutated:
                    current = load_lifecycle_state(
                        candidate.data_dir, root, previous.project,
                        missing_status=previous.status,
                    )
                    restored = current.transition(
                        previous.status,
                        atlas_version=previous.atlas_version,
                        provider_version=previous.provider_version,
                        index_generation=previous.index_generation,
                        failure_reason=(
                            previous.failure_reason
                            if previous.status == "failed" else ""
                        ),
                    )
                    publish_lifecycle_state(candidate.data_dir, restored)
            except (OSError, ValueError):
                pass
        else:
            try:
                failed_config = (
                    AtlasConfig.load(selected_config)
                    if selected_config.is_file() and not selected_config.is_symlink()
                    else candidate
                )
                if failed_config.project:
                    baseline = load_lifecycle_state(
                        failed_config.data_dir,
                        failed_config.repository,
                        failed_config.project,
                        missing_status="failed",
                    )
                    failed = baseline.transition(
                        "failed", atlas_version=__version__, failure_reason=str(exc)
                    )
                    publish_lifecycle_state(failed_config.data_dir, failed)
                    state_mutated = True
            except (OSError, ValueError):
                pass
        return _result(
            "enable", "incomplete", root, mutates=state_mutated,
            project_state="failed", index_status="unknown",
            connection_status="unchanged", error=str(exc),
        ), 2
    finally:
        operation_lock.release()


def stop_project(
    repository: Path, *, timeout_seconds: float = 30.0
) -> tuple[dict[str, Any], int]:
    root, resolution = _repository_root(repository)
    if resolution.status != "configured" or resolution.config is None:
        return _result(
            "stop", "not_enabled", root, mutates=False,
            project_state=resolution.status, index_status="unavailable",
            connection_status="unchanged",
        ), 0
    config = AtlasConfig.load(resolution.config)
    operation_lock = ProjectOperationLease(
        config.data_dir, root, _operation_project(root)
    )
    if not operation_lock.acquire():
        return _result(
            "stop", "blocked", root, mutates=False,
            project_state="busy", index_status="unchanged",
            connection_status="unchanged",
            error="another lifecycle operation owns this project",
        ), 2
    refresh = ProjectRefreshLease(
        config.data_dir, config.repository, config.project
    )
    try:
        previous = load_lifecycle_state(
            config.data_dir, config.repository, config.project
        )
        if previous.status == "stopped":
            return _result(
                "stop", "stopped", root, mutates=False,
                project_state="stopped", index_status="preserved",
                connection_status="stopped",
            ), 0
        if previous.status == "removed":
            return _result(
                "stop", "removed", root, mutates=False,
                project_state="removed", index_status="recovery_area",
                connection_status="removed",
            ), 0
        if not _acquire_refresh(refresh, timeout_seconds=timeout_seconds):
            return _result(
                "stop", "blocked", root, mutates=False,
                project_state=previous.status, index_status="preserved",
                connection_status="unchanged",
                error="timed out waiting for the active project refresh",
            ), 2
        operation_id = secrets.token_hex(16)
        stopping = previous.transition("stopping", operation_id=operation_id)
        publish_lifecycle_state(config.data_dir, stopping)
        stopped = stopping.transition("stopped")
        publish_lifecycle_state(config.data_dir, stopped)
        return _result(
            "stop", "stopped", root, mutates=True,
            project_state="stopped", index_status="preserved",
            connection_status="stopped",
        ), 0
    except (OSError, ValueError) as exc:
        return _result(
            "stop", "incomplete", root, mutates=False,
            project_state="failed", index_status="preserved",
            connection_status="unchanged", error=str(exc),
        ), 2
    finally:
        refresh.release()
        operation_lock.release()


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    operation = payload["operation"]
    status = payload["status"]
    repository = payload["repository"]
    print(f"Atlas {operation}: {status} — {repository}")
    if payload.get("error"):
        print(f"Error: {payload['error']}")
    if payload.get("current_session_refresh_required"):
        print("Codex connection configured; start a new task once to load the MCP entry.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    enable = commands.add_parser("enable", help="enable Atlas for one exact Git repository")
    enable.add_argument("--repo", type=Path, default=Path.cwd())
    enable.add_argument("--config", type=Path)
    enable.add_argument("--language", choices=("python", "typescript"))
    enable.add_argument("--node", type=Path)
    enable.add_argument("--cbm-binary", type=Path)
    enable.add_argument("--serena-python", type=Path)
    enable.add_argument("--node-bin-dir", type=Path)
    enable.add_argument("--tsconfig", type=Path)
    enable.add_argument("--data-dir", type=Path)
    enable.add_argument("--mode", choices=("fast", "moderate", "full"), default="fast")
    enable.add_argument("--json", action="store_true")
    stop = commands.add_parser("stop", help="stop Atlas queries without deleting project data")
    stop.add_argument("--repo", type=Path, default=Path.cwd())
    stop.add_argument("--timeout", type=float, default=30.0)
    stop.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "enable":
            payload, code = enable_project(
                args.repo, config_path=args.config, language=args.language,
                node=args.node, cbm_binary=args.cbm_binary,
                serena_python=args.serena_python, node_bin_dir=args.node_bin_dir,
                tsconfig=args.tsconfig, data_dir=args.data_dir, mode=args.mode,
            )
        else:
            if args.timeout < 0 or args.timeout > 300:
                raise ValueError("--timeout must be between 0 and 300 seconds")
            payload, code = stop_project(args.repo, timeout_seconds=args.timeout)
    except (OSError, RuntimeError, ValueError) as exc:
        payload = _result(
            args.command, "blocked", args.repo, mutates=False,
            project_state="unknown", index_status="unknown",
            connection_status="unchanged", error=str(exc),
        )
        code = 2
    _emit(payload, as_json=args.json)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
