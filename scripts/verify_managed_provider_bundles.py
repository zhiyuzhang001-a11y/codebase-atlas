#!/usr/bin/env python3
"""Verify the complete Codebase Atlas managed Provider release set."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import tempfile
import zipfile
from pathlib import Path

try:
    from build_managed_provider import DEFAULT_COMMIT, DEFAULT_VERSION, TARGETS
except ModuleNotFoundError:  # Imported as scripts.verify_managed_provider_bundles.
    from scripts.build_managed_provider import DEFAULT_COMMIT, DEFAULT_VERSION, TARGETS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected-commit", default=DEFAULT_COMMIT)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    args = parser.parse_args()
    directory = args.directory.resolve()
    results = []
    for target, (_system, binary_name, archive_kind) in TARGETS.items():
        suffix = ".zip" if archive_kind == "zip" else ".tar.gz"
        archive = directory / f"codebase-atlas-provider-{args.version}-{target}{suffix}"
        sidecar = directory / f"{archive.name}.sha256"
        if not archive.is_file() or not sidecar.is_file():
            raise RuntimeError(f"missing release files for {target}")
        expected_archive_hash, sidecar_name = sidecar.read_text(encoding="utf-8").split()
        if sidecar_name != archive.name or sha256(archive) != expected_archive_hash:
            raise RuntimeError(f"archive checksum mismatch for {target}")
        with tempfile.TemporaryDirectory() as raw:
            extracted = Path(raw)
            safely_extract(archive, extracted)
            if sorted(path.name for path in extracted.iterdir()) != [target]:
                raise RuntimeError(f"unexpected archive root for {target}")
            bundle = extracted / target
            inventory = sorted(path.name for path in bundle.iterdir())
            if inventory != sorted([binary_name, "LICENSE", "manifest.json"]):
                raise RuntimeError(f"unexpected inventory for {target}: {inventory}")
            manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            if manifest["source"]["commit"] != args.expected_commit:
                raise RuntimeError(f"source commit mismatch for {target}")
            if manifest["build"]["managed_version"] != args.version:
                raise RuntimeError(f"managed version mismatch for {target}")
            if manifest["build"]["platform_arch"] != target:
                raise RuntimeError(f"platform mismatch for {target}")
            if not manifest["build"]["reproducible"] or manifest["build"]["independent_builds"] != 2:
                raise RuntimeError(f"reproducibility evidence missing for {target}")
            binary = bundle / binary_name
            if sha256(binary) != manifest["artifact"]["sha256"]:
                raise RuntimeError(f"binary checksum mismatch for {target}")
            if binary.stat().st_size != manifest["artifact"]["size"]:
                raise RuntimeError(f"binary size mismatch for {target}")
            if args.version not in manifest["artifact"]["version_output"]:
                raise RuntimeError(f"binary version mismatch for {target}")
            if "MIT License" not in (bundle / "LICENSE").read_text(encoding="utf-8"):
                raise RuntimeError(f"MIT license missing for {target}")
        results.append({"target": target, "archive": archive.name, "sha256": expected_archive_hash})
    extras = sorted(
        path.name for path in directory.iterdir()
        if path.is_file() and path.name.startswith("codebase-atlas-provider-")
        and path.name not in {item["archive"] for item in results}
        and not path.name.endswith(".sha256")
    )
    if extras:
        raise RuntimeError(f"unexpected managed Provider archives: {extras}")
    print(json.dumps({"status": "pass", "bundles": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
