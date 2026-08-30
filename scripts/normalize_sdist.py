#!/usr/bin/env python3
"""Normalize an existing sdist archive to reproducible tar/gzip metadata."""

from __future__ import annotations

import argparse
import copy
import gzip
from pathlib import Path, PurePosixPath
import tarfile


def _safe_archive_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def normalize_sdist(source_path: Path, output_path: Path, epoch: int) -> None:
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    if source_path == output_path:
        raise ValueError("input and output sdist paths must differ")
    if epoch < 0:
        raise ValueError("epoch must be non-negative")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(source_path, mode="r:gz") as source:
        members = sorted(source.getmembers(), key=lambda member: member.name)
        for member in members:
            if not _safe_archive_path(member.name):
                raise ValueError(f"unsafe sdist member path: {member.name}")
            if (member.issym() or member.islnk()) and not _safe_archive_path(
                member.linkname
            ):
                raise ValueError(f"unsafe sdist link target: {member.linkname}")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError(f"unsupported sdist member type: {member.name}")

        with output_path.open("wb") as raw_output:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw_output, mtime=epoch
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT
                ) as target:
                    for member in members:
                        normalized = copy.copy(member)
                        normalized.uid = 0
                        normalized.gid = 0
                        normalized.uname = ""
                        normalized.gname = ""
                        normalized.mtime = epoch
                        normalized.pax_headers = {}
                        payload = source.extractfile(member) if member.isfile() else None
                        target.addfile(normalized, payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--epoch", type=int, required=True)
    args = parser.parse_args()
    normalize_sdist(args.source, args.output, args.epoch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
