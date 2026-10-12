"""Versioned Rust T1 syntax shard staging with no product routing."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
import tempfile
from typing import Any, Callable

from ..contracts import EvidenceProvenance, Node, SourceRange
from ..rust_scope import RustScopeError, validate_rust_source_scope
from ..rust_owned_command import run_owned


PROVIDER_NAME = "rust-native-syntax"
PROVIDER_VERSION = "0.2.0"
ENGINE = (
    "tree-sitter-rust 0.24.2 via tree-sitter 0.27.0; "
    "syntax validation via syn 3.0.6"
)
MAX_OUTPUT_BYTES = 256 * 1024 * 1024
MAX_SOURCE_FILES = 100_000
MAX_SOURCE_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 512 * 1024 * 1024
CANDIDATE_RECORD = struct.Struct(">7I")
NO_OWNER = 0xFFFFFFFF
POINTER_NAME = "rust-syntax-current-v1.json"
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_FACT_KINDS = frozenset({
    "const_item", "enum_item", "function_item", "function_signature_item",
    "impl", "macro_definition", "macro_invocation", "mod_item", "static_item",
    "struct_item", "trait_item", "type_item", "union_item", "use_declaration",
})
Runner = Callable[..., subprocess.CompletedProcess[bytes]]


class RustSyntaxError(RuntimeError):
    """The Rust syntax candidate failed without publishing a shard."""


def _require_safe_directory(path: Path, label: str) -> None:
    metadata = os.lstat(path)
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise RustSyntaxError(f"Rust syntax {label} directory is unsafe")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RustSyntaxError("Rust syntax fact path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or ".." in path.parts:
        raise RustSyntaxError("Rust syntax fact path escapes the repository")
    return value


def _position(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise RustSyntaxError(f"Rust syntax {label} position is invalid")
    line, column = value.get("line"), value.get("column")
    if (
        not isinstance(line, int) or isinstance(line, bool) or line < 1
        or not isinstance(column, int) or isinstance(column, bool) or column < 1
    ):
        raise RustSyntaxError(f"Rust syntax {label} position is invalid")
    return {"line": line, "column": column}


def _fact(value: Any, source_paths: set[str], *, identifier: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RustSyntaxError("Rust syntax fact is invalid")
    path = _safe_path(value.get("path"))
    if path not in source_paths:
        raise RustSyntaxError("Rust syntax fact is outside the frozen source scope")
    kind, name, owner = value.get("kind"), value.get("name"), value.get("owner")
    if not isinstance(kind, str) or not kind:
        raise RustSyntaxError("Rust syntax fact kind is invalid")
    if identifier and kind != "identifier_candidate":
        raise RustSyntaxError("Rust identifier candidate kind is invalid")
    if not identifier and kind not in _FACT_KINDS:
        raise RustSyntaxError("Rust syntax fact kind is unsupported")
    if not isinstance(name, str) or not name:
        raise RustSyntaxError("Rust syntax fact name is invalid")
    if owner is not None and not isinstance(owner, str):
        raise RustSyntaxError("Rust syntax fact owner is invalid")
    start = _position(value.get("start"), "start")
    end = _position(value.get("end"), "end")
    if (end["line"], end["column"]) < (start["line"], start["column"]):
        raise RustSyntaxError("Rust syntax fact range is reversed")
    return {
        "path": path, "kind": kind, "name": name, "owner": owner,
        "start": start, "end": end,
    }


def validate_scanner_output(value: Any, source_paths: tuple[str, ...]) -> dict[str, Any]:
    """Validate native output against the exact S2 source scope."""
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        raise RustSyntaxError("Rust syntax output schema is invalid")
    if value.get("provider") != PROVIDER_NAME or value.get("provider_version") != PROVIDER_VERSION:
        raise RustSyntaxError("Rust syntax provider identity mismatch")
    if value.get("engine") != ENGINE:
        raise RustSyntaxError("Rust syntax engine identity mismatch")
    expected = set(source_paths)
    raw_files = value.get("files")
    if not isinstance(raw_files, list):
        raise RustSyntaxError("Rust syntax file records are invalid")
    files = []
    for raw in raw_files:
        if not isinstance(raw, dict) or not isinstance(raw.get("has_parse_error"), bool):
            raise RustSyntaxError("Rust syntax file record is invalid")
        files.append({
            "path": _safe_path(raw.get("path")),
            "has_parse_error": raw["has_parse_error"],
        })
    observed = [item["path"] for item in files]
    if len(set(observed)) != len(observed) or set(observed) != expected:
        raise RustSyntaxError("Rust syntax output does not cover the exact source scope")
    error_files = value.get("error_files")
    if not isinstance(error_files, list) or not all(isinstance(path, str) for path in error_files):
        raise RustSyntaxError("Rust syntax error file list is invalid")
    normalized_errors = sorted({_safe_path(path) for path in error_files})
    recorded_errors = sorted(item["path"] for item in files if item["has_parse_error"])
    if normalized_errors != recorded_errors:
        raise RustSyntaxError("Rust syntax parse-error records disagree")
    raw_facts = value.get("facts")
    raw_candidates = value.get("identifier_candidates_hex")
    candidate_count = value.get("identifier_candidate_count")
    if (
        not isinstance(raw_facts, list)
        or not isinstance(raw_candidates, str)
        or not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or candidate_count < 0
        or len(raw_candidates) != candidate_count * CANDIDATE_RECORD.size * 2
        or re.fullmatch(r"[0-9a-f]*", raw_candidates) is None
    ):
        raise RustSyntaxError("Rust syntax facts are invalid")
    facts = [_fact(item, expected, identifier=False) for item in raw_facts]
    candidate_paths = value.get("candidate_paths")
    candidate_names = value.get("candidate_names")
    candidate_owners = value.get("candidate_owners")
    if (
        not isinstance(candidate_paths, list)
        or [_safe_path(path) for path in candidate_paths] != sorted(expected)
        or not isinstance(candidate_names, list)
        or not all(isinstance(name, str) and name for name in candidate_names)
        or candidate_names != sorted(set(candidate_names))
        or not isinstance(candidate_owners, list)
        or not all(isinstance(owner, str) and owner for owner in candidate_owners)
        or len(candidate_owners) != len(set(candidate_owners))
    ):
        raise RustSyntaxError("Rust syntax candidate dictionaries are invalid")
    candidate_bytes = bytes.fromhex(raw_candidates)
    for raw in CANDIDATE_RECORD.iter_unpack(candidate_bytes):
        path_index, name_index, owner_index, *coordinates = raw
        if (
            path_index >= len(candidate_paths)
            or name_index >= len(candidate_names)
            or owner_index != NO_OWNER and owner_index >= len(candidate_owners)
            or any(coordinate < 1 for coordinate in coordinates)
            or tuple(coordinates[2:]) < tuple(coordinates[:2])
        ):
            raise RustSyntaxError("Rust identifier candidate is invalid")
    key = lambda item: (
        item["path"], item["start"]["line"], item["start"]["column"],
        item["end"]["line"], item["end"]["column"], item["kind"],
        item["name"], item["owner"] or "",
    )
    return {
        "schema_version": 2,
        "provider": PROVIDER_NAME,
        "provider_version": PROVIDER_VERSION,
        "engine": ENGINE,
        "files": sorted(files, key=lambda item: item["path"]),
        "facts": sorted(facts, key=key),
        "candidate_paths": candidate_paths,
        "candidate_names": candidate_names,
        "candidate_owners": candidate_owners,
        "identifier_candidate_count": candidate_count,
        "identifier_candidates_hex": raw_candidates,
        "error_files": normalized_errors,
    }


class StagedRustSyntaxShard:
    def __init__(self, temporary: Path, destination: Path, document: dict[str, Any]):
        self.temporary = temporary
        self.destination = destination
        self.document = document
        self.published = False

    def publish(self) -> None:
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        _require_safe_directory(self.destination.parent, "shard")
        if self.destination.exists():
            if self.destination.is_symlink() or not self.destination.is_file():
                raise RustSyntaxError("Rust syntax shard destination is unsafe")
            if self.destination.read_bytes() != self.temporary.read_bytes():
                raise RustSyntaxError("Rust syntax shard identity collision")
            self.temporary.unlink()
            return
        os.replace(self.temporary, self.destination)
        self.published = True

    def rollback(self) -> None:
        self.temporary.unlink(missing_ok=True)
        if self.published:
            self.destination.unlink(missing_ok=True)
            self.published = False

    def close(self) -> None:
        self.temporary.unlink(missing_ok=True)

    def __enter__(self) -> "StagedRustSyntaxShard":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


class StagedRustSyntaxPointer:
    """Fsynced pointer candidate; publication is one atomic replacement."""

    def __init__(self, temporary: Path, destination: Path, document: dict[str, Any]):
        self.temporary = temporary
        self.destination = destination
        self.document = document
        self.published = False

    def publish(self) -> None:
        if self.destination != rust_syntax_pointer_path(self.destination.parent):
            raise RustSyntaxError("Rust syntax pointer publication target is invalid")
        if self.destination.exists():
            metadata = os.lstat(self.destination)
            if not stat.S_ISREG(metadata.st_mode) or self.destination.is_symlink():
                raise RustSyntaxError("existing Rust syntax pointer is unsafe")
        os.replace(self.temporary, self.destination)
        self.published = True

    def close(self) -> None:
        self.temporary.unlink(missing_ok=True)

    def __enter__(self) -> "StagedRustSyntaxPointer":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def rust_syntax_pointer_path(data_dir: Path) -> Path:
    return data_dir / POINTER_NAME


def rust_syntax_shard_identity(staged: StagedRustSyntaxShard) -> dict[str, Any]:
    """Return the final-path identity of one fsynced immutable shard candidate."""
    metadata = os.lstat(staged.temporary)
    if not stat.S_ISREG(metadata.st_mode):
        raise RustSyntaxError("Rust syntax shard candidate is unsafe")
    return {
        "path": str(staged.destination),
        "size": metadata.st_size,
        "sha256": _sha256(staged.temporary),
    }


def validate_rust_syntax_pointer(
    value: Any, repository: Path, project: str, data_dir: Path
) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RustSyntaxError("Rust syntax pointer schema is invalid")
    expected = {
        "repository": str(repository.resolve()),
        "project": project,
        "fact_tier": "T1",
        "provider": PROVIDER_NAME,
        "provider_version": PROVIDER_VERSION,
        "engine": ENGINE,
    }
    if any(value.get(name) != expected_value for name, expected_value in expected.items()):
        raise RustSyntaxError("Rust syntax pointer identity mismatch")
    generation_id = value.get("generation_id")
    if not isinstance(generation_id, str) or _SAFE_COMPONENT.fullmatch(generation_id) is None:
        raise RustSyntaxError("Rust syntax pointer generation id is unsafe")
    artifact = value.get("artifact")
    if not isinstance(artifact, dict):
        raise RustSyntaxError("Rust syntax pointer artifact is invalid")
    path = Path(artifact.get("path", ""))
    shard_root = (data_dir.resolve() / "rust-syntax")
    if path.parent != shard_root or not path.name.startswith(f"{generation_id}-"):
        raise RustSyntaxError("Rust syntax pointer artifact path is unsafe")
    size, digest = artifact.get("size"), artifact.get("sha256")
    if (
        not isinstance(size, int) or isinstance(size, bool) or size < 0
        or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise RustSyntaxError("Rust syntax pointer artifact identity is invalid")
    return {**expected, "schema_version": 1, "generation_id": generation_id,
            "artifact": {"path": str(path), "size": size, "sha256": digest}}


def stage_rust_syntax_pointer(
    data_dir: Path,
    repository: Path,
    project: str,
    generation_id: str,
    artifact: dict[str, Any],
) -> StagedRustSyntaxPointer:
    destination = rust_syntax_pointer_path(data_dir)
    value = validate_rust_syntax_pointer({
        "schema_version": 1,
        "repository": str(repository.resolve()),
        "project": project,
        "generation_id": generation_id,
        "fact_tier": "T1",
        "provider": PROVIDER_NAME,
        "provider_version": PROVIDER_VERSION,
        "engine": ENGINE,
        "artifact": artifact,
    }, repository, project, data_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _require_safe_directory(destination.parent, "pointer")
    descriptor, raw = tempfile.mkstemp(
        prefix=".rust-syntax-pointer-", suffix=".json", dir=destination.parent
    )
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return StagedRustSyntaxPointer(temporary, destination, value)


def load_rust_syntax_pointer(
    data_dir: Path, repository: Path, project: str, *, verify_artifact: bool = True
) -> dict[str, Any] | None:
    path = rust_syntax_pointer_path(data_dir)
    if not path.exists():
        return None
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise RustSyntaxError("Rust syntax pointer is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RustSyntaxError("Rust syntax pointer is invalid") from exc
    pointer = validate_rust_syntax_pointer(value, repository, project, data_dir)
    if verify_artifact:
        artifact = pointer["artifact"]
        shard = Path(artifact["path"])
        try:
            shard_metadata = os.lstat(shard)
        except OSError as exc:
            raise RustSyntaxError("Rust syntax shard is unavailable") from exc
        if (
            not stat.S_ISREG(shard_metadata.st_mode)
            or shard.is_symlink()
            or shard_metadata.st_size != artifact["size"]
            or _sha256(shard) != artifact["sha256"]
        ):
            raise RustSyntaxError("Rust syntax shard identity mismatch")
    return pointer


class RustSyntaxProvider:
    """Run one fixed scanner over an already-frozen Rust source scope."""

    def __init__(
        self,
        scanner: Path,
        repository: Path,
        data_dir: Path,
        project: str,
        *,
        runner: Runner = run_owned,
        execution_preflight: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self.scanner = scanner.absolute()
        self.repository = repository.resolve()
        self.data_dir = data_dir.resolve()
        self.project = project
        self.runner = runner
        self.execution_preflight = execution_preflight

    def stage(self, generation: dict[str, Any], *, timeout_seconds: float = 120.0) -> StagedRustSyntaxShard:
        if generation.get("repository") != str(self.repository) or generation.get("project") != self.project:
            raise RustSyntaxError("Rust generation identity mismatch")
        if generation.get("language") != "rust":
            raise RustSyntaxError("Rust syntax provider requires a Rust generation")
        generation_id = generation.get("generation_id")
        if not isinstance(generation_id, str) or _SAFE_COMPONENT.fullmatch(generation_id) is None:
            raise RustSyntaxError("Rust generation id is unsafe")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 0 < timeout_seconds <= 300
        ):
            raise RustSyntaxError("Rust syntax timeout must be between 0 and 300 seconds")
        try:
            scope = validate_rust_source_scope(generation.get("source_scope"))
        except RustScopeError as exc:
            raise RustSyntaxError(str(exc)) from exc
        files = generation.get("files")
        if not isinstance(files, list) or len(files) != len(scope["source_paths"]):
            raise RustSyntaxError("Rust generation file inventory is invalid")
        if len(files) > MAX_SOURCE_FILES:
            raise RustSyntaxError("Rust source scope exceeds the file-count limit")
        source_paths = set(scope["source_paths"])
        total_bytes = 0
        for entry in files:
            if not isinstance(entry, dict) or entry.get("path") not in source_paths:
                raise RustSyntaxError("Rust generation file inventory is invalid")
            size = entry.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise RustSyntaxError("Rust generation source size is invalid")
            if size > MAX_SOURCE_FILE_BYTES:
                raise RustSyntaxError("Rust source file exceeds the per-file limit")
            total_bytes += size
            if total_bytes > MAX_TOTAL_SOURCE_BYTES:
                raise RustSyntaxError("Rust source scope exceeds the byte limit")
        try:
            scanner_metadata = os.lstat(self.scanner)
        except OSError as exc:
            raise RustSyntaxError("Rust syntax scanner is unavailable") from exc
        if not stat.S_ISREG(scanner_metadata.st_mode) or self.scanner.is_symlink():
            raise RustSyntaxError("Rust syntax scanner is not a safe regular file")
        staging = self.data_dir / ".rust-syntax-staging"
        staging.mkdir(parents=True, exist_ok=True)
        _require_safe_directory(staging, "staging")
        scope_fd, scope_raw = tempfile.mkstemp(prefix="scope-", suffix=".json", dir=staging)
        output_fd, output_raw = tempfile.mkstemp(prefix="output-", suffix=".json", dir=staging)
        os.close(output_fd)
        Path(output_raw).unlink()
        scope_path, output_path = Path(scope_raw), Path(output_raw)
        try:
            with os.fdopen(scope_fd, "wb") as stream:
                stream.write(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode())
                stream.flush()
                os.fsync(stream.fileno())
            environment = (self.execution_preflight() if self.execution_preflight is not None
                           else {"PATH": os.environ.get("PATH", ""), "CARGO_NET_OFFLINE": "true"})
            completed = self.runner(
                [str(self.scanner), str(self.repository), str(scope_path), str(output_path)],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.repository,
                env=environment,
                timeout=timeout_seconds,
            )
            if completed.returncode != 0:
                detail = (
                    completed.stderr.decode("utf-8", errors="replace")
                    if isinstance(completed.stderr, bytes)
                    else str(completed.stderr)
                )[:2000]
                raise RustSyntaxError(f"Rust syntax scanner failed: {detail}")
            metadata = os.lstat(output_path)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_OUTPUT_BYTES:
                raise RustSyntaxError("Rust syntax scanner output is unsafe or oversized")
            raw = json.loads(output_path.read_text(encoding="utf-8"))
            output = validate_scanner_output(raw, tuple(scope["source_paths"]))
            del raw
            shard = {
                "schema_version": 1,
                "repository": str(self.repository),
                "project": self.project,
                "generation_id": generation_id,
                "source_fingerprint": generation["source_fingerprint"],
                "fact_tier": "T1",
                "completeness": scope["status"],
                "provider_identity": {
                    "name": PROVIDER_NAME,
                    "version": PROVIDER_VERSION,
                    "engine": ENGINE,
                    "scanner_sha256": _sha256(self.scanner),
                },
                "scope": scope,
                "files": output["files"],
                "facts": output["facts"],
                "candidate_paths": output["candidate_paths"],
                "candidate_names": output["candidate_names"],
                "candidate_owners": output["candidate_owners"],
                "identifier_candidate_count": output["identifier_candidate_count"],
                "identifier_candidates_hex": output["identifier_candidates_hex"],
                "error_files": output["error_files"],
            }
            candidate_fd, candidate_raw = tempfile.mkstemp(
                prefix="shard-", suffix=".json", dir=staging
            )
            candidate = Path(candidate_raw)
            try:
                with os.fdopen(candidate_fd, "w", encoding="utf-8") as stream:
                    json.dump(shard, stream, sort_keys=True, separators=(",", ":"))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                candidate.unlink(missing_ok=True)
                raise
            digest = _sha256(candidate)
            destination = self.data_dir / "rust-syntax" / f"{generation_id}-{digest[:16]}.json"
            return StagedRustSyntaxShard(candidate, destination, shard)
        except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            raise RustSyntaxError(f"Rust syntax staging failed: {exc}") from exc
        finally:
            scope_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)


class RustSyntaxIndex:
    """Read-only T1 candidate queries over one validated staged shard."""

    def __init__(self, document: dict[str, Any]) -> None:
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise RustSyntaxError("Rust syntax shard schema is invalid")
        if document.get("fact_tier") != "T1":
            raise RustSyntaxError("Rust syntax shard fact tier is invalid")
        identity = document.get("provider_identity")
        if (
            not isinstance(identity, dict)
            or identity.get("name") != PROVIDER_NAME
            or identity.get("version") != PROVIDER_VERSION
            or identity.get("engine") != ENGINE
        ):
            raise RustSyntaxError("Rust syntax shard provider identity mismatch")
        for name in ("repository", "project", "generation_id", "source_fingerprint"):
            if not isinstance(document.get(name), str) or not document[name]:
                raise RustSyntaxError(f"Rust syntax shard {name} is invalid")
        try:
            scope = validate_rust_source_scope(document.get("scope"))
        except RustScopeError as exc:
            raise RustSyntaxError(str(exc)) from exc
        output = validate_scanner_output({
            "schema_version": 2,
            "provider": PROVIDER_NAME,
            "provider_version": PROVIDER_VERSION,
            "engine": ENGINE,
            "files": document.get("files"),
            "facts": document.get("facts"),
            "candidate_paths": document.get("candidate_paths"),
            "candidate_names": document.get("candidate_names"),
            "candidate_owners": document.get("candidate_owners"),
            "identifier_candidate_count": document.get("identifier_candidate_count"),
            "identifier_candidates_hex": document.get("identifier_candidates_hex"),
            "error_files": document.get("error_files"),
        }, tuple(scope["source_paths"]))
        self.document = dict(document)
        self.scope = scope
        self.facts = tuple(output["facts"])
        self.candidate_paths = tuple(output["candidate_paths"])
        self.candidate_names = tuple(output["candidate_names"])
        self.candidate_owners = tuple(output["candidate_owners"])
        self.identifier_candidate_count = output["identifier_candidate_count"]
        self.identifier_candidates = bytes.fromhex(output["identifier_candidates_hex"])
        self._repository_identity = hashlib.sha256(
            document["repository"].encode("utf-8")
        ).hexdigest()

    def _node(self, fact: dict[str, Any]) -> Node:
        canonical = json.dumps(fact, sort_keys=True, separators=(",", ":")).encode()
        evidence_hash = hashlib.sha256(canonical).hexdigest()
        start, end = fact["start"], fact["end"]
        # Scanner 0.2.0 wire positions are UTF-8 byte columns. Public positions
        # are Unicode codepoints; keep the native artifact/evidence hash intact.
        candidate = Path(self.document["repository"]) / fact["path"]
        try:
            root = Path(self.document["repository"]).resolve(strict=True)
            if candidate.resolve(strict=True) != candidate or not candidate.is_relative_to(root):
                raise RustSyntaxError("Rust syntax source path is unsafe")
            metadata = os.lstat(candidate)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_SOURCE_FILE_BYTES:
                raise RustSyntaxError("Rust syntax source is unsafe or oversized")
            lines = candidate.read_bytes().splitlines()
            start_column = self._public_column(lines, start)
            end_column = self._public_column(lines, end)
        except (OSError, UnicodeError, IndexError) as exc:
            raise RustSyntaxError("Rust syntax source coordinate is unavailable") from exc
        node_id = (
            f"rust:{self.document['generation_id']}:{fact['path']}:"
            f"{start['line']}:{start['column']}:{fact['kind']}:{evidence_hash[:16]}"
        )
        return Node(
            node_id,
            fact["kind"],
            fact["name"],
            SourceRange(
                fact["path"], start["line"], end["line"],
                start_column, end_column,
            ),
            PROVIDER_NAME,
            0.5,
            evidence_hash,
            attributes={
                "owner": fact["owner"],
                "fact_tier": "T1",
                "resolution": "syntactic_candidate",
                "scope_status": self.scope["status"],
            },
            provenance=EvidenceProvenance(
                self._repository_identity,
                self.document["generation_id"],
                "T1",
                PROVIDER_NAME,
                PROVIDER_VERSION,
                "syntactic_candidates",
            ),
        )

    @staticmethod
    def _public_column(lines: list[bytes], position: dict[str, int]) -> int:
        line = lines[position["line"] - 1]
        offset = position["column"] - 1
        if offset > len(line):
            raise RustSyntaxError("Rust syntax source column is outside the file")
        return len(line[:offset].decode("utf-8")) + 1

    @staticmethod
    def _matches(
        fact: dict[str, Any], symbol: str, target_path: str, target_owner: str
    ) -> bool:
        return (
            fact["name"] == symbol
            and (not target_path or fact["path"] == target_path)
            and (not target_owner or fact["owner"] == target_owner)
        )

    def definition_candidates(
        self, symbol: str, *, target_path: str = "", target_owner: str = ""
    ) -> tuple[Node, ...]:
        return tuple(
            self._node(fact)
            for fact in self.facts
            if fact["kind"] not in {"use_declaration", "macro_invocation"}
            and self._matches(fact, symbol, target_path, target_owner)
        )

    def reference_candidates(
        self, symbol: str, *, target_path: str = "", target_owner: str = ""
    ) -> tuple[Node, ...]:
        nodes = []
        for candidate in CANDIDATE_RECORD.iter_unpack(self.identifier_candidates):
            path_index, name_index, owner_index, start_line, start_column, end_line, end_column = candidate
            path = self.candidate_paths[path_index]
            name = self.candidate_names[name_index]
            owner = (
                self.candidate_owners[owner_index]
                if owner_index != NO_OWNER else None
            )
            if (
                name == symbol
                and (not target_path or path == target_path)
                and (not target_owner or owner == target_owner)
            ):
                nodes.append(self._node({
                    "path": path,
                    "kind": "identifier_candidate",
                    "name": name,
                    "owner": owner,
                    "start": {"line": start_line, "column": start_column},
                    "end": {"line": end_line, "column": end_column},
                }))
        return tuple(nodes)
