"""Crash recovery for the internal Rust T0/T1 publication transaction."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from .config import AtlasConfig
from .index_state import state_path
from .providers.rust_syntax import rust_syntax_pointer_path
from .refresh_planner import manifest_path


JOURNAL_NAME = "rust-refresh-transaction-v1.json"
RUST_REFRESH_PHASES = (
    "prepared",
    "shard_published",
    "manifest_published",
    "pointer_published",
    "state_published",
    "committing",
)
ACCEPT_PUBLISHED_PHASES = frozenset({"state_published", "committing"})
_GENERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def rust_refresh_journal_path(data_dir: Path) -> Path:
    return data_dir / JOURNAL_NAME


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(
        prefix=".rust-refresh-journal-", suffix=".json", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(raw, path)
    finally:
        if os.path.exists(raw):
            os.unlink(raw)


def _backup(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        return {"destination": str(path), "existed": False, "backup": ""}
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise ValueError(f"Rust refresh artifact is unsafe: {label}")
    descriptor, raw = tempfile.mkstemp(
        prefix=f".rust-refresh-recovery-{label}-", suffix=".bak", dir=path.parent
    )
    os.close(descriptor)
    os.unlink(raw)
    backup = Path(raw)
    os.link(path, backup)
    return {"destination": str(path), "existed": True, "backup": str(backup)}


def _restore(entry: dict[str, Any]) -> None:
    destination = Path(entry["destination"])
    if entry["existed"]:
        backup = Path(entry["backup"])
        metadata = os.lstat(backup)
        if not stat.S_ISREG(metadata.st_mode) or backup.parent != destination.parent:
            raise ValueError("Rust refresh recovery backup is unsafe")
        os.replace(backup, destination)
        entry["backup"] = ""
    else:
        destination.unlink(missing_ok=True)


def _discard(entry: dict[str, Any]) -> None:
    raw = entry.get("backup")
    if raw:
        Path(raw).unlink(missing_ok=True)
        entry["backup"] = ""


def _validate_shard_path(config: AtlasConfig, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError("Rust refresh shard path is invalid")
    path = Path(raw)
    if path.parent != config.data_dir / "rust-syntax" or path.suffix != ".json":
        raise ValueError("Rust refresh shard path is outside the owned directory")
    return path


def _cleanup_owned_stage_files(config: AtlasConfig) -> int:
    locations = (
        (config.data_dir, (
            (".generation-manifest-candidate-", ".json"),
            (".generation-manifest-backup-", ".json"),
            (".rust-syntax-pointer-", ".json"),
            (".rust-refresh-journal-", ".json"),
            (".rust-refresh-recovery-", ".bak"),
        )),
        (config.data_dir / ".rust-syntax-staging", (
            ("scope-", ".json"),
            ("output-", ".json"),
            ("shard-", ".json"),
        )),
    )
    removed = 0
    for directory, patterns in locations:
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if not any(
                path.name.startswith(prefix) and path.name.endswith(suffix)
                for prefix, suffix in patterns
            ):
                continue
            metadata = os.lstat(path)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("owned Rust refresh staging path is unsafe")
            path.unlink()
            removed += 1
    return removed


@dataclass
class RustRefreshRecoveryJournal:
    path: Path
    document: dict[str, Any]

    @classmethod
    def begin(
        cls,
        config: AtlasConfig,
        generation_before: str | None,
        generation_after: str,
        shard_path: Path,
    ) -> "RustRefreshRecoveryJournal":
        if config.language != "rust":
            raise ValueError("Rust refresh recovery requires a Rust project")
        shard_path = _validate_shard_path(config, str(shard_path))
        artifacts: dict[str, Any] = {}
        destinations = {
            "manifest": manifest_path(config.data_dir),
            "pointer": rust_syntax_pointer_path(config.data_dir),
            "state": state_path(config.data_dir),
        }
        try:
            for label, destination in destinations.items():
                destination.parent.mkdir(parents=True, exist_ok=True)
                artifacts[label] = _backup(destination, label)
            shard_existed = shard_path.exists()
            if shard_existed:
                metadata = os.lstat(shard_path)
                if not stat.S_ISREG(metadata.st_mode) or shard_path.is_symlink():
                    raise ValueError("existing Rust syntax shard is unsafe")
        except BaseException:
            for entry in artifacts.values():
                _discard(entry)
            raise
        document = {
            "schema_version": 1,
            "repository": str(config.repository),
            "project": config.project,
            "data_dir": str(config.data_dir),
            "generation_before": generation_before,
            "generation_after": generation_after,
            "phase": "prepared",
            "artifacts": artifacts,
            "shard": {"path": str(shard_path), "existed": shard_existed},
        }
        path = rust_refresh_journal_path(config.data_dir)
        _write_atomic(path, document)
        return cls(path, document)

    def advance(self, phase: str) -> None:
        current = self.document.get("phase")
        if phase not in RUST_REFRESH_PHASES or current not in RUST_REFRESH_PHASES:
            raise ValueError("Rust refresh recovery phase is invalid")
        if RUST_REFRESH_PHASES.index(phase) <= RUST_REFRESH_PHASES.index(current):
            raise ValueError(f"Rust refresh phase did not advance: {current} -> {phase}")
        self.document["phase"] = phase
        _write_atomic(self.path, self.document)

    def rollback(self) -> None:
        for label in ("state", "pointer", "manifest"):
            _restore(self.document["artifacts"][label])
        shard = self.document["shard"]
        if not shard["existed"]:
            Path(shard["path"]).unlink(missing_ok=True)
        self.commit()

    def commit(self) -> None:
        for entry in self.document["artifacts"].values():
            _discard(entry)
        self.path.unlink(missing_ok=True)


def recover_rust_refresh_transaction(config: AtlasConfig) -> dict[str, Any]:
    path = rust_refresh_journal_path(config.data_dir)
    if not path.exists():
        removed = _cleanup_owned_stage_files(config)
        return {"status": "clean", "action": "removed_owned_staging", "removed": removed}
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise ValueError("Rust refresh recovery journal is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Rust refresh recovery journal is invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Rust refresh recovery journal schema is invalid")
    expected = {
        "repository": str(config.repository),
        "project": config.project,
        "data_dir": str(config.data_dir),
    }
    if any(value.get(name) != expected_value for name, expected_value in expected.items()):
        raise ValueError("Rust refresh recovery journal identity mismatch")
    if value.get("phase") not in RUST_REFRESH_PHASES:
        raise ValueError("Rust refresh recovery journal phase is invalid")
    generation_after = value.get("generation_after")
    if (
        not isinstance(generation_after, str)
        or _GENERATION_ID.fullmatch(generation_after) is None
    ):
        raise ValueError("Rust refresh recovery generation identity is invalid")
    expected_destinations = {
        "manifest": str(manifest_path(config.data_dir)),
        "pointer": str(rust_syntax_pointer_path(config.data_dir)),
        "state": str(state_path(config.data_dir)),
    }
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(expected_destinations):
        raise ValueError("Rust refresh recovery artifact set is invalid")
    for label, destination in expected_destinations.items():
        entry = artifacts[label]
        if not isinstance(entry, dict) or entry.get("destination") != destination:
            raise ValueError("Rust refresh recovery artifact identity mismatch")
        existed, backup = entry.get("existed"), entry.get("backup")
        if not isinstance(existed, bool) or not isinstance(backup, str):
            raise ValueError("Rust refresh recovery backup record is invalid")
        if existed:
            backup_path = Path(backup)
            if (
                backup_path.parent != Path(destination).parent
                or not backup_path.name.startswith(f".rust-refresh-recovery-{label}-")
                or backup_path.suffix != ".bak"
            ):
                raise ValueError("Rust refresh recovery backup path is unsafe")
        elif backup:
            raise ValueError("Rust refresh recovery backup record is inconsistent")
    shard = value.get("shard")
    if not isinstance(shard, dict) or not isinstance(shard.get("existed"), bool):
        raise ValueError("Rust refresh recovery shard record is invalid")
    shard_path = _validate_shard_path(config, shard.get("path"))
    if re.fullmatch(
        rf"{re.escape(generation_after)}-[0-9a-f]{{16}}\.json", shard_path.name
    ) is None:
        raise ValueError("Rust refresh recovery shard identity is invalid")
    journal = RustRefreshRecoveryJournal(path, value)
    if value["phase"] in ACCEPT_PUBLISHED_PHASES:
        journal.commit()
        removed = _cleanup_owned_stage_files(config)
        return {"status": "recovered", "action": "accepted_published_generation", "removed": removed}
    journal.rollback()
    removed = _cleanup_owned_stage_files(config)
    return {"status": "recovered", "action": "restored_previous_generation", "removed": removed}
