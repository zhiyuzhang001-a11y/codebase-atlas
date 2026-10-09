"""Explicit, pinned Rust component acquisition; never installs or executes tools.

The packaged release lock is the only source of URLs and digests. Cached archives
are reusable across projects, but are not themselves an installed-tool receipt.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler

from .provider_layout import atlas_data_root
from .rust_installation import _private_directory, release_lock
from .rust_runtime import RustRuntimeError

MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
DOWNLOAD_SECONDS = 180.0
COMPONENTS = {"cargo", "rustc", "rust-std", "rust-analyzer-preview", "rust-src"}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RustRuntimeError("Rust component redirects are forbidden")


def _identities(target: str) -> dict:
    lock = release_lock()
    if target not in lock["targets"]:
        raise RustRuntimeError("Unsupported Rust acquisition target")
    identities = dict(lock["targets"][target]["components"])
    identities["rust-src"] = lock["rust_src"]
    if set(identities) != COMPONENTS:
        raise RustRuntimeError("Rust acquisition component set is incomplete")
    for identity in identities.values():
        url = urlsplit(identity["url"])
        if (identity.get("available") is not True
                or url.scheme != "https" or url.netloc != "static.rust-lang.org"
                or not url.path.startswith("/dist/") or not url.path.endswith(".tar.xz")
                or url.query or url.fragment or ".." in url.path.split("/")
                or not re.fullmatch(r"[a-f0-9]{64}", identity["sha256"])):
            raise RustRuntimeError("Rust acquisition source lock is invalid")
    return identities


def _store(repository: Path, store: Path | None) -> Path:
    repository = repository.resolve(strict=True)
    result = (store if store is not None else atlas_data_root() / "_rust-downloads" / "v1").absolute()
    # Check lexical ancestors as well as resolved identity; never repair a foreign
    # cache or follow a link supplied by project configuration.
    for path in (result, *result.parents):
        if path.is_symlink():
            raise RustRuntimeError("Rust download store contains a symlink")
    if result.resolve() != result or result.is_relative_to(repository):
        raise RustRuntimeError("Rust download store must be canonical and outside the project")
    if result.exists():
        _private_directory(result)
    return result


def _cached(path: Path, digest: str) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_DOWNLOAD_BYTES:
        raise RustRuntimeError("Rust cached archive is unsafe or oversized")
    if os.name != "nt":
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise RustRuntimeError("Rust cached archive is not private and owned")
    else:
        from .windows_private_store import verify_windows_private_path
        try:
            verify_windows_private_path(path)
        except (OSError, ValueError) as exc:
            raise RustRuntimeError("Rust cached archive ACL is unsafe") from exc
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(metadata, opened):
            raise RustRuntimeError("Rust cached archive changed during verification")
        actual = hashlib.sha256()
        count = 0
        while block := stream.read(1024 * 1024):
            count += len(block)
            if count > MAX_DOWNLOAD_BYTES:
                raise RustRuntimeError("Rust cached archive is oversized")
            actual.update(block)
        final = os.fstat(stream.fileno())
    after = os.lstat(path)
    if (not os.path.samestat(opened, after) or opened.st_size != final.st_size
            or opened.st_mtime_ns != final.st_mtime_ns or actual.hexdigest() != digest):
        raise RustRuntimeError("Rust cached archive checksum or identity mismatch")
    return True


def component_acquisition_plan(repository: Path, target: str, *, store: Path | None = None) -> dict:
    """Read-only plan. No mkdir, transport, tool execution, or project writes."""
    identities = _identities(target)
    cache = _store(repository, store)
    return {name: {**identity, "path": cache / (identity["sha256"] + ".tar.xz"),
                   "cached": _cached(cache / (identity["sha256"] + ".tar.xz"), identity["sha256"])}
            for name, identity in identities.items()}


def _download(url: str, stream) -> str:
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    actual = hashlib.sha256()
    count = 0
    # Each blocking transport operation is also bounded. No rustup, installer,
    # shell, project code, or redirect destination is ever invoked here.
    with build_opener(_NoRedirect()).open(url, timeout=15) as response:
        if response.geturl() != url or response.status != 200:
            raise RustRuntimeError("Rust component response identity mismatch")
        while True:
            if time.monotonic() >= deadline:
                raise RustRuntimeError("Rust component download deadline exceeded")
            block = response.read(1024 * 1024)
            if time.monotonic() >= deadline:
                raise RustRuntimeError("Rust component download deadline exceeded")
            if not block:
                break
            count += len(block)
            if count > MAX_DOWNLOAD_BYTES:
                raise RustRuntimeError("Rust component download is oversized")
            actual.update(block)
            stream.write(block)
    return actual.hexdigest()


def acquire_components(repository: Path, target: str, *, network_authorized: bool = False,
                       store: Path | None = None) -> dict[str, Path]:
    """Reuse verified archives; fetch missing ones only with explicit authority.

    Publication is no-replace. Successful components survive a later failure for
    reuse; partial temporary bytes are removed, never published as valid archives.
    """
    plan = component_acquisition_plan(repository, target, store=store)
    missing = [identity for identity in plan.values() if not identity["cached"]]
    if missing and network_authorized is not True:
        raise RustRuntimeError("Rust component acquisition requires explicit network authorization")
    if missing:
        cache = next(iter(plan.values()))["path"].parent
        absent = []
        ancestor = cache
        while not ancestor.exists():
            absent.append(ancestor)
            ancestor = ancestor.parent
        for directory in reversed(absent):
            directory.mkdir(mode=0o700, exist_ok=True)
            _private_directory(directory)
        _private_directory(cache)
        for identity in missing:
            destination = identity["path"]
            if _cached(destination, identity["sha256"]):
                continue
            fd, name = tempfile.mkstemp(prefix=".component-", dir=cache)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    actual = _download(identity["url"], stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                if actual != identity["sha256"] or not _cached(temporary, identity["sha256"]):
                    raise RustRuntimeError("Rust component download checksum mismatch")
                _private_directory(cache)
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    pass
                _cached(destination, identity["sha256"])
            finally:
                temporary.unlink(missing_ok=True)
    # Revalidate cached components too, including ones published by another process.
    for identity in plan.values():
        if not _cached(identity["path"], identity["sha256"]):
            raise RustRuntimeError("Rust component disappeared before acquisition completed")
    return {name: identity["path"] for name, identity in plan.items()}
