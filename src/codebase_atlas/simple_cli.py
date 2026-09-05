"""Four-command project lifecycle interface for Codebase Atlas."""

from __future__ import annotations

import argparse
import ast
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import hashlib
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
from .codex_integration import codex_apply, codex_plan, codex_remove
from .config import AtlasConfig, CONFIG_NAME, diagnose
from .index_state import index_freshness
from .lifecycle import (
    ProjectOperationLease,
    ProjectRefreshLease,
    default_project_operation_dir,
)
from .maintenance import inspect_installation
from .onboarding import OnboardingInputs, apply_plan, build_plan
from .project_discovery import ProjectResolution, resolve_project
from .project_lifecycle import (
    ProjectLifecycleState,
    lifecycle_state_path,
    load_lifecycle_state,
    load_removal_marker,
    project_recovery_root,
    publish_lifecycle_state,
    publish_removal_marker,
    removal_marker_path,
)
from .provider_layout import provider_project_identity
from .runtime import required_checks_ok
from .release_installation import (
    VersionedInstallation,
    fetch_stable_release,
    install_stable_release,
)
from .version_check import _version_tuple


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
    return provider_project_identity(repository)


def _project_operation_lock(repository: Path) -> ProjectOperationLease:
    return ProjectOperationLease(
        default_project_operation_dir(), repository, _operation_project(repository)
    )


def _load_removal_receipt(repository: Path, marker: dict[str, Any]) -> dict[str, Any]:
    receipt_path = Path(str(marker["receipt"]))
    recovery_root = project_recovery_root(repository).resolve()
    if (
        not receipt_path.is_absolute()
        or not receipt_path.resolve().is_relative_to(recovery_root)
        or receipt_path.is_symlink()
        or not receipt_path.is_file()
    ):
        raise RuntimeError("removal receipt is unavailable or unsafe")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("removal receipt is unreadable") from exc
    required = {
        "schema_version", "status", "operation_id", "repository", "project",
        "original_config", "recovered_config", "original_data_dir",
        "recovered_data_dir", "config_sha256", "codex_config_changed",
        "shared_installation_removed",
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != required
        or receipt.get("schema_version") != 1
        or receipt.get("status") != "removed"
        or receipt.get("operation_id") != marker["operation_id"]
        or receipt.get("repository") != str(repository.resolve())
        or receipt.get("project") != marker["project"]
    ):
        raise RuntimeError("removal receipt schema or identity is invalid")
    return receipt


