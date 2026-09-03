"""Read-only planning for an exact, repository-bounded refresh generation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any, Mapping

from .index_state import repository_snapshot, state_path
from .providers.python_inventory import SourceInventoryError, supported_source_files


MANIFEST_SCHEMA_VERSION = 2
MANIFEST_NAME = "generation-manifest-v2.json"
LANGUAGE_EXTENSIONS = {
    "python": frozenset({".py"}),
    "typescript": frozenset({".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"}),
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class RefreshPlanError(ValueError):
    """Refresh planning failed closed without changing published state."""


@dataclass
class StagedGenerationManifest:
    """Durable candidate bytes with no Stage 1 publication operation."""

    temporary: Path
    document: dict[str, Any]
    destination: Path | None = None
    backup: Path | None = None
    published: bool = False

    def publish(self, destination: Path) -> None:
        if destination != manifest_path(destination.parent):
            raise RefreshPlanError("generation manifest publication target is invalid")
        if destination.exists():
            if not destination.is_file() or destination.is_symlink():
                raise RefreshPlanError("existing generation manifest is not a safe regular file")
            descriptor, raw_backup = tempfile.mkstemp(
                prefix=".generation-manifest-backup-", suffix=".json", dir=destination.parent
            )
            os.close(descriptor)
            os.unlink(raw_backup)
            self.backup = Path(raw_backup)
            os.link(destination, self.backup)
        self.destination = destination
        try:
            os.replace(self.temporary, destination)
            self.published = True
        except BaseException:
            if self.backup is not None:
                self.backup.unlink(missing_ok=True)
                self.backup = None
            raise

    def rollback(self) -> None:
        if not self.published or self.destination is None:
            self.close()
            return
        if self.backup is None:
            self.destination.unlink(missing_ok=True)
        else:
            os.replace(self.backup, self.destination)
            self.backup = None
        self.published = False

    def commit(self) -> bool:
        try:
            if self.backup is not None:
                self.backup.unlink(missing_ok=True)
                self.backup = None
            return True
        except OSError:
            return False

    def close(self) -> None:
        if self.published:
            self.commit()
        else:
            self.temporary.unlink(missing_ok=True)

    def __enter__(self) -> "StagedGenerationManifest":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def manifest_path(data_dir: Path) -> Path:
    return data_dir / MANIFEST_NAME


def generation_manifest_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize a validated manifest in one deterministic representation."""
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def generation_artifact_identity(path: Path) -> dict[str, Any]:
    """Return the exact regular-file identity recorded in a generation manifest."""
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise RefreshPlanError(
            f"generation artifact is not a regular file: {path.name}"
        )
    return {
        "path": str(path),
        "size": metadata.st_size,
        "sha256": _content_sha256(path),
    }


def stage_generation_manifest_candidate(
    data_dir: Path,
    value: Any,
    repository: Path,
    project: str,
) -> StagedGenerationManifest:
    """Fsync candidate bytes; intentionally provide no publication method."""
    document = validate_generation_manifest(value, repository, project)
    data_dir.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=".generation-manifest-candidate-",
        suffix=".json",
        dir=data_dir,
    )
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(generation_manifest_bytes(document))
            stream.flush()
            os.fsync(stream.fileno())
        parsed = json.loads(temporary.read_text(encoding="utf-8"))
        validate_generation_manifest(parsed, repository, project)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return StagedGenerationManifest(temporary, document)


def _content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _language_for(path: Path) -> str:
    return "python" if path.suffix == ".py" else "typescript"


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RefreshPlanError("manifest contains an invalid source path")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or ".." in path.parts:
        raise RefreshPlanError("manifest source path is not repository-relative")
    return value


