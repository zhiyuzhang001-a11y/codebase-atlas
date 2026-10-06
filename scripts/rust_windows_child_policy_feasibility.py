#!/usr/bin/env python3
"""I0 own-fixture Windows deny-all experiment; NEVER Rust qualification.

No AppContainer profile, privileged service, project execution or product change.
The outer controller is in run_owned's kill-on-close Job. Fixture workers inherit
that Job and receive child policy atomically at CreateProcessW, not after startup.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from codebase_atlas.rust_owned_command import run_owned
try:
    from rust_toolchain_qualification import validate_identity
except ModuleNotFoundError:
    from scripts.rust_toolchain_qualification import validate_identity


def supported_machine() -> bool:
    return os.name == "nt"


def windows_directory() -> str:
    """Read the OS directory through the native API, not parent environment."""
    if not supported_machine():
        raise ValueError("Native Windows required")
    native = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
    function = native.GetWindowsDirectoryW
    function.argtypes, function.restype = [W.LPWSTR, W.UINT], W.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise RuntimeError("Windows directory unavailable or truncated")
    return buffer.value


def controller_environment() -> dict[str, str]:
    # Supply a minimal OS loader environment for CREATE_NO_WINDOW fixtures.
    # Whether it resolves the observed DLL-init failure requires native evidence.
    # Both controls receive the same root; never copy PATH/tokens/wrappers.
    return {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "SystemRoot": windows_directory()}


def fixture(scratch: Path, restricted: bool) -> dict:
    """Same executable/identity/scratch and payload for both controls."""
    if not supported_machine():
        raise ValueError("Native Windows required")
    positive = scratch / "same-domain-positive"
    positive.write_bytes(b"positive")
    if positive.read_bytes() != b"positive":
        raise RuntimeError("Same-domain scratch control failed")
    attempts = []

    def attempt(name):
        marker = scratch / name
        if marker.exists():
            raise RuntimeError("Marker must initially be absent")
        command = [sys.executable, "-I", "-c",
                   "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'executed')",
                   str(marker)]
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=3, check=True)
            evidence = {"name": name, "returncode": result.returncode, "winerror": None}
        except OSError as exc:
            evidence = {"name": name, "returncode": None, "winerror": exc.winerror}
        evidence["marker_executed"] = marker.exists() and marker.read_bytes() == b"executed"
        if restricted:
            # WinSDK winerror.h: ERROR_CHILD_PROCESS_BLOCKED, not arbitrary failure.
            if evidence["winerror"] != 367 or marker.exists():
                raise RuntimeError(f"Exact child-policy denial not proven: {evidence}")
        elif evidence["returncode"] != 0 or not evidence["marker_executed"]:
            raise RuntimeError(f"Unrestricted execution control failed: {evidence}")
        attempts.append(evidence)

    attempt("direct-child")
    errors = []

    def background():
        try:
            attempt("background-child")
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=background)
    thread.start()
    thread.join(4)
    if thread.is_alive():
        raise TimeoutError("Background attempt did not finish")
    if errors:
        raise errors[0]
    return {"process_id": os.getpid(), "restricted": restricted,
            "same_domain_scratch_control": True, "attempts": attempts}


def launch_fixture(scratch: Path, restricted: bool) -> dict:
    """Experimental native launcher only; no production launcher modifications."""
    if not supported_machine():
        raise ValueError("Native Windows required")
    native = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
    pointer, size = ctypes.c_void_p, ctypes.c_size_t

    def api(name, arguments, result=W.BOOL):
        function = getattr(native, name)
        function.argtypes, function.restype = arguments, result
        return function

    current = api("GetCurrentProcess", [], W.HANDLE)
    in_job = api("IsProcessInJob", [W.HANDLE, W.HANDLE, ctypes.POINTER(W.BOOL)])
    job_member = W.BOOL()
    if not in_job(current(), None, ctypes.byref(job_member)) or not job_member.value:
        raise RuntimeError("Controller must already belong to the owned Job")
    initialize = api("InitializeProcThreadAttributeList", [pointer, W.DWORD, W.DWORD, pointer])
    update = api("UpdateProcThreadAttribute", [pointer, W.DWORD, size, pointer, size, pointer, pointer])
    delete = api("DeleteProcThreadAttributeList", [pointer], None)
    create = api("CreateProcessW", [W.LPCWSTR, W.LPWSTR, pointer, pointer, W.BOOL,
                                    W.DWORD, pointer, W.LPCWSTR, pointer, pointer])
    wait = api("WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD)
    exit_code = api("GetExitCodeProcess", [W.HANDLE, ctypes.POINTER(W.DWORD)])
    terminate = api("TerminateProcess", [W.HANDLE, W.UINT])
    close = api("CloseHandle", [W.HANDLE])

    class Startup(ctypes.Structure):
        _fields_ = [("cb", W.DWORD), ("reserved", W.LPWSTR), ("desktop", W.LPWSTR),
                    ("title", W.LPWSTR), ("x", W.DWORD), ("y", W.DWORD),
                    ("width", W.DWORD), ("height", W.DWORD), ("xchars", W.DWORD),
                    ("ychars", W.DWORD), ("fill", W.DWORD), ("flags", W.DWORD),
                    ("show", W.WORD), ("reserved_size", W.WORD), ("reserved_bytes", pointer),
                    ("stdin", W.HANDLE), ("stdout", W.HANDLE), ("stderr", W.HANDLE)]

    class StartupEx(ctypes.Structure):
        _fields_ = [("startup", Startup), ("attributes", pointer)]

    class Process(ctypes.Structure):
        _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE),
                    ("pid", W.DWORD), ("tid", W.DWORD)]

    length = size()
    initialize(None, 1, 0, ctypes.byref(length))
    if not 0 < length.value <= 65536:
        raise RuntimeError("Invalid attribute buffer size")
    buffer = ctypes.create_string_buffer(length.value)
    if not initialize(buffer, 1, 0, ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())
    process = Process()
    try:
        policy = W.DWORD(1 if restricted else 0)
        # WinSDK WinBase.h: input attribute number14 (0x20000 |14), DWORD policy.
        if not update(buffer, 0, 0x2000E, ctypes.byref(policy), ctypes.sizeof(policy), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        startup = StartupEx()
        startup.startup.cb = ctypes.sizeof(startup)
        startup.attributes = ctypes.cast(buffer, pointer)
        output = scratch / "fixture.json"
        args = [sys.executable, str(Path(__file__).resolve()), "--fixture", str(scratch),
                "--restricted" if restricted else "--unrestricted"]
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
        # No console or inherited handles. Environment is the explicit owned
        # controller environment; no BREAKAWAY flag, so the owned Job is inherited.
        if not create(args[0], command, None, None, False, 0x80000 | 0x08000000,
                      None, str(scratch), ctypes.byref(startup), ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        if wait(process.process, 8000) != 0:
            raise TimeoutError("Fixture process did not finish")
        code = W.DWORD()
        if not exit_code(process.process, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if code.value != 0:
            if output.is_file():
                detail = output.read_text(encoding="utf-8")[:8192]
                raise RuntimeError(f"Fixture failed, exit code {code.value}: {detail}")
            raise RuntimeError(f"Fixture failed, restricted={restricted}, "
                               f"exit code {code.value} (0x{code.value:08X})")
        evidence = json.loads(output.read_text(encoding="utf-8"))
        if evidence.get("process_id") != process.pid or evidence.get("restricted") != restricted:
            raise ValueError("Fixture identity mismatch")
        return {**evidence, "policy_at_creation": policy.value, "fixture_reaped": True}
    finally:
        if process.process:
            terminate(process.process, 1)  # Only our handle; outer Job owns descendants.
            wait(process.process, 1000)
            close(process.process)
        if process.thread:
            close(process.thread)
        delete(buffer)


def controller(scratch: Path) -> dict:
    results = []
    for restricted in (False, True):
        directory = scratch / ("restricted" if restricted else "baseline")
        directory.mkdir()
        results.append(launch_fixture(directory, restricted))
    return {"status": "deny_all_probe_passed", "process_id": os.getpid(), "fixtures": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha")
    parser.add_argument("--target")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--controller", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--restricted", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--unrestricted", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.controller or args.fixture:
        if args.source_sha or args.target or args.output or (args.controller and args.fixture):
            parser.error("Internal modes cannot accept evidence arguments")
        if args.fixture:
            if not (args.restricted or args.unrestricted):
                parser.error("Fixture policy required")
            try:
                evidence = fixture(args.fixture.resolve(strict=True), args.restricted)
            except Exception as exc:
                # No inherited stderr handle: retain the owned fixture failure
                # explicitly, without converting it to a passed policy result.
                with (args.fixture / "fixture.json").open("x", encoding="utf-8") as output:
                    json.dump({"status": "failed", "type": type(exc).__name__,
                               "message": str(exc), "winerror": getattr(exc, "winerror", None)}, output)
                return 1
            with (args.fixture / "fixture.json").open("x", encoding="utf-8") as output:
                json.dump(evidence, output)
        else:
            print(json.dumps(controller(args.controller.resolve(strict=True))))
        return 0
    if not (args.source_sha and args.target and args.output):
        parser.error("source-sha, target and output required")
    validate_identity(Path(__file__).resolve().parents[1], args.source_sha, args.target,
                      require_clean=True)
    report = {"source_sha": args.source_sha, "target": args.target,
              "stage": "I0-Windows-deny-all-experiment", "qualification_status": "blocked",
              "public_rust_enabled": False, "product_enforcement": False,
              "not_proven": ["selective exact argv/env/parent/stdin enforcement",
                             "official Rust compatibility", "network/filesystem isolation",
                             "parent-exit/controller-disconnect adversarial cases",
                             "immutable source/tool views", "product qualification"],
              "experiments": []}
    with args.output.open("x", encoding="utf-8") as output:
        try:
            if not supported_machine():
                raise ValueError("Native Windows required")
            for repeat in range(3):
                with tempfile.TemporaryDirectory(prefix="atlas-win-policy-i0-") as temporary:
                    result = run_owned([sys.executable, str(Path(__file__).resolve()),
                                        "--controller", temporary], cwd=temporary,
                                       env=controller_environment(),
                                       timeout=20, capture_output=True, text=True, check=True)
                    evidence = json.loads(result.stdout)
                    if evidence.get("status") != "deny_all_probe_passed":
                        raise ValueError("Unexpected controller evidence")
                    report["experiments"].append({"repeat": repeat + 1, **evidence,
                                                  "owned_job_cleanup_completed": True})
            report["collection_status"] = "complete"
        except Exception as exc:
            report["collection_status"] = "failed"
            report["error"] = {"type": type(exc).__name__, "message": str(exc)}
            if isinstance(exc, subprocess.CalledProcessError):
                # Captured owned-fixture output only; no parent environment or
                # token dump. Keep bounded diagnostics instead of losing cause.
                report["error"]["controller_stdout"] = (exc.stdout or "")[:8192]
                report["error"]["controller_stderr"] = (exc.stderr or "")[:8192]
        json.dump(report, output, indent=2, sort_keys=True)
        output.write("\n")
    return 0 if report["collection_status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
