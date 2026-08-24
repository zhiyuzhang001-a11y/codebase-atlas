"""Persistent, deterministic storage for exact Python registration relations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from .contracts import Edge, Node, SourceRange
from .providers.python_registrations import (
    PythonRegistrationProvider,
    Registration,
    RegistrationIndex,
)


SCHEMA_VERSION = 1
PROVIDER_VERSION = "atlas-python-registrations-v1"
PROVIDER_NAME = PythonRegistrationProvider.name
FILENAME = "python-registrations-v1.json"
MAX_INDEX_BYTES = 100 * 1024 * 1024
_DIRECTORY_FSYNC_SUPPORTED = os.name != "nt"


class RegistrationIndexError(RuntimeError):
    """The persistent registration index cannot safely be used."""


def registration_index_path(data_dir: Path) -> Path:
    return data_dir / FILENAME


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _generation_hash(document: dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("generation_hash", None)
    return hashlib.sha256(_canonical(unsigned)).hexdigest()


def _content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _registration_value(registration: Registration) -> dict[str, Any]:
    return {
        "source": asdict(registration.source),
        "target": asdict(registration.target),
        "edge": asdict(registration.edge),
    }


def build_registration_document(
    repository: Path,
    project: str,
    source_fingerprint: str,
) -> dict[str, Any]:
    """Build one complete sidecar document without publishing it."""
    repository = repository.resolve()
    if not project or not source_fingerprint:
        raise RegistrationIndexError("project and source fingerprint are required")
    provider = PythonRegistrationProvider(repository, project)
    files = provider.source_files()
    hashes = {
        path.relative_to(repository).as_posix(): _content_hash(path)
        for path in files
    }
    registrations = provider.scan().registrations
    return _document_from_parts(
        repository, project, source_fingerprint, hashes, registrations
    )


def _document_from_parts(
    repository: Path,
    project: str,
    source_fingerprint: str,
    hashes: dict[str, str],
    registrations: tuple[Registration, ...],
) -> dict[str, Any]:
    by_path: dict[str, list[Registration]] = {}
    for registration in registrations:
        by_path.setdefault(registration.source.location.path, []).append(registration)

    inventory = []
    for relative in sorted(hashes):
        records = sorted(
            by_path.get(relative, ()),
            key=lambda item: (
                item.source.location.start_line,
                item.source.id,
                item.target.id,
                item.edge.evidence_hash,
            ),
        )
        dependencies = sorted({
            dependency
            for item in records
            for dependency in (
                item.source.location.path,
                item.target.location.path,
            )
            if dependency in hashes
        })
        inventory.append({
            "path": relative,
            "content_sha256": hashes[relative],
            "dependencies": [
                {"path": dependency, "content_sha256": hashes[dependency]}
                for dependency in dependencies
            ],
            "registrations": [_registration_value(item) for item in records],
        })
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "provider": PROVIDER_NAME,
        "provider_version": PROVIDER_VERSION,
        "repository": str(repository),
        "project": project,
        "source_fingerprint": source_fingerprint,
        "files": inventory,
    }
    document["generation_hash"] = _generation_hash(document)
    return document


def build_incremental_registration_document(
    data_dir: Path,
    repository: Path,
    project: str,
    source_fingerprint: str,
    previous_source_fingerprint: str,
) -> dict[str, Any]:
    """Reuse unchanged file records and parse only the affected source set."""
    repository = repository.resolve()
    previous, previous_index = _load_document(
        registration_index_path(data_dir),
        repository,
        project,
        previous_source_fingerprint,
    )
    provider = PythonRegistrationProvider(repository, project)
    hashes = {
        path.relative_to(repository).as_posix(): _content_hash(path)
        for path in provider.source_files()
    }
    old_files = {entry["path"]: entry for entry in previous["files"]}
    changed = {
        path for path in set(old_files) | set(hashes)
        if path not in old_files
        or path not in hashes
        or old_files[path]["content_sha256"] != hashes[path]
    }
    affected = set(changed)
    for path, entry in old_files.items():
        if any(
            dependency.get("path") in changed
            for dependency in entry["dependencies"]
        ):
            affected.add(path)
    affected.intersection_update(hashes)

    retained = tuple(
        item for item in previous_index.registrations
        if item.source.location.path in hashes
        and item.source.location.path not in affected
    )
    known_nodes = tuple({
        item.target.id: item.target for item in previous_index.registrations
        if item.target.location.path not in changed
    }.values())
    rebuilt = provider.scan_files(affected, known_nodes=known_nodes).registrations
    combined = tuple(sorted(
        (*retained, *rebuilt),
        key=lambda item: (
            item.source.location.path,
            item.source.location.start_line,
            item.target.location.path,
            item.target.location.start_line,
        ),
    ))
    return _document_from_parts(
        repository, project, source_fingerprint, hashes, combined
    )


def _json_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"


@dataclass
class StagedRegistrationIndex:
    destination: Path
    temporary: Path
    document: dict[str, Any]
    published: bool = False
    backup: Path | None = None

    def publish(self) -> None:
        if os.path.lexists(self.destination):
            metadata = os.lstat(self.destination)
            if not stat.S_ISREG(metadata.st_mode):
                raise RegistrationIndexError(
                    "existing registration index is not a safe regular file"
                )
            descriptor, raw_backup = tempfile.mkstemp(
                prefix=".python-registrations-backup-",
                suffix=".json",
                dir=self.destination.parent,
            )
            os.close(descriptor)
            os.unlink(raw_backup)
            self.backup = Path(raw_backup)
            os.link(self.destination, self.backup)
        try:
            os.replace(self.temporary, self.destination)
            self.published = True
            self._sync_directory()
        except BaseException:
            if self.published:
                self.rollback()
            elif self.backup is not None:
                self.backup.unlink(missing_ok=True)
                self.backup = None
            raise

    def _sync_directory(self) -> None:
        if not _DIRECTORY_FSYNC_SUPPORTED:
            # Windows does not allow opening a directory with os.open, and has
            # no Python directory-fsync equivalent. The file itself was synced
            # before os.replace; replacement is the strongest portable step.
            return
        directory = os.open(self.destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def commit(self) -> bool:
        """Best-effort cleanup after the generation is durably advertised."""
        if self.backup is None:
            return True
        try:
            self.backup.unlink(missing_ok=True)
            self.backup = None
            self._sync_directory()
        except OSError:
            return False
        return True

    def rollback(self) -> None:
        if not self.published:
            self.close()
            return
        if self.backup is None:
            self.destination.unlink(missing_ok=True)
        else:
            os.replace(self.backup, self.destination)
            self.backup = None
        self._sync_directory()
        self.published = False

    def close(self) -> None:
        if self.published:
            self.commit()
        else:
            try:
                self.temporary.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self) -> "StagedRegistrationIndex":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def stage_registration_index(
    data_dir: Path,
    repository: Path,
    project: str,
    source_fingerprint: str,
    *,
    previous_source_fingerprint: str | None = None,
) -> StagedRegistrationIndex:
    destination = registration_index_path(data_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if previous_source_fingerprint:
        try:
            document = build_incremental_registration_document(
                data_dir,
                repository,
                project,
                source_fingerprint,
                previous_source_fingerprint,
            )
        except RegistrationIndexError:
            document = build_registration_document(
                repository, project, source_fingerprint
            )
    else:
        document = build_registration_document(
            repository, project, source_fingerprint
        )
    payload = _json_bytes(document)
    if len(payload) > MAX_INDEX_BYTES:
        raise RegistrationIndexError("registration index exceeds the 100 MiB limit")
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=".python-registrations-", suffix=".json", dir=destination.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Parse and validate the staged bytes before they become visible.
        _load_document(temporary, repository, project, source_fingerprint)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return StagedRegistrationIndex(destination, temporary, document)


def _source_range(value: Any) -> SourceRange:
    if not isinstance(value, dict):
        raise RegistrationIndexError("invalid source range")
    try:
        return SourceRange(**value)
    except (TypeError, ValueError) as exc:
        raise RegistrationIndexError("invalid source range") from exc


def _node(value: Any) -> Node:
    if not isinstance(value, dict):
        raise RegistrationIndexError("invalid node")
    try:
        raw = dict(value)
        raw["location"] = _source_range(raw.get("location"))
        return Node(**raw)
    except (TypeError, ValueError) as exc:
        raise RegistrationIndexError("invalid node") from exc


def _edge(value: Any) -> Edge:
    if not isinstance(value, dict):
        raise RegistrationIndexError("invalid edge")
    try:
        return Edge(**value)
    except (TypeError, ValueError) as exc:
        raise RegistrationIndexError("invalid edge") from exc


def _load_document(
    path: Path,
    repository: Path,
    project: str,
    source_fingerprint: str,
) -> tuple[dict[str, Any], RegistrationIndex]:
    try:
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_INDEX_BYTES:
            raise RegistrationIndexError("registration index is not a safe regular file")
        payload = path.read_bytes()
        value = json.loads(payload)
    except RegistrationIndexError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistrationIndexError("registration index is unreadable") from exc
    if not isinstance(value, dict):
        raise RegistrationIndexError("registration index root must be an object")
    expected = {
        "schema_version": SCHEMA_VERSION,
        "provider": PROVIDER_NAME,
        "provider_version": PROVIDER_VERSION,
        "repository": str(repository.resolve()),
        "project": project,
        "source_fingerprint": source_fingerprint,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise RegistrationIndexError(f"registration index {key} mismatch")
    if value.get("generation_hash") != _generation_hash(value):
        raise RegistrationIndexError("registration index generation hash mismatch")
    if payload != _json_bytes(value):
        raise RegistrationIndexError("registration index bytes are not canonical")
    raw_files = value.get("files")
    if not isinstance(raw_files, list):
        raise RegistrationIndexError("registration index files must be a list")
    registrations: list[Registration] = []
    previous = ""
    for file_value in raw_files:
        if not isinstance(file_value, dict):
            raise RegistrationIndexError("invalid registration file entry")
        relative = file_value.get("path")
        if not isinstance(relative, str) or relative <= previous:
            raise RegistrationIndexError("registration file paths are invalid or unsorted")
        _source_range({"path": relative, "start_line": 1, "end_line": 1})
        previous = relative
        content_hash = file_value.get("content_sha256")
        if (
            not isinstance(content_hash, str)
            or len(content_hash) != 64
            or any(character not in "0123456789abcdef" for character in content_hash)
        ):
            raise RegistrationIndexError("invalid source content hash")
        if not isinstance(file_value.get("dependencies"), list) or not isinstance(file_value.get("registrations"), list):
            raise RegistrationIndexError("invalid registration file collections")
        dependency_paths: list[str] = []
        for dependency in file_value["dependencies"]:
            if not isinstance(dependency, dict):
                raise RegistrationIndexError("invalid dependency entry")
            dependency_path = dependency.get("path")
            dependency_hash = dependency.get("content_sha256")
            if not isinstance(dependency_path, str):
                raise RegistrationIndexError("invalid dependency path")
            _source_range({
                "path": dependency_path, "start_line": 1, "end_line": 1
            })
            if (
                not isinstance(dependency_hash, str)
                or len(dependency_hash) != 64
                or any(character not in "0123456789abcdef" for character in dependency_hash)
            ):
                raise RegistrationIndexError("invalid dependency content hash")
            dependency_paths.append(dependency_path)
        if dependency_paths != sorted(set(dependency_paths)):
            raise RegistrationIndexError("dependency paths are unsorted or duplicated")
        for raw in file_value["registrations"]:
            if not isinstance(raw, dict):
                raise RegistrationIndexError("invalid registration record")
            source = _node(raw.get("source"))
            target = _node(raw.get("target"))
            edge = _edge(raw.get("edge"))
            if source.location.path != relative or edge.source_id != source.id or edge.target_id != target.id or edge.relation != "registers":
                raise RegistrationIndexError("inconsistent registration record")
            registrations.append(Registration(source, target, edge))
    return value, RegistrationIndex(tuple(registrations))


def load_registration_index(
    data_dir: Path,
    repository: Path,
    project: str,
    source_fingerprint: str,
) -> RegistrationIndex:
    return load_registration_index_state(
        data_dir, repository, project, source_fingerprint
    )[0]


def load_registration_index_state(
    data_dir: Path,
    repository: Path,
    project: str,
    source_fingerprint: str,
) -> tuple[RegistrationIndex, dict[str, Any]]:
    value, index = _load_document(
        registration_index_path(data_dir), repository, project, source_fingerprint
    )
    return index, {
        "status": "ready",
        "ok": True,
        "reason": "registration_index_valid",
        "generation_hash": value["generation_hash"],
        "files": len(value["files"]),
        "registrations": len(index.registrations),
    }


def registration_index_health(
    data_dir: Path,
    repository: Path,
    project: str,
    source_fingerprint: str | None,
) -> dict[str, Any]:
    if not source_fingerprint:
        return {"status": "rebuild_required", "ok": False, "reason": "source_fingerprint_missing"}
    try:
        _index, health = load_registration_index_state(
            data_dir, repository, project, source_fingerprint
        )
    except RegistrationIndexError as exc:
        return {"status": "rebuild_required", "ok": False, "reason": str(exc)}
    return health