def _restore_removed_project(
    repository: Path, marker: dict[str, Any]
) -> None:
    receipt = _load_removal_receipt(repository, marker)
    config_path = Path(str(receipt["original_config"]))
    recovered_config = Path(str(receipt["recovered_config"]))
    data_dir = Path(str(receipt["original_data_dir"]))
    recovered_data = Path(str(receipt["recovered_data_dir"]))
    recovery_root = project_recovery_root(repository).resolve()
    if (
        not recovered_config.resolve().is_relative_to(recovery_root)
        or not recovered_data.resolve().is_relative_to(recovery_root)
        or recovered_config.is_symlink() or not recovered_config.is_file()
        or recovered_data.is_symlink() or not recovered_data.is_dir()
        or config_path.exists() or data_dir.exists()
    ):
        raise RuntimeError("removed project assets cannot be restored safely")
    config_bytes = recovered_config.read_bytes()
    if hashlib.sha256(config_bytes).hexdigest() != receipt["config_sha256"]:
        raise RuntimeError("recovered project config checksum mismatch")
    recovered = AtlasConfig.load(recovered_config)
    if (
        recovered.repository != repository.resolve()
        or recovered.data_dir != data_dir.resolve()
        or recovered.project != marker["project"]
    ):
        raise RuntimeError("recovered project config identity mismatch")
    operation_lock = _project_operation_lock(repository)
    if not operation_lock.acquire():
        raise RuntimeError("another lifecycle operation owns this project")
    restored_config_identity: tuple[int, int] | None = None
    data_restored = False
    try:
        current_marker = load_removal_marker(repository)
        if current_marker != marker:
            raise RuntimeError("removal marker changed before recovery")
        os.replace(recovered_data, data_dir)
        data_restored = True
        _write_recovery_file(config_path, config_bytes)
        config_meta = os.lstat(config_path)
        restored_config_identity = (config_meta.st_dev, config_meta.st_ino)
        removed_state = load_lifecycle_state(
            data_dir, repository, recovered.project, missing_status="removed"
        )
        if removed_state.status != "removed":
            raise RuntimeError("recovered project lifecycle is not removed")
        publish_lifecycle_state(data_dir, removed_state.transition("stopped"))
        marker_path = removal_marker_path(repository)
        marker_meta = os.lstat(marker_path)
        _unlink_verified(marker_path, (marker_meta.st_dev, marker_meta.st_ino))
    except (OSError, RuntimeError, ValueError):
        if restored_config_identity is not None and config_path.exists():
            try:
                _unlink_verified(config_path, restored_config_identity)
            except (OSError, RuntimeError):
                pass
        if data_restored and data_dir.exists() and not recovered_data.exists():
            try:
                os.replace(data_dir, recovered_data)
            except OSError:
                pass
        raise
    finally:
        operation_lock.release()


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
    config: AtlasConfig,
    config_path: Path,
    symbol: str,
    target_path: str,
    *,
    executable: Path | None = None,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    arguments = [
            "query", "definition", symbol,
            "--config", str(config_path),
            "--target-path", target_path,
            "--stale-policy", "error",
    ]
    if executable is None:
        output = StringIO()
        with redirect_stdout(output):
            code = advanced_main(arguments)
        raw_output = output.getvalue()
    else:
        completed = runner(
            [str(executable), *arguments],
            check=False, capture_output=True, text=True,
        )
        code = completed.returncode
        raw_output = completed.stdout
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("verification query returned invalid JSON") from exc
    if code != 0:
        raise RuntimeError(str(payload.get("message") or payload.get("error") or "query failed"))
    return payload


