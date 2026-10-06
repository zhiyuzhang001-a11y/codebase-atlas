#!/usr/bin/env python3
"""I0 Linux resource restriction experiment, NEVER Rust product qualification.

Only a fresh owned helper is restricted. No project/tool execution, downloads,
global changes or production provider fallback. Exact argv/env/stdin is unproved.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import socket
import sys
import tempfile

from codebase_atlas.rust_owned_command import run_owned
try:
    from rust_toolchain_qualification import validate_identity
except ModuleNotFoundError:
    from scripts.rust_toolchain_qualification import validate_identity


def supported_machine() -> bool:
    return sys.platform.startswith("linux") and platform.machine().lower() in {
        "x86_64", "aarch64", "arm64"}


def experiment(scratch: Path) -> dict:
    if not supported_machine():
        raise ValueError("Landlock experiment requires native Linux x86_64/ARM64")
    # Fixed Linux syscall numbers for these two native architectures only.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(444, ctypes.c_void_p(), ctypes.c_size_t(0), ctypes.c_uint(1))
    if abi < 4:
        return {"status": "blocked", "process_id": os.getpid(), "abi": abi, "errno": ctypes.get_errno(),
                "reason": "TCP restriction requires Landlock ABI >=4"}

    class Ruleset(ctypes.Structure):
        _fields_ = [("filesystem", ctypes.c_uint64), ("network", ctypes.c_uint64)]
    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    # EXECUTE + WRITE_FILE + READ_FILE + READ_DIR; TCP bind/connect. No other
    # filesystem operation or network/IPC coverage is claimed by this experiment.
    attributes = Ruleset(15, 3)
    descriptor = libc.syscall(444, ctypes.byref(attributes), ctypes.sizeof(attributes), 0)
    if descriptor < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")
    directory = os.open(scratch, os.O_PATH | os.O_CLOEXEC)
    listener = socket.socket()
    try:
        rule = PathRule(14, directory)  # Scratch may be read/written, never executed.
        if libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0) != 0:
            raise OSError(ctypes.get_errno(), "landlock_add_rule")
        # Controlled loopback bind before restriction, never an external endpoint.
        with socket.socket() as control:
            control.bind(("127.0.0.1", 0))
            positive_bind = True
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        address = listener.getsockname()
        with socket.socket() as control:
            control.settimeout(1)
            control.connect(address)
        accepted, _ = listener.accept()
        accepted.close()
        if os.spawnve(os.P_WAIT, "/bin/true", ["/bin/true"], {}) != 0:
            raise RuntimeError("OS executable positive control failed")
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
        if libc.syscall(446, descriptor, 0) != 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    except BaseException:
        listener.close()
        raise
    finally:
        os.close(directory)
        os.close(descriptor)

    # Positive marker under the SAME restricted identity, not outside sandbox.
    marker = scratch / "same-domain-positive"
    marker.write_bytes(b"positive")
    if marker.read_bytes() != b"positive":
        raise RuntimeError("same-domain scratch control failed")
    with socket.socket() as blocked:
        try:
            blocked.bind(("127.0.0.1", 0))
        except OSError as exc:
            bind_errno = exc.errno
        else:
            raise RuntimeError("Landlock failed to deny controlled TCP bind")
    if bind_errno not in {errno.EACCES, errno.EPERM}:
        raise RuntimeError("TCP bind failed for a reason other than policy denial")
    try:
        with socket.socket() as blocked:
            blocked.settimeout(1)
            try:
                blocked.connect(address)
            except OSError as exc:
                connect_errno = exc.errno
            else:
                raise RuntimeError("Landlock failed to deny controlled TCP connect")
        if connect_errno not in {errno.EACCES, errno.EPERM}:
            raise RuntimeError("TCP connect failed for a reason other than policy denial")
    finally:
        listener.close()

    # A forked child inherits the rule before exec. Report exact exec errno via
    # an owned pipe; the target is a harmless OS program, not project code.
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(reader)
        try:
            os.execve("/bin/true", ["/bin/true"], {})
        except OSError as exc:
            os.write(writer, str(exc.errno).encode("ascii"))
            os._exit(0)
        except BaseException:
            os._exit(2)
    os.close(writer)
    try:
        value = os.read(reader, 32)
    finally:
        os.close(reader)
        _, status = os.waitpid(pid, 0)
    if status != 0 or value not in {b"13", b"1"}:
        raise RuntimeError("Inherited executable denial not proven")
    return {"status": "resource_probe_passed", "process_id": os.getpid(), "abi": abi,
            "same_domain_scratch_control": True, "loopback_bind_positive": positive_bind,
            "before_restriction_exec_positive": True,
            "loopback_connect_positive": True, "loopback_connect_deny_errno": connect_errno,
            "loopback_bind_deny_errno": bind_errno, "fork_exec_deny_errno": int(value),
            "child_reaped": True,
            "not_proven": ["exact argv/env/parent/stdin", "official Rust compatibility",
                           "full filesystem operations", "UDP/Unix/IPC/inherited-fd/io_uring",
                           "immutable source/tool views", "other platforms", "product qualification"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha")
    parser.add_argument("--target")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child-scratch", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child_scratch is not None:
        if args.source_sha or args.target or args.output:
            parser.error("child mode cannot accept evidence identity arguments")
        print(json.dumps(experiment(args.child_scratch.resolve(strict=True))))
        return 0
    if not (args.source_sha and args.target and args.output):
        parser.error("source-sha, target and output required")
    validate_identity(Path(__file__).resolve().parents[1], args.source_sha, args.target,
                      require_clean=True)
    report = {"source_sha": args.source_sha, "target": args.target,
              "stage": "I0-resource-experiment", "qualification_status": "blocked",
              "public_rust_enabled": False, "product_enforcement": False}
    with args.output.open("x", encoding="utf-8") as output:
        try:
            if not supported_machine():
                raise ValueError("Not a native Linux experimental target")
            report["experiments"] = []
            for repeat in range(3):
                with tempfile.TemporaryDirectory(prefix="atlas-landlock-i0-") as temporary:
                    result = run_owned([sys.executable, str(Path(__file__).resolve()),
                                        "--child-scratch", temporary], cwd=Path(temporary),
                                       env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
                                       timeout=10, capture_output=True, text=True, check=True)
                    evidence = json.loads(result.stdout)
                    if not isinstance(evidence, dict) or evidence.get("status") not in {
                            "resource_probe_passed", "blocked"}:
                        raise ValueError("Unexpected helper evidence status")
                    report["experiments"].append({"repeat": repeat + 1, **evidence})
            report["collection_status"] = "complete"
        except Exception as exc:
            report["collection_status"] = "failed"
            report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        json.dump(report, output, indent=2, sort_keys=True)
        output.write("\n")
    # Exit0 means evidence collected, including a blocked ABI. NEVER admission.
    return 0 if report["collection_status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
