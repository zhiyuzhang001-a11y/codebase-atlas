"""Deterministic Python source inventory bounded by the exact Git repository."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess


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


def _regular_python(repository: Path, relative: str) -> Path | None:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or candidate.suffix != ".py":
        return None
    path = repository / candidate
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        return None
    if not resolved.is_relative_to(repository):
        return None
    return resolved


def _git_python_files(repository: Path) -> tuple[Path, ...] | None:
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
        "*.py",
    )
    if listed.returncode != 0:
        raise RuntimeError("Git Python source inventory failed")
    paths = {
        path
        for raw in listed.stdout.split(b"\0")
        if raw
        if (path := _regular_python(repository, os.fsdecode(raw))) is not None
    }
    return tuple(sorted(paths))


def _fallback_python_files(repository: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for raw_directory, raw_subdirectories, raw_files in os.walk(
        repository, followlinks=False
    ):
        directory = Path(raw_directory)
        raw_subdirectories[:] = sorted(
            name for name in raw_subdirectories
            if name not in EXCLUDED_PARTS
            and not (directory / name).is_symlink()
        )
        for name in sorted(raw_files):
            path = _regular_python(repository, str((directory / name).relative_to(repository)))
            if path is not None:
                files.append(path)
    return tuple(files)


def python_source_files(repository: Path) -> tuple[Path, ...]:
    """Return exact-root Python sources, honoring standard Git ignore rules."""
    root = repository.resolve()
    git_files = _git_python_files(root)
    return git_files if git_files is not None else _fallback_python_files(root)
