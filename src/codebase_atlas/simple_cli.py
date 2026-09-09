"""Simple project lifecycle and diagnostics for Codebase Atlas."""

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
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

from . import __version__
from .cli import _index_repository, main as advanced_main
from .codex_integration import (
    _remove_project_block,
    codex_apply,
    codex_plan,
    codex_remove,
)
from .config import AtlasConfig, CONFIG_NAME, diagnose
from .index_state import index_freshness
from .lifecycle import (
    ProjectOperationLease,
    ProjectRefreshLease,
    default_project_operation_dir,
)
from .maintenance import inspect_installation
from .onboarding import OnboardingInputs, apply_plan, build_plan
from .operations import operational_index_status
from .project_discovery import ProjectResolution, resolve_project
from .project_lifecycle import (
    ProjectLifecycleState,
    lifecycle_state_path,
    load_lifecycle_state,
    load_removal_marker,
    operational_lifecycle_status,
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
    load_versioned_installation,
)
from .version_check import _version_tuple
from .verification_state import protected_snapshot
from .routing_assets import decode_routing_bundle, routing_bundle
from .routing_transaction import RoutingTransaction
from .routing_state import load_routing_state, publish_routing_state
from .enable_transaction import EnableTransaction
from .lifecycle_recovery import (
    LifecycleRecoveryJournal,
    journal_path as lifecycle_recovery_path,
    recover_lifecycle_transaction,
)
from .provider_transport import CodebaseMemoryMcpTransport
from .providers.cbm_impact import CodebaseMemoryImpactProvider

STATUS_MAX_DIRECTORIES = 4096
STATUS_MAX_DEPTH = 6
STATUS_DISCOVERY_TIMEOUT_SECONDS = 2.0
STATUS_GIT_PROBE_TIMEOUT_SECONDS = 0.25


def _nested_git_repositories(repository: Path) -> dict[str, Any]:
    """Find bounded, real nested Git roots without following symlinks."""
    roots: list[str] = []
    visited = 0
    partial_reason = ""
    started = time.monotonic()
    deadline = started + STATUS_DISCOVERY_TIMEOUT_SECONDS
    def scan_error(_error: OSError) -> None:
        nonlocal partial_reason
        partial_reason = "nested_repository_scan_unavailable"

    for current, directories, _files in os.walk(
        repository, followlinks=False, onerror=scan_error
    ):
        if time.monotonic() >= deadline:
            partial_reason = "nested_repository_time_budget_exceeded"
            break
        current_path = Path(current)
        depth = len(current_path.relative_to(repository).parts)
        directories[:] = sorted(
            name for name in directories
            if name != ".git" and not (current_path / name).is_symlink()
        )
        if depth >= STATUS_MAX_DEPTH:
            if directories:
                partial_reason = "nested_repository_depth_budget_exceeded"
            directories[:] = []
            continue
        retained: list[str] = []
        for name in directories:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                partial_reason = "nested_repository_time_budget_exceeded"
                directories[:] = []
                break
            visited += 1
            if visited > STATUS_MAX_DIRECTORIES:
                partial_reason = "nested_repository_directory_budget_exceeded"
                directories[:] = []
                break
            candidate = current_path / name
            marker = candidate / ".git"
            if os.path.lexists(marker):
                if marker.is_symlink():
                    partial_reason = "nested_repository_marker_unsafe"
                    continue
                try:
                    probe = subprocess.run(
                        ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
                        capture_output=True, text=True, check=False,
                        timeout=min(STATUS_GIT_PROBE_TIMEOUT_SECONDS, remaining),
                    )
                    if probe.returncode == 0 and Path(probe.stdout.strip()).resolve() == candidate.resolve():
                        roots.append(candidate.relative_to(repository).as_posix())
                    else:
                        partial_reason = "nested_repository_identity_unverified"
                except (OSError, subprocess.TimeoutExpired):
                    partial_reason = "nested_repository_identity_unavailable"
            else:
                retained.append(name)
        else:
            directories[:] = retained
            continue
        break
    return {
        "status": "partial" if partial_reason else "complete",
        "repositories": roots,
        "visited_directories": min(visited, STATUS_MAX_DIRECTORIES),
        "max_directories": STATUS_MAX_DIRECTORIES,
        "max_depth": STATUS_MAX_DEPTH,
        "timeout_seconds": STATUS_DISCOVERY_TIMEOUT_SECONDS,
        "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
        "reason": partial_reason or "bounded_scan_complete",
    }


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


def _recover_before_mutation(
    repository: Path, operation: str
) -> tuple[dict[str, Any], int] | None:
    lock = _project_operation_lock(repository)
    if not lock.acquire():
        return _result(
            operation, "blocked", repository, mutates=False,
            project_state="busy", index_status="preserved",
            connection_status="unchanged",
            error="another lifecycle operation owns this project",
        ), 2
    try:
        removal = load_removal_marker(repository)
        if removal is not None and removal["status"] == "removing":
            _recover_incomplete_removal(repository, removal)
        recover_lifecycle_transaction(repository)
    except (OSError, RuntimeError, ValueError) as exc:
        return _result(
            operation, "incomplete", repository, mutates=False,
            project_state="unknown", index_status="unknown",
            connection_status="unchanged", error=str(exc),
            reason_code="lifecycle_recovery_required",
        ), 2
    finally:
        lock.release()
    return None


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
    if isinstance(receipt, dict) and receipt.get("schema_version") in {2, 3}:
        required.add("routing_assets")
    if isinstance(receipt, dict) and receipt.get("schema_version") == 3:
        required.update({
            "config_mode", "previous_lifecycle", "codex_config_path",
            "codex_config_existed", "codex_config_sha256",
            "codex_removed_sha256", "codex_config_mode", "codex_backup",
        })
    if (
        not isinstance(receipt, dict)
        or set(receipt) != required
        or receipt.get("schema_version") not in {1, 2, 3}
        or receipt.get("status") not in {"removing", "removed"}
        or (
            receipt.get("status") == "removing"
            and receipt.get("schema_version") != 3
        )
        or receipt.get("operation_id") != marker["operation_id"]
        or receipt.get("repository") != str(repository.resolve())
        or receipt.get("project") != marker["project"]
    ):
        raise RuntimeError("removal receipt schema or identity is invalid")
    if marker["status"] == "removed" and receipt.get("status") != "removed":
        raise RuntimeError("removal marker and receipt status mismatch")
    if receipt.get("schema_version") == 3:
        hashes = (
            receipt.get("config_sha256"), receipt.get("codex_config_sha256"),
            receipt.get("codex_removed_sha256"),
        )
        if (
            type(receipt.get("config_mode")) is not int
            or type(receipt.get("codex_config_mode")) is not int
            or not 0 <= receipt["config_mode"] <= 0o777
            or not 0 <= receipt["codex_config_mode"] <= 0o777
            or type(receipt.get("codex_config_existed")) is not bool
            or not isinstance(receipt.get("codex_config_path"), str)
            or not isinstance(receipt.get("codex_backup"), str)
            or not isinstance(receipt.get("previous_lifecycle"), dict)
            or not all(
                value == "absent" or (
                    isinstance(value, str)
                    and re.fullmatch(r"[0-9a-f]{64}", value) is not None
                ) for value in hashes
            )
        ):
            raise RuntimeError("removal receipt recovery fields are invalid")
    return receipt