def _identity(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise RefreshPlanError(f"manifest {name} must be a non-empty object")
    return dict(value)


def validate_generation_manifest(
    value: Any,
    repository: Path,
    project: str,
) -> dict[str, Any]:
    """Validate and normalize a v2 manifest for one exact repository identity."""
    if not isinstance(value, dict) or value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise RefreshPlanError("generation manifest schema is invalid")
    root = repository.resolve()
    if value.get("repository") != str(root):
        raise RefreshPlanError("generation manifest repository identity mismatch")
    if value.get("project") != project:
        raise RefreshPlanError("generation manifest project identity mismatch")
    for name in ("generation_id", "source_kind", "source_fingerprint", "created_at"):
        if not isinstance(value.get(name), str) or not value[name]:
            raise RefreshPlanError(f"generation manifest field is invalid: {name}")
    if value.get("source_head") is not None and not isinstance(value.get("source_head"), str):
        raise RefreshPlanError("generation manifest source_head is invalid")
    raw_files = value.get("files")
    if not isinstance(raw_files, list):
        raise RefreshPlanError("generation manifest files are invalid")
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise RefreshPlanError("generation manifest file entry is invalid")
        path = _safe_relative(raw.get("path"))
        if path in seen:
            raise RefreshPlanError("generation manifest contains duplicate normalized paths")
        digest = raw.get("content_sha256")
        language = raw.get("language")
        size = raw.get("size")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise RefreshPlanError("generation manifest content identity is invalid")
        if language not in LANGUAGE_EXTENSIONS:
            raise RefreshPlanError("generation manifest file language is invalid")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise RefreshPlanError("generation manifest file size is invalid")
        if raw.get("source_state") != "present":
            raise RefreshPlanError("generation manifest source state is invalid")
        seen.add(path)
        files.append({
            "path": path,
            "content_sha256": digest,
            "language": language,
            "size": size,
            "source_state": "present",
        })
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "repository": str(root),
        "project": project,
        "generation_id": value["generation_id"],
        "source_kind": value["source_kind"],
        "source_fingerprint": value["source_fingerprint"],
        "source_head": value.get("source_head"),
        "files": sorted(files, key=lambda item: item["path"]),
        "provider_identity": _identity(value.get("provider_identity"), "provider_identity"),
        "sidecar_identity": _identity(value.get("sidecar_identity"), "sidecar_identity"),
        "created_at": value["created_at"],
    }


def build_generation_manifest(
    repository: Path,
    project: str,
    language: str,
    *,
    generation_id: str,
    provider_identity: Mapping[str, Any],
    sidecar_identity: Mapping[str, Any],
    created_at: str,
) -> dict[str, Any]:
    """Build but never publish a content-addressed candidate manifest."""
    if language not in LANGUAGE_EXTENSIONS:
        raise RefreshPlanError(f"unsupported project language: {language}")
    root = repository.resolve()
    before = repository_snapshot(root)
    if before.kind != "git" or not before.fingerprint:
        raise RefreshPlanError("repository snapshot is unavailable")
    try:
        sources = supported_source_files(
            root, LANGUAGE_EXTENSIONS[language], reject_unsafe=True
        )
    except SourceInventoryError as exc:
        raise RefreshPlanError(str(exc)) from exc
    files = []
    for path in sources:
        relative = path.relative_to(root).as_posix()
        try:
            size = os.lstat(path).st_size
            digest = _content_sha256(path)
        except OSError as exc:
            raise RefreshPlanError(f"source changed during planning: {relative}") from exc
        files.append({
            "path": relative,
            "content_sha256": digest,
            "language": _language_for(path),
            "size": size,
            "source_state": "present",
        })
    after = repository_snapshot(root)
    if (
        after.kind != "git"
        or after.fingerprint != before.fingerprint
        or after.head != before.head
    ):
        raise RefreshPlanError("snapshot_changed_during_plan")
    return validate_generation_manifest({
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "repository": str(root),
        "project": project,
        "generation_id": generation_id,
        "source_kind": after.kind,
        "source_fingerprint": after.fingerprint,
        "source_head": after.head,
        "files": files,
        "provider_identity": dict(provider_identity),
        "sidecar_identity": dict(sidecar_identity),
        "created_at": created_at,
    }, root, project)


