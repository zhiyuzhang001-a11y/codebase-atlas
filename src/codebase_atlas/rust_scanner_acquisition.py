"""Acquire a scanner only from this repository's exact published stable tag.

Candidate tests patch transport, not production trust policy. No arbitrary URL,
draft, Actions-artifact or caller-supplied commit/checksum route is exposed here.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .release_installation import (
    MAX_MANIFEST_BYTES, REPOSITORY_RELEASE_PREFIX, ReleaseAsset, _asset,
    current_platform_target, download_asset, parse_checksum_manifest,
)
from .rust_acquisition import _NoRedirect
from .rust_installation import _private_directory, release_lock
from .rust_runtime import RustRuntimeError
from .rust_scanner_installation import (
    MAX_BUNDLE_BYTES, _load_scanner, install_scanner_asset,
    scanner_lock, scanner_store, verified_scanner,
)

API = "https://api.github.com/repos/zhiyuzhang001-a11y/codebase-atlas/"
PAGE = "https://github.com/zhiyuzhang001-a11y/codebase-atlas/releases/tag/"


@dataclass(frozen=True)
class ScannerRelease:
    tag: str
    commit: str
    target: str
    archive: ReleaseAsset
    adjacent: ReleaseAsset
    aggregate: ReleaseAsset
    archives: tuple[ReleaseAsset, ...]


def _json(path: str) -> dict:
    request = Request(API + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "codebase-atlas-rust-installer"})
    with build_opener(_NoRedirect()).open(request, timeout=10) as response:
        if response.geturl() != API + path:
            raise RustRuntimeError("Scanner metadata response identity mismatch")
        payload = response.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise RustRuntimeError("Scanner metadata exceeds size limit")
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise RustRuntimeError("Scanner metadata must be an object")
    return document


def _release_assets(payload: dict, tag: str, target: str) -> tuple[ReleaseAsset, ReleaseAsset, ReleaseAsset, tuple[ReleaseAsset, ...]]:
    if (payload.get("draft") is not False or payload.get("prerelease") is not False
            or payload.get("tag_name") != tag or payload.get("html_url") != PAGE + tag):
        raise RustRuntimeError("Scanner requires an exact published stable Release")
    assets = {}
    if not isinstance(payload.get("assets"), list):
        raise RustRuntimeError("Scanner release assets are missing")
    for raw in payload["assets"]:
        asset = _asset(raw)
        if asset.name in assets:
            raise RustRuntimeError("Scanner release contains duplicate assets")
        assets[asset.name] = asset
    version = scanner_lock()["version"]
    names = []
    for platform in release_lock()["targets"]:
        suffix = ".zip" if platform.startswith("windows-") else ".tar.gz"
        name = f"codebase-atlas-rust-syntax-{version}-{platform}{suffix}"
        names.extend((name, name + ".sha256"))
    names.append("RUST_SYNTAX_SHA256SUMS.txt")
    for name in names:
        asset = assets.get(name)
        if asset is None:
            raise RustRuntimeError("Stable Release lacks the complete five-platform scanner assets")
        parsed = urlsplit(asset.url)
        if (parsed.netloc != "github.com" or parsed.query or parsed.fragment
                or asset.url != "https://github.com" + REPOSITORY_RELEASE_PREFIX + tag + "/" + name):
            raise RustRuntimeError("Scanner asset does not belong to the exact Release tag")
        limit = MAX_BUNDLE_BYTES if name.endswith((".zip", ".tar.gz")) else MAX_MANIFEST_BYTES
        if asset.size <= 0 or asset.size > limit or not asset.digest:
            raise RustRuntimeError("Scanner asset size or API SHA256 identity is missing")
    suffix = ".zip" if target.startswith("windows-") else ".tar.gz"
    name = f"codebase-atlas-rust-syntax-{version}-{target}{suffix}"
    archives = tuple(assets[name] for name in names if name.endswith((".zip", ".tar.gz")))
    return assets[name], assets[name + ".sha256"], assets["RUST_SYNTAX_SHA256SUMS.txt"], archives


def fetch_scanner_release(tag: str, *, network_authorized: bool = False) -> ScannerRelease:
    if network_authorized is not True:
        raise RustRuntimeError("Scanner discovery requires explicit network authorization")
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise RustRuntimeError("Scanner acquisition requires an exact stable version tag")
    target = current_platform_target()
    if target not in release_lock()["targets"]:
        raise RustRuntimeError("Unsupported scanner acquisition target")
    archive, adjacent, aggregate, archives = _release_assets(_json("releases/tags/" + tag), tag, target)
    reference = _json("git/ref/tags/" + tag)
    if reference.get("ref") != "refs/tags/" + tag:
        raise RustRuntimeError("Scanner source tag identity mismatch")
    object_value = reference.get("object", {})
    seen = set()
    for _ in range(5):
        if not isinstance(object_value, dict):
            raise RustRuntimeError("Scanner source object identity mismatch")
        sha = object_value.get("sha")
        kind = object_value.get("type")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha) or sha in seen:
            raise RustRuntimeError("Scanner source object identity mismatch")
        seen.add(sha)
        if kind == "commit":
            return ScannerRelease(tag, sha, target, archive, adjacent, aggregate, archives)
        if kind != "tag":
            break
        document = _json("git/tags/" + sha)
        if document.get("sha") != sha:
            raise RustRuntimeError("Scanner annotated tag identity mismatch")
        object_value = document.get("object", {})
    raise RustRuntimeError("Scanner tag cannot be resolved to an exact commit")


class _ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if (parsed.scheme != "https" or parsed.hostname not in {
                "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise RustRuntimeError("Scanner release redirect leaves trusted HTTPS asset hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def acquire_scanner(repository: Path, tag: str, *, network_authorized: bool = False) -> Path:
    release = fetch_scanner_release(tag, network_authorized=network_authorized)
    repo = repository.resolve(strict=True)
    store = scanner_store().absolute()
    if (store.resolve() != store or store.is_relative_to(repo)
            or any(path.is_symlink() for path in (store, *store.parents))):
        raise RustRuntimeError("Scanner acquisition store must be canonical and outside the project")
    if store.exists():
        _private_directory(store)
    destination = store / scanner_lock()["version"] / release.target
    expected = release.archive.digest.removeprefix("sha256:")
    if destination.exists() or destination.is_symlink():
        receipt = _load_scanner(destination, release.target)
        if receipt["commit"] != release.commit or receipt["archive_sha256"] != expected:
            raise RustRuntimeError("Scanner stable Release conflicts with the existing installation")
        return verified_scanner(repo).verify()
    opener = build_opener(_ReleaseRedirects()).open
    # Temporary download workspace is owned and outside the project. Installation
    # does its own immutable private publication; nothing executes here.
    with tempfile.TemporaryDirectory(prefix="atlas-scanner-download-") as temporary:
        directory = Path(temporary)
        identities = {}
        for asset, limit in ((release.aggregate, MAX_MANIFEST_BYTES),
                             (release.adjacent, MAX_MANIFEST_BYTES),
                             (release.archive, MAX_BUNDLE_BYTES)):
            identities[asset.name] = download_asset(asset, directory / asset.name,
                                                   maximum_bytes=limit, opener=opener)
        aggregate = parse_checksum_manifest((directory / release.aggregate.name).read_bytes())
        adjacent = parse_checksum_manifest((directory / release.adjacent.name).read_bytes())
        actual = identities[release.archive.name]
        if (actual != expected or aggregate.get(release.archive.name) != actual
                or adjacent != {release.archive.name: actual}
                or any(aggregate.get(asset.name) != asset.digest.removeprefix("sha256:") for asset in release.archives)):
            raise RustRuntimeError("Scanner API/aggregate/adjacent checksums disagree")
        return install_scanner_asset(directory / release.archive.name, sha256=actual,
                                     commit=release.commit, target=release.target)
