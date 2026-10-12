#!/usr/bin/env python3
"""Offline qualification of an existing toolchain; never runs tools/installers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from codebase_atlas.rust_installation import release_lock, verify_existing_toolchain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--archives", type=Path, required=True)
    parser.add_argument("--target", choices=tuple(release_lock()["targets"]), required=True)
    args = parser.parse_args()
    lock = release_lock()
    components = dict(lock["targets"][args.target]["components"])
    components["rust-src"] = lock["rust_src"]
    archives = {name: args.archives / Path(value["url"]).name for name, value in components.items()}
    document = verify_existing_toolchain(args.root, archives, args.target)
    print(json.dumps({"status": "verified", "target": args.target,
                      "toolchain": document["toolchain"], "components": document["components"],
                      "files_verified": len(document["files"]), "tools": document["tools"],
                      "licenses_verified": len(document["licenses"]),
                      "installation_modified": False, "receipt_published": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
