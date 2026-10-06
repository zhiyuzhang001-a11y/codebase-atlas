"""Verify and reuse an existing Rust toolchain without executing installers.

Acquisition is a separate, explicitly requested operation. These functions
never download, run rustup, execute a tool, or modify an installed toolchain.
"""
from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
import tempfile
from typing import BinaryIO

from .rust_runtime import PINNED_TOOLCHAIN, RustRuntimeError, RustToolchainRuntime, VerifiedRustTool


MAX_ARCHIVE_MEMBERS = 100000
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


def release_lock() -> dict:
    return json.loads(files("codebase_atlas").joinpath("rust_release_lock.json").read_text())


def toolchain_store() -> Path:
    from .provider_layout import atlas_data_root
    return atlas_data_root() / "_rust-toolchains" / "v1"


def runtime_from_receipt(path: Path, *, repository: Path) -> RustToolchainRuntime:
    """Resolve the account-owned installation, never a project-selected store."""
    from .release_installation import current_platform_target
    store = toolchain_store()
    repo = repository.resolve(strict=True)
    if store.is_relative_to(repo):
        raise RustRuntimeError("Rust installation store cannot be project-local")
    document = load_toolchain_receipt(path, store=store)
    if document["target"] != current_platform_target():
        raise RustRuntimeError("Rust receipt platform does not match this machine")
    root = Path(document["root"])
    if root.is_relative_to(repo):
        raise RustRuntimeError("Rust toolchain cannot be project-local")
    return _runtime_from_document(document)


