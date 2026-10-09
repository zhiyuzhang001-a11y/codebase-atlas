"""Private, immutable scanner installation from an already trusted release asset.

Acquisition supplies the release checksum and commit. This module never fetches
an asset or runs a scanner/installer. Project configuration cannot supply them.
"""
from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile

from .provider_layout import atlas_data_root
from .release_installation import current_platform_target
from .rust_installation import _private_directory, _path_digest, _relative, release_lock
from .rust_runtime import RustRuntimeError, VerifiedRustTool

MAX_BUNDLE_BYTES = 128 * 1024 * 1024


def scanner_lock() -> dict:
    return json.loads(files("codebase_atlas").joinpath("rust_scanner_lock.json").read_text())


def scanner_store() -> Path:
    return atlas_data_root() / "_rust-scanners" / "v1"


def _binary_name(target: str) -> str:
    if target not in release_lock()["targets"]:
        raise RustRuntimeError("Unsupported scanner platform")
    return "atlas-rust-syntax.exe" if target.startswith("windows-") else "atlas-rust-syntax"


def _manifest(payloads: dict[str, bytes], target: str, commit: str) -> dict:
    try:
        return _validate_manifest(payloads, target, commit)
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise RustRuntimeError("Scanner manifest/source/license identity mismatch") from exc


