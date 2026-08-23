#!/usr/bin/env python3
"""Build the newest prior tagged release in an isolated exported tree."""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile


def version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tag.removeprefix("v").split("."))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    current = version_key(args.current)
    raw_tags = subprocess.run(
        ["git", "tag", "--list", "v[0-9]*"], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    candidates = sorted((tag for tag in raw_tags if version_key(tag) < current), key=version_key)
    if not candidates:
        raise SystemExit("no prior release tag is available for lifecycle acceptance")
    selected = candidates[-1]
    archive = subprocess.run(
        ["git", "archive", "--format=tar", selected], check=True, capture_output=True
    ).stdout
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as raw:
        checkout = Path(raw) / "previous"
        checkout.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as value:
            value.extractall(checkout, filter="data")
        # Releases before M15 referenced an ignored node_modules path. Rehydrate
        # the byte-identical pinned TypeScript 5.9.3 assets now tracked in vendor/
        # so the previous source release can participate in lifecycle testing.
        vendored = Path(__file__).resolve().parents[1] / "vendor" / "typescript"
        shutil.copytree(vendored, checkout / "node_modules" / "typescript")
        subprocess.run(
            [
                sys.executable, "-m", "pip", "wheel", str(checkout),
                "--no-deps", "--no-build-isolation", "--wheel-dir", str(args.output),
            ],
            check=True,
        )
    print(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
