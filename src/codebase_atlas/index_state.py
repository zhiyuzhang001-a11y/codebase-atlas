"""Atlas-owned index freshness state.

The Provider owns and atomically publishes the graph database. Atlas records a
small, separate source fingerprint only after that publication succeeds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import tomllib

from .cbmignore import CbmIgnore


STATE_SCHEMA_VERSION = 1
@dataclass(frozen=True)
class RepositorySnapshot:
    kind: str
    fingerprint: str | None
    head: str | None
    changed_paths: int | None
    reason: str


@dataclass(frozen=True)
class IndexState:
    schema_version: int
    repository: str
    project: str
    mode: str
    source_kind: str
    source_fingerprint: str | None
    source_head: str | None
    changed_paths: int | None
    updated_at: str


def state_path(data_dir: Path) -> Path:
    return data_dir / "index-state.json"


def provider_database_health(cache_dir: Path, project: str) -> dict[str, str | bool | int]:
    if not project:
        return {"status": "missing", "ok": False, "reason": "project_not_configured"}
    root = cache_dir.resolve()
    database = (root / f"{project}.db").resolve()
    if database.parent != root:
        return {"status": "invalid", "ok": False, "reason": "project_name_is_not_safe"}
    try:
        size = database.stat().st_size
    except OSError:
        return {"status": "missing", "ok": False, "reason": "provider_database_missing"}
    if size <= 0:
        return {"status": "invalid", "ok": False, "reason": "provider_database_empty", "size": size}
    return {
        "status": "ready",
        "ok": True,
        "reason": "provider_database_present",
        "size": size,
    }


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
    )


def _paths(output: bytes) -> list[str]:
    return [os.fsdecode(value) for value in output.split(b"\0") if value]


def _is_atlas_runtime_config(repository: Path, relative: str) -> bool:
    """Recognize only a regular Atlas config for this exact repository."""
    return _atlas_config_targets_repository(repository / relative, repository)


def _atlas_config_targets_repository(path: Path, repository: Path) -> bool:
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return False
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        project = value.get("project")
        runtime = value.get("runtime")
        if value.get("schema_version") != 1 or not isinstance(project, dict) or not isinstance(runtime, dict):
            return False
        required_project = ("repository", "language", "data_dir", "cbm_project", "tsconfig")
        required_runtime = ("node", "node_bin_dir", "cbm_binary", "serena_python")
        if not all(isinstance(project.get(key), str) for key in required_project):
            return False
        if not all(isinstance(runtime.get(key), str) for key in required_runtime):
            return False
        return Path(project["repository"]).resolve() == repository
    except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError):
        return False


def _is_atlas_project_codex_config(repository: Path, relative: str) -> bool:
    """Recognize only a Codex config containing Atlas's exact managed marker."""
    if Path(relative).as_posix() != ".codex/config.toml":
        return False
    try:
        path = repository / relative
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return False
        text = path.read_text(encoding="utf-8")
        if (
            text.count("# >>> codebase-atlas managed project mcp v1 >>>") != 1
            or text.count("# <<< codebase-atlas managed project mcp v1 <<<") != 1
        ):
            return False
        value = tomllib.loads(text)
        servers = value.get("mcp_servers")
        if not isinstance(servers, dict):
            return False
        for entry in servers.values():
            if not isinstance(entry, dict):
                continue
            args = entry.get("args")
            if not isinstance(args, list) or not all(
                isinstance(argument, str) for argument in args
            ):
                continue
            if "--config" in args:
                position = args.index("--config")
                if position + 1 < len(args):
                    atlas_config = Path(args[position + 1])
                    if _atlas_config_targets_repository(atlas_config, repository):
                        return True
            if "--root" in args:
                position = args.index("--root")
                if position + 1 < len(args):
                    root = Path(args[position + 1])
                    try:
                        if root.resolve() == repository:
                            return True
                    except OSError:
                        pass
        return False
    except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError):
        return False


