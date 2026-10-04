#!/usr/bin/env python3
"""Build one reproducible, checksum-bound Rust syntax scanner bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "native/rust-syntax-scanner"
SOURCE_FILES = ("Cargo.toml", "Cargo.lock", "src/main.rs", "THIRD_PARTY_NOTICES.md")
TARGETS = {
    "linux-x86_64": ("Linux", {"x86_64", "amd64"}, "atlas-rust-syntax", "tar.gz"),
    "linux-arm64": ("Linux", {"aarch64", "arm64"}, "atlas-rust-syntax", "tar.gz"),
    "macos-arm64": ("Darwin", {"aarch64", "arm64"}, "atlas-rust-syntax", "tar.gz"),
    "windows-x86_64": ("Windows", {"x86_64", "amd64"}, "atlas-rust-syntax.exe", "zip"),
    "windows-arm64": ("Windows", {"aarch64", "arm64"}, "atlas-rust-syntax.exe", "zip"),
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


def source_identity(source: Path) -> dict[str, object]:
    files = {name: sha256(source / name) for name in SOURCE_FILES}
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    commit = run(["git", "rev-parse", "HEAD"], cwd=ROOT)
    return {
        "repository": "https://github.com/zhiyuzhang001-a11y/codebase-atlas",
        "commit": commit,
        "files": files,
        "source_sha256": hashlib.sha256(canonical).hexdigest(),
        "license": "Apache-2.0",
    }


def add_tree(archive: tarfile.TarFile, bundle: Path, epoch: int) -> None:
    for path in sorted(bundle.rglob("*")):
        relative = Path(bundle.name) / path.relative_to(bundle)
        info = archive.gettarinfo(str(path), arcname=str(relative))
        info.uid = info.gid = 0
        info.uname = info.gname = ""
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
    timestamp = tuple(time.gmtime(max(epoch, 315532800))[:6])
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(bundle.rglob("*")):
            if not path.is_file():
                continue
            relative = (Path(bundle.name) / path.relative_to(bundle)).as_posix()
            info = zipfile.ZipInfo(relative, timestamp)
            mode = 0o755 if path.name.endswith((".exe", "atlas-rust-syntax")) else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def reproducibility_flags(source: Path, target_dir: Path, *, windows: bool) -> list[str]:
    flags = [
        f"--remap-path-prefix={source}=/atlas-rust-syntax",
        f"--remap-path-prefix={target_dir}=/atlas-build",
    ]
    if windows:
        # PE timestamps and PDB paths otherwise vary across independent builds.
        flags.extend(["-Clink-arg=/Brepro", "-Clink-arg=/INCREMENTAL:NO",
                      "-Clink-arg=/DEBUG:NONE"])
    return flags


def build_once(source: Path, target_dir: Path, cargo: str, epoch: int) -> Path:
    environment = os.environ.copy()
    if environment.get("RUSTFLAGS") or environment.get("CARGO_ENCODED_RUSTFLAGS"):
        raise RuntimeError("release build refuses ambient Rust compiler flags")
    environment.update({"CARGO_TARGET_DIR": str(target_dir), "SOURCE_DATE_EPOCH": str(epoch)})
    environment["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(
        reproducibility_flags(source, target_dir, windows=os.name == "nt")
    )
    run([cargo, "build", "--locked", "--offline", "--release"], cwd=source, env=environment)
    binary = target_dir / "release" / ("atlas-rust-syntax.exe" if os.name == "nt" else "atlas-rust-syntax")
    if not binary.is_file():
        raise RuntimeError(f"build completed without scanner binary: {binary}")
    return binary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cargo", default="cargo")
    args = parser.parse_args()
    source = SCANNER.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"output is not empty; refusing overwrite: {output}")
    output.mkdir(parents=True, exist_ok=True)
    expected_system, machines, binary_name, archive_kind = TARGETS[args.target]
    if platform.system() != expected_system or platform.machine().lower() not in machines:
        raise RuntimeError(f"target {args.target} does not match host {platform.system()}-{platform.machine()}")
    package = tomllib.loads((source / "Cargo.toml").read_text(encoding="utf-8"))["package"]
    version = package["version"]
    identity = source_identity(source)
    epoch = int(run(["git", "show", "-s", "--format=%ct", identity["commit"]], cwd=ROOT))
    first_dir = Path(tempfile.mkdtemp(prefix="atlas-rust-syntax-first-"))
    second_dir = Path(tempfile.mkdtemp(prefix="atlas-rust-syntax-second-"))
    staging = output / args.target
    try:
        first = build_once(source, first_dir, args.cargo, epoch)
        second = build_once(source, second_dir, args.cargo, epoch)
        first_hash, second_hash = sha256(first), sha256(second)
        if first_hash != second_hash:
            raise RuntimeError(f"non-reproducible scanner binaries: {first_hash} != {second_hash}")
        staging.mkdir()
        shutil.copyfile(first, staging / binary_name)
        (staging / binary_name).chmod(0o755)
        shutil.copyfile(ROOT / "LICENSE", staging / "LICENSE")
        shutil.copyfile(source / "THIRD_PARTY_NOTICES.md", staging / "THIRD_PARTY_NOTICES.md")
        version_output = run([str(first), "--version"], cwd=source)
        if f"atlas-rust-syntax {version}" not in version_output:
            raise RuntimeError(f"scanner version missing from binary: {version_output}")
        manifest = {
            "schema_version": 1,
            "product": "Codebase Atlas Rust syntax scanner",
            "source": identity,
            "build": {
                "source_date_epoch": epoch,
                "command": "cargo build --locked --offline --release",
                "independent_builds": 2,
                "rustflags": reproducibility_flags(
                    Path("<source>"), Path("<target-dir>"), windows=expected_system == "Windows"
                ),
                "reproducible": True,
                "platform_arch": args.target,
                "scanner_version": version,
                "rustc_version": run(["rustc", "--version"], cwd=source),
                "cargo_version": run([args.cargo, "--version"], cwd=source),
            },
            "artifact": {
                "file": binary_name,
                "sha256": first_hash,
                "size": (staging / binary_name).stat().st_size,
                "version_output": version_output,
            },
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        suffix = ".zip" if archive_kind == "zip" else ".tar.gz"
        archive = output / f"codebase-atlas-rust-syntax-{version}-{args.target}{suffix}"
        (write_zip if archive_kind == "zip" else write_tar)(staging, archive, epoch)
        archive_hash = sha256(archive)
        archive.with_name(archive.name + ".sha256").write_text(f"{archive_hash}  {archive.name}\n", encoding="utf-8")
        print(json.dumps({"archive": str(archive), "sha256": archive_hash, "binary_sha256": first_hash}))
    finally:
        shutil.rmtree(first_dir, ignore_errors=True)
        shutil.rmtree(second_dir, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
