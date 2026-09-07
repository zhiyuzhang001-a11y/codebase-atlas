"""Durable recovery for outer Atlas lifecycle publication.

The refresh coordinator protects an index generation while it is being built.
This journal protects the wider enable/update boundary: project configuration,
Codex routing, lifecycle/index metadata, and the previously active Provider
database. Recovery is deliberately fail-closed for repository-owned files that
no longer match a state authorized by the interrupted Atlas operation.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any

from .config import AtlasConfig
from .index_state import state_path
from .project_lifecycle import lifecycle_state_path, project_recovery_root
from .python_registration_store import registration_index_path
from .refresh_planner import manifest_path
from .routing_transaction import RoutingTransaction


JOURNAL_NAME = "active-lifecycle-v1.json"
ABSENT = "absent"
MAX_TRACKED_FILE_BYTES = 16 * 1024 * 1024
OPERATION_DIRECTORY = re.compile(r"lifecycle-[0-9a-f]{32}")


def journal_path(repository: Path) -> Path:
    return project_recovery_root(repository) / JOURNAL_NAME


def _cleanup_orphans(repository: Path) -> int:
    root = project_recovery_root(repository)
    if not root.exists():
        return 0
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("project recovery root is unsafe")
    removed = 0
    allowed_backups = {
        f"{label}.bak" for label in {
            "config", "codex", "rule", "skill", "lifecycle", "index",
            "manifest", "registrations", "provider",
        }
    }
    for candidate in root.iterdir():
        if candidate.is_file() and candidate.name.startswith(".lifecycle-journal-"):
            metadata = os.lstat(candidate)
            if stat.S_ISREG(metadata.st_mode) and not candidate.is_symlink():
                candidate.unlink()
                removed += 1
            continue
        if not OPERATION_DIRECTORY.fullmatch(candidate.name):
            continue
        metadata = os.lstat(candidate)
        if not stat.S_ISDIR(metadata.st_mode) or candidate.is_symlink():
            raise RuntimeError("owned lifecycle staging directory is unsafe")
        children = tuple(candidate.iterdir())
        if any(
            child.name not in allowed_backups
            or child.is_symlink()
            or not stat.S_ISREG(os.lstat(child).st_mode)
            for child in children
        ):
            raise RuntimeError("owned lifecycle staging contents are unsafe")
        shutil.rmtree(candidate)
        removed += 1
    return removed


def _digest(path: Path) -> str:
    if not os.path.lexists(path):
        return ABSENT
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"lifecycle artifact is not a safe regular file: {path}")
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _payload_digest(payload: bytes | None) -> str:
    return ABSENT if payload is None else hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise RuntimeError("project recovery root must not be a symlink")
    descriptor, temporary = tempfile.mkstemp(
        prefix=".lifecycle-journal-", suffix=".json", dir=path.parent
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


def _copy_backup(source: Path, destination: Path, *, link: bool) -> None:
    if link:
        try:
            os.link(source, destination)
            return
        except OSError:
            pass
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        outgoing.flush()
        os.fsync(outgoing.fileno())


def _restore(entry: dict[str, Any]) -> None:
    destination = Path(entry["destination"])
    if not entry["existed"]:
        destination.unlink(missing_ok=True)
        return
    backup = Path(entry["backup"])
    metadata = os.lstat(backup)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("lifecycle recovery backup is unsafe")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".atlas-lifecycle-restore-", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as outgoing, backup.open("rb") as incoming:
            descriptor = -1
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.chmod(temporary, entry["mode"])
        os.replace(temporary, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)


class LifecycleRecoveryJournal:
    def __init__(self, path: Path, document: dict[str, Any]):
        self.path = path
        self.document = document

    @classmethod
    def begin(
        cls,
        config: AtlasConfig,
        config_path: Path,
        *,
        operation: str,
        operation_id: str,
        routing: RoutingTransaction | None,
    ) -> "LifecycleRecoveryJournal":
        if operation not in {"enable", "update", "stop"} or not operation_id:
            raise ValueError("unsupported durable lifecycle operation")
        repository = config.repository.resolve()
        root = project_recovery_root(repository).resolve()
        path = root / JOURNAL_NAME
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise RuntimeError("project recovery root must be a real directory")
        if path.exists():
            raise RuntimeError("an interrupted lifecycle operation requires recovery")
        operation_root = root / f"lifecycle-{operation_id}"
        operation_root.mkdir(mode=0o700)
        destinations = {
            "config": config_path.resolve(),
            "codex": repository / ".codex/config.toml",
            "rule": repository / "AGENTS.md",
            "skill": repository / ".agents/skills/codebase-atlas/SKILL.md",
            "lifecycle": lifecycle_state_path(config.data_dir),
            "index": state_path(config.data_dir),
            "manifest": manifest_path(config.data_dir),
            "registrations": registration_index_path(config.data_dir),
            "provider": config.cache_dir / f"{config.project}.db",
        }
        routing_after = {
            str(plan.path): plan.after for plan in routing.plans
        } if routing is not None else {}
        artifacts: dict[str, dict[str, Any]] = {}
        try:
            for label, destination in destinations.items():
                existed = os.path.lexists(destination)
                mode = 0o644
                backup = ""
                before = ABSENT
                if existed:
                    metadata = os.lstat(destination)
                    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                        raise RuntimeError(
                            f"lifecycle artifact is not a safe regular file: {label}"
                        )
                    if label != "provider" and metadata.st_size > MAX_TRACKED_FILE_BYTES:
                        raise RuntimeError(f"lifecycle artifact exceeds recovery budget: {label}")
                    mode = stat.S_IMODE(metadata.st_mode)
                    before = _digest(destination)
                    backup_path = operation_root / f"{label}.bak"
                    _copy_backup(destination, backup_path, link=label == "provider")
                    backup = str(backup_path)
                allowed = {before}
                if str(destination) in routing_after:
                    allowed.add(_payload_digest(routing_after[str(destination)]))
                artifacts[label] = {
                    "destination": str(destination),
                    "existed": existed,
                    "backup": backup,
                    "mode": mode,
                    "before": before,
                    "allowed": sorted(allowed),
                    "owned": label in {
                        "lifecycle", "index", "manifest", "registrations", "provider"
                    },
                }
            document = {
                "schema_version": 1,
                "repository": str(repository),
                "project": config.project,
                "data_dir": str(config.data_dir.resolve()),
                "cache_dir": str(config.cache_dir.resolve()),
                "config_path": str(config_path.resolve()),
                "operation": operation,
                "operation_id": operation_id,
                "phase": "prepared",
                "operation_root": str(operation_root),
                "artifacts": artifacts,
            }
            _write_json(path, document)
            return cls(path, document)
        except BaseException:
            shutil.rmtree(operation_root, ignore_errors=True)
            raise

    def allow(self, path: Path, payload: bytes | None) -> None:
        selected = str(path.resolve())
        for entry in self.document["artifacts"].values():
            if entry["destination"] == selected:
                digest = _payload_digest(payload)
                if digest not in entry["allowed"]:
                    entry["allowed"].append(digest)
                    entry["allowed"].sort()
                    _write_json(self.path, self.document)
                return
        raise RuntimeError(f"untracked lifecycle artifact: {path}")

    def accept(self) -> None:
        self.document["phase"] = "accepted"
        _write_json(self.path, self.document)

    def rollback(self) -> list[str]:
        return _finish(self.path, self.document, accept=False)

    def commit(self) -> bool:
        self.accept()
        errors = _finish(self.path, self.document, accept=True)
        return not errors


def _validate(repository: Path, value: Any, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError("lifecycle recovery journal schema is invalid")
    required = {
        "schema_version", "repository", "project", "data_dir", "cache_dir",
        "config_path", "operation", "operation_id", "phase", "operation_root",
        "artifacts",
    }
    if set(value) != required or value["repository"] != str(repository.resolve()):
        raise RuntimeError("lifecycle recovery journal identity mismatch")
    if (
        value["operation"] not in {"enable", "update", "stop"}
        or not isinstance(value["operation_id"], str)
        or not value["operation_id"]
        or value["phase"] not in {
        "prepared", "accepted"
        }
        or not isinstance(value["project"], str)
        or not value["project"]
    ):
        raise RuntimeError("lifecycle recovery journal state is invalid")
    root = project_recovery_root(repository).resolve()
    operation_root = Path(value["operation_root"]).resolve()
    expected_operation_root = root / f"lifecycle-{value['operation_id']}"
    if (
        path.resolve() != root / JOURNAL_NAME
        or operation_root != expected_operation_root
        or not operation_root.is_dir()
        or operation_root.is_symlink()
    ):
        raise RuntimeError("lifecycle recovery location is unsafe")
    artifacts = value["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "config", "codex", "rule", "skill", "lifecycle", "index", "manifest",
        "registrations", "provider",
    }:
        raise RuntimeError("lifecycle recovery artifact set is invalid")
    repository = repository.resolve()
    data_dir = Path(value["data_dir"]).resolve()
    cache_dir = Path(value["cache_dir"]).resolve()
    expected = {
        "config": Path(value["config_path"]).resolve(),
        "codex": repository / ".codex/config.toml",
        "rule": repository / "AGENTS.md",
        "skill": repository / ".agents/skills/codebase-atlas/SKILL.md",
        "lifecycle": lifecycle_state_path(data_dir),
        "index": state_path(data_dir),
        "manifest": manifest_path(data_dir),
        "registrations": registration_index_path(data_dir),
        "provider": cache_dir / f"{value['project']}.db",
    }
    if not expected["config"].is_relative_to(repository):
        raise RuntimeError("lifecycle recovery config path is unsafe")
    for label, destination in expected.items():
        entry = artifacts.get(label)
        if not isinstance(entry, dict) or entry.get("destination") != str(destination):
            raise RuntimeError("lifecycle recovery artifact identity mismatch")
        if set(entry) != {
            "destination", "existed", "backup", "mode", "before", "allowed", "owned"
        } or type(entry["existed"]) is not bool or type(entry["owned"]) is not bool:
            raise RuntimeError("lifecycle recovery artifact schema is invalid")
        expected_owned = label in {
            "lifecycle", "index", "manifest", "registrations", "provider"
        }
        if entry["owned"] != expected_owned:
            raise RuntimeError("lifecycle recovery ownership is invalid")
        if type(entry["mode"]) is not int or not 0 <= entry["mode"] <= 0o777:
            raise RuntimeError("lifecycle recovery artifact mode is invalid")
        allowed = entry["allowed"]
        if not isinstance(allowed, list) or not allowed or not all(
            item == ABSENT or (
                isinstance(item, str) and len(item) == 64
                and all(character in "0123456789abcdef" for character in item)
            ) for item in allowed
        ):
            raise RuntimeError("lifecycle recovery artifact hashes are invalid")
        if entry["before"] not in allowed:
            raise RuntimeError("lifecycle recovery original hash is invalid")
        backup = entry["backup"]
        if entry["existed"]:
            backup_path = Path(backup).resolve()
            if (
                backup_path != operation_root / f"{label}.bak"
                or not backup_path.is_file()
                or backup_path.is_symlink()
            ):
                raise RuntimeError("lifecycle recovery backup path is unsafe")
        elif backup:
            raise RuntimeError("absent lifecycle artifact has a backup")
    return value


def _finish(path: Path, document: dict[str, Any], *, accept: bool) -> list[str]:
    errors: list[str] = []
    if not accept:
        # Validate every repository-owned destination before restoring the
        # first artifact. A conflict must not leave a half-rolled-back project.
        for label, entry in document["artifacts"].items():
            if entry["owned"]:
                continue
            try:
                if _digest(Path(entry["destination"])) not in entry["allowed"]:
                    raise RuntimeError("external modification preserved")
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{label}: {exc}")
        if errors:
            return errors
        for label, entry in reversed(tuple(document["artifacts"].items())):
            destination = Path(entry["destination"])
            try:
                _restore(entry)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{label}: {exc}")
    if errors:
        return errors
    if not accept:
        repository = Path(document["repository"])
        for directory in (
            repository / ".agents/skills/codebase-atlas",
            repository / ".agents/skills",
            repository / ".agents",
        ):
            try:
                directory.rmdir()
            except FileNotFoundError:
                pass
            except OSError:
                # A nonempty directory contains pre-existing or user-created
                # content and must be preserved.
                pass
    operation_root = Path(document["operation_root"])
    try:
        path.unlink(missing_ok=True)
        shutil.rmtree(operation_root)
    except OSError as exc:
        errors.append(f"cleanup: {exc}")
    return errors


def recover_lifecycle_transaction(repository: Path) -> dict[str, Any]:
    repository = repository.resolve(strict=True)
    path = journal_path(repository)
    if not path.exists():
        removed = _cleanup_orphans(repository)
        return {"status": "clean", "action": "removed_owned_staging", "removed": removed}
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("lifecycle recovery journal is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("lifecycle recovery journal is invalid") from exc
    document = _validate(repository, value, path)
    accepted = document["phase"] == "accepted"
    errors = _finish(path, document, accept=accepted)
    if errors:
        raise RuntimeError("lifecycle recovery incomplete: " + "; ".join(errors))
    removed = _cleanup_orphans(repository)
    return {
        "status": "recovered",
        "action": "accepted_completed_operation" if accepted else "restored_previous_state",
        "operation": document["operation"],
        "operation_id": document["operation_id"],
        "removed": removed,
    }
