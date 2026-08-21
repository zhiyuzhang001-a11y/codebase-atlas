"""Read-only inspection of Atlas-owned index and runtime state."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from urllib.parse import quote

from .config import AtlasConfig
from .index_state import index_freshness


def _tree_size(path: Path) -> tuple[int, int]:
    """Return bytes and file count without following directory symlinks."""
    try:
        if path.is_symlink() or path.is_file():
            return path.lstat().st_size, 1
        if not path.is_dir():
            return 0, 0
    except OSError:
        return 0, 0
    total = 0
    files = 0
    for root, _directories, names in os.walk(path, followlinks=False):
        for name in names:
            try:
                total += (Path(root) / name).lstat().st_size
                files += 1
            except OSError:
                continue
    return total, files


def _storage(config: AtlasConfig) -> dict[str, object]:
    components = {
        "provider": config.cache_dir,
        "serena_home": config.serena_home,
        "serena_metadata": config.metadata_root,
        "index_state": config.data_dir / "index-state.json",
    }
    entries: list[dict[str, object]] = []
    for name, path in components.items():
        size, files = _tree_size(path)
        entries.append({
            "name": name,
            "path": str(path),
            "present": path.exists() or path.is_symlink(),
            "bytes": size,
            "files": files,
        })
    total, files = _tree_size(config.data_dir)
    return {
        "data_dir": str(config.data_dir),
        "total_bytes": total,
        "total_files": files,
        "components": entries,
    }


def _database_path(config: AtlasConfig) -> Path | None:
    if not config.project or Path(config.project).name != config.project:
        return None
    return config.cache_dir / f"{config.project}.db"


def inspect_provider_database(config: AtlasConfig, *, deep: bool = False) -> dict[str, object]:
    """Inspect the Provider database through a read-only SQLite connection."""
    database = _database_path(config)
    if database is None:
        return {
            "status": "invalid",
            "ok": False,
            "reason": "project_not_configured_or_unsafe",
            "deep_check": deep,
        }
    result: dict[str, object] = {
        "path": str(database),
        "deep_check": deep,
    }
    try:
        size = database.stat().st_size
    except FileNotFoundError:
        return result | {"status": "missing", "ok": False, "reason": "provider_database_missing"}
    except OSError as exc:
        return result | {
            "status": "unavailable", "ok": False,
            "reason": "provider_database_unavailable", "detail": str(exc),
        }
    result["size"] = size
    if size == 0:
        return result | {"status": "invalid", "ok": False, "reason": "provider_database_empty"}

    uri = f"file:{quote(str(database), safe='/')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            connection.execute("PRAGMA query_only=ON")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            required = {"projects", "nodes", "edges"}
            missing = sorted(required - tables)
            if missing:
                return result | {
                    "status": "incompatible", "ok": False,
                    "reason": "provider_schema_incomplete", "missing_tables": missing,
                }
            projects = [(str(name), str(root)) for name, root in connection.execute(
                "SELECT name, root_path FROM projects"
            )]
            primary = [row for row in projects if row[0] == config.project]
            allowed_projects = {config.project, config.project + "::missed"}
            unexpected = [row for row in projects if row[0] not in allowed_projects]
            if len(primary) != 1 or unexpected:
                return result | {
                    "status": "incompatible", "ok": False,
                    "reason": "provider_project_identity_count",
                    "project_rows": len(projects),
                    "primary_project_rows": len(primary),
                    "unexpected_projects": [row[0] for row in unexpected],
                }
            stored_project, stored_root = primary[0]
            if stored_project != config.project or Path(stored_root).resolve() != config.repository:
                return result | {
                    "status": "incompatible", "ok": False,
                    "reason": "provider_project_identity_mismatch",
                    "stored_project": stored_project,
                    "stored_repository": stored_root,
                }
            if deep:
                integrity = [str(row[0]) for row in connection.execute("PRAGMA quick_check(1)")]
                result["quick_check"] = integrity
                if integrity != ["ok"]:
                    return result | {
                        "status": "corrupt", "ok": False,
                        "reason": "provider_quick_check_failed",
                    }
        finally:
            connection.close()
    except sqlite3.Error as exc:
        message = str(exc)
        lowered = message.lower()
        transient = any(token in lowered for token in ("locked", "busy", "unable to open"))
        return result | {
            "status": "unavailable" if transient else "corrupt",
            "ok": False,
            "reason": "provider_database_transient_error" if transient else "provider_database_invalid",
            "detail": message,
        }
    return result | {
        "status": "healthy", "ok": True,
        "reason": "provider_database_verified",
        "project_rows": len(projects),
        "auxiliary_projects": [row[0] for row in projects if row[0] != config.project],
        "stored_project": config.project,
        "stored_repository": str(config.repository),
    }


def _residue(config: AtlasConfig) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    database = _database_path(config)
    candidates: dict[Path, tuple[str, str]] = {}
    for path in config.data_dir.glob(".index-state-*.json"):
        candidates[path] = ("atlas_state_temporary", "warning")
    if database is not None:
        for path in config.cache_dir.glob(database.name + ".stage.*"):
            candidates[path] = ("provider_staging", "warning")
        for path in config.cache_dir.glob(database.name + ".corrupt*"):
            kind = "provider_quarantine_pending" if ".pending." in path.name else "provider_quarantine"
            candidates[path] = (kind, "warning")
        for suffix in ("-wal", "-shm", "-journal"):
            path = Path(str(database) + suffix)
            if path.exists() or path.is_symlink():
                candidates[path] = ("provider_live_sidecar", "info")
    for path, (kind, severity) in sorted(candidates.items(), key=lambda item: str(item[0])):
        size, files = _tree_size(path)
        findings.append({
            "kind": kind,
            "severity": severity,
            "path": str(path),
            "bytes": size,
            "files": files,
        })
    return findings


def inspect_installation(config: AtlasConfig, *, deep: bool = False) -> dict[str, object]:
    """Return a stable, JSON-serializable, read-only maintenance report."""
    database = inspect_provider_database(config, deep=deep)
    freshness = index_freshness(config.data_dir, config.repository, config.project)
    findings = _residue(config)
    core_ok = bool(database["ok"]) and bool(freshness["ok"])
    remediation: list[str] = []
    if not database["ok"]:
        remediation.append("run 'codebase-atlas index' to publish a new Provider generation")
    elif not freshness["ok"]:
        remediation.append("run 'codebase-atlas update' to refresh the index safely")
    if any(item["severity"] == "warning" for item in findings):
        remediation.append("review detected residue before using a future cleanup command")
    return {
        "schema_version": 1,
        "status": "healthy" if core_ok else "attention_required",
        "ok": core_ok,
        "mode": "read_only",
        "deep_check": deep,
        "repository": str(config.repository),
        "project": config.project,
        "index": freshness,
        "provider_database": database,
        "storage": _storage(config),
        "findings": findings,
        "remediation": remediation,
    }


def repair_plan(report: dict[str, object]) -> dict[str, object]:
    """Choose a non-mutating recovery route from an inspection report."""
    database = report["provider_database"]
    index = report["index"]
    assert isinstance(database, dict) and isinstance(index, dict)
    database_status = str(database["status"])
    index_status = str(index["status"])
    if database_status == "unavailable":
        return {
            "action": "wait_and_retry", "applicable": False,
            "reason": "transient_provider_database_failure_must_not_be_quarantined",
        }
    if not bool(database["ok"]):
        return {
            "action": "provider_managed_rebuild", "applicable": True,
            "reason": f"provider_database_{database_status}",
        }
    if not bool(index["ok"]):
        return {
            "action": "safe_update", "applicable": True,
            "reason": f"atlas_index_{index_status}",
        }
    return {
        "action": "none", "applicable": False,
        "reason": "index_and_provider_database_are_usable",
    }


def cleanup_plan(config: AtlasConfig) -> dict[str, object]:
    """Plan cleanup only for narrowly recognized obsolete Atlas-owned files."""
    root = config.data_dir.resolve()
    database = _database_path(config)
    proposed: dict[Path, str] = {}
    retained: list[dict[str, object]] = []
    refused: list[dict[str, object]] = []

    for path in config.data_dir.glob(".index-state-*.json"):
        proposed[path] = "atlas_state_temporary"
    if database is not None:
        for path in config.cache_dir.glob(database.name + ".stage.*"):
            proposed[path] = "provider_staging"
        quarantines = sorted(
            (
                path for path in config.cache_dir.glob(database.name + ".corrupt*")
                if ".pending." not in path.name
            ),
            key=lambda path: path.lstat().st_mtime_ns if path.exists() else 0,
            reverse=True,
        )
        if quarantines:
            newest = quarantines.pop(0)
            retained.append({
                "kind": "provider_quarantine",
                "path": str(newest),
                "reason": "retain_newest_quarantine_for_diagnosis",
            })
        for path in quarantines:
            proposed[path] = "provider_quarantine_obsolete"
        for path in config.cache_dir.glob(database.name + ".corrupt.pending.*"):
            retained.append({
                "kind": "provider_quarantine_pending",
                "path": str(path),
                "reason": "pending_recovery_is_not_safe_to_clean_automatically",
            })

    for log_root in (config.cache_dir / "logs", config.serena_home / "logs"):
        rotated = sorted(
            log_root.glob("*.log.*"),
            key=lambda path: path.lstat().st_mtime_ns if path.exists() else 0,
            reverse=True,
        )
        if rotated:
            newest = rotated.pop(0)
            retained.append({
                "kind": "rotated_log",
                "path": str(newest),
                "reason": "retain_newest_rotated_log",
            })
        for path in rotated:
            proposed[path] = "rotated_log_obsolete"

    targets: list[dict[str, object]] = []
    for path, kind in sorted(proposed.items(), key=lambda item: str(item[0])):
        try:
            stat = path.lstat()
            resolved = path.resolve()
            if path.is_symlink() or not path.is_file() or not resolved.is_relative_to(root):
                raise ValueError("target is not a regular file contained by the Atlas data root")
        except (OSError, ValueError) as exc:
            refused.append({"kind": kind, "path": str(path), "reason": str(exc)})
            continue
        targets.append({
            "kind": kind,
            "path": str(path),
            "bytes": stat.st_size,
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "mtime_ns": stat.st_mtime_ns,
        })
    return {
        "schema_version": 1,
        "status": "cleanup_available" if targets else "nothing_to_clean",
        "mode": "dry_run",
        "data_dir": str(root),
        "target_count": len(targets),
        "reclaimable_bytes": sum(int(item["bytes"]) for item in targets),
        "targets": targets,
        "retained": retained,
        "refused": refused,
    }


def apply_cleanup(config: AtlasConfig, plan: dict[str, object]) -> dict[str, object]:
    """Apply an in-memory plan only if every file still has the planned identity."""
    root = config.data_dir.resolve()
    if plan.get("refused"):
        raise ValueError("cleanup plan contains refused targets; nothing was removed")
    targets = plan.get("targets", [])
    if not isinstance(targets, list):
        raise ValueError("cleanup plan targets are invalid")
    verified: list[tuple[Path, dict[str, object]]] = []
    for item in targets:
        if not isinstance(item, dict):
            raise ValueError("cleanup target is invalid")
        path = Path(str(item["path"]))
        stat = path.lstat()
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root)
            or stat.st_dev != item["device"]
            or stat.st_ino != item["inode"]
            or stat.st_mtime_ns != item["mtime_ns"]
            or stat.st_size != item["bytes"]
        ):
            raise RuntimeError(f"cleanup target changed after planning: {path}")
        verified.append((path, item))
    for path, _item in verified:
        path.unlink()
    return {
        "schema_version": 1,
        "status": "cleaned",
        "mode": "applied",
        "data_dir": str(root),
        "removed_count": len(verified),
        "reclaimed_bytes": sum(int(item["bytes"]) for _path, item in verified),
        "removed": [item for _path, item in verified],
        "retained": plan.get("retained", []),
        "refused": plan.get("refused", []),
    }
