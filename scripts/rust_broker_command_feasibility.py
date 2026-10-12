"""I0 controller-command construction precursor, NOT a cooperative OS broker.

Own Python fixture only. No restricted requester, authenticated IPC, atomic
disconnect-to-launch boundary, network sandbox or official Rust integration.
Actual output is evidence checked AFTER execution, not execution enforcement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
from time import monotonic, sleep

try:
    import rust_cooperative_broker_protocol as protocol
    from rust_toolchain_qualification import validate_identity
    from rust_windows_child_policy_feasibility import windows_directory
except ModuleNotFoundError:
    from scripts import rust_cooperative_broker_protocol as protocol
    from scripts.rust_toolchain_qualification import validate_identity
    from scripts.rust_windows_child_policy_feasibility import windows_directory


OUTPUT_LIMIT = 8192


def feed_fixed_input(stream, payload: bytes) -> None:
    try:
        if payload != protocol.FIXED_STDIN:
            raise ValueError("Only constant fixture stdin may be sent")
        remaining = memoryview(payload)
        while remaining:
            count = stream.write(remaining)
            if type(count) is not int or count <= 0 or count > len(remaining):
                raise ValueError("Fixture stdin write did not make valid progress")
            remaining = remaining[count:]
    finally:
        stream.close()


def run_fixture(command: protocol.Command) -> dict:
    """Own fixed, short stdin fixture; no general executable/argv interface."""
    expected = protocol.own_fixture_command(
        str(Path(sys.executable).resolve(strict=True)), command.cwd,
        system_root=windows_directory() if os.name == "nt" else None)
    if command != expected:
        raise ValueError("Only controller fixed own-fixture command may run")
    creation_started_at = monotonic()
    deadline = creation_started_at + 5
    # Synchronous native creation is not interruptible by this deadline;
    # measure it, but do not call this an enforced end-to-end hard five seconds.
    if os.name == "nt":
        from codebase_atlas.windows_owned_process import WindowsOwnedProcess
        process = WindowsOwnedProcess(command.argv, cwd=command.cwd, env=dict(command.environment))
    else:
        process = subprocess.Popen(command.argv, cwd=command.cwd, env=dict(command.environment),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
                                   bufsize=0)
    created_at = monotonic()
    buffers = [bytearray(), bytearray()]
    errors = []
    overflow = threading.Event()

    def drain(stream, destination):
        try:
            while True:
                block = stream.read(4096)
                if not block:
                    return
                if len(destination) + len(block) > OUTPUT_LIMIT:
                    overflow.set()
                    return
                destination.extend(block)
        except OSError as exc:
            errors.append(exc)

    def feed():
        try:
            feed_fixed_input(process.stdin, command.stdin)
        except (OSError, ValueError) as exc:
            errors.append(exc)

    threads = [threading.Thread(target=drain, args=(stream, buffer), daemon=True)
               for stream, buffer in zip((process.stdout, process.stderr), buffers)]
    threads.append(threading.Thread(target=feed, daemon=True))
    try:
        for thread in threads:
            thread.start()
        while process.poll() is None:
            if monotonic() >= deadline or overflow.is_set():
                raise TimeoutError("Fixture deadline/output limit")
            sleep(0.005)
        signaled_at = monotonic()
        if signaled_at >= deadline:
            raise TimeoutError("Fixture exceeded deadline")
        code = process.returncode
    finally:
        cleanup_started = monotonic()
        cleanup_deadline = cleanup_started + 10
        try:
            if os.name == "nt":
                process.close_owned_job(max(0, cleanup_deadline - monotonic()))
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=max(0, cleanup_deadline - monotonic()))
            for thread in threads:
                if thread.ident is not None:
                    thread.join(max(0, cleanup_deadline - monotonic()))
            if any(thread.is_alive() for thread in threads):
                raise TimeoutError("Fixture pipes remained live after owned cleanup")
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    if errors or overflow.is_set():
        raise ValueError("Fixture input/output incomplete")
    return {"process_id": process.pid, "returncode": code,
            "stdout": bytes(buffers[0]).decode("utf-8"),
            "stderr": bytes(buffers[1]).decode("utf-8"),
            "owned_cleanup_completed": True,
            "timeline": {"creation_started_at": creation_started_at,
                         "created_at": created_at, "signaled_at": signaled_at,
                         "cleanup_started_at": cleanup_started, "cleanup_completed_at": monotonic()}}


def compare_actual(command: protocol.Command, result: dict, parent_id: int) -> dict:
    if result["returncode"] != 0 or result["stderr"] or not result["owned_cleanup_completed"]:
        raise ValueError("Fixture did not complete cleanly")
    actual = json.loads(result["stdout"])
    expected = {"argv": ["-c", protocol.FIXTURE], "cwd": command.cwd,
                "env": dict(command.environment), "parent_id": parent_id,
                "process_id": result["process_id"],
                "stdin_sha256": hashlib.sha256(command.stdin).hexdigest()}
    if type(actual) is not dict or set(actual) != set(expected):
        raise ValueError("Actual fixture evidence fields invalid")
    # Retain mismatches, NEVER normalize runtime additions into a passed contract.
    matches = {key: actual[key] == value for key, value in expected.items()}
    return {"actual": actual, "expected": expected, "field_matches": matches,
            "command_identity_match": all(matches.values())}


def collect(report: dict) -> None:
    python = str(Path(sys.executable).resolve(strict=True))
    binary_hash = hashlib.sha256(Path(python).read_bytes()).hexdigest()
    for repeat in range(3):
        with tempfile.TemporaryDirectory(prefix="atlas-broker-command-i0-") as temporary:
            command = protocol.own_fixture_command(
                python, str(Path(temporary).resolve(strict=True)),
                system_root=windows_directory() if os.name == "nt" else None)
            request = bytearray(json.dumps({"operation": protocol.OPERATION,
                                           "fixture": protocol.FIXTURE, "sequence": 1}).encode() + b"\n")
            original = bytes(request)
            session = protocol.Session(command)
            approved = session.approve(original)
            # Deterministic controller-side mutation, NOT a cross-process IPC race test.
            request[:] = b'{"operation":"shell","argv":["foreign"],"stdin":"project"}\n'
            claimed = session.claim(approved)
            result = run_fixture(claimed)
            comparison = compare_actual(claimed, result, os.getpid())
            if hashlib.sha256(Path(python).read_bytes()).hexdigest() != binary_hash:
                raise ValueError("Own fixture executable changed during observation")
            report["experiments"].append({"repeat": repeat + 1, **result, **comparison,
                "controller_id": os.getpid(), "request_before": original.decode(),
                "request_after": bytes(request).decode(), "command_argv": list(command.argv),
                "command_environment": dict(command.environment), "command_cwd": command.cwd,
                "fixture_executable_sha256": binary_hash,
                "identity_check_scope": "pre/post bytes only, NOT creation-bound identity"})
    report["collection_status"] = "complete"
    report["command_observation_status"] = (
        "matched" if all(item["command_identity_match"] for item in report["experiments"])
        else "mismatch")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    validate_identity(Path(__file__).resolve().parents[1], args.source_sha, args.target, require_clean=True)
    report = {"source_sha": args.source_sha, "target": args.target,
              "stage": "I0-controller-command-construction-precursor",
              "qualification_status": "blocked", "product_enforcement": False,
              "public_rust_enabled": False, "collection_status": "incomplete", "experiments": [],
              "not_proven": ["restricted requester and native authenticated IPC",
                             "atomic disconnect/launch cancellation", "creation-bound file identity",
                             "full native argv including -I and program bytes",
                             "hard interruptible native creation deadline",
                             "native output/timeout/reader/cleanup failure paths",
                             "network/filesystem sandbox", "all native execution control",
                             "unchanged official Rust tool compatibility", "full I0/product qualification"]}
    with args.output.open("x", encoding="utf-8") as output:
        try:
            collect(report)
            code = 0
        except Exception as exc:
            report["error"] = {"type": type(exc).__name__, "message": str(exc)}
            code = 1
        json.dump(report, output, indent=2, sort_keys=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