def _restore_removed_project(
    repository: Path, marker: dict[str, Any]
) -> None:
    receipt = _load_removal_receipt(repository, marker)
    if receipt["status"] != "removed":
        raise RuntimeError("removal is incomplete and must be recovered first")
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
    routing = RoutingTransaction.for_recovery(repository, receipt.get("routing_assets", []))
    operation_lock = _project_operation_lock(repository)
    if not operation_lock.acquire():
        raise RuntimeError("another lifecycle operation owns this project")
    restored_config_identity: tuple[int, int] | None = None
    data_restored = False
    lifecycle_before: bytes | None = None
    lifecycle_after: tuple[tuple[int, int], bytes] | None = None
    try:
        current_marker = load_removal_marker(repository)
        if current_marker != marker:
            raise RuntimeError("removal marker changed before recovery")
        routing.apply()
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
        state_path = lifecycle_state_path(data_dir)
        lifecycle_before = state_path.read_bytes()
        publish_lifecycle_state(data_dir, removed_state.transition("stopped"))
        lifecycle_after = _regular_snapshot(state_path)
        marker_path = removal_marker_path(repository)
        marker_meta = os.lstat(marker_path)
        _unlink_verified(marker_path, (marker_meta.st_dev, marker_meta.st_ino))
    except (OSError, RuntimeError, ValueError) as exc:
        routing_errors = routing.rollback()
        if lifecycle_after is not None and lifecycle_before is not None:
            try:
                state_path = lifecycle_state_path(data_dir)
                if _regular_snapshot(state_path) != lifecycle_after:
                    raise RuntimeError("lifecycle changed during recovery rollback")
                AtlasConfig.restore_verified(state_path, lifecycle_after[0], lifecycle_before)
            except (OSError, RuntimeError, ValueError) as rollback:
                routing_errors.append(f"lifecycle: {rollback}")
        if restored_config_identity is not None and config_path.exists():
            try:
                _unlink_verified(config_path, restored_config_identity)
            except (OSError, RuntimeError) as rollback:
                routing_errors.append(f"config: {rollback}")
        if data_restored and data_dir.exists() and not recovered_data.exists():
            try:
                os.replace(data_dir, recovered_data)
            except OSError as rollback:
                routing_errors.append(f"data: {rollback}")
        if routing_errors:
            raise RuntimeError(str(exc) + "; recovery rollback incomplete: " + "; ".join(routing_errors)) from exc
        raise
    finally:
        operation_lock.release()


def _publish_recovery_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".removal-receipt-", suffix=".json", dir=path.parent
    )
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)


def _path_digest(path: Path) -> str:
    if not os.path.lexists(path):
        return "absent"
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("removal recovery target is unsafe")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _restore_recovery_backup(path: Path, backup: Path, mode: int) -> None:
    if backup.is_symlink() or not backup.is_file():
        raise RuntimeError("removal recovery backup is unsafe")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".atlas-remove-restore-", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as output, backup.open("rb") as source:
            descriptor = -1
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)


