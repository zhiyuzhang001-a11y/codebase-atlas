"""Install pinned official component bytes privately, without an installer.

No rustup, shell, project code, global toolchain registration or PATH mutation.
This operation is separate from queries and must be explicitly requested.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile

from .installation_publication import publish_installation
from .release_installation import current_platform_target
from .rust_acquisition import acquire_components, component_acquisition_plan
from .rust_installation import (
    MAX_ARCHIVE_MEMBERS, MAX_MEMBER_BYTES, MAX_TOTAL_BYTES, _path_digest,
    _private_directory, _relative, _runtime_from_document, load_toolchain_receipt,
    release_lock, toolchain_store, verify_existing_toolchain,
)
from .rust_runtime import PINNED_TOOLCHAIN, RustRuntimeError


def _location(repository: Path) -> tuple[Path, Path, str, Path]:
    repo = repository.resolve(strict=True)
    store = toolchain_store().absolute()
    if (not repo.is_dir() or store.resolve() != store
            or any(path.is_symlink() for path in (store, *store.parents))
            or store.is_relative_to(repo)):
        raise RustRuntimeError("Rust installation requires a canonical store outside the project")
    if store.exists():
        _private_directory(store)
    target = current_platform_target()
    if target not in release_lock()["targets"]:
        raise RustRuntimeError("Unsupported Rust installation target")
    return repo, store, target, store / PINNED_TOOLCHAIN / target


def _reuse(repo: Path, store: Path, destination: Path) -> dict | None:
    if not (destination.exists() or destination.is_symlink()):
        return None
    document = load_toolchain_receipt(destination / "receipt.json", store=store)
    if Path(document["root"]).is_relative_to(repo):
        raise RustRuntimeError("Rust installed toolchain cannot be project-local")
    _runtime_from_document(document).environment(repo)
    return document


def plan_toolchain_installation(repository: Path) -> dict:
    repo, store, target, destination = _location(repository)
    document = _reuse(repo, store, destination)
    result = {"schema_version": 1, "mode": "plan", "repository": str(repo),
              "target": target, "toolchain": PINNED_TOOLCHAIN,
              "receipt": str(destination / "receipt.json"), "executes_tools": False,
              "project_writes": [], "reuse_receipt": document is not None}
    if document is not None:
        return {**result, "status": "planned", "root": document["root"]}
    acquisition = component_acquisition_plan(repo, target)
    missing = sorted(name for name, identity in acquisition.items() if not identity["cached"])
    return {**result, "status": "planned", "root": str(destination / "toolchain"),
            "missing_components": missing, "network_authorization_required": bool(missing)}


def _extract_components(root: Path, archives: dict[str, Path], target: str) -> dict[str, str]:
    lock = release_lock()
    expected = dict(lock["targets"][target]["components"])
    expected["rust-src"] = lock["rust_src"]
    if set(archives) != set(expected):
        raise RustRuntimeError("Rust installation component set is incomplete")
    # Check the ENTIRE input set before extracting any component.
    for name, archive in archives.items():
        metadata = os.lstat(archive)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_MEMBER_BYTES
                or _path_digest(archive) != expected[name]["sha256"]):
            raise RustRuntimeError("Rust installation archive checksum or identity mismatch")
    total = 0
    licenses = {}
    for component, archive in archives.items():
        with tarfile.open(archive, "r:xz") as bundle:
            for count, member in enumerate(bundle, 1):
                if count > MAX_ARCHIVE_MEMBERS:
                    raise RustRuntimeError("Rust installation member limit exceeded")
                parts = _relative(member.name).parts
                if member.isdir():
                    continue
                if not (member.isfile() or member.issym() or member.islnk()):
                    raise RustRuntimeError("Rust installation contains a special file")
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise RustRuntimeError("Rust installation member is oversized")
                total += member.size
                if total > MAX_TOTAL_BYTES:
                    raise RustRuntimeError("Rust installation content limit exceeded")
                license_key = None
                if len(parts) == 2 and parts[-1] in {"LICENSE-MIT", "LICENSE-APACHE"}:
                    license_key = component + "/" + parts[-1]
                    relative = "share/codebase-atlas/licenses/" + license_key
                elif len(parts) >= 3 and parts[-1] != "manifest.in":
                    relative = "/".join(parts[2:])
                else:
                    # Archive-root installer scripts/metadata are NEVER installed
                    # or invoked. Component resources may legitimately be scripts.
                    continue
                _relative(relative)
                if member.issym() or member.islnk():
                    _relative(member.linkname)
                path = root / relative
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise RustRuntimeError("Rust installation content is unavailable")
                # Materialize link content as regular bytes; do not create tar
                # links that could escape the private staging root.
                fd, temporary_name = tempfile.mkstemp(prefix=".file-", dir=path.parent)
                temporary = Path(temporary_name)
                try:
                    written = 0
                    with stream, os.fdopen(fd, "wb") as output:
                        while block := stream.read(1024 * 1024):
                            written += len(block)
                            if written > MAX_MEMBER_BYTES:
                                raise RustRuntimeError("Rust installation linked content is oversized")
                            output.write(block)
                        output.flush()
                        os.fsync(output.fileno())
                    # Link expansion must count toward the same total-byte gate.
                    total += max(0, written - member.size)
                    if total > MAX_TOTAL_BYTES:
                        raise RustRuntimeError("Rust installation content limit exceeded")
                    temporary.chmod(0o700 if member.mode & 0o111 else 0o600)
                    try:
                        os.link(temporary, path)
                    except FileExistsError:
                        if _path_digest(path) != _path_digest(temporary):
                            raise RustRuntimeError("Rust installation components disagree about a file")
                    if license_key:
                        licenses[relative] = _path_digest(path)
                finally:
                    temporary.unlink(missing_ok=True)
    if len(licenses) != len(expected) * 2:
        raise RustRuntimeError("Rust installation license set is incomplete")
    return licenses


def install_toolchain(repository: Path, *, archives: dict[str, Path] | None = None,
                      network_authorized: bool = False) -> dict:
    repo, store, target, destination = _location(repository)
    existing = _reuse(repo, store, destination)
    if existing is not None:
        return {"schema_version": 1, "status": "prepared", "receipt": str(destination / "receipt.json"),
                "root": existing["root"], "target": target, "reuse_receipt": True,
                "verified_files": len(existing["files"]), "verified_licenses": len(existing["licenses"]),
                "executes_tools": False, "project_writes": [], "project_enabled": False}
    if archives is None:
        archives = acquire_components(repo, target, network_authorized=network_authorized)
    parent = destination.parent
    for directory in (store, parent):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private_directory(directory)
    staging = Path(tempfile.mkdtemp(prefix=".toolchain-", dir=parent))
    reused = False
    try:
        root = staging / "toolchain"
        root.mkdir(mode=0o700)
        licenses = _extract_components(root, archives, target)
        document = verify_existing_toolchain(root, archives, target)
        _runtime_from_document(document).environment(repo)
        document["root"] = str(destination / "toolchain")
        document["managed_licenses"] = licenses
        receipt = staging / "receipt.json"
        receipt.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        receipt.chmod(0o600)
        def validate():
            result = load_toolchain_receipt(destination / "receipt.json", store=store)
            _runtime_from_document(result).environment(repo)
            return result
        try:
            document = publish_installation(staging, destination, receipt="receipt.json", validate=validate)
        except FileExistsError:
            # Another completed identical operation may win the reservation. A
            # pending/foreign/damaged directory fails closed; it is not repaired.
            document = _reuse(repo, store, destination)
            if document is None:
                raise RustRuntimeError("Rust installation publication lost its candidate")
            reused = True
    finally:
        shutil.rmtree(staging)
    return {"schema_version": 1, "status": "prepared", "receipt": str(destination / "receipt.json"),
            "root": document["root"], "target": target, "reuse_receipt": reused,
            "verified_files": len(document["files"]), "verified_licenses": len(document["licenses"]),
            "executes_tools": False, "project_writes": [], "project_enabled": False}
