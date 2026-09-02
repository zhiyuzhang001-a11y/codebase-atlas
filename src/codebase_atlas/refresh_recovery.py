"""Crash-recovery journal for Atlas-owned refresh publication artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from .config import AtlasConfig
from .index_state import state_path
from .python_registration_store import FILENAME as REGISTRATION_FILENAME, registration_index_path
from .refresh_planner import manifest_path


JOURNAL_NAME = "refresh-transaction-v1.json"


def _cleanup_owned_stage_files(config: AtlasConfig) -> int:
    recognized = (
        (config.data_dir, (
            (".generation-manifest-candidate-", ".json"),
            (".generation-manifest-backup-", ".json"),
            (".python-registrations-backup-", ".json"),
            (".python-registrations-", ".json"),
            (".refresh-journal-", ".json"),
        )),
        (config.cache_dir, ((".provider-generation-backup-", ".db"),)),
    )
    removed = 0
    for directory, patterns in recognized:
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if path.name == REGISTRATION_FILENAME:
                continue
            if not any(path.name.startswith(prefix) and path.name.endswith(suffix) for prefix, suffix in patterns):
                continue
            metadata = os.lstat(path)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("owned refresh staging path is not a safe regular file")
            path.unlink()
            removed += 1
    return removed


def journal_path(data_dir: Path) -> Path:
    return data_dir / JOURNAL_NAME


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=".refresh-journal-", suffix=".json", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(raw_temporary, path)
    finally:
        if os.path.exists(raw_temporary):
            os.unlink(raw_temporary)


def _backup(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        return {"destination": str(path), "existed": False, "backup": ""}
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"refresh artifact is not a safe regular file: {label}")
    descriptor, raw_backup = tempfile.mkstemp(
        prefix=f".refresh-recovery-{label}-", suffix=".bak", dir=path.parent
    )
    os.close(descriptor)
    os.unlink(raw_backup)
    backup = Path(raw_backup)
    os.link(path, backup)
    return {"destination": str(path), "existed": True, "backup": str(backup)}


def _restore(entry: dict[str, Any]) -> None:
    destination = Path(entry["destination"])
    if entry["existed"]:
        backup = Path(entry["backup"])
        metadata = os.lstat(backup)
        if not stat.S_ISREG(metadata.st_mode) or backup.parent != destination.parent:
            raise ValueError("refresh recovery backup is unsafe")
        os.replace(backup, destination)
        entry["backup"] = ""
    else:
        destination.unlink(missing_ok=True)


def _discard(entry: dict[str, Any]) -> None:
    raw = entry.get("backup")
    if raw:
        Path(raw).unlink(missing_ok=True)
        entry["backup"] = ""


@dataclass
class RefreshRecoveryJournal:
    path: Path
    document: dict[str, Any]

    @classmethod
    def begin(
        cls, config: AtlasConfig, generation_before: str | None
    ) -> "RefreshRecoveryJournal":
        destinations = {
            "provider": config.cache_dir / f"{config.project}.db",
            "sidecar": registration_index_path(config.data_dir),
            "manifest": manifest_path(config.data_dir),
            "state": state_path(config.data_dir),
        }
        artifacts: dict[str, Any] = {}
        try:
            for label, destination in destinations.items():
                destination.parent.mkdir(parents=True, exist_ok=True)
                artifacts[label] = _backup(destination, label)
        except BaseException:
            for entry in artifacts.values():
                _discard(entry)
            raise
        document = {
            "schema_version": 1,
            "repository": str(config.repository.resolve()),
            "project": config.project,
            "data_dir": str(config.data_dir.resolve()),
            "cache_dir": str(config.cache_dir.resolve()),
            "generation_before": generation_before,
            "generation_after": None,
            "phase": "prepared",
            "artifacts": artifacts,
        }
        path = journal_path(config.data_dir)
        _write_atomic(path, document)
        return cls(path, document)

    def set_candidate(self, generation_after: str) -> None:
        self.document["generation_after"] = generation_after
        self.document["phase"] = "candidate_ready"
        _write_atomic(self.path, self.document)

    def mark_state_published(self) -> None:
        self.document["phase"] = "state_published"
        _write_atomic(self.path, self.document)

    def rollback(self) -> None:
        for label in ("state", "manifest", "sidecar", "provider"):
            _restore(self.document["artifacts"][label])
        self.commit()

    def commit(self) -> None:
        for entry in self.document["artifacts"].values():
            _discard(entry)
        self.path.unlink(missing_ok=True)


def recover_refresh_transaction(config: AtlasConfig) -> dict[str, Any]:
    """Recover one exact owned journal; foreign identity fails closed."""
    path = journal_path(config.data_dir)
    if not path.exists():
        removed = _cleanup_owned_stage_files(config)
        return {"status": "clean", "action": "removed_owned_staging", "removed": removed}
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("refresh recovery journal is not a safe regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("refresh recovery journal is invalid") from exc
    expected = {
        "repository": str(config.repository.resolve()),
        "project": config.project,
        "data_dir": str(config.data_dir.resolve()),
        "cache_dir": str(config.cache_dir.resolve()),
    }
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("refresh recovery journal schema is invalid")
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise ValueError("refresh recovery journal identity mismatch")
    artifacts = value.get("artifacts")
    expected_destinations = {
        "provider": str(config.cache_dir / f"{config.project}.db"),
        "sidecar": str(registration_index_path(config.data_dir)),
        "manifest": str(manifest_path(config.data_dir)),
        "state": str(state_path(config.data_dir)),
    }
    if not isinstance(artifacts, dict) or set(artifacts) != set(expected_destinations):
        raise ValueError("refresh recovery artifact set is invalid")
    for label, destination in expected_destinations.items():
        entry = artifacts[label]
        if not isinstance(entry, dict) or entry.get("destination") != destination:
            raise ValueError("refresh recovery artifact identity mismatch")
    journal = RefreshRecoveryJournal(path, value)
    if value.get("phase") == "state_published":
        journal.commit()
        removed = _cleanup_owned_stage_files(config)
        return {"status": "recovered", "action": "accepted_published_generation", "removed": removed}
    journal.rollback()
    removed = _cleanup_owned_stage_files(config)
    return {"status": "recovered", "action": "restored_previous_generation", "removed": removed}