def _hash_path(digest: "hashlib._Hash", repository: Path, relative: str) -> None:
    digest.update(relative.encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")
    path = repository / relative
    if path.is_symlink():
        digest.update(b"symlink\0")
        digest.update(os.fsencode(os.readlink(path)))
    elif path.is_file():
        digest.update(b"file\0")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    elif path.is_dir():
        # Git reports submodules as paths. Their own HEAD and worktree status
        # distinguish daily edits without hashing the whole nested repository.
        digest.update(b"directory\0")
        head = _git(path, "rev-parse", "HEAD")
        status = _git(path, "status", "--porcelain=v1", "--untracked-files=all")
        digest.update(head.stdout if head.returncode == 0 else b"no-head")
        digest.update(status.stdout if status.returncode == 0 else b"unknown-status")
    else:
        digest.update(b"missing\0")
    digest.update(b"\0")


def repository_snapshot(repository: Path) -> RepositorySnapshot:
    repository = repository.resolve()
    inside = _git(repository, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        return RepositorySnapshot("unknown", None, None, None, "repository_is_not_git")

    head_result = _git(repository, "rev-parse", "HEAD")
    head = os.fsdecode(head_result.stdout.strip()) if head_result.returncode == 0 else "unborn"
    tracked = _git(repository, "diff", "--name-only", "-z", "HEAD")
    if tracked.returncode != 0 and head != "unborn":
        return RepositorySnapshot("unknown", None, head, None, "git_diff_failed")
    if head == "unborn":
        tracked = _git(repository, "ls-files", "-z")
    untracked = _git(repository, "ls-files", "--others", "--exclude-standard", "-z")
    if tracked.returncode != 0 or untracked.returncode != 0:
        return RepositorySnapshot("unknown", None, head, None, "git_status_failed")

    # Atlas updates its project-local runtime configuration after a successful
    # index. Any valid Atlas config for this repository is operational metadata,
    # not source; arbitrary TOML and foreign Atlas configs remain fingerprinted.
    cbmignore = CbmIgnore.load(repository)
    changed = sorted(
        path
        for path in set(_paths(tracked.stdout) + _paths(untracked.stdout))
        if not _is_atlas_runtime_config(repository, path)
        and not _is_atlas_project_codex_config(repository, path)
        and (
            Path(path).as_posix() == ".cbmignore"
            or not cbmignore.ignores(Path(path).as_posix())
        )
    )
    digest = hashlib.sha256()
    digest.update(b"codebase-atlas-source-v1\0")
    digest.update(head.encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")
    for relative in changed:
        _hash_path(digest, repository, relative)
    return RepositorySnapshot("git", digest.hexdigest(), head, len(changed), "snapshot_complete")


def record_index_state(
    data_dir: Path,
    repository: Path,
    project: str,
    mode: str,
    *,
    snapshot: RepositorySnapshot | None = None,
) -> IndexState:
    snapshot = snapshot or repository_snapshot(repository)
    state = IndexState(
        schema_version=STATE_SCHEMA_VERSION,
        repository=str(repository.resolve()),
        project=project,
        mode=mode,
        source_kind=snapshot.kind,
        source_fingerprint=snapshot.fingerprint,
        source_head=snapshot.head,
        changed_paths=snapshot.changed_paths,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    destination = state_path(data_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".index-state-", suffix=".json", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(asdict(state), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return state


def index_freshness(data_dir: Path, repository: Path, project: str) -> dict[str, str | bool | int | None]:
    destination = state_path(data_dir)
    if not project:
        return {"status": "rebuild_required", "ok": False, "reason": "project_not_configured"}
    if not destination.is_file():
        return {"status": "rebuild_required", "ok": False, "reason": "index_state_missing"}
    try:
        value = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "rebuild_required", "ok": False, "reason": "index_state_invalid"}
    if value.get("schema_version") != STATE_SCHEMA_VERSION:
        return {"status": "rebuild_required", "ok": False, "reason": "index_state_schema_changed"}
    if value.get("repository") != str(repository.resolve()) or value.get("project") != project:
        return {"status": "rebuild_required", "ok": False, "reason": "index_state_identity_mismatch"}

    snapshot = repository_snapshot(repository)
    if snapshot.kind == "unknown":
        return {"status": "unknown", "ok": True, "reason": snapshot.reason}
    if not value.get("source_fingerprint"):
        return {"status": "unknown", "ok": True, "reason": "recorded_source_was_not_git"}
    if snapshot.fingerprint != value.get("source_fingerprint"):
        return {
            "status": "stale",
            "ok": False,
            "reason": "repository_changed",
            "head": snapshot.head,
            "changed_paths": snapshot.changed_paths,
            "mode": value.get("mode"),
            "indexed_source_fingerprint": value.get("source_fingerprint"),
        }
    return {
        "status": "fresh",
        "ok": True,
        "reason": "repository_matches_index_state",
        "head": snapshot.head,
        "changed_paths": snapshot.changed_paths,
        "mode": value.get("mode"),
        "source_fingerprint": snapshot.fingerprint,
    }
