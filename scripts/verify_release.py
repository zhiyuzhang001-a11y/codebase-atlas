#!/usr/bin/env python3
"""Verify source, tag, and wheel release invariants."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def source_version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    metadata_version = project["project"]["version"]
    package_text = (ROOT / "src/codebase_atlas/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', package_text, re.MULTILINE)
    if not match or match.group(1) != metadata_version:
        raise SystemExit("pyproject.toml and codebase_atlas.__version__ do not match")
    return metadata_version


def verify_wheel(path: Path, version: str) -> str:
    expected = {
        "codebase_atlas/runtime.py",
        "codebase_atlas/cli.py",
        "codebase_atlas/service.py",
        "share/codebase-atlas/serena_runner.py",
        "share/codebase-atlas/ts_test_analyzer.mjs",
        "share/codebase-atlas/node_modules/typescript/LICENSE.txt",
        "share/codebase-atlas/node_modules/typescript/lib/typescript.js",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
    }
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        missing = sorted(item for item in expected if not any(name.endswith(item) for name in names))
        if missing:
            raise SystemExit(f"wheel is missing packaged assets: {missing}")
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
        if f"Version: {version}\n" not in metadata:
            raise SystemExit("wheel metadata version does not match source")
        if "Requires-Python: <3.15,>=3.11" not in metadata and "Requires-Python: >=3.11,<3.15" not in metadata:
            raise SystemExit("wheel does not declare the supported Python range")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    version = source_version()
    if args.tag and args.tag != f"v{version}":
        raise SystemExit(f"tag {args.tag} does not match source version {version}")
    result = {"version": version}
    if args.wheel:
        result.update(wheel=str(args.wheel), sha256=verify_wheel(args.wheel, version))
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
