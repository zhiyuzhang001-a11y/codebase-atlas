"""Fail-closed, non-executing Rust runtime preflight for candidate routing.

Hash identities must come from a separately verified installation receipt.
Computing a local executable's hash is not by itself a trusted acquisition.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import tomllib
from typing import Mapping


PINNED_TOOLCHAIN = "1.98.0"
MAX_CONFIG_BYTES = 1024 * 1024


class RustRuntimeError(ValueError):
    """Runtime cannot safely execute even a version/metadata probe."""


@dataclass(frozen=True)
class VerifiedRustTool:
    path: Path
    sha256: str

    def verify(self) -> Path:
        path = self.path.absolute()
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise RustRuntimeError("Rust tool receipt checksum is invalid")
        try:
            # Receipt records the final regular executable, never a rustup shim.
            if not stat.S_ISREG(os.lstat(path).st_mode) or path.resolve() != path:
                raise RustRuntimeError("Rust tool path is not a canonical regular file")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise RustRuntimeError("Rust tool is unavailable") from exc
        if digest.hexdigest() != self.sha256:
            raise RustRuntimeError("Rust tool receipt checksum mismatch")
        return path


@dataclass(frozen=True)
class RustToolchainRuntime:
    cargo: VerifiedRustTool
    rustc: VerifiedRustTool
    analyzer: VerifiedRustTool
    cargo_home: Path
    rustup_home: Path

    def environment(self, repository: Path) -> dict[str, str]:
        return rust_runtime_environment(
            repository, cargo=self.cargo, rustc=self.rustc, analyzer=self.analyzer,
            cargo_home=self.cargo_home, rustup_home=self.rustup_home,
        )


def _read_config(path: Path) -> dict | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RustRuntimeError("Rust configuration is inaccessible") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_CONFIG_BYTES:
        raise RustRuntimeError("Rust configuration is unsafe or oversized")
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise RustRuntimeError("Rust configuration cannot be safely parsed") from exc


def rust_runtime_environment(
    repository: Path,
    *,
    cargo: VerifiedRustTool,
    rustc: VerifiedRustTool,
    analyzer: VerifiedRustTool,
    cargo_home: Path,
    rustup_home: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Validate all configuration before returning a child env; executes nothing.

This first policy is deliberately conservative: unknown Cargo configuration
requires a reviewed preparation plan, not silent removal of semantic options.
No files, toolchains or global environment variables are changed.
"""
    inherited = os.environ if environment is None else environment
    paths = [tool.verify() for tool in (cargo, rustc, analyzer)]
    repo = repository.resolve(strict=True)
    if not repo.is_dir():
        raise RustRuntimeError("Rust repository is unavailable")
    for name, value in inherited.items():
        if not value:
            continue
        if name in {"RUSTC", "CARGO"}:
            expected = paths[1] if name == "RUSTC" else paths[0]
            if value != str(expected):
                raise RustRuntimeError("Rust executable environment override requires review")
        elif name.startswith(("RUST", "CARGO_", "DYLD_", "LD_")):
            if name == "CARGO_NET_OFFLINE" and value.lower() == "true":
                continue
            if name == "CARGO_HOME" and Path(value).absolute() == cargo_home.absolute():
                continue
            if name == "RUSTUP_HOME" and Path(value).absolute() == rustup_home.absolute():
                continue
            raise RustRuntimeError("Rust execution environment override requires review")

    config_paths = [cargo_home / "config", cargo_home / "config.toml"]
    for directory in (repo, *repo.parents):
        config_paths.extend((directory / ".cargo/config", directory / ".cargo/config.toml"))
        for name in ("rust-toolchain", "rust-toolchain.toml"):
            toolchain_path = directory / name
            if toolchain_path.exists() or toolchain_path.is_symlink():
                if name == "rust-toolchain":
                    try:
                        if not stat.S_ISREG(os.lstat(toolchain_path).st_mode) or toolchain_path.stat().st_size > MAX_CONFIG_BYTES:
                            raise RustRuntimeError("Rust toolchain configuration is unsafe")
                        channel = toolchain_path.read_text(encoding="utf-8").strip()
                    except (OSError, UnicodeError) as exc:
                        raise RustRuntimeError("Rust toolchain configuration is invalid") from exc
                else:
                    document = _read_config(toolchain_path)
                    table = document.get("toolchain", {}) if document is not None else {}
                    if not isinstance(table, dict) or set(table) != {"channel"}:
                        raise RustRuntimeError("Rust toolchain options require separate preparation")
                    channel = table.get("channel")
                if channel != PINNED_TOOLCHAIN:
                    raise RustRuntimeError("Rust toolchain selection differs from verified toolchain")
    for path in dict.fromkeys(config_paths):
        document = _read_config(path)
        if document is not None and document not in ({}, {"net": {"offline": True}}):
            raise RustRuntimeError("Cargo configuration requires reviewed safe preparation")

    settings = _read_config(rustup_home / "settings.toml")
    if settings is not None:
        overrides = settings.get("overrides", {})
        if not isinstance(overrides, dict):
            raise RustRuntimeError("Rustup override configuration is invalid")
        for directory in overrides:
            if not isinstance(directory, str):
                raise RustRuntimeError("Rustup override path is invalid")
            target = Path(directory).resolve()
            if target == repo or target in repo.parents:
                raise RustRuntimeError("Rustup repository override requires reviewed preparation")

    # No arbitrary inherited PATH, wrapper, preload or registry variables.
    result = {name: inherited[name] for name in (
        "HOME", "USERPROFILE", "SystemRoot", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"
    ) if inherited.get(name)}
    result.update({
        "PATH": os.pathsep.join(dict.fromkeys(str(path.parent) for path in paths)),
        "CARGO": str(paths[0]),
        "RUSTC": str(paths[1]),
        "CARGO_HOME": str(cargo_home.absolute()),
        "CARGO_NET_OFFLINE": "true",
        "RUST_BACKTRACE": "0",
    })
    return result
