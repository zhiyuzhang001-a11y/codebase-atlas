"""Contained filesystem and process environment for Go runtimes."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import uuid


TELEMETRY_MODE = b"off\n"


class GoEnvironmentError(RuntimeError):
    """Raised when an Atlas-owned Go environment path is unsafe."""


def go_environment_paths(root: Path) -> dict[str, Path]:
    """Return the single cross-platform path policy without creating it."""
    root = root.absolute()
    return {
        "HOME": root / "home",
        "TMPDIR": root / "tmp",
        "GOMODCACHE": root / "gomodcache",
        "GOCACHE": root / "gocache",
        "GOPATH": root / "gopath",
        "XDG_CONFIG_HOME": root / "config",
        "APPDATA": root / "appdata",
        "ATLAS_LOG_DIR": root / "logs",
    }


def telemetry_mode_paths(root: Path) -> tuple[Path, ...]:
    paths = go_environment_paths(root)
    return (
        paths["HOME"] / "Library/Application Support/go/telemetry/mode",
        paths["XDG_CONFIG_HOME"] / "go/telemetry/mode",
        paths["APPDATA"] / "go/telemetry/mode",
    )


def _require_directory(path: Path) -> None:
    try:
        value = os.lstat(path)
    except FileNotFoundError:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            value = os.lstat(path)
        else:
            value = os.lstat(path)
    if not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode):
        raise GoEnvironmentError(f"unsafe Go environment directory: {path}")


def _ensure_directory(root: Path, destination: Path) -> None:
    root = root.absolute()
    destination = destination.absolute()
    if destination != root and root not in destination.parents:
        raise GoEnvironmentError(f"Go environment path escaped data root: {destination}")
    if root.is_symlink():
        raise GoEnvironmentError(f"unsafe Go environment root: {root}")
    root.mkdir(parents=True, exist_ok=True)
    _require_directory(root)
    relative = destination.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        _require_directory(current)


def _publish_mode(root: Path, destination: Path) -> None:
    _ensure_directory(root, destination.parent)
    try:
        current = os.lstat(destination)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(current.st_mode) or stat.S_ISLNK(current.st_mode):
            raise GoEnvironmentError(f"unsafe Go telemetry mode path: {destination}")
        if destination.read_bytes() == TELEMETRY_MODE:
            return
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(TELEMETRY_MODE)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def contained_go_environment(root: Path, *, create: bool) -> dict[str, str]:
    """Return contained Go paths and optionally publish Atlas-owned state."""
    root = root.absolute()
    paths = go_environment_paths(root)
    if create:
        for path in paths.values():
            _ensure_directory(root, path)
        for path in telemetry_mode_paths(root):
            _publish_mode(root, path)
    elif root.is_symlink():
        raise GoEnvironmentError(f"unsafe Go environment root: {root}")
    return {
        name: str(path)
        for name, path in paths.items()
        if name != "ATLAS_LOG_DIR"
    }
