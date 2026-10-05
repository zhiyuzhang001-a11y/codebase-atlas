#!/usr/bin/env python3
"""Phase-2 real official tool qualification, NOT installed-wheel/product acceptance.

Only three owned --version commands execute. No project code, rustup or shell.
The default installation and homes are temporary; an existing private data root
can be explicitly reused for local evidence without another toolchain download.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from time import monotonic

from codebase_atlas.languages import get_language
from codebase_atlas.release_installation import current_platform_target
from codebase_atlas.rust_installation import load_toolchain_receipt, runtime_from_receipt, toolchain_store
from codebase_atlas.rust_owned_command import run_owned
from codebase_atlas.rust_runtime import PINNED_TOOLCHAIN, RustRuntimeError
from codebase_atlas.rust_toolchain_installation import install_toolchain


def validate_identity(repository: Path, source_sha: str, target: str, *, require_clean: bool = False) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("Qualification requires an exact full source SHA")
    actual = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository,
                            capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    if actual != source_sha or current_platform_target() != target:
        raise ValueError("Qualification source or native target mismatch")
    if get_language("rust").public_enabled:
        raise ValueError("Internal qualification must retain the closed public gate")
    if require_clean:
        subprocess.run(["git", "diff", "--exit-code", "HEAD", "--"], cwd=repository,
                       capture_output=True, check=True, timeout=5)
        subprocess.run(["git", "ls-files", "--error-unmatch", "scripts/rust_toolchain_qualification.py"],
                       cwd=repository, capture_output=True, check=True, timeout=5)


@contextmanager
def isolated_environment(base: Path, data_root: Path):
    # This script's positive fixture only. Removing inherited overrides is NOT
    # evidence of rejecting hostile config in a real user repository.
    original = dict(os.environ)
    removed = sorted(name for name in original
                     if name == "CARGO" or name.startswith(("CARGO_", "RUST", "LD_", "DYLD_")))
    try:
        for name in removed:
            os.environ.pop(name, None)
        homes = {"HOME": base / "home", "USERPROFILE": base / "home",
                 "CARGO_HOME": base / "cargo", "RUSTUP_HOME": base / "rustup"}
        for path in set(homes.values()):
            path.mkdir(mode=0o700)
        os.environ.update({name: str(path) for name, path in homes.items()})
        os.environ["XDG_DATA_HOME"] = str(data_root)
        yield removed
    finally:
        os.environ.clear()
        os.environ.update(original)


def validate_version(name: str, output: str) -> None:
    prefix = "rust-analyzer" if name == "analyzer" else name
    if not re.match(r"^" + re.escape(prefix + " " + PINNED_TOOLCHAIN) + r"(?:\s|$)", output):
        raise ValueError("Official tool version mismatch: " + name)


def qualify_preflight(report: dict, runtime, project: Path, base: Path) -> None:
    """Real receipt/tools, hostile fixture config; Python audit, NOT OS tracing.

    A forbidden audit event fails the harness even if preflight catches it. This
    observer cannot see arbitrary native API calls and does not prove whole-tree
    network/argv behavior during enable/query/refresh. Those remain open gates.
    """
    report["preflight_negatives"] = []
    active = {"events": None}

    def audit(event, args):
        if active["events"] is not None and (event in {
                "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn",
                "socket.connect", "socket.getaddrinfo"} or event.startswith("os.spawn")):
            active["events"].append(event)
            raise RuntimeError("Forbidden execution/network attempt during preflight")

    sys.addaudithook(audit)
    sentinel = base / "untrusted-wrapper"
    # No executable is installed: the observer must reject any attempt BEFORE
    # launching even a missing wrapper. No fake wrapper-execution proof claimed.
    cases = [("env-" + name, None, None, {name: str(sentinel)}) for name in (
        "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "RUSTUP_TOOLCHAIN", "RUSTC",
        "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER", "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES", "CARGO_REGISTRIES_CRATES_IO_INDEX")]
    cases.extend([
        ("project-cargo", project / ".cargo/config.toml", '[build]\nrustc-wrapper="untrusted"\n', {}),
        ("ancestor-cargo", base / ".cargo/config.toml", '[build]\nrustc-wrapper="untrusted"\n', {}),
        ("user-cargo", runtime.cargo_home / "config.toml", '[build]\nrustc-wrapper="untrusted"\n', {}),
        ("project-analyzer", project / "rust-analyzer.toml", '[cargo.buildScripts]\nenable=true\n', {}),
        ("toolchain-download", project / "rust-toolchain.toml", '[toolchain]\nchannel="nightly"\n', {}),
        ("rustup-override", runtime.rustup_home / "settings.toml",
         '[overrides]\n' + json.dumps(str(project)) + '="nightly"\n', {}),
    ])
    for name, path, content, overrides in cases:
        old = dict(os.environ)
        evidence = {"case": name, "observer": "Python audit events only",
                    "events": [], "rejected": False}
        report["preflight_negatives"].append(evidence)
        if path is not None:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as output:
                output.write(content)
            before = path.read_bytes()
            evidence["config_sha256"] = hashlib.sha256(before).hexdigest()
        try:
            os.environ.update(overrides)
            active["events"] = evidence["events"]
            try:
                runtime.environment(project)
            except RustRuntimeError as exc:
                evidence.update(rejected=True, error=str(exc))
            finally:
                active["events"] = None
            if not evidence["rejected"] or evidence["events"]:
                raise ValueError("Hostile preflight did not fail closed: " + name)
            if path is not None and path.read_bytes() != before:
                raise ValueError("Preflight modified hostile fixture config")
            evidence["config_unchanged"] = True
        finally:
            active["events"] = None
            os.environ.clear()
            os.environ.update(old)
            if path is not None:
                path.unlink()
    # Remove only our now-empty fixture directory; do not touch user config.
    (project / ".cargo").rmdir()


def qualify(report: dict, base: Path, data_root: Path, *, allow_network: bool) -> None:
    project = base / "empty-project"
    project.mkdir(mode=0o700)
    with isolated_environment(base, data_root) as removed:
        report["fixture_removed_environment_names"] = removed
        start = monotonic()
        first = install_toolchain(project, network_authorized=allow_network)
        report["installation_seconds"] = monotonic() - start
        report["installation"] = first
        receipt = Path(first["receipt"])
        identity = receipt.stat()
        repeat = install_toolchain(project, network_authorized=False)
        report["offline_repeat"] = repeat
        if not repeat["reuse_receipt"] or not os.path.samestat(identity, receipt.stat()):
            raise ValueError("Offline repeat did not reuse the exact receipt")
        document = load_toolchain_receipt(receipt, store=toolchain_store())
        report["verified_receipt"] = document
        runtime = runtime_from_receipt(receipt, repository=project)
        environment = runtime.environment(project)
        report["version_probes"] = []
        for name in ("cargo", "rustc", "analyzer"):
            tool = getattr(runtime, name)
            probe = {"tool": name, "argv": [str(tool.path), "--version"],
                     "cwd": str(project), "environment": environment,
                     "timeout_seconds": 5, "cleanup_grace_seconds": 10,
                     "sha256": tool.sha256}
            report["version_probes"].append(probe)
            start = monotonic()
            result = run_owned(probe["argv"], cwd=project, env=environment,
                               timeout=5, capture_output=True, text=True)
            probe.update(returncode=result.returncode, stdout=result.stdout,
                         stderr=result.stderr, elapsed_seconds=monotonic() - start)
            result.check_returncode()
            validate_version(name, result.stdout.strip())
        qualify_preflight(report, runtime, project, base)
        if list(project.iterdir()):
            raise ValueError("Version qualification unexpectedly wrote to project")
        report["project_writes"] = []
        report["status"] = "passed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--reuse-data-root", type=Path)
    parser.add_argument("--require-clean-source", action="store_true")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    report = {"schema_version": 1, "stage": "phase2-official-tools-only",
              "source_sha": args.source_sha, "target": args.target,
              "status": "failed", "public_rust_enabled": False,
              "source_identity_scope": ("exact clean tracked checkout" if args.require_clean_source
                                        else "HEAD with local harness; NOT clean candidate proof"),
              "not_proven": ["installed-wheel semantics", "OS-level no-network observation",
                             "prohibited project execution observation", "scanner qualification",
                             "resource gates", "fresh stdio MCP", "external repositories"]}
    # Reserve a new evidence file before any acquisition; never overwrite one.
    with args.output.open("x", encoding="utf-8") as output:
        try:
            validate_identity(source, args.source_sha, args.target, require_clean=args.require_clean_source)
            with tempfile.TemporaryDirectory(prefix="atlas-official-qualification-") as temporary:
                base = Path(temporary).resolve()
                data_root = base / "data"
                if args.reuse_data_root is not None:
                    data_root = args.reuse_data_root.absolute()
                    if (data_root.resolve(strict=True) != data_root or not data_root.is_dir()
                            or data_root.is_relative_to(source)):
                        raise ValueError("Reuse data root must be canonical, existing and outside source")
                qualify(report, base, data_root, allow_network=args.allow_network)
        except Exception as exc:
            report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            json.dump(report, output, indent=2, sort_keys=True)
            output.write("\n")
    print(json.dumps({key: report[key] for key in ("status", "stage", "source_sha", "target")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
