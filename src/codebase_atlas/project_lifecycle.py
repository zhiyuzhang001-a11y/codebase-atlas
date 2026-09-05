"""Identity-safe durable state for project lifecycle operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from .provider_layout import atlas_data_root


SCHEMA_VERSION = 1
STABLE_STATES = frozenset({"ready", "stopped", "removed", "failed"})
TRANSITION_STATES = frozenset({"enabling", "stopping", "updating", "removing"})
LIFECYCLE_STATES = STABLE_STATES | TRANSITION_STATES


def lifecycle_state_path(data_dir: Path) -> Path:
    return data_dir.resolve() / "lifecycle-state.json"


def project_recovery_root(repository: Path) -> Path:
    canonical = repository.resolve()
    readable = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in canonical.name
    ).strip("-._") or "project"
    digest = hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()[:24]
    return atlas_data_root() / "_recovery" / "projects" / f"{readable[:40]}-{digest}"


def removal_marker_path(repository: Path) -> Path:
    return project_recovery_root(repository) / "removed.json"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ProjectLifecycleState:
    schema_version: int
    repository: str
    project: str
    status: str
    operation_generation: int
    operation_id: str
    atlas_version: str
    provider_version: str
    index_generation: str
    last_ready_version: str
    failure_reason: str
    updated_at: str

    @classmethod
    def initial(
        cls,
        repository: Path,
        project: str,
        *,
        status: str = "ready",
        atlas_version: str = "",
        provider_version: str = "",
        index_generation: str = "",
    ) -> "ProjectLifecycleState":
        if status not in STABLE_STATES:
            raise ValueError("initial lifecycle status must be stable")
        return cls(
            schema_version=SCHEMA_VERSION,
            repository=str(repository.resolve()),
            project=project,
            status=status,
            operation_generation=0,
            operation_id="",
            atlas_version=atlas_version,
            provider_version=provider_version,
            index_generation=index_generation,
            last_ready_version=atlas_version if status == "ready" else "",
            failure_reason="",
            updated_at=_timestamp(),
        )

    def transition(
        self,
        status: str,
        *,
        operation_id: str = "",
        atlas_version: str | None = None,
        provider_version: str | None = None,
        index_generation: str | None = None,
        failure_reason: str = "",
    ) -> "ProjectLifecycleState":
        if status not in LIFECYCLE_STATES:
            raise ValueError(f"unsupported lifecycle status: {status}")
        if status in TRANSITION_STATES and not operation_id:
            raise ValueError("transition lifecycle status requires an operation id")
        selected_atlas = self.atlas_version if atlas_version is None else atlas_version
        return replace(
            self,
            status=status,
            operation_generation=self.operation_generation + 1,
            operation_id=operation_id if status in TRANSITION_STATES else "",
            atlas_version=selected_atlas,
            provider_version=(
                self.provider_version if provider_version is None else provider_version
            ),
            index_generation=(
                self.index_generation if index_generation is None else index_generation
            ),
            last_ready_version=(
                selected_atlas if status == "ready" else self.last_ready_version
            ),
            failure_reason=failure_reason if status == "failed" else "",
            updated_at=_timestamp(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decode_state(
    value: Any, repository: Path, project: str
) -> ProjectLifecycleState:
    if not isinstance(value, dict):
        raise ValueError("lifecycle state must be a JSON object")
    expected_fields = set(ProjectLifecycleState.__dataclass_fields__)
    if set(value) != expected_fields:
        raise ValueError("lifecycle state fields do not match schema")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported lifecycle state schema")
    expected_repository = str(repository.resolve())
    if value.get("repository") != expected_repository or value.get("project") != project:
        raise ValueError("lifecycle state identity mismatch")
    if value.get("status") not in LIFECYCLE_STATES:
        raise ValueError("unsupported lifecycle status")
    generation = value.get("operation_generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        raise ValueError("invalid lifecycle operation generation")
    for name in expected_fields - {"schema_version", "operation_generation"}:
        if not isinstance(value.get(name), str):
            raise ValueError(f"lifecycle state field must be text: {name}")
    if value["status"] in TRANSITION_STATES and not value["operation_id"]:
        raise ValueError("transition lifecycle state has no operation id")
    return ProjectLifecycleState(**value)


def load_lifecycle_state(
    data_dir: Path,
    repository: Path,
    project: str,
    *,
    missing_status: str = "ready",
) -> ProjectLifecycleState:
    """Load exact project state; legacy configured projects default to ready."""
    path = lifecycle_state_path(data_dir)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return ProjectLifecycleState.initial(
            repository, project, status=missing_status
        )
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError("lifecycle state must be a regular non-symlink file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("lifecycle state is unreadable") from exc
    return _decode_state(value, repository, project)


def operational_lifecycle_status(
    data_dir: Path, repository: Path, project: str
) -> dict[str, Any]:
    """Return a fail-closed request gate for one exact configured project."""
    marker = load_removal_marker(repository)
    if marker is not None:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": str(marker["status"]),
            "ok": False,
            "reason": (
                "project_removed"
                if marker["status"] == "removed"
                else "project_removal_in_progress"
            ),
            "repository": str(repository.resolve()),
            "project": str(marker["project"]),
            "receipt": str(marker["receipt"]),
            "operation_id": str(marker["operation_id"]),
        }
    try:
        state = load_lifecycle_state(data_dir, repository, project)
    except ValueError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "ok": False,
            "reason": "lifecycle_state_invalid",
            "detail": str(exc),
            "repository": str(repository.resolve()),
            "project": project,
        }
    reasons = {
        "ready": "project_ready",
        "stopped": "project_stopped",
        "removed": "project_removed",
        "failed": "lifecycle_operation_failed",
        "enabling": "lifecycle_operation_in_progress",
        "stopping": "lifecycle_operation_in_progress",
        "updating": "lifecycle_operation_in_progress",
        "removing": "lifecycle_operation_in_progress",
    }
    return state.to_dict() | {
        "ok": state.status == "ready",
        "reason": reasons[state.status],
    }


def load_removal_marker(repository: Path) -> dict[str, Any] | None:
    path = removal_marker_path(repository)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError("removal marker must be a regular non-symlink file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("removal marker is unreadable") from exc
    expected = {
        "schema_version", "status", "repository", "project",
        "operation_id", "receipt", "updated_at",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("status") not in {"removing", "removed"}
        or value.get("repository") != str(repository.resolve())
        or not all(
            isinstance(value.get(name), str) and value.get(name)
            for name in ("project", "operation_id", "receipt", "updated_at")
        )
    ):
        raise ValueError("removal marker schema or identity is invalid")
    receipt = Path(str(value["receipt"]))
    if not receipt.is_absolute() or not receipt.is_relative_to(
        project_recovery_root(repository).resolve()
    ):
        raise ValueError("removal marker receipt path is unsafe")
    return value


def publish_removal_marker(
    repository: Path,
    project: str,
    operation_id: str,
    receipt: Path,
    *,
    status: str,
) -> Path:
    if status not in {"removing", "removed"}:
        raise ValueError("unsupported removal marker status")
    root = project_recovery_root(repository).resolve()
    selected_receipt = receipt.resolve()
    if not selected_receipt.is_relative_to(root):
        raise ValueError("removal receipt must remain inside the project recovery root")
    path = removal_marker_path(repository)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError("project recovery root must not be a symlink")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "repository": str(repository.resolve()),
        "project": project,
        "operation_id": operation_id,
        "receipt": str(selected_receipt),
        "updated_at": _timestamp(),
    }
    descriptor, temporary = tempfile.mkstemp(
        prefix=".removed-", suffix=".json", dir=path.parent
    )
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def publish_lifecycle_state(data_dir: Path, state: ProjectLifecycleState) -> Path:
    """Atomically publish one validated Atlas-owned lifecycle state."""
    repository = Path(state.repository)
    _decode_state(state.to_dict(), repository, state.project)
    path = lifecycle_state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError("lifecycle state directory must not be a symlink")
    original_identity: tuple[int, int] | None = None
    if os.path.lexists(path):
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ValueError("lifecycle state must be a regular non-symlink file")
        original_identity = (metadata.st_dev, metadata.st_ino)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=".lifecycle-state-", suffix=".json", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(state.to_dict(), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(path):
            current = os.lstat(path)
            if (
                original_identity is None
                or not stat.S_ISREG(current.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or (current.st_dev, current.st_ino) != original_identity
            ):
                raise ValueError("lifecycle state changed before publication")
        elif original_identity is not None:
            raise ValueError("lifecycle state changed before publication")
        os.replace(temporary, path)
        return path
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
