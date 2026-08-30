#!/usr/bin/env python3
"""Build a reproducible managed Codebase Memory Provider bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path


DEFAULT_COMMIT = "ca90facf3f4e786236fd0e915a1ad9fe6a41b45b"
DEFAULT_VERSION = "0.10.8-atlas.1+ca90facf"
FORK = "https://github.com/zhiyuzhang001-a11y/codebase-memory-mcp"
UPSTREAM = "https://github.com/DeusData/codebase-memory-mcp"
TARGETS = {
    "linux-x86_64": ("linux", "codebase-memory-mcp", "tar.gz"),
    "linux-arm64": ("linux", "codebase-memory-mcp", "tar.gz"),
    "macos-x86_64": ("darwin", "codebase-memory-mcp", "tar.gz"),
    "macos-arm64": ("darwin", "codebase-memory-mcp", "tar.gz"),
    "windows-x86_64": ("windows", "codebase-memory-mcp.exe", "zip"),
    "windows-arm64": ("windows", "codebase-memory-mcp.exe", "zip"),
}


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout[-2000:]}\n{completed.stderr[-4000:]}"
        )
    return completed.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_tree(archive: tarfile.TarFile, bundle: Path, epoch: int) -> None:
    for path in sorted(bundle.rglob("*")):
        relative = Path(bundle.name) / path.relative_to(bundle)
        info = archive.gettarinfo(str(path), arcname=str(relative))
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = epoch
        if info.isfile():
            with path.open("rb") as handle:
                archive.addfile(info, handle)
        else:
            archive.addfile(info)


def write_tar(bundle: Path, destination: Path, epoch: int) -> None:
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                add_tree(archive, bundle, epoch)


def write_zip(bundle: Path, destination: Path, epoch: int) -> None:
    timestamp = tuple(__import__("time").gmtime(max(epoch, 315532800))[:6])
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(bundle.rglob("*")):
            if not path.is_file():
                continue
            relative = (Path(bundle.name) / path.relative_to(bundle)).as_posix()
            info = zipfile.ZipInfo(relative, timestamp)
            mode = 0o755 if path.name.endswith((".exe", "codebase-memory-mcp")) else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with path.open("rb") as handle:
                archive.writestr(info, handle.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build_once(
    source: Path,
    build_dir: Path,
    binary_name: str,
    version: str,
    epoch: int,
    build_args: list[str],
) -> Path:
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(epoch)
    relative = build_dir.relative_to(source).as_posix()
    command = ["bash", "scripts/build.sh", "--version", version, f"BUILD_DIR={relative}", *build_args]
    run(command, cwd=source, env=environment)
    binary = build_dir / binary_name
    if not binary.is_file():
        raise RuntimeError(f"build completed without binary: {binary}")
    return binary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-commit", default=DEFAULT_COMMIT)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--build-arg", action="append", default=[])
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    commit = run(["git", "rev-parse", "HEAD"], cwd=source)
    if commit != args.expected_commit:
        raise RuntimeError(f"Provider commit mismatch: expected {args.expected_commit}, got {commit}")
    if run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source):
        raise RuntimeError("Provider has tracked changes; refusing managed build")
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"output is not empty; refusing overwrite: {output}")
    output.mkdir(parents=True, exist_ok=True)
    epoch = int(run(["git", "show", "-s", "--format=%ct", commit], cwd=source))
    _system, binary_name, archive_kind = TARGETS[args.target]

    build_root = source / "build"
    build_root.mkdir(exist_ok=True)
    first_dir = Path(tempfile.mkdtemp(prefix="atlas-managed-first-", dir=build_root))
    second_dir = Path(tempfile.mkdtemp(prefix="atlas-managed-second-", dir=build_root))
    staging = output / args.target
    try:
        first = build_once(source, first_dir, binary_name, args.version, epoch, args.build_arg)
        second = build_once(source, second_dir, binary_name, args.version, epoch, args.build_arg)
        first_hash = sha256(first)
        second_hash = sha256(second)
        if first_hash != second_hash:
            raise RuntimeError(f"non-reproducible binaries: {first_hash} != {second_hash}")

        staging.mkdir()
        shutil.copyfile(first, staging / binary_name)
        (staging / binary_name).chmod(0o755)
        shutil.copyfile(source / "LICENSE", staging / "LICENSE")
        version_output = run([str(first), "--version"], cwd=source)
        if args.version not in version_output:
            raise RuntimeError(f"managed version missing from binary: {version_output}")
        manifest = {
            "schema_version": 1,
            "product": "Codebase Atlas managed Codebase Memory Provider",
            "source": {"commit": commit, "fork": FORK, "upstream": UPSTREAM, "license": "MIT"},
            "build": {
                "source_date_epoch": epoch,
                "command": "scripts/build.sh --version <managed-version> BUILD_DIR=<isolated>",
                "build_arguments": args.build_arg,
                "independent_builds": 2,
                "reproducible": True,
                "platform_arch": args.target,
                "managed_version": args.version,
            },
            "artifact": {
                "file": binary_name,
                "sha256": first_hash,
                "size": (staging / binary_name).stat().st_size,
                "version_output": version_output,
            },
            "verification": {
                "source_ci": "34/34 passed at ca90facf; upstream PR run 33295286000",
                "bundle_build": "two independent same-runner builds are byte-identical",
            },
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        suffix = ".zip" if archive_kind == "zip" else ".tar.gz"
        archive = output / f"codebase-atlas-provider-{args.version}-{args.target}{suffix}"
        if archive_kind == "zip":
            write_zip(staging, archive, epoch)
        else:
            write_tar(staging, archive, epoch)
        archive_hash = sha256(archive)
        sidecar = archive.with_name(archive.name + ".sha256")
        sidecar.write_text(f"{archive_hash}  {archive.name}\n", encoding="utf-8")
        print(json.dumps({"archive": str(archive), "sha256": archive_hash, "binary_sha256": first_hash}))
    finally:
        shutil.rmtree(first_dir, ignore_errors=True)
        shutil.rmtree(second_dir, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
