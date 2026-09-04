"""Deterministic Python source inventory bounded by the exact Git repository."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
from collections.abc import Collection

from ..cbmignore import CbmIgnore


EXCLUDED_PARTS = {
    ".git", ".atlas", ".agent-token-manager", ".venv", "venv",
    "node_modules", "build", "dist", "__pycache__",
}


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
    )


class SourceInventoryError(ValueError):
    """A candidate source path cannot be safely bounded to the repository."""


def _regular_source(
    repository: Path,
    relative: str,
    extensions: Collection[str],
    *,
    reject_unsafe: bool,
) -> Path | None:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or candidate.suffix not in extensions:
        return None
    path = repository / candidate
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if path.is_symlink() or not resolved.is_relative_to(repository):
        if reject_unsafe:
            raise SourceInventoryError(f"source path escapes repository boundary: {candidate.as_posix()}")
        return None
    if not stat.S_ISREG(metadata.st_mode):
        return None
    return resolved


def _git_source_files(
    repository: Path,
    extensions: Collection[str],
    *,
    reject_unsafe: bool,
) -> tuple[Path, ...] | None:
    top = _git(repository, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return None
    try:
        if Path(os.fsdecode(top.stdout.strip())).resolve() != repository:
            return None
    except OSError:
        return None
    listed = _git(
        repository,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        *(f"*{extension}" for extension in sorted(extensions)),
    )
    if listed.returncode != 0:
        raise RuntimeError("Git Python source inventory failed")
    cbmignore = CbmIgnore.load(repository)
    paths = {
        path
        for raw in listed.stdout.split(b"\0")
        if raw
        if (
            path := _regular_source(
                repository,
                os.fsdecode(raw),
                extensions,
                reject_unsafe=reject_unsafe,
            )
        ) is not None
        and not cbmignore.ignores(os.fsdecode(raw))
    }
    return tuple(sorted(paths))


def _fallback_source_files(
    repository: Path,
    extensions: Collection[str],
    *,
    reject_unsafe: bool,
) -> tuple[Path, ...]:
    files: list[Path] = []
    cbmignore = CbmIgnore.load(repository)
    for raw_directory, raw_subdirectories, raw_files in os.walk(
        repository, followlinks=False
    ):
        directory = Path(raw_directory)
        raw_subdirectories[:] = sorted(
            name for name in raw_subdirectories
            if name not in EXCLUDED_PARTS
            and not (directory / name).is_symlink()
            and not cbmignore.ignores(
                (directory / name).relative_to(repository).as_posix(), is_dir=True
            )
        )
        for name in sorted(raw_files):
            path = _regular_source(
                repository,
                str((directory / name).relative_to(repository)),
                extensions,
                reject_unsafe=reject_unsafe,
            )
            if path is not None:
                relative = path.relative_to(repository).as_posix()
                if not cbmignore.ignores(relative):
                    files.append(path)
    return tuple(files)


def supported_source_files(
    repository: Path,
    extensions: Collection[str],
    *,
    reject_unsafe: bool = False,
) -> tuple[Path, ...]:
    """Return exact-root supported sources, honoring standard Git ignore rules."""
    root = repository.resolve()
    normalized = frozenset(extensions)
    git_files = _git_source_files(root, normalized, reject_unsafe=reject_unsafe)
    return (
        git_files
        if git_files is not None
        else _fallback_source_files(root, normalized, reject_unsafe=reject_unsafe)
    )


def python_source_files(repository: Path) -> tuple[Path, ...]:
    """Return exact-root Python sources, honoring standard Git ignore rules."""
    return supported_source_files(repository, {".py"})