def _validate_manifest(payloads: dict[str, bytes], target: str, commit: str) -> dict:
    lock = scanner_lock()
    binary = _binary_name(target)
    if set(payloads) != {binary, "manifest.json", "LICENSE", "THIRD_PARTY_NOTICES.md"}:
        raise RustRuntimeError("Scanner bundle inventory mismatch")
    value = json.loads(payloads["manifest.json"])
    source, build, artifact = value["source"], value["build"], value["artifact"]
    source_sha = hashlib.sha256(json.dumps(lock["files"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if (value["schema_version"] != 1 or source["repository"] != lock["repository"]
            or source["commit"] != commit or not re.fullmatch(r"[0-9a-f]{40}", commit)
            or source["files"] != lock["files"] or source["source_sha256"] != source_sha
            or source["license"] != lock["license"]
            or build["scanner_version"] != lock["version"] or build["platform_arch"] != target
            or build["independent_builds"] != 2 or build["reproducible"] is not True
            or artifact["file"] != binary or artifact["size"] != len(payloads[binary])
            or artifact["sha256"] != hashlib.sha256(payloads[binary]).hexdigest()
            or not artifact["version_output"].startswith("atlas-rust-syntax " + lock["version"] + " ")
            or hashlib.sha256(payloads["LICENSE"]).hexdigest() != lock["license_sha256"]
            or hashlib.sha256(payloads["THIRD_PARTY_NOTICES.md"]).hexdigest() != lock["files"]["THIRD_PARTY_NOTICES.md"]):
        raise RustRuntimeError("Scanner manifest/source/license identity mismatch")
    return value


def _archive_payloads(archive: Path, target: str) -> dict[str, bytes]:
    result = {}
    total = 0
    def admit(name, size):
        nonlocal total
        parts = _relative(name).parts
        if len(parts) != 2 or parts[0] != target or parts[1] in result:
            raise RustRuntimeError("Scanner archive path/inventory is unsafe")
        total += size
        if size < 0 or total > MAX_BUNDLE_BYTES:
            raise RustRuntimeError("Scanner archive content is oversized")
        return parts[1]
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            if len(bundle.infolist()) > 5:
                raise RustRuntimeError("Scanner archive member limit exceeded")
            for member in bundle.infolist():
                mode = member.external_attr >> 16
                if member.is_dir() or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    raise RustRuntimeError("Scanner ZIP member is not a regular file")
                name = admit(member.filename, member.file_size)
                result[name] = bundle.read(member)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            for index, member in enumerate(bundle):
                if index >= 5:
                    raise RustRuntimeError("Scanner archive member limit exceeded")
                if member.isdir() and _relative(member.name).parts == (target,):
                    continue
                if not member.isfile():
                    raise RustRuntimeError("Scanner tar member is not a regular file")
                name = admit(member.name, member.size)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise RustRuntimeError("Scanner archive member is unavailable")
                with stream:
                    result[name] = stream.read(MAX_BUNDLE_BYTES + 1)
    return result


def install_scanner_asset(archive: Path, *, sha256: str, commit: str, target: str) -> Path:
    """Only called by trusted acquisition; checksum alone is not a provenance oracle."""
    if (not re.fullmatch(r"[0-9a-f]{64}", sha256) or not stat.S_ISREG(os.lstat(archive).st_mode)
            or archive.stat().st_size > MAX_BUNDLE_BYTES or _path_digest(archive) != sha256):
        raise RustRuntimeError("Scanner archive checksum mismatch")
    payloads = _archive_payloads(archive, target)
    _manifest(payloads, target, commit)
    store = scanner_store()
    version = scanner_lock()["version"]
    parent = store / version
    for directory in (store, parent):
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        _private_directory(directory)
    destination = parent / target
    if destination.exists():
        receipt = _load_scanner(destination, target)
        if receipt["archive_sha256"] != sha256 or receipt["commit"] != commit:
            raise RustRuntimeError("Scanner installation conflicts with existing receipt")
        return destination / _binary_name(target)
    staging = Path(tempfile.mkdtemp(prefix=".scanner-", dir=parent))
    try:
        for name, payload in payloads.items():
            path = staging / name
            path.write_bytes(payload)
            path.chmod(0o755 if name == _binary_name(target) else 0o600)
        receipt = {"schema_version": 1, "target": target, "version": version,
                   "archive_sha256": sha256, "commit": commit,
                   "files": {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()}}
        (staging / "receipt.json").write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
        (staging / "receipt.json").chmod(0o600)
        # Reserve the canonical directory without replacing even an empty
        # foreign directory. Publish completed files via no-replace hard links;
        # receipt comes last and readers fail closed during publication.
        from .installation_publication import publish_installation
        publish_installation(staging, destination, receipt="receipt.json",
                             validate=lambda: _load_scanner(destination, target))
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination / _binary_name(target)


def _load_scanner(directory: Path, target: str) -> dict:
    try:
        return _read_scanner(directory, target)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RustRuntimeError("Scanner installation receipt is invalid") from exc


def _read_scanner(directory: Path, target: str) -> dict:
    for parent in (scanner_store(), directory.parent, directory):
        _private_directory(parent)
    payloads = {}
    expected = {_binary_name(target), "manifest.json", "LICENSE", "THIRD_PARTY_NOTICES.md", "receipt.json"}
    if {path.name for path in directory.iterdir()} != expected:
        raise RustRuntimeError("Scanner installation inventory mismatch")
    for name in expected:
        path = directory / name
        if not stat.S_ISREG(os.lstat(path).st_mode) or path.stat().st_size > MAX_BUNDLE_BYTES:
            raise RustRuntimeError("Scanner installed file is unsafe")
        if os.name == "nt":
            from .windows_private_store import verify_windows_private_path
            verify_windows_private_path(path)
        payloads[name] = path.read_bytes()
    receipt = json.loads(payloads.pop("receipt.json"))
    if (receipt["schema_version"] != 1 or receipt["target"] != target
            or receipt["version"] != scanner_lock()["version"]
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["archive_sha256"])
            or receipt["files"] != {name: hashlib.sha256(value).hexdigest() for name, value in payloads.items()}):
        raise RustRuntimeError("Scanner installation receipt mismatch")
    _manifest(payloads, target, receipt["commit"])
    return receipt


def verified_scanner(repository: Path) -> VerifiedRustTool:
    target = current_platform_target()
    directory = scanner_store() / scanner_lock()["version"] / target
    if directory.is_relative_to(repository.resolve()):
        raise RustRuntimeError("Scanner installation cannot be project-local")
    try:
        receipt = _load_scanner(directory, target)
        name = _binary_name(target)
        tool = VerifiedRustTool(directory / name, receipt["files"][name])
        tool.verify()
        return tool
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RustRuntimeError("Verified Rust scanner installation is unavailable") from exc
