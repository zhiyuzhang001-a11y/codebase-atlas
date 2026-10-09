#!/usr/bin/env python3
"""Internal phase-2 scanner install/reuse/version proof, NOT product semantics.

Input is an exact-head CI-built bundle, never an end-user deployment source.
Production manifest/source/license validation is used without transport shortcuts.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from time import monotonic

from codebase_atlas.release_installation import parse_checksum_manifest
from codebase_atlas.rust_owned_command import run_owned
from codebase_atlas.rust_scanner_installation import install_scanner_asset, scanner_lock, verified_scanner
try:
    from rust_toolchain_qualification import isolated_environment, validate_identity
except ModuleNotFoundError:
    from scripts.rust_toolchain_qualification import isolated_environment, validate_identity


def qualify(report: dict, archive: Path, *, target: str, source_sha: str, project: Path) -> None:
    checksums = parse_checksum_manifest(archive.with_name(archive.name + ".sha256").read_bytes())
    if set(checksums) != {archive.name}:
        raise ValueError("Scanner adjacent checksum inventory mismatch")
    sha = checksums[archive.name]
    binary = install_scanner_asset(archive, sha256=sha, commit=source_sha, target=target)
    identity = binary.stat()
    repeated = install_scanner_asset(archive, sha256=sha, commit=source_sha, target=target)
    if repeated != binary or not os.path.samestat(identity, repeated.stat()):
        raise ValueError("Scanner repeat did not reuse exact installed binary")
    tool = verified_scanner(project)
    report.update(archive_sha256=sha, binary_sha256=tool.sha256, reused_binary=True,
                  receipt=json.loads((binary.parent / "receipt.json").read_text()),
                  manifest=json.loads((binary.parent / "manifest.json").read_text()))
    environment = {name: os.environ[name] for name in (
        "HOME", "USERPROFILE", "SystemRoot", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"
    ) if os.environ.get(name)}
    environment["CARGO_NET_OFFLINE"] = "true"
    probe = {"argv": [str(tool.verify()), "--version"], "cwd": str(project),
             "environment": environment, "timeout_seconds": 5, "cleanup_grace_seconds": 10}
    report["version_probe"] = probe
    start = monotonic()
    result = run_owned(probe["argv"], cwd=project, env=environment, timeout=5,
                       capture_output=True, text=True)
    probe.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
                 elapsed_seconds=monotonic() - start)
    result.check_returncode()
    if not result.stdout.startswith("atlas-rust-syntax " + scanner_lock()["version"] + " "):
        raise ValueError("Installed scanner version mismatch")
    if list(project.iterdir()):
        raise ValueError("Scanner version probe changed the empty fixture")
    report.update(status="passed", project_writes=[])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"stage": "phase2-scanner-only", "source_sha": args.source_sha,
              "target": args.target, "status": "failed", "public_rust_enabled": False,
              "not_proven": ["stable download acquisition", "T1/T2 product semantics",
                             "OS network/full child argv", "installed-wheel lifecycle", "resource gates"]}
    with args.output.open("x", encoding="utf-8") as output:
        try:
            validate_identity(Path(__file__).resolve().parents[1], args.source_sha, args.target,
                              require_clean=True)
            suffix = ".zip" if args.target.startswith("windows-") else ".tar.gz"
            archive = args.directory / ("codebase-atlas-rust-syntax-" + scanner_lock()["version"]
                                        + "-" + args.target + suffix)
            with tempfile.TemporaryDirectory(prefix="atlas-scanner-qualification-") as temporary:
                base = Path(temporary).resolve()
                project = base / "empty-project"
                project.mkdir(mode=0o700)
                with isolated_environment(base, base / "data"):
                    qualify(report, archive, target=args.target, source_sha=args.source_sha, project=project)
        except Exception as exc:
            report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            json.dump(report, output, indent=2, sort_keys=True)
            output.write("\n")
    print(json.dumps({key: report[key] for key in ("status", "stage", "source_sha", "target")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