def load_generation_manifest(
    data_dir: Path,
    repository: Path,
    project: str,
) -> dict[str, Any] | None:
    path = manifest_path(data_dir)
    if not path.exists():
        return None
    try:
        if not path.is_file() or path.is_symlink():
            raise RefreshPlanError("generation manifest is not a safe regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RefreshPlanError("generation manifest JSON is invalid") from exc
    except OSError as exc:
        raise RefreshPlanError("generation manifest cannot be read") from exc
    return validate_generation_manifest(value, repository, project)


def _baseline_reason(data_dir: Path) -> str:
    path = state_path(data_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "generation_manifest_missing"
    return (
        "index_state_v1_has_no_file_manifest"
        if value.get("schema_version") == 1
        else "generation_manifest_missing"
    )


def plan_refresh(
    data_dir: Path,
    repository: Path,
    project: str,
    language: str,
) -> dict[str, Any]:
    """Return a deterministic plan without publishing state or calling Provider."""
    root = repository.resolve()
    base = load_generation_manifest(data_dir, root, project)
    if base is None:
        return {
            "schema_version": 1,
            "status": "full_baseline_required",
            "mode": "read_only",
            "repository": str(root),
            "project": project,
            "route": "full_rebuild",
            "full_fallback_reason": _baseline_reason(data_dir),
            "base_generation": None,
            "dirty_paths": [],
        }
    snapshot = repository_snapshot(root)
    if (
        snapshot.kind == "git"
        and snapshot.fingerprint == base["source_fingerprint"]
        and snapshot.head == base["source_head"]
    ):
        return {
            "schema_version": 1,
            "status": "planned",
            "mode": "read_only",
            "repository": str(root),
            "project": project,
            "route": "same_connection",
            "base_generation": base["generation_id"],
            "base_source_fingerprint": base["source_fingerprint"],
            "observed_snapshot": {
                "source_kind": snapshot.kind,
                "source_fingerprint": snapshot.fingerprint,
                "source_head": snapshot.head,
            },
            "dirty_paths": [],
            "changes": {"added": [], "modified": [], "deleted": [], "renamed": []},
            "reasons": [],
            "full_fallback_reason": "",
        }
    current = build_generation_manifest(
        root,
        project,
        language,
        generation_id=base["generation_id"],
        provider_identity=base["provider_identity"],
        sidecar_identity=base["sidecar_identity"],
        created_at=base["created_at"],
    )
    old = {entry["path"]: entry for entry in base["files"]}
    new = {entry["path"]: entry for entry in current["files"]}
    added = sorted(set(new) - set(old))
    deleted = sorted(set(old) - set(new))
    modified = sorted(
        path for path in set(old) & set(new)
        if old[path]["content_sha256"] != new[path]["content_sha256"]
        or old[path]["size"] != new[path]["size"]
    )
    by_hash: dict[str, list[str]] = {}
    for path in added:
        by_hash.setdefault(new[path]["content_sha256"], []).append(path)
    renames = []
    for old_path in deleted:
        matches = by_hash.get(old[old_path]["content_sha256"], [])
        if matches:
            renames.append({
                "from": old_path,
                "to": matches.pop(0),
                "content_sha256": old[old_path]["content_sha256"],
            })
    reasons = (
        [{"path": path, "reason": "added"} for path in added]
        + [{"path": path, "reason": "modified"} for path in modified]
        + [{"path": path, "reason": "deleted"} for path in deleted]
    )
    return {
        "schema_version": 1,
        "status": "planned",
        "mode": "read_only",
        "repository": str(root),
        "project": project,
        "route": "same_connection",
        "base_generation": base["generation_id"],
        "base_source_fingerprint": base["source_fingerprint"],
        "observed_snapshot": {
            "source_kind": current["source_kind"],
            "source_fingerprint": current["source_fingerprint"],
            "source_head": current["source_head"],
        },
        "dirty_paths": sorted(set(added) | set(modified) | set(deleted)),
        "changes": {
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "renamed": renames,
        },
        "reasons": sorted(reasons, key=lambda item: (item["path"], item["reason"])),
        "full_fallback_reason": "",
    }
