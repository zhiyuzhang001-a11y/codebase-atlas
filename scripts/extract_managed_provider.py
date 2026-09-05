#!/usr/bin/env python3
"""Verify and extract one exact managed Provider bundle for native tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from build_managed_provider import DEFAULT_COMMIT, DEFAULT_VERSION, TARGETS
    from verify_managed_provider_bundles import safely_extract, sha256
except ModuleNotFoundError:  # Imported as scripts.extract_managed_provider.
    from scripts.build_managed_provider import DEFAULT_COMMIT, DEFAULT_VERSION, TARGETS
    from scripts.verify_managed_provider_bundles import safely_extract, sha256


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("target", choices=sorted(TARGETS))
    parser.add_argument("destination", type=Path)
    parser.add_argument("--expected-commit", default=DEFAULT_COMMIT)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    args = parser.parse_args()

    _system, binary_name, archive_kind = TARGETS[args.target]
    suffix = ".zip" if archive_kind == "zip" else ".tar.gz"
    archive = args.directory.resolve() / (
        f"codebase-atlas-provider-{args.version}-{args.target}{suffix}"
    )
    sidecar = archive.with_name(f"{archive.name}.sha256")
    expected_hash, sidecar_name = sidecar.read_text(encoding="utf-8").split()
    if sidecar_name != archive.name or sha256(archive) != expected_hash:
        raise RuntimeError("managed Provider archive checksum mismatch")

    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    safely_extract(archive, destination)
    bundle = destination / args.target
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if manifest["source"]["commit"] != args.expected_commit:
        raise RuntimeError("managed Provider source commit mismatch")
    if manifest["build"]["managed_version"] != args.version:
        raise RuntimeError("managed Provider version mismatch")
    if manifest["build"]["platform_arch"] != args.target:
        raise RuntimeError("managed Provider target mismatch")
    binary = bundle / binary_name
    if sha256(binary) != manifest["artifact"]["sha256"]:
        raise RuntimeError("managed Provider binary checksum mismatch")
    print(json.dumps({
        "status": "pass",
        "target": args.target,
        "binary": str(binary),
        "sha256": manifest["artifact"]["sha256"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
