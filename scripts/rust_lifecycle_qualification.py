#!/usr/bin/env python3
"""Five-native-platform source-API lifecycle evidence, not installed-wheel proof.

Preparation alone may download locked official archives with explicit permission.
All fixture operations reuse one verified installation. No global/user deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import sys

from codebase_atlas.release_installation import parse_checksum_manifest
from codebase_atlas.rust_acquisition import acquire_components
from codebase_atlas.rust_toolchain_installation import install_toolchain
from codebase_atlas.rust_scanner_installation import scanner_lock
from codebase_atlas.rust_installation import load_toolchain_receipt, toolchain_store, release_lock
from codebase_atlas.rust_runtime import SYSROOT_LIBRARY
try:
    from rust_toolchain_qualification import isolated_environment, validate_identity
    from rust_lifecycle_integration import main as lifecycle_main
    from rust_linux_trace import observe, require_metadata_contexts, require_rust_argument_templates
except ModuleNotFoundError:
    from scripts.rust_toolchain_qualification import isolated_environment, validate_identity
    from scripts.rust_lifecycle_integration import main as lifecycle_main
    from scripts.rust_linux_trace import observe, require_metadata_contexts, require_rust_argument_templates


def qualify(report: dict, base: Path, scanner: Path, *, allow_network: bool, trace_directory: Path | None = None) -> None:
    preparation = base / "preparation"
    preparation.mkdir(mode=0o700)
    home = base / "environment"
    home.mkdir(mode=0o700)
    work = base / "lifecycle"
    with isolated_environment(home, base / "data") as removed:
        report["fixture_removed_environment_names"] = removed
        installation = install_toolchain(preparation, network_authorized=allow_network)
        report["toolchain_installation"] = installation
        receipt = Path(installation["receipt"])
        document = load_toolchain_receipt(receipt, store=toolchain_store())
        raw = receipt.read_bytes()
        if json.loads(raw) != document:
            raise ValueError("Verified receipt changed before evidence retention")
        if document["root"] != installation["root"] or document["target"] != report["target"]:
            raise ValueError("Lifecycle installation/receipt identity mismatch")
        report["verified_receipt"] = document
        report["verified_receipt_sha256"] = hashlib.sha256(raw).hexdigest()
        report["verified_receipt_raw_utf8"] = raw.decode("utf-8", errors="strict")
        report["toolchain_source_lock"] = release_lock()
        report["verified_executable_map"] = {
            str(Path(document["root"]) / tool["path"]): {"role": name, "sha256": tool["sha256"]}
            for name, tool in document["tools"].items()
        }
        # Offline cached archives are verified again. No aliases/copies/reinstall.
        archives = acquire_components(preparation, report["target"], network_authorized=False)
        archive_map = base / "archives.json"
        archive_map.write_text(json.dumps({name: str(path) for name, path in archives.items()}), encoding="utf-8")
        checksums = parse_checksum_manifest(scanner.with_name(scanner.name + ".sha256").read_bytes())
        if set(checksums) != {scanner.name}:
            raise ValueError("Scanner adjacent checksum inventory mismatch")
        try:
            lifecycle_args = [
                "--work-dir", str(work), "--toolchain", installation["root"],
                "--archive-map", str(archive_map), "--scanner", str(scanner),
                "--scanner-sha256", checksums[scanner.name],
                "--scanner-commit", report["source_sha"], "--execution-sentinels",
            ]
            if trace_directory is not None:
                report["linux_native_observation"] = observe(
                    [sys.executable, str(Path(__file__).with_name("rust_lifecycle_integration.py")), *lifecycle_args],
                    cwd=Path(__file__).resolve().parents[1], directory=trace_directory,
                    verified_tools=report["verified_executable_map"])
                project = work.resolve() / "project"
                library = Path(document["root"]) / SYSROOT_LIBRARY
                contexts = {
                    str(project): {"manifest": str(project / "Cargo.toml"), "all_features": True},
                    str(library): {"manifest": str(library / "Cargo.toml"), "all_features": False},
                }
                target = {"linux-x86_64": "x86_64-unknown-linux-gnu",
                          "linux-arm64": "aarch64-unknown-linux-gnu"}[report["target"]]
                observation = report["linux_native_observation"]
                observation["metadata_context_policy"] = contexts
                observation["context_bound_metadata_launches"] = require_metadata_contexts(
                    observation["lifecycle"], contexts=contexts, native_target=target)
                observation["diagnostic_argument_template_matches"] = require_rust_argument_templates(
                    observation["lifecycle"], verified_tools=report["verified_executable_map"],
                    contexts=contexts, native_target=target)
                observation["argument_template_scope"] = "argv/cwd diagnostic only; NOT env/parent/stdin/immutable-window approval or exec/network enforcement"
                summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
                report["lifecycle_summary"] = summary
            else:
                report["lifecycle_summary"] = lifecycle_main(lifecycle_args)
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
    parser.add_argument("--linux-native-trace-dir", type=Path)
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
                        allow_network=args.allow_network,
                        **({"trace_directory": args.linux_native_trace_dir.resolve()} if args.linux_native_trace_dir else {}))
        except Exception as exc:
            report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            json.dump(report, output, indent=2, sort_keys=True)
            output.write("\n")
    print(json.dumps({key: report[key] for key in ("status", "stage", "source_sha", "target")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
