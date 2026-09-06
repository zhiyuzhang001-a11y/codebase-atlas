"""Bounded, non-writing snapshots for diagnostic query acceptance."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess
import time

from .config import AtlasConfig


MAX_FILES = 100_000
MAX_BYTES = 512 * 1024 * 1024
SNAPSHOT_SECONDS = 30.0


def protected_snapshot(config: AtlasConfig, config_path: Path) -> dict[str, str]:
    """Hash Git-visible files plus Atlas configuration and index artifacts.

    Symlinks are hashed as links and never followed. Missing files are included
    so a creation or deletion is visible in the next snapshot.
    """
    deadline = time.monotonic() + SNAPSHOT_SECONDS
    try:
        listed = subprocess.run(
            ["git", "-C", str(config.repository), "ls-files", "-z", "--cached",
             "--others", "--exclude-standard"],
            check=True, capture_output=True, timeout=SNAPSHOT_SECONDS,
        )
    except subprocess.SubprocessError as exc:
        raise RuntimeError("verification snapshot Git discovery failed") from exc
    paths = {config.repository / os.fsdecode(raw)
             for raw in listed.stdout.split(b"\0") if raw}
    paths.update({
        config_path, config.repository / ".codex/config.toml",
        config.repository / "AGENTS.md",
        config.repository / ".agents/skills/codebase-atlas/SKILL.md",
        config.data_dir / "index-state.json",
        config.data_dir / "lifecycle-state.json",
        config.data_dir / "python-registrations-v1.json",
        config.cache_dir / f"{config.project}.db",
        config.cache_dir / f"{config.project}.db-wal",
    })
    if len(paths) > MAX_FILES:
        raise RuntimeError("verification snapshot file budget exceeded")
    result = {}
    consumed = 0
    for path in sorted(paths):
        if time.monotonic() > deadline:
            raise RuntimeError("verification snapshot deadline exceeded")
        # A symlinked ancestor could redirect a nominal repository file outside.
        if path.is_relative_to(config.repository):
            parent = path.parent
            while parent != config.repository:
                if parent.is_symlink():
                    raise RuntimeError("verification snapshot has a symlinked ancestor")
                parent = parent.parent
        try:
            before = path.lstat()
        except FileNotFoundError:
            result[str(path)] = "missing"
            continue
        if stat.S_ISLNK(before.st_mode):
            result[str(path)] = "link:" + os.readlink(path)
            continue
        if stat.S_ISDIR(before.st_mode):
            # Gitlinks belong to a different repository identity.
            result[str(path)] = "directory"
            continue
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError("verification snapshot encountered a special file")
        if consumed + before.st_size > MAX_BYTES:
            raise RuntimeError("verification snapshot byte budget exceeded")
        digest = hashlib.sha256()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino, before.st_mode) != (
                opened.st_dev, opened.st_ino, opened.st_mode
            ):
                raise RuntimeError("file replaced while taking verification snapshot")
            while chunk := stream.read(1024 * 1024):
                consumed += len(chunk)
                if consumed > MAX_BYTES:
                    raise RuntimeError("verification snapshot byte budget exceeded")
                if time.monotonic() > deadline:
                    raise RuntimeError("verification snapshot deadline exceeded")
                digest.update(chunk)
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns
        ):
            raise RuntimeError("file changed while taking verification snapshot")
        result[str(path)] = f"{stat.S_IMODE(before.st_mode)}:{digest.hexdigest()}"
    return result