def _runtime_from_document(document: dict) -> RustToolchainRuntime:
    """Construct only from a verified document; also used before publication."""
    root = Path(document["root"])
    tools = {name: VerifiedRustTool(root / value["path"], value["sha256"])
             for name, value in document["tools"].items()}
    return RustToolchainRuntime(
        cargo=tools["cargo"], rustc=tools["rustc"], analyzer=tools["rust-analyzer"],
        cargo_home=Path(os.environ.get("CARGO_HOME", str(Path.home() / ".cargo"))),
        rustup_home=Path(os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup"))),
        toolchain_root=root,
    )


def _digest(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def _path_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return _digest(stream)


def _relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or "\\" in name or path.is_absolute() or ".." in path.parts or ":" in name:
        raise RustRuntimeError("Rust archive path is unsafe")
    return path


def verify_existing_toolchain(root: Path, archives: dict[str, Path], target: str) -> dict:
    """Compare installed files to checksum-pinned official component bytes.

    The receipt records all component files, not just cargo/rustc/analyzer:
    dynamic libraries, sysroot and rust-src are part of the executable trust.
    """
    lock = release_lock()
    if target not in lock["targets"]:
        raise RustRuntimeError("Rust target is not supported")
    expected = dict(lock["targets"][target]["components"])
    expected["rust-src"] = lock["rust_src"]
    if set(archives) != set(expected):
        raise RustRuntimeError("Rust component set is incomplete")
    root = root.absolute()
    if root.resolve(strict=True) != root or not root.is_dir():
        raise RustRuntimeError("Rust toolchain root is not canonical")
    identities: dict[str, dict] = {}
    component_proof: dict[str, dict] = {}
    licenses: dict[str, str] = {}
    total = 0
    for component, archive in archives.items():
        metadata = os.lstat(archive)
        if not stat.S_ISREG(metadata.st_mode) or _path_digest(archive) != expected[component]["sha256"]:
            raise RustRuntimeError("Rust component archive checksum mismatch")
        count = 0
        component_files = 0
        with tarfile.open(archive, "r:xz") as bundle:
            for member in bundle:
                count += 1
                if count > MAX_ARCHIVE_MEMBERS:
                    raise RustRuntimeError("Rust component member limit exceeded")
                parts = _relative(member.name).parts
                if member.isdir():
                    continue
                if not (member.isfile() or member.issym() or member.islnk()):
                    raise RustRuntimeError("Rust component contains a special file")
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise RustRuntimeError("Rust component member is oversized")
                total += member.size
                if total > MAX_TOTAL_BYTES:
                    raise RustRuntimeError("Rust toolchain content limit exceeded")
                if len(parts) == 2 and parts[-1] in {"LICENSE-APACHE", "LICENSE-MIT"}:
                    stream = bundle.extractfile(member)
                    if stream is None:
                        raise RustRuntimeError("Rust license is unavailable")
                    with stream:
                        licenses[component + "/" + parts[-1]] = _digest(stream)
                    continue
                # Installer metadata lives at archive root. The second path
                # component is the installation component, not part of prefix.
                if len(parts) < 3 or parts[-1] == "manifest.in":
                    continue
                relative = PurePosixPath(*parts[2:]).as_posix()
                installed = root / relative
                resolved = installed.resolve(strict=True)
                if not resolved.is_relative_to(root) or not resolved.is_file():
                    raise RustRuntimeError("Rust installed component escapes its root")
                if member.issym() or member.islnk():
                    _relative(member.linkname)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise RustRuntimeError("Rust component content is unavailable")
                with stream:
                    digest = _digest(stream)
                if _path_digest(resolved) != digest:
                    raise RustRuntimeError("Rust installed component differs from official archive")
                prior = identities.get(relative)
                identity = {"sha256": digest, "resolved": resolved.relative_to(root).as_posix()}
                if prior is not None and prior != identity:
                    raise RustRuntimeError("Rust components disagree about a shared file")
                identities[relative] = identity
                component_files += 1
        if not component_files or not all(component + "/" + name in licenses for name in ("LICENSE-APACHE", "LICENSE-MIT")):
            raise RustRuntimeError("Rust component files or licenses are incomplete")
        component_proof[component] = {"url": expected[component]["url"], "sha256": expected[component]["sha256"]}
    suffix = ".exe" if target.startswith("windows-") else ""
    tools = {}
    for name in ("cargo", "rustc", "rust-analyzer"):
        relative = "bin/" + name + suffix
        if relative not in identities:
            raise RustRuntimeError("Rust executable is missing from verified components")
        tool = VerifiedRustTool(root / relative, identities[relative]["sha256"])
        tool.verify()
        tools[name] = {"path": relative, "sha256": tool.sha256}
    return {"schema_version": 1, "kind": "verified-existing-rust-toolchain", "toolchain": PINNED_TOOLCHAIN,
            "target": target, "root": str(root), "manifest_sha256": lock["manifest_sha256"],
            "components": component_proof, "files": identities, "tools": tools, "licenses": licenses}


def _private_directory(path: Path) -> None:
    metadata = os.lstat(path)
    if not stat.S_ISDIR(metadata.st_mode) or path.resolve() != path:
        raise RustRuntimeError("Rust receipt store is unsafe")
    if os.name != "nt" and (metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077):
        raise RustRuntimeError("Rust receipt store must be private and owned")
    if os.name == "nt":
        from .windows_private_store import verify_windows_private_path
        try:
            verify_windows_private_path(path)
        except (OSError, ValueError) as exc:
            raise RustRuntimeError("Rust store Windows ownership/ACL is unsafe") from exc


def save_toolchain_receipt(document: dict, store: Path) -> Path:
    """Publish a preparation result to an owned store without replacement."""
    target = document["target"]
    if target not in release_lock()["targets"]:
        raise RustRuntimeError("Rust target is not supported")
    store = store.absolute()
    for directory in (store, store / PINNED_TOOLCHAIN, store / PINNED_TOOLCHAIN / target):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private_directory(directory)
    receipt = store / PINNED_TOOLCHAIN / target / "receipt.json"
    payload = json.dumps(document, sort_keys=True, indent=2) + "\n"
    if receipt.exists():
        load_toolchain_receipt(receipt, store=store)
        if receipt.read_text() != payload:
            raise RustRuntimeError("Rust receipt conflicts with an existing installation")
        return receipt
    descriptor, temporary_name = tempfile.mkstemp(prefix=".receipt-", dir=receipt.parent)
    temporary = Path(temporary_name)
    published = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link publication is atomic and does not replace another writer's
        # receipt. Readers never observe the temporary file while it is written.
        try:
            os.link(temporary, receipt)
            published = True
        except FileExistsError:
            load_toolchain_receipt(receipt, store=store)
            if receipt.read_text() != payload:
                raise RustRuntimeError("Rust receipt conflicts with an existing installation")
        load_toolchain_receipt(receipt, store=store)
    except BaseException:
        if published and receipt.exists() and os.path.samestat(os.lstat(receipt), os.lstat(temporary)):
            receipt.unlink()
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return receipt


def load_toolchain_receipt(path: Path, *, store: Path) -> dict:
    """Trust only an owned canonical receipt, then revalidate installed bytes."""
    store = store.absolute()
    _private_directory(store)
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 32 * 1024 * 1024:
        raise RustRuntimeError("Rust receipt is unsafe or oversized")
    if os.name != "nt" and (metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077):
        raise RustRuntimeError("Rust receipt is not private and owned")
    if os.name == "nt":
        from .windows_private_store import verify_windows_private_path
        try:
            verify_windows_private_path(path)
        except (OSError, ValueError) as exc:
            raise RustRuntimeError("Rust receipt Windows ownership/ACL is unsafe") from exc
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        lock = release_lock()
        target = document["target"]
        if target not in lock["targets"] or path.absolute() != store / PINNED_TOOLCHAIN / target / "receipt.json":
            raise RustRuntimeError("Rust receipt is outside the canonical store")
        for directory in (path.parent, path.parent.parent):
            _private_directory(directory)
        if document["schema_version"] != 1 or document["kind"] != "verified-existing-rust-toolchain" or document["toolchain"] != PINNED_TOOLCHAIN or document["manifest_sha256"] != lock["manifest_sha256"]:
            raise RustRuntimeError("Rust receipt source identity mismatch")
        expected = dict(lock["targets"][target]["components"])
        expected["rust-src"] = lock["rust_src"]
        if document["components"] != {key: {"url": value["url"], "sha256": value["sha256"]} for key, value in expected.items()}:
            raise RustRuntimeError("Rust receipt component source mismatch")
        if set(document["tools"]) != {"cargo", "rustc", "rust-analyzer"}:
            raise RustRuntimeError("Rust receipt tool set mismatch")
        root = Path(document["root"])
        if root.resolve(strict=True) != root or not root.is_dir():
            raise RustRuntimeError("Rust receipt root is unsafe")
        if not document["files"] or not all(key + "/" + name in document["licenses"] for key in expected for name in ("LICENSE-APACHE", "LICENSE-MIT")):
            raise RustRuntimeError("Rust receipt file/license proof is incomplete")
        if "managed_licenses" in document:
            prefix = "share/codebase-atlas/licenses/"
            licenses = document["managed_licenses"]
            if licenses != {prefix + key: value for key, value in document["licenses"].items()}:
                raise RustRuntimeError("Rust managed license identity is incomplete")
            for relative, digest in licenses.items():
                _relative(relative)
                path = root / relative
                if path.resolve(strict=True) != path or not stat.S_ISREG(os.lstat(path).st_mode) or _path_digest(path) != digest:
                    raise RustRuntimeError("Rust managed license content mismatch")
        for relative, identity in document["files"].items():
            _relative(relative)
            _relative(identity["resolved"])
            installed = root / relative
            resolved = installed.resolve(strict=True)
            if resolved != root / identity["resolved"] or not resolved.is_relative_to(root) or _path_digest(resolved) != identity["sha256"]:
                raise RustRuntimeError("Rust receipt installed content mismatch")
        suffix = ".exe" if target.startswith("windows-") else ""
        for name, tool in document["tools"].items():
            if tool["path"] != "bin/" + name + suffix or tool["sha256"] != document["files"][tool["path"]]["sha256"]:
                raise RustRuntimeError("Rust receipt executable identity mismatch")
            VerifiedRustTool(root / tool["path"], tool["sha256"]).verify()
        return document
    except (KeyError, TypeError, OSError, ValueError) as exc:
        if isinstance(exc, RustRuntimeError):
            raise
        raise RustRuntimeError("Rust receipt is invalid") from exc