def _verification_query(
    config: AtlasConfig,
    config_path: Path,
    *,
    executable: Path | None = None,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    symbol, target_path, before = _verification_candidate(config)
    positive = _query_payload(
        config, config_path, symbol, target_path,
        executable=executable, runner=runner,
    )
    nodes = positive.get("nodes")
    if not isinstance(nodes, list) or not any(
        isinstance(node, dict)
        and isinstance(node.get("source"), dict)
        and node["source"].get("path") == target_path
        for node in nodes
    ):
        raise RuntimeError("verification symbol did not resolve to the target source file")
    negative_symbol = "__atlas_wrong_project_" + secrets.token_hex(12)
    negative = _query_payload(
        config, config_path, negative_symbol, "",
        executable=executable, runner=runner,
    )
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
    removal = load_removal_marker(root)
    if removal is not None:
        if removal["status"] != "removed":
            return _result(
                "enable", "blocked", root, mutates=False,
                project_state="removing", index_status="recovery_area",
                connection_status="removed", receipt=removal["receipt"],
                error="an incomplete removal must be recovered before enable",
            ), 2
        _restore_removed_project(root, removal)
        root, resolution = _repository_root(root)
        restored_from_receipt = True
    else:
        restored_from_receipt = False
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
    operation_lock = _project_operation_lock(root)
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
            or bool(codex_result.get("mutates")) or state_mutated
            or restored_from_receipt,
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
    operation_lock = _project_operation_lock(root)
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


def _regular_snapshot(path: Path) -> tuple[tuple[int, int], bytes]:
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise RuntimeError("managed file must remain a regular non-symlink file")
    identity = (metadata.st_dev, metadata.st_ino)
    payload = path.read_bytes()
    current = os.lstat(path)
    if (current.st_dev, current.st_ino) != identity:
        raise RuntimeError("managed file changed while taking a snapshot")
    return identity, payload


def _external_doctor(
    installation: VersionedInstallation,
    config_path: Path,
    *,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    completed = runner(
        [str(installation.atlas_executable), "doctor", "--config", str(config_path)],
        check=False, capture_output=True, text=True,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("updated Atlas doctor returned invalid JSON") from exc
    if completed.returncode != 0 or payload.get("status") != "ready":
        raise RuntimeError("updated Atlas doctor did not report ready")
    return payload


def update_project(
    repository: Path,
    *,
    timeout_seconds: float = 30.0,
    release_fetcher: Any = fetch_stable_release,
    installer: Any = install_stable_release,
    runner: Any = subprocess.run,
) -> tuple[dict[str, Any], int]:
    root, resolution = _repository_root(repository)
    if resolution.status != "configured" or resolution.config is None:
        return _result(
            "update", "not_enabled", root, mutates=False,
            project_state=resolution.status, index_status="unavailable",
            connection_status="unchanged", next_action="atlas enable",
        ), 2
    config_path = resolution.config.resolve()
    config = AtlasConfig.load(config_path)
    previous = load_lifecycle_state(
        config.data_dir, config.repository, config.project
    )
    if previous.status not in {"ready", "stopped"}:
        return _result(
            "update", "blocked", root, mutates=False,
            project_state=previous.status, index_status="preserved",
            connection_status="unchanged",
            error="project must be ready or stopped before update",
        ), 2
    release = release_fetcher()
    current_version = previous.atlas_version or __version__
    latest_tuple = _version_tuple(release.version)
    current_tuple = _version_tuple(current_version)
    if latest_tuple is None or current_tuple is None:
        raise RuntimeError("Atlas version cannot be compared safely")
    if latest_tuple <= current_tuple:
        return _result(
            "update", "current", root, mutates=False,
            project_state=previous.status, index_status="preserved",
            connection_status="unchanged", latest_version=release.version,
        ), 0
    installation, installation_mutated = installer(release)
    operation_lock = _project_operation_lock(root)
    if not operation_lock.acquire():
        return _result(
            "update", "blocked", root, mutates=installation_mutated,
            project_state=previous.status, index_status="preserved",
            connection_status="unchanged",
            error="another lifecycle operation owns this project",
        ), 2
    refresh = ProjectRefreshLease(config.data_dir, config.repository, config.project)
    config_identity: tuple[int, int] | None = None
    config_bytes = b""
    codex_target: Path | None = None
    codex_identity: tuple[int, int] | None = None
    codex_bytes = b""
    lifecycle_mutated = False
    try:
        if not _acquire_refresh(refresh, timeout_seconds=timeout_seconds):
            raise RuntimeError("timed out waiting for the active project refresh")
        current = load_lifecycle_state(
            config.data_dir, config.repository, config.project
        )
        if current.operation_generation != previous.operation_generation:
            raise RuntimeError("project lifecycle changed while preparing update")
        operation_id = secrets.token_hex(16)
        updating = current.transition("updating", operation_id=operation_id)
        publish_lifecycle_state(config.data_dir, updating)
        lifecycle_mutated = True
        preview = codex_plan(config_path, scope="project", codex_project_root=root)
        if preview["status"] == "blocked":
            raise RuntimeError("project Codex MCP configuration conflicts with Atlas")
        codex_target = Path(str(preview["target"]))
        if codex_target.exists():
            codex_identity, codex_bytes = _regular_snapshot(codex_target)
        config_identity, config_bytes = _regular_snapshot(config_path)
        candidate = replace(config, cbm_binary=installation.provider_binary)
        candidate.write_verified(config_path, config_identity)
        codex_apply(
            config_path, scope="project", codex_project_root=root,
            atlas_executable=installation.atlas_executable,
        )
        candidate_ready = updating.transition(
            "ready",
            atlas_version=installation.version,
            provider_version=installation.provider_version,
        )
        publish_lifecycle_state(config.data_dir, candidate_ready)
        doctor = _external_doctor(installation, config_path, runner=runner)
        inspection = inspect_installation(candidate, deep=True)
        verification = _verification_query(
            candidate, config_path,
            executable=installation.atlas_executable, runner=runner,
        )
        if not inspection.get("ok"):
            raise RuntimeError("updated Provider deep inspection failed")
        final = (
            candidate_ready
            if previous.status == "ready"
            else candidate_ready.transition("stopped")
        )
        if final != candidate_ready:
            publish_lifecycle_state(config.data_dir, final)
        return _result(
            "update", "updated", root,
            mutates=True,
            project_state=final.status, index_status="fresh",
            connection_status="configured_task_start_required",
            previous_version=current_version,
            atlas_version=installation.version,
            provider_version=installation.provider_version,
            installation_reused=not installation_mutated,
            doctor=doctor.get("status"), verification=verification,
            current_session_refresh_required=True,
        ), 0
    except (OSError, RuntimeError, ValueError) as exc:
        rollback_errors = []
        if config_identity is not None:
            try:
                AtlasConfig.restore_verified(config_path, config_identity, config_bytes)
            except (OSError, ValueError) as rollback:
                rollback_errors.append(f"config: {rollback}")
        if codex_target is not None:
            try:
                if codex_identity is not None:
                    AtlasConfig.restore_verified(
                        codex_target, codex_identity, codex_bytes
                    )
                elif codex_target.exists():
                    from .codex_integration import codex_remove
                    codex_remove(config_path, scope="project", codex_project_root=root)
            except (OSError, RuntimeError, ValueError) as rollback:
                rollback_errors.append(f"codex: {rollback}")
        if lifecycle_mutated:
            try:
                rollback_state = load_lifecycle_state(
                    config.data_dir, config.repository, config.project,
                    missing_status=previous.status,
                ).transition(
                    previous.status,
                    atlas_version=previous.atlas_version,
                    provider_version=previous.provider_version,
                    index_generation=previous.index_generation,
                    failure_reason=(
                        previous.failure_reason if previous.status == "failed" else ""
                    ),
                )
                publish_lifecycle_state(config.data_dir, rollback_state)
            except (OSError, ValueError) as rollback:
                rollback_errors.append(f"lifecycle: {rollback}")
        detail = str(exc)
        if rollback_errors:
            detail += "; rollback failed: " + "; ".join(rollback_errors)
        return _result(
            "update", "incomplete", root,
            mutates=installation_mutated or lifecycle_mutated,
            project_state=previous.status, index_status="preserved",
            connection_status="unchanged", error=detail,
            previous_version=current_version,
        ), 2
    finally:
        refresh.release()
        operation_lock.release()


def _write_recovery_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _unlink_verified(path: Path, identity: tuple[int, int]) -> None:
    current = os.lstat(path)
    if (
        not stat.S_ISREG(current.st_mode)
        or stat.S_ISLNK(current.st_mode)
        or (current.st_dev, current.st_ino) != identity
    ):
        raise RuntimeError("managed file changed before recoverable removal")
    path.unlink()


def _restore_snapshot(
    path: Path, identity: tuple[int, int], payload: bytes
) -> None:
    if path.exists():
        AtlasConfig.restore_verified(path, identity, payload)
    else:
        _write_recovery_file(path, payload)


def remove_project(
    repository: Path, *, timeout_seconds: float = 30.0
) -> tuple[dict[str, Any], int]:
    root, resolution = _repository_root(repository)
    existing_marker = load_removal_marker(root)
    if existing_marker is not None and existing_marker["status"] == "removed":
        return _result(
            "remove", "removed", root, mutates=False,
            project_state="removed", index_status="recovery_area",
            connection_status="removed", receipt=existing_marker["receipt"],
        ), 0
    if resolution.status != "configured" or resolution.config is None:
        return _result(
            "remove", "not_enabled", root, mutates=False,
            project_state=resolution.status, index_status="unavailable",
            connection_status="unchanged",
        ), 0
    config_path = resolution.config.resolve()
    config = AtlasConfig.load(config_path)
    operation_lock = _project_operation_lock(root)
    if not operation_lock.acquire():
        return _result(
            "remove", "blocked", root, mutates=False,
            project_state="busy", index_status="preserved",
            connection_status="unchanged",
            error="another lifecycle operation owns this project",
        ), 2
    refresh = ProjectRefreshLease(config.data_dir, config.repository, config.project)
    refresh_acquired = False
    operation_id = secrets.token_hex(16)
    recovery_root = project_recovery_root(root).resolve()
    operation_root = recovery_root / operation_id
    receipt_path = operation_root / "receipt.json"
    data_destination = operation_root / "data"
    config_destination = operation_root / "project-config.toml"
    config_identity: tuple[int, int] | None = None
    config_bytes = b""
    codex_target: Path | None = None
    codex_identity: tuple[int, int] | None = None
    codex_bytes = b""
    marker_identity: tuple[int, int] | None = None
    data_moved = False
    config_removed = False
    codex_changed = False
    previous: ProjectLifecycleState | None = None
    try:
        if existing_marker is not None:
            raise RuntimeError("an incomplete prior removal requires recovery")
        recovery_root.mkdir(parents=True, exist_ok=True)
        if recovery_root.is_symlink() or not recovery_root.is_dir():
            raise RuntimeError("project recovery root must be a real directory")
        if os.name != "nt":
            recovery_root.chmod(0o700)
        operation_root.mkdir(mode=0o700)
        if os.stat(config.data_dir).st_dev != os.stat(operation_root).st_dev:
            raise RuntimeError(
                "custom data directory is on another filesystem; refusing non-atomic removal"
            )
        if not _acquire_refresh(refresh, timeout_seconds=timeout_seconds):
            raise RuntimeError("timed out waiting for the active project refresh")
        refresh_acquired = True
        previous = load_lifecycle_state(
            config.data_dir, config.repository, config.project
        )
        if previous.status not in {"ready", "stopped", "failed"}:
            raise RuntimeError("project lifecycle is not stable enough to remove")
        config_identity, config_bytes = _regular_snapshot(config_path)
        preview = codex_plan(config_path, scope="project", codex_project_root=root)
        if preview["status"] == "blocked":
            raise RuntimeError("project Codex MCP configuration conflicts with Atlas")
        codex_target = Path(str(preview["target"]))
        if codex_target.exists():
            codex_identity, codex_bytes = _regular_snapshot(codex_target)
        removing = previous.transition("removing", operation_id=operation_id)
        publish_lifecycle_state(config.data_dir, removing)
        publish_removal_marker(
            root, config.project, operation_id, receipt_path, status="removing"
        )
        marker_meta = os.lstat(removal_marker_path(root))
        marker_identity = (marker_meta.st_dev, marker_meta.st_ino)
        codex_result = codex_remove(
            config_path, scope="project", codex_project_root=root
        )
        codex_changed = bool(codex_result.get("mutates"))
        _write_recovery_file(config_destination, config_bytes)
        if hashlib.sha256(config_destination.read_bytes()).digest() != hashlib.sha256(config_bytes).digest():
            raise RuntimeError("recovered project config digest mismatch")
        _unlink_verified(config_path, config_identity)
        config_removed = True
        removed_state = removing.transition("removed")
        publish_lifecycle_state(config.data_dir, removed_state)
        refresh.release()
        refresh_acquired = False
        os.replace(config.data_dir, data_destination)
        data_moved = True
        receipt = {
            "schema_version": 1,
            "status": "removed",
            "operation_id": operation_id,
            "repository": str(root),
            "project": config.project,
            "original_config": str(config_path),
            "recovered_config": str(config_destination),
            "original_data_dir": str(config.data_dir),
            "recovered_data_dir": str(data_destination),
            "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "codex_config_changed": codex_changed,
            "shared_installation_removed": False,
        }
        _write_recovery_file(
            receipt_path,
            (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        publish_removal_marker(
            root, config.project, operation_id, receipt_path, status="removed"
        )
        return _result(
            "remove", "removed", root, mutates=True,
            project_state="removed", index_status="recovery_area",
            connection_status="removed", receipt=str(receipt_path),
            recovery_data=str(data_destination),
        ), 0
    except (OSError, RuntimeError, ValueError) as exc:
        rollback_errors = []
        if data_moved:
            try:
                if config.data_dir.exists():
                    raise RuntimeError("original data directory was recreated")
                os.replace(data_destination, config.data_dir)
                data_moved = False
            except (OSError, RuntimeError) as rollback:
                rollback_errors.append(f"data: {rollback}")
        if config_removed and config_identity is not None:
            try:
                _restore_snapshot(config_path, config_identity, config_bytes)
                config_removed = False
            except (OSError, ValueError) as rollback:
                rollback_errors.append(f"config: {rollback}")
        if codex_changed and codex_target is not None and codex_identity is not None:
            try:
                _restore_snapshot(codex_target, codex_identity, codex_bytes)
            except (OSError, ValueError) as rollback:
                rollback_errors.append(f"codex: {rollback}")
        if previous is not None and config.data_dir.exists():
            try:
                current = load_lifecycle_state(
                    config.data_dir, config.repository, config.project,
                    missing_status=previous.status,
                )
                restored = current.transition(
                    previous.status,
                    atlas_version=previous.atlas_version,
                    provider_version=previous.provider_version,
                    index_generation=previous.index_generation,
                    failure_reason=(
                        previous.failure_reason if previous.status == "failed" else ""
                    ),
                )
                publish_lifecycle_state(config.data_dir, restored)
            except (OSError, ValueError) as rollback:
                rollback_errors.append(f"lifecycle: {rollback}")
        if marker_identity is not None:
            try:
                _unlink_verified(removal_marker_path(root), marker_identity)
            except (OSError, RuntimeError) as rollback:
                rollback_errors.append(f"marker: {rollback}")
        detail = str(exc)
        if rollback_errors:
            detail += "; rollback failed: " + "; ".join(rollback_errors)
        return _result(
            "remove", "incomplete", root,
            mutates=bool(config_removed or data_moved or codex_changed),
            project_state=previous.status if previous else "unknown",
            index_status="preserved", connection_status="unchanged",
            error=detail,
        ), 2
    finally:
        if refresh_acquired:
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
    update = commands.add_parser("update", help="update this project to the latest stable Atlas Release")
    update.add_argument("--repo", type=Path, default=Path.cwd())
    update.add_argument("--timeout", type=float, default=30.0)
    update.add_argument("--json", action="store_true")
    remove = commands.add_parser("remove", help="recoverably remove Atlas from this project")
    remove.add_argument("--repo", type=Path, default=Path.cwd())
    remove.add_argument("--timeout", type=float, default=30.0)
    remove.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "enable":
            payload, code = enable_project(
                args.repo, config_path=args.config, language=args.language,
                node=args.node, cbm_binary=args.cbm_binary,
                serena_python=args.serena_python, node_bin_dir=args.node_bin_dir,
                tsconfig=args.tsconfig, data_dir=args.data_dir, mode=args.mode,
            )
        elif args.command == "stop":
            if args.timeout < 0 or args.timeout > 300:
                raise ValueError("--timeout must be between 0 and 300 seconds")
            payload, code = stop_project(args.repo, timeout_seconds=args.timeout)
        elif args.command == "update":
            if args.timeout < 0 or args.timeout > 300:
                raise ValueError("--timeout must be between 0 and 300 seconds")
            payload, code = update_project(args.repo, timeout_seconds=args.timeout)
        else:
            if args.timeout < 0 or args.timeout > 300:
                raise ValueError("--timeout must be between 0 and 300 seconds")
            payload, code = remove_project(args.repo, timeout_seconds=args.timeout)
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
