#!/usr/bin/env python3
"""Verify a complete Codebase Atlas Rust syntax scanner release set."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

try:
    from build_rust_syntax_scanner import SCANNER, SOURCE_FILES, TARGETS, sha256
except ModuleNotFoundError:
    from scripts.build_rust_syntax_scanner import SCANNER, SOURCE_FILES, TARGETS, sha256


def safely_extract(archive: Path, destination: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as handle:
            names = handle.namelist()
            if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
                raise RuntimeError(f"unsafe ZIP member in {archive.name}")
            handle.extractall(destination)
    else:
        with tarfile.open(archive, "r:gz") as handle:
            members = handle.getmembers()
            if any(
                Path(member.name).is_absolute()
                or ".." in Path(member.name).parts
                or not (member.isfile() or member.isdir())
                for member in members
            ):
                raise RuntimeError(f"unsafe tar member in {archive.name}")
            handle.extractall(destination, filter="data")


def scanner_version() -> str:
    import tomllib

    return tomllib.loads((SCANNER / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"]


def expected_source_files() -> dict[str, str]:
    return {name: sha256(SCANNER / name) for name in SOURCE_FILES}


def expected_source_identity() -> tuple[dict[str, str], str, str]:
    files = expected_source_files()
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(payload).hexdigest()
    root = SCANNER.parents[1]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    return files, digest, commit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version", default=scanner_version())
    args = parser.parse_args()
    directory = args.directory.resolve()
    expected_files, expected_source_sha, expected_commit = expected_source_identity()
    results = []
    expected_archives = set()
    for target, (_system, _machines, binary_name, archive_kind) in TARGETS.items():
        suffix = ".zip" if archive_kind == "zip" else ".tar.gz"
        archive = directory / f"codebase-atlas-rust-syntax-{args.version}-{target}{suffix}"
        sidecar = archive.with_name(archive.name + ".sha256")
        expected_archives.add(archive.name)
        if not archive.is_file() or not sidecar.is_file():
            raise RuntimeError(f"missing Rust syntax release files for {target}")
        expected_hash, sidecar_name = sidecar.read_text(encoding="utf-8").split()
        if sidecar_name != archive.name or sha256(archive) != expected_hash:
            raise RuntimeError(f"archive checksum mismatch for {target}")
        with tempfile.TemporaryDirectory() as raw:
            extracted = Path(raw)
            safely_extract(archive, extracted)
            if sorted(path.name for path in extracted.iterdir()) != [target]:
                raise RuntimeError(f"unexpected archive root for {target}")
            bundle = extracted / target
            inventory = sorted(path.name for path in bundle.iterdir())
            expected_inventory = sorted([
                binary_name, "LICENSE", "THIRD_PARTY_NOTICES.md", "manifest.json"
            ])
            if inventory != expected_inventory:
                raise RuntimeError(f"unexpected inventory for {target}: {inventory}")
            manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("schema_version") != 1:
                raise RuntimeError(f"manifest schema mismatch for {target}")
            source = manifest.get("source", {})
            if (
                source.get("files") != expected_files
                or source.get("source_sha256") != expected_source_sha
                or source.get("commit") != expected_commit
                or source.get("license") != "Apache-2.0"
            ):
                raise RuntimeError(f"scanner source identity mismatch for {target}")
            build = manifest.get("build", {})
            if build.get("scanner_version") != args.version or build.get("platform_arch") != target:
                raise RuntimeError(f"scanner build identity mismatch for {target}")
            if build.get("independent_builds") != 2 or build.get("reproducible") is not True:
                raise RuntimeError(f"reproducibility evidence missing for {target}")
            binary = bundle / binary_name
            artifact = manifest.get("artifact", {})
            if artifact.get("file") != binary_name or artifact.get("sha256") != sha256(binary):
                raise RuntimeError(f"scanner checksum mismatch for {target}")
            if artifact.get("size") != binary.stat().st_size:
                raise RuntimeError(f"scanner size mismatch for {target}")
            if f"atlas-rust-syntax {args.version}" not in artifact.get("version_output", ""):
                raise RuntimeError(f"scanner version output mismatch for {target}")
            if "Apache License" not in (bundle / "LICENSE").read_text(encoding="utf-8"):
                raise RuntimeError(f"Apache license missing for {target}")
            if "The MIT License" not in (bundle / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8"):
                raise RuntimeError(f"third-party license notice missing for {target}")
        results.append({"target": target, "archive": archive.name, "sha256": expected_hash})
    extras = sorted(
        path.name for path in directory.iterdir()
        if path.is_file()
        and path.name.startswith("codebase-atlas-rust-syntax-")
        and not path.name.endswith(".sha256")
        and path.name not in expected_archives
    )
    if extras:
        raise RuntimeError(f"unexpected Rust syntax scanner archives: {extras}")
    print(json.dumps({"status": "pass", "bundles": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
