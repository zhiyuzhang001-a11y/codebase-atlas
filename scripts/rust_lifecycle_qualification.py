#!/usr/bin/env python3
"""Five-native-platform source-API lifecycle evidence, not installed-wheel proof.

Preparation alone may download locked official archives with explicit permission.
All fixture operations reuse one verified installation. No global/user deployment.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from codebase_atlas.release_installation import parse_checksum_manifest
from codebase_atlas.rust_acquisition import acquire_components
from codebase_atlas.rust_toolchain_installation import install_toolchain
from codebase_atlas.rust_scanner_installation import scanner_lock
try:
    from rust_toolchain_qualification import isolated_environment, validate_identity
    from rust_lifecycle_integration import main as lifecycle_main
except ModuleNotFoundError:
    from scripts.rust_toolchain_qualification import isolated_environment, validate_identity
    from scripts.rust_lifecycle_integration import main as lifecycle_main


def qualify(report: dict, base: Path, scanner: Path, *, allow_network: bool) -> None:
    preparation = base / "preparation"
    preparation.mkdir(mode=0o700)
    home = base / "environment"
    home.mkdir(mode=0o700)
    work = base / "lifecycle"
    with isolated_environment(home, base / "data") as removed:
        report["fixture_removed_environment_names"] = removed
        installation = install_toolchain(preparation, network_authorized=allow_network)
        report["toolchain_installation"] = installation
        # Offline cached archives are verified again. No aliases/copies/reinstall.
        archives = acquire_components(preparation, report["target"], network_authorized=False)
        archive_map = base / "archives.json"
        archive_map.write_text(json.dumps({name: str(path) for name, path in archives.items()}), encoding="utf-8")
        checksums = parse_checksum_manifest(scanner.with_name(scanner.name + ".sha256").read_bytes())
        if set(checksums) != {scanner.name}:
            raise ValueError("Scanner adjacent checksum inventory mismatch")
        try:
            report["lifecycle_summary"] = lifecycle_main([
                "--work-dir", str(work), "--toolchain", installation["root"],
                "--archive-map", str(archive_map), "--scanner", str(scanner),
                "--scanner-sha256", checksums[scanner.name],
                "--scanner-commit", report["source_sha"], "--execution-sentinels",
            ])
        finally:
            evidence = work / "results.json"
            if evidence.is_file():
                report["operations"] = json.loads(evidence.read_text(encoding="utf-8"))
            hostile = work / "hostile-hooks.json"
            if hostile.is_file():
                report["hostile_hooks"] = json.loads(hostile.read_text(encoding="utf-8"))
        report["status"] = "passed"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parents[1]
    report = {"schema_version": 1, "stage": "phase2-source-api-lifecycle",
              "source_sha": args.source_sha, "target": args.target, "status": "failed",
              "public_rust_enabled": False,
              "not_proven": ["installed-wheel", "fresh stdio MCP", "live Codex task",
                             "OS-level network observation", "full child argv", "resource gates"]}
    with args.output.open("x", encoding="utf-8") as output:
        try:
            validate_identity(source, args.source_sha, args.target, require_clean=True)
            suffix = ".zip" if args.target.startswith("windows-") else ".tar.gz"
            scanner = args.directory / ("codebase-atlas-rust-syntax-" + scanner_lock()["version"]
                                        + "-" + args.target + suffix)
            with tempfile.TemporaryDirectory(prefix="atlas-lifecycle-qualification-") as temporary:
                qualify(report, Path(temporary).resolve(), scanner.resolve(strict=True),
                        allow_network=args.allow_network)
        except Exception as exc:
            report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            json.dump(report, output, indent=2, sort_keys=True)
            output.write("\n")
    print(json.dumps({key: report[key] for key in ("status", "stage", "source_sha", "target")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