def _recover_incomplete_removal(
    repository: Path, marker: dict[str, Any]
) -> dict[str, Any]:
    if marker["status"] != "removing":
        return {"status": "clean", "action": "none"}
    receipt = _load_removal_receipt(repository, marker)
    if receipt.get("schema_version") != 3:
        raise RuntimeError("legacy incomplete removal requires manual recovery")
    receipt_path = Path(marker["receipt"])
    operation_root = receipt_path.parent.resolve()
    recovery_root = project_recovery_root(repository).resolve()
    if (
        operation_root.parent != recovery_root
        or receipt_path.resolve() != operation_root / "receipt.json"
        or operation_root.is_symlink()
        or not operation_root.is_dir()
    ):
        raise RuntimeError("removal recovery receipt location is unsafe")
    if receipt["status"] == "removed":
        publish_removal_marker(
            repository, receipt["project"], receipt["operation_id"], receipt_path,
            status="removed",
        )
        return {"status": "recovered", "action": "completed_removal"}

    config_path = Path(receipt["original_config"])
    config_backup = Path(receipt["recovered_config"])
    data_dir = Path(receipt["original_data_dir"])
    data_backup = Path(receipt["recovered_data_dir"])
    codex_path = Path(receipt["codex_config_path"])
    codex_backup = Path(receipt["codex_backup"]) if receipt["codex_backup"] else None
    if (
        config_backup.resolve() != operation_root / "project-config.toml"
        or data_backup.resolve() != operation_root / "data"
    ):
        raise RuntimeError("removal recovery paths are unsafe")
    if config_path.resolve() != Path(receipt["original_config"]).resolve():
        raise RuntimeError("removal recovery config path is unsafe")
    if not config_path.resolve().is_relative_to(repository.resolve()):
        raise RuntimeError("removal recovery config is outside the repository")
    if codex_path.resolve() != repository.resolve() / ".codex/config.toml":
        raise RuntimeError("removal recovery Codex path is unsafe")
    if codex_backup is not None and codex_backup.resolve() != operation_root / "codex-config.toml":
        raise RuntimeError("removal recovery Codex backup is unsafe")
    if hashlib.sha256(config_backup.read_bytes()).hexdigest() != receipt["config_sha256"]:
        raise RuntimeError("removal recovery config checksum mismatch")
    recovered_config = AtlasConfig.load(config_backup)
    if (
        recovered_config.repository != repository.resolve()
        or recovered_config.project != receipt["project"]
        or recovered_config.data_dir != data_dir.resolve()
    ):
        raise RuntimeError("removal recovery config identity mismatch")
    config_current = _path_digest(config_path)
    if config_current not in {receipt["config_sha256"], "absent"}:
        raise RuntimeError("external config modification preserved")
    codex_current = _path_digest(codex_path)
    if codex_current not in {
        receipt["codex_config_sha256"], receipt["codex_removed_sha256"]
    }:
        raise RuntimeError("external Codex modification preserved")
    for directory in (data_dir, data_backup):
        if os.path.lexists(directory):
            metadata = os.lstat(directory)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise RuntimeError("removal recovery data directory is unsafe")
    if data_dir.exists() and data_backup.exists():
        raise RuntimeError("both original and recovered data directories exist")
    if not data_dir.exists() and not data_backup.exists():
        raise RuntimeError("removal recovery data directory is missing")
    previous_value = receipt["previous_lifecycle"]
    try:
        previous = ProjectLifecycleState(**previous_value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("removal recovery lifecycle snapshot is invalid") from exc
    if (
        previous.repository != str(repository.resolve())
        or previous.project != receipt["project"]
        or previous.status not in {"ready", "stopped", "failed"}
    ):
        raise RuntimeError("removal recovery lifecycle identity is invalid")
    routing = RoutingTransaction.for_recovery(repository, receipt["routing_assets"])

    marker_metadata = os.lstat(removal_marker_path(repository))
    marker_identity = (marker_metadata.st_dev, marker_metadata.st_ino)
    routing.apply_recovery()
    if data_backup.exists():
        os.replace(data_backup, data_dir)
    if config_current == "absent":
        _restore_recovery_backup(config_path, config_backup, receipt["config_mode"])
    if receipt["codex_config_existed"]:
        if codex_backup is None:
            raise RuntimeError("removal recovery Codex backup is missing")
        if codex_current != receipt["codex_config_sha256"]:
            _restore_recovery_backup(
                codex_path, codex_backup, receipt["codex_config_mode"]
            )
    elif codex_current != "absent":
        codex_path.unlink()
    publish_lifecycle_state(data_dir, previous)
    _unlink_verified(removal_marker_path(repository), marker_identity)
    shutil.rmtree(operation_root)
    return {"status": "recovered", "action": "restored_incomplete_removal"}


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
        if any(part.startswith(".") for part in relative.parts):
            continue
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
    deprioritized = {
        "example", "examples", "fixture", "fixtures", "node_modules",
        "test", "tests", "third_party", "vendor",
    }

    def acceptance_rank(relative: Path) -> tuple[int, str]:
        parts = tuple(part.lower() for part in relative.parts)
        if parts and parts[0] in {"app", "lib", "src"}:
            group = 0
        elif any(part in deprioritized for part in parts):
            group = 2
        else:
            group = 1
        return group, relative.as_posix()

    paths.sort(key=acceptance_rank)
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
    _require_complete_verification_response(positive)
    nodes = positive.get("nodes")
    if not isinstance(nodes, list) or not any(
        isinstance(node, dict)
        and any(
            isinstance(node.get(field), dict)
            and node[field].get("path") == target_path
            for field in ("location", "source")
        )
        for node in nodes
    ):
        raise RuntimeError("verification symbol did not resolve to the target source file")
    negative_symbol = "__atlas_wrong_project_" + secrets.token_hex(12)
    negative = _query_payload(
        config, config_path, negative_symbol, "",
        executable=executable, runner=runner,
    )
    _require_complete_verification_response(negative)
    if negative.get("nodes"):
        raise RuntimeError("cross-project negative verification returned unexpected facts")
    if (config.repository / target_path).read_bytes() != before:
        raise RuntimeError("target source changed during Atlas verification")
    return {
        "symbol": symbol,
        "target_path": target_path,
        "matched_nodes": len(nodes),
        "cross_project_negative": "pass",
        "negative_check_scope": "nonexistent_symbol_only",
    }


def _require_complete_verification_response(payload: dict[str, Any]) -> None:
    """An absent or incomplete result is not evidence of an empty answer."""
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        raise RuntimeError("verification query returned an invalid nodes result")
    if payload.get("truncated") or payload.get("error"):
        raise RuntimeError("verification query returned incomplete evidence")
    if payload.get("status") in {"partial", "failed", "error", "timeout", "not_run"}:
        raise RuntimeError("verification query did not complete")


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
    recovery_failure = _recover_before_mutation(root, "enable")
    if recovery_failure is not None:
        return recovery_failure
    root, resolution = _repository_root(root)
    try:
        RoutingTransaction(root, preserve_custom_skill=True)
    except (OSError, RuntimeError, ValueError) as exc:
        return _result(
            "enable", "blocked", root, mutates=False,
            project_state=resolution.status, index_status="unknown",
            connection_status="unchanged", reason_code="routing_preflight_failed",
            error=str(exc),
            next_action="review the existing routing assets; preserve user content before migration",
        ), 2
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
    transaction: EnableTransaction | None = None
    routing: RoutingTransaction | None = None
    durable: LifecycleRecoveryJournal | None = None
    refresh: ProjectRefreshLease | None = None
    try:
        plan, candidate = build_plan(inputs)
        if plan["status"] != "planned" or candidate is None:
            return _result(
                "enable", "blocked", root, mutates=False,
                project_state="not_enabled", index_status="unknown",
                connection_status="unchanged", error=plan.get("error", ""),
            ), 2
        if not candidate.project:
            candidate = candidate.with_project(_operation_project(root))
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
        refresh = ProjectRefreshLease(candidate.data_dir, root, candidate.project)
        if not _acquire_refresh(refresh, timeout_seconds=30):
            raise RuntimeError("timed out waiting for the active project refresh")
        transaction = EnableTransaction(candidate, selected_config)
        routing = RoutingTransaction(root, preserve_custom_skill=True)
        durable = LifecycleRecoveryJournal.begin(
            candidate, selected_config, operation="enable",
            operation_id=operation_id, routing=routing,
        )
        transaction.attach_recovery(durable)
        routing.apply()
        prior_routing_state = load_routing_state(candidate.data_dir, root)
        created_rule_file = (
            bool(prior_routing_state["created_rule_file"])
            if prior_routing_state is not None
            else routing.plans[0].before is None
        )
        transaction.run(lambda: publish_routing_state(
            candidate.data_dir, root, created_rule_file=created_rule_file
        ))
        if previous is not None and (previous.status != "ready" or not state_existed):
            transaction.run(lambda: publish_lifecycle_state(
                candidate.data_dir,
                previous.transition("enabling", operation_id=operation_id),
            ))
            state_mutated = True
        applied, code = transaction.run(lambda: apply_plan(
            plan, candidate, indexer=_index_repository, mode=mode
        ), indexes=True)
        if code != 0:
            raise RuntimeError(str(applied.get("error") or applied["status"]))
        configured = AtlasConfig.load(selected_config)
        codex_preview = codex_plan(
            selected_config, scope="project", codex_project_root=root
        )
        if codex_preview["status"] == "blocked":
            raise RuntimeError("project Codex MCP configuration conflicts with Atlas")
        transaction.allow_codex_plan(codex_preview)
        codex_result = transaction.run(lambda: codex_apply(
            selected_config, scope="project", codex_project_root=root
        ))
        checks = diagnose(configured)
        freshness = index_freshness(
            configured.data_dir, configured.repository, configured.project
        )
        inspection = inspect_installation(configured, deep=True)
        acceptance_state = load_lifecycle_state(
            configured.data_dir, configured.repository, configured.project,
            missing_status="ready",
        )
        if acceptance_state.status != "ready":
            transaction.run(lambda: publish_lifecycle_state(
                configured.data_dir,
                acceptance_state.transition("ready", atlas_version=__version__),
            ))
            state_mutated = True
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
            transaction.run(lambda: publish_lifecycle_state(configured.data_dir, final))
            state_mutated = True
        backup_cleaned = transaction.commit()
        durable_cleaned = durable.commit()
        return _result(
            "enable", "ready", root,
            mutates=bool(applied.get("config_created"))
            or bool(codex_result.get("mutates")) or state_mutated
            or restored_from_receipt or any(p.before != p.after for p in routing.plans),
            project_state="ready", index_status="fresh",
            connection_status="configured_task_start_required",
            config=str(selected_config), project=configured.project,
            verification=verification,
            current_session_refresh_required=True,
            routing_status=(
                "custom_preserved" if routing.preserved_conflicts else "installed"
            ),
            preserved_routing_assets=list(routing.preserved_conflicts),
            backup_cleanup="complete" if backup_cleaned and durable_cleaned else "pending",
        ), 0
    except BaseException as exc:
        if transaction is not None:
            rollback_errors = routing.rollback() if routing is not None else []
            rollback_errors.extend(transaction.rollback())
            if durable is not None:
                rollback_errors.extend(
                    f"durable: {error}" for error in durable.rollback()
                )
            if not isinstance(exc, (OSError, RuntimeError, ValueError)):
                if rollback_errors:
                    exc.add_note("enable rollback incomplete: " + "; ".join(rollback_errors))
                raise
            return _result(
                "enable", "incomplete", root, mutates=bool(rollback_errors) or restored_from_receipt,
                project_state=(previous.status if previous and was_operational else "not_enabled") if not rollback_errors else "unknown",
                index_status="preserved" if not rollback_errors else "unknown",
                connection_status="unchanged", error=str(exc),
                reason_code="enable_acceptance_failed", rollback_errors=rollback_errors,
                previous_state_preserved=not rollback_errors,
            ), 2
        if not isinstance(exc, (OSError, RuntimeError, ValueError)):
            raise
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
        if refresh is not None:
            refresh.release()
        operation_lock.release()


def _codex_project_status(config_path: Path, repository: Path) -> dict[str, Any]:
    try:
        plan = codex_plan(
            config_path, scope="project", codex_project_root=repository
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "status": "invalid",
            "ok": False,
            "reason": "codex_project_config_unreadable",
            "detail": str(exc),
        }
    existing = str(plan.get("existing", "unknown"))
    states = {
        "matching": ("configured", True, "codex_project_mcp_matches"),
        "absent": ("not_configured", False, "codex_project_mcp_missing"),
        "managed_different": ("outdated", False, "codex_project_mcp_outdated"),
        "conflict": ("conflict", False, "codex_project_mcp_conflict"),
    }
    status, ok, reason = states.get(
        existing, ("unknown", False, "codex_project_mcp_state_unknown")
    )
    return {
        "status": status,
        "ok": ok,
        "reason": reason,
        "existing": existing,
        "target": plan.get("target"),
    }


def status_project(repository: Path) -> tuple[dict[str, Any], int]:
    """Read the lightweight state of one exact project without starting providers."""
    root, resolution = _repository_root(repository)
    nested = _nested_git_repositories(root)
    if lifecycle_recovery_path(root).exists():
        return _result(
            "status", "incomplete", root, mutates=False,
            project_state="recovery_required", index_status="unknown",
            connection_status="unknown", reason_code="lifecycle_recovery_required",
            next_action=f"atlas enable --repo {root}",
            nested_repositories=nested, checks=[],
        ), 2
    removal = load_removal_marker(root)
    if removal is not None:
        state = str(removal["status"])
        discovery_complete = nested["status"] == "complete"
        return _result(
            "status", state if discovery_complete else "incomplete", root, mutates=False,
            project=str(removal["project"]), project_state=state,
            index_status="recovery_area", codex_config_status="unknown",
            current_task_connection="unknown", task_reload_required=None,
            nested_repositories=nested,
            checks=[{
                "name": "project_lifecycle", "ok": False,
                "status": state,
                "reason": "project_removed" if state == "removed" else "project_removal_in_progress",
            }],
            next_action=(
                f"atlas enable --repo {root}"
                if state == "removed"
                else "finish or recover the active removal operation"
            ),
            reason_code=(
                "project_removed" if discovery_complete and state == "removed"
                else "project_removal_in_progress" if discovery_complete
                else nested["reason"]
            ),
        ), 0 if discovery_complete else 2
    if resolution.config is None:
        discovery_complete = nested["status"] == "complete"
        return _result(
            "status", "not_enabled" if discovery_complete else "incomplete", root,
            mutates=False,
            project_state="not_enabled", index_status="unavailable",
            codex_config_status="not_configured",
            current_task_connection="unknown", task_reload_required=None,
            nested_repositories=nested,
            checks=[{
                "name": "project_discovery", "ok": True,
                "status": resolution.status, "reason": resolution.reason,
            }],
            next_action=f"atlas enable --repo {root}",
            reason_code=(
                "atlas_not_enabled" if discovery_complete else nested["reason"]
            ),
        ), 0 if discovery_complete else 2
    config = AtlasConfig.load(resolution.config)
    lifecycle = operational_lifecycle_status(
        config.data_dir, config.repository, config.project
    )
    index = operational_index_status(
        config.data_dir, config.repository, config.cache_dir, config.project
    )
    codex = _codex_project_status(resolution.config, root)
    project_state = str(lifecycle.get("status", "unknown"))
    index_status = str(index.get("status", "unknown"))
    if project_state != "ready":
        observed = project_state
        next_action = (
            f"atlas enable --repo {root}"
            if project_state in {"stopped", "failed"}
            else "wait for the active Atlas lifecycle operation to finish"
        )
    elif not bool(index.get("ok")):
        observed = index_status
        next_action = f"atlas enable --repo {root}"
    elif not bool(codex.get("ok")):
        observed = "incomplete"
        next_action = f"atlas enable --repo {root}"
    else:
        observed = "ready"
        next_action = "none"
    if nested["status"] != "complete":
        observed = "incomplete"
        next_action = "inspect the partial nested repository discovery result"
    reason_code = (
        str(nested["reason"])
        if nested["status"] != "complete"
        else "project_ready" if observed == "ready"
        else "project_state_observed"
    )
    return _result(
        "status", observed, root, mutates=False,
        project=config.project, project_state=project_state,
        index_status=index_status, codex_config_status=codex["status"],
        current_task_connection="unknown", task_reload_required=None,
        nested_repositories=nested,
        checks=[
            {"name": "project_lifecycle", **lifecycle},
            {"name": "index", **index},
            {"name": "codex_project_mcp", **codex},
        ],
        next_action=next_action, reason_code=reason_code,
    ), 0 if nested["status"] == "complete" else 2


class _VerificationTransport:
    """Reject incomplete raw search evidence before node normalization."""

    def __init__(self, transport: CodebaseMemoryMcpTransport):
        self.transport = transport

    def call(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = self.transport.call(*args, **kwargs)
        if (not isinstance(payload, dict)
                or not isinstance(payload.get("groups"), list)
                or not isinstance(payload.get("cols"), list)
                or payload.get("truncated") or payload.get("error")
                or payload.get("status") in {"partial", "failed", "error", "timeout", "not_run"}):
            raise RuntimeError("verification search returned incomplete evidence")
        if any(not isinstance(group, dict) or not isinstance(group.get("rows"), list)
               for group in payload["groups"]):
            raise RuntimeError("verification search returned malformed groups")
        return payload


def _owned_verification_query(config: AtlasConfig) -> dict[str, Any]:
    symbol, path, _before = _verification_candidate(config)
    transport = CodebaseMemoryMcpTransport(
        config.cbm_binary, config.repository, config.cache_dir,
        exclusive=config.provider_layout != "shared-v1",
        client_version=__version__, managed_cache=config.provider_layout == "shared-v1",
    )
    process = None
    try:
        transport.start(timeout_seconds=20)
        process = transport.process
        provider = CodebaseMemoryImpactProvider(
            config.cbm_binary, config.repository, config.cache_dir, config.project,
            transport=_VerificationTransport(transport),
        )
        nodes = provider.definitions(symbol, target_path=path)
        if not nodes or any(node.location.path != path for node in nodes):
            raise RuntimeError("verification target did not resolve to its exact file")
        if provider.definitions("__atlas_absent_" + secrets.token_hex(12)):
            raise RuntimeError("verification nonexistent symbol returned facts")
    finally:
        transport.close()
    if process is None or process.poll() is None:
        raise RuntimeError("verification Provider child cleanup not confirmed")
    return {
        "symbol": symbol, "target_path": path, "matched_nodes": len(nodes),
        "nonexistent_symbol": "pass", "owned_process_cleanup": "pass",
        "process_id": process.pid,
        "process_scope": "owned_stdio_child; shared daemon preserved",
    }


def verify_project(repository: Path) -> tuple[dict[str, Any], int]:
    """Run the existing acceptance checks without refreshing project state."""
    root, resolution = _repository_root(repository)
    if lifecycle_recovery_path(root).exists():
        return _result(
            "verify", "INCOMPLETE", root, mutates=False,
            project_state="recovery_required", index_status="unknown",
            connection_status="unknown", reason_code="lifecycle_recovery_required",
            next_action=f"atlas enable --repo {root}", checks=[],
        ), 2
    if resolution.config is None:
        return _result(
            "verify", "INCOMPLETE", root, mutates=False,
            project_state="not_enabled", index_status="unavailable",
            connection_status="not_configured",
            reason_code="atlas_not_enabled",
            next_action=f"atlas enable --repo {root}", checks=[],
        ), 2
    config = AtlasConfig.load(resolution.config)
    lifecycle = operational_lifecycle_status(
        config.data_dir, config.repository, config.project
    )
    if lifecycle.get("status") == "stopped":
        return _result(
            "verify", "BLOCKED", root, mutates=False,
            project=config.project, project_state="stopped",
            index_status="preserved", connection_status="stopped",
            reason_code="project_stopped",
            next_action=f"atlas enable --repo {root}",
            checks=[{"name": "project_lifecycle", **lifecycle}],
        ), 4
    checks = diagnose(config)
    freshness = index_freshness(
        config.data_dir, config.repository, config.project
    )
    inspection = inspect_installation(config, deep=True)
    codex = _codex_project_status(resolution.config, root)
    results: list[dict[str, Any]] = [
        {"name": "project_lifecycle", **lifecycle},
        {"name": "runtime", "ok": required_checks_ok(checks), "checks": checks},
        {"name": "index_freshness", **freshness},
        {
            "name": "provider_database", "ok": bool(inspection.get("ok")),
            "status": inspection.get("status"),
            "findings": inspection.get("findings", []),
        },
        {"name": "codex_project_mcp", **codex},
    ]
    prerequisites_ok = (
        bool(lifecycle.get("ok"))
        and required_checks_ok(checks)
        and freshness.get("status") == "fresh"
        and bool(inspection.get("ok"))
        and bool(codex.get("ok"))
    )
    if not prerequisites_ok:
        return _result(
            "verify", "INCOMPLETE", root, mutates=False,
            project=config.project,
            project_state=str(lifecycle.get("status", "unknown")),
            index_status=str(freshness.get("status", "unknown")),
            connection_status=str(codex.get("status", "unknown")),
            reason_code="acceptance_prerequisite_failed",
            next_action=f"atlas enable --repo {root}", checks=results,
        ), 2
    try:
        before = protected_snapshot(config, resolution.config)
        query = _owned_verification_query(config)
        if query.get("owned_process_cleanup") != "pass":
            raise RuntimeError("verification process cleanup not confirmed")
        after = protected_snapshot(config, resolution.config)
        if before != after:
            raise RuntimeError("protected state changed during verification")
    except (OSError, RuntimeError, ValueError) as exc:
        results.append({
            "name": "verification_query", "ok": False,
            "status": "failed", "reason": str(exc),
        })
        return _result(
            "verify", "INCOMPLETE", root, mutates=False,
            project=config.project, project_state="ready", index_status="fresh",
            connection_status="configured",
            reason_code="verification_query_failed",
            next_action="inspect the failed verification query", checks=results,
        ), 2
    results.append({"name": "verification_query", "ok": True, **query})
    results.extend([
        {"name": "protected_state_unchanged", "status": "pass", "ok": True},
        {"name": "owned_process_cleanup", "status": query.get("owned_process_cleanup"),
         "ok": query.get("owned_process_cleanup") == "pass"},
        {"name": "cross_repository_isolation", "status": "not_run", "required": False},
        {"name": "current_codex_task_connection", "status": "not_run", "required": False},
    ])
    return _result(
        "verify", "PASS", root, mutates=False,
        project=config.project, project_state="ready", index_status="fresh",
        connection_status="configured", reason_code="target_checks_passed",
        next_action="none",
        checks=results, verification=query,
    ), 0


def stop_project(
    repository: Path, *, timeout_seconds: float = 30.0
) -> tuple[dict[str, Any], int]:
    root, resolution = _repository_root(repository)
    recovery_failure = _recover_before_mutation(root, "stop")
    if recovery_failure is not None:
        return recovery_failure
    root, resolution = _repository_root(root)
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
    durable: LifecycleRecoveryJournal | None = None
    previous: ProjectLifecycleState | None = None
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
        durable = LifecycleRecoveryJournal.begin(
            config, resolution.config.resolve(), operation="stop",
            operation_id=operation_id, routing=None,
        )
        stopping = previous.transition("stopping", operation_id=operation_id)
        publish_lifecycle_state(config.data_dir, stopping)
        stopped = stopping.transition("stopped")
        publish_lifecycle_state(config.data_dir, stopped)
        cleanup_complete = durable.commit()
        return _result(
            "stop", "stopped", root, mutates=True,
            project_state="stopped", index_status="preserved",
            connection_status="stopped",
            backup_cleanup="complete" if cleanup_complete else "pending",
        ), 0
    except BaseException as exc:
        rollback_errors = durable.rollback() if durable is not None else []
        if not isinstance(exc, (OSError, RuntimeError, ValueError)):
            if rollback_errors:
                exc.add_note("stop rollback incomplete: " + "; ".join(rollback_errors))
            raise
        return _result(
            "stop", "incomplete", root, mutates=False,
            project_state=(
                "failed" if rollback_errors or previous is None else previous.status
            ),
            index_status="unknown" if rollback_errors else "preserved",
            connection_status="unchanged", error=str(exc),
            rollback_errors=rollback_errors,
            previous_state_preserved=not rollback_errors,
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


def _external_routing_bundle(
    installation: VersionedInstallation, *, runner: Any = subprocess.run
):
    completed = runner(
        [str(installation.python), "-m", "codebase_atlas.simple_cli",
         "_routing-assets"],
        check=False, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("updated Atlas did not export routing assets")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("updated Atlas returned invalid routing assets") from exc
    return decode_routing_bundle(payload)


def update_project(
    repository: Path,
    *,
    timeout_seconds: float = 30.0,
    release_fetcher: Any = fetch_stable_release,
    installer: Any = install_stable_release,
    runner: Any = subprocess.run,
) -> tuple[dict[str, Any], int]:
    root, resolution = _repository_root(repository)
    recovery_failure = _recover_before_mutation(root, "update")
    if recovery_failure is not None:
        return recovery_failure
    root, resolution = _repository_root(root)
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
    transaction: EnableTransaction | None = None
    routing: RoutingTransaction | None = None
    durable: LifecycleRecoveryJournal | None = None
    operation_id = secrets.token_hex(16)
    try:
        if not _acquire_refresh(refresh, timeout_seconds=timeout_seconds):
            raise RuntimeError("timed out waiting for the active project refresh")
        current = load_lifecycle_state(
            config.data_dir, config.repository, config.project
        )
        if current.operation_generation != previous.operation_generation:
            raise RuntimeError("project lifecycle changed while preparing update")
        config_identity, _ = _regular_snapshot(config_path)
        transaction = EnableTransaction(config, config_path)
        target_bundle = _external_routing_bundle(installation, runner=runner)
        routing = RoutingTransaction(
            root, bundle=target_bundle, preserve_custom_skill=True
        )
        durable = LifecycleRecoveryJournal.begin(
            config, config_path, operation="update", operation_id=operation_id,
            routing=routing,
        )
        transaction.attach_recovery(durable)
        routing.apply()
        updating = current.transition("updating", operation_id=operation_id)
        transaction.run(lambda: publish_lifecycle_state(config.data_dir, updating))
        preview = codex_plan(
            config_path, scope="project", codex_project_root=root,
            atlas_executable=installation.atlas_executable,
        )
        if preview["status"] == "blocked":
            raise RuntimeError("project Codex MCP configuration conflicts with Atlas")
        candidate = replace(config, cbm_binary=installation.provider_binary)
        transaction.allow_config(candidate)
        transaction.run(lambda: candidate.write_verified(
            config_path, config_identity
        ))
        transaction.allow_codex_plan(preview)
        transaction.run(lambda: codex_apply(
            config_path, scope="project", codex_project_root=root,
            atlas_executable=installation.atlas_executable,
        ))
        candidate_ready = updating.transition(
            "ready",
            atlas_version=installation.version,
            provider_version=installation.provider_version,
        )
        transaction.run(lambda: publish_lifecycle_state(config.data_dir, candidate_ready))
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
            transaction.run(lambda: publish_lifecycle_state(config.data_dir, final))
        backup_cleaned = transaction.commit()
        durable_cleaned = durable.commit()
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
            routing_status=(
                "custom_preserved" if routing.preserved_conflicts else "updated"
            ),
            preserved_routing_assets=list(routing.preserved_conflicts),
            backup_cleanup="complete" if backup_cleaned and durable_cleaned else "pending",
        ), 0
    except BaseException as exc:
        rollback_errors: list[str] = []
        if routing is not None:
            rollback_errors.extend(f"routing: {error}" for error in routing.rollback())
        if transaction is not None:
            rollback_errors.extend(transaction.rollback())
        if durable is not None:
            rollback_errors.extend(
                f"durable: {error}" for error in durable.rollback()
            )
        if not isinstance(exc, (OSError, RuntimeError, ValueError)):
            if rollback_errors:
                exc.add_note("update rollback incomplete: " + "; ".join(rollback_errors))
            raise
        detail = str(exc)
        if rollback_errors:
            detail += "; rollback failed: " + "; ".join(rollback_errors)
        return _result(
            "update", "incomplete", root,
            mutates=installation_mutated or bool(rollback_errors),
            project_state=previous.status if not rollback_errors else "unknown",
            index_status="preserved" if not rollback_errors else "unknown",
            connection_status="unchanged", error=detail,
            previous_version=current_version,
            rollback_errors=rollback_errors,
            previous_state_preserved=not rollback_errors,
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


def _cleanup_empty_project_routing_directories(repository: Path) -> None:
    """Remove only empty directories that Atlas routing may have created."""
    for directory in (
        repository / ".agents/skills/codebase-atlas",
        repository / ".agents/skills",
        repository / ".agents",
        repository / ".codex",
    ):
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError:
            # Preserve nonempty or concurrently changed directories.
            continue


def remove_project(
    repository: Path, *, timeout_seconds: float = 30.0
) -> tuple[dict[str, Any], int]:
    root, resolution = _repository_root(repository)
    recovery_failure = _recover_before_mutation(root, "remove")
    if recovery_failure is not None:
        return recovery_failure
    root, resolution = _repository_root(root)
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
    codex_removed_sha256 = "absent"
    marker_identity: tuple[int, int] | None = None
    data_moved = False
    config_removed = False
    codex_changed = False
    previous: ProjectLifecycleState | None = None
    routing: RoutingTransaction | None = None
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
        routing_state = load_routing_state(config.data_dir, root)
        routing = RoutingTransaction(
            root, remove=True,
            remove_created_rule_file=bool(
                routing_state and routing_state["created_rule_file"]
            ),
        )
        routing_records = routing.recovery_record()
        RoutingTransaction.for_recovery(root, routing_records)
        config_identity, config_bytes = _regular_snapshot(config_path)
        config_mode = stat.S_IMODE(os.lstat(config_path).st_mode)
        preview = codex_plan(config_path, scope="project", codex_project_root=root)
        if preview["status"] == "blocked":
            raise RuntimeError("project Codex MCP configuration conflicts with Atlas")
        codex_target = Path(str(preview["target"]))
        if codex_target.exists():
            codex_identity, codex_bytes = _regular_snapshot(codex_target)
            if preview.get("existing") in {"matching", "managed_different"}:
                remainder = _remove_project_block(codex_bytes.decode("utf-8"))
                codex_removed_sha256 = (
                    hashlib.sha256(remainder.encode("utf-8")).hexdigest()
                    if remainder.strip() else "absent"
                )
            else:
                codex_removed_sha256 = hashlib.sha256(codex_bytes).hexdigest()
        _write_recovery_file(config_destination, config_bytes)
        codex_backup = operation_root / "codex-config.toml"
        if codex_identity is not None:
            _write_recovery_file(codex_backup, codex_bytes)
        receipt = {
            "schema_version": 3,
            "status": "removing",
            "operation_id": operation_id,
            "repository": str(root),
            "project": config.project,
            "original_config": str(config_path),
            "recovered_config": str(config_destination),
            "original_data_dir": str(config.data_dir),
            "recovered_data_dir": str(data_destination),
            "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "config_mode": config_mode,
            "previous_lifecycle": previous.to_dict(),
            "codex_config_changed": False,
            "codex_config_path": str(codex_target),
            "codex_config_existed": codex_identity is not None,
            "codex_config_sha256": (
                hashlib.sha256(codex_bytes).hexdigest()
                if codex_identity is not None else "absent"
            ),
            "codex_removed_sha256": codex_removed_sha256,
            "codex_config_mode": (
                stat.S_IMODE(os.lstat(codex_target).st_mode)
                if codex_identity is not None else 0o644
            ),
            "codex_backup": str(codex_backup) if codex_identity is not None else "",
            "shared_installation_removed": False,
            "routing_assets": routing_records,
        }
        _publish_recovery_json(receipt_path, receipt)
        publish_removal_marker(
            root, config.project, operation_id, receipt_path, status="removing"
        )
        marker_meta = os.lstat(removal_marker_path(root))
        marker_identity = (marker_meta.st_dev, marker_meta.st_ino)
        removing = previous.transition("removing", operation_id=operation_id)
        publish_lifecycle_state(config.data_dir, removing)
        codex_result = codex_remove(
            config_path, scope="project", codex_project_root=root
        )
        codex_changed = bool(codex_result.get("mutates"))
        receipt["codex_config_changed"] = codex_changed
        _publish_recovery_json(receipt_path, receipt)
        routing.apply()
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
        receipt["status"] = "removed"
        _publish_recovery_json(receipt_path, receipt)
        publish_removal_marker(
            root, config.project, operation_id, receipt_path, status="removed"
        )
        _cleanup_empty_project_routing_directories(root)
        return _result(
            "remove", "removed", root, mutates=True,
            project_state="removed", index_status="recovery_area",
            connection_status="removed", receipt=str(receipt_path),
            recovery_data=str(data_destination),
            preserved_routing_assets=list(routing.conflicts),
            routing_cleanup="partial" if routing.conflicts else "complete",
        ), 0
    except (OSError, RuntimeError, ValueError) as exc:
        rollback_errors = []
        if routing is not None:
            rollback_errors.extend("routing: " + error for error in routing.rollback())
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
    if operation == "status":
        print(f"Project: {payload.get('project_state', 'unknown')}")
        print(f"Index: {payload.get('index_status', 'unknown')}")
        print(f"Codex: {payload.get('codex_config_status', 'unknown')}; current task unknown")
    if payload.get("next_action") and payload["next_action"] != "none":
        print(f"Next action: {payload['next_action']}")
    if payload.get("error"):
        print(f"Error: {payload['error']}")
    if payload.get("current_session_refresh_required"):
        print("Codex connection configured; start a new task once to load the MCP entry.")


def _enable_runtime_installation() -> VersionedInstallation:
    try:
        release = fetch_stable_release()
    except OSError:
        return load_versioned_installation(__version__)
    installation, _created = install_stable_release(release)
    return installation


def _same_executable(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left.resolve() == right.resolve()


def _delegated_enable_arguments(args: argparse.Namespace) -> list[str]:
    command = [
        "-m", "codebase_atlas.simple_cli", "enable",
        "--repo", str(args.repo), "--mode", args.mode,
    ]
    for option, value in (
        ("--config", args.config),
        ("--language", args.language),
        ("--node", args.node),
        ("--cbm-binary", args.cbm_binary),
        ("--serena-python", args.serena_python),
        ("--node-bin-dir", args.node_bin_dir),
        ("--tsconfig", args.tsconfig),
        ("--data-dir", args.data_dir),
    ):
        if value is not None:
            command.extend((option, str(value)))
    if args.json:
        command.append("--json")
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("_routing-assets", help=argparse.SUPPRESS)
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
    status = commands.add_parser("status", help="show read-only Atlas project status")
    status.add_argument("--repo", type=Path, default=Path.cwd())
    status.add_argument("--json", action="store_true")
    verify = commands.add_parser("verify", help="run read-only Atlas acceptance checks")
    verify.add_argument("--repo", type=Path, default=Path.cwd())
    verify.add_argument("--json", action="store_true")
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
        if args.command == "_routing-assets":
            print(json.dumps(routing_bundle(), separators=(",", ":")))
            return 0
        if args.command == "enable":
            installation = _enable_runtime_installation()
            if not _same_executable(Path(sys.executable), installation.python):
                completed = subprocess.run(
                    [str(installation.python), *_delegated_enable_arguments(args)],
                    check=False,
                )
                return completed.returncode
            if args.cbm_binary is None:
                args.cbm_binary = installation.provider_binary
            payload, code = enable_project(
                args.repo, config_path=args.config, language=args.language,
                node=args.node, cbm_binary=args.cbm_binary,
                serena_python=args.serena_python, node_bin_dir=args.node_bin_dir,
                tsconfig=args.tsconfig, data_dir=args.data_dir, mode=args.mode,
            )
        elif args.command == "status":
            payload, code = status_project(args.repo)
        elif args.command == "verify":
            payload, code = verify_project(args.repo)
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
