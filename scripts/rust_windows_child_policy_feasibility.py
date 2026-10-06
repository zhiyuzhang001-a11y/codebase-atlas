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
from time import monotonic, sleep

from codebase_atlas.rust_owned_command import run_owned
try:
    from rust_toolchain_qualification import validate_identity
except ModuleNotFoundError:
    from scripts.rust_toolchain_qualification import validate_identity


FIXTURE_CREATION_FLAGS = 0x80000 | 0x8  # Extended startup + DETACHED_PROCESS.


def await_start_barrier(scratch: Path, timeout: float = 5) -> None:
    """Owned fixture only: do not execute the payload before native membership check."""
    deadline = monotonic() + timeout
    barrier = scratch / "membership-checked"
    while not barrier.exists():
        if monotonic() >= deadline:
            raise TimeoutError("Exact Job membership barrier was not released")
        sleep(0.01)
    if barrier.read_bytes() != b"exact-job-checked":
        raise ValueError("Invalid owned start barrier")


def publish_control(path: Path, content: bytes) -> None:
    """Own temporary IPC: readers never observe a partially written record."""
    staging = path.with_name(path.name + ".staging")
    with staging.open("xb") as output:
        output.write(content)
    if path.exists():
        raise ValueError("Control record already exists")
    staging.rename(path)


def await_control(path: Path, timeout: float = 5) -> dict:
    deadline = monotonic() + timeout
    while not path.exists():
        if monotonic() >= deadline:
            raise TimeoutError("Owned control record missing")
        sleep(0.01)
    return json.loads(path.read_text(encoding="utf-8"))


def native_function(native, name, arguments, result=W.BOOL):
    function = getattr(native, name)
    function.argtypes, function.restype = arguments, result
    return function


def duplicate_to(native, source_handle, target_process) -> int:
    current = native_function(native, "GetCurrentProcess", [], W.HANDLE)
    duplicate = native_function(native, "DuplicateHandle", [
        W.HANDLE, W.HANDLE, W.HANDLE, ctypes.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD])
    result = W.HANDLE()
    if not duplicate(current(), source_handle, target_process, ctypes.byref(result),
                     0, False, 2):  # SAME_ACCESS, non-inheritable; owned fixture only.
        raise ctypes.WinError(ctypes.get_last_error())
    return result.value


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
    # Supply a minimal OS loader environment for detached console fixtures.
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


def launch_fixture(scratch: Path, restricted: bool, *, handoff=None) -> dict:
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
    create_job = api("CreateJobObjectW", [pointer, W.LPCWSTR], W.HANDLE)
    set_job = api("SetInformationJobObject", [W.HANDLE, ctypes.c_int, pointer, W.DWORD])
    kill_job = api("TerminateJobObject", [W.HANDLE, W.UINT])

    class BasicLimit(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", W.DWORD), ("minimum", size), ("maximum", size),
                    ("active_limit", W.DWORD), ("affinity", size), ("priority", W.DWORD),
                    ("scheduling", W.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [("basic", BasicLimit), ("io", IoCounters), ("process_memory", size),
                    ("job_memory", size), ("peak_process", size), ("peak_job", size)]

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
    initialize(None, 2, 0, ctypes.byref(length))
    if not 0 < length.value <= 65536:
        raise RuntimeError("Invalid attribute buffer size")
    buffer = ctypes.create_string_buffer(length.value)
    if not initialize(buffer, 2, 0, ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())
    process = Process()
    owned_job = None
    handed_off = False
    remote_worker = None
    try:
        # A private unnamed nested Job provides an exact handle, not "any Job".
        # No inherited Job handle and no breakaway; outer run_owned remains owner.
        owned_job = handoff["job_handle"] if handoff else create_job(None, None)
        if not owned_job:
            raise ctypes.WinError(ctypes.get_last_error())
        limit = ExtendedLimit()
        limit.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE only.
        if not handoff and not set_job(owned_job, 9, ctypes.byref(limit), ctypes.sizeof(limit)):
            raise ctypes.WinError(ctypes.get_last_error())
        jobs = (W.HANDLE * 1)(owned_job)
        if not update(buffer, 0, 0x2000D, jobs, ctypes.sizeof(jobs), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
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
        if not create(args[0], command, None, None, False, FIXTURE_CREATION_FLAGS,
                      None, str(scratch), ctypes.byref(startup), ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        exact_member = W.BOOL()
        if not in_job(process.process, owned_job, ctypes.byref(exact_member)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not exact_member.value:
            raise RuntimeError("Fixture does not belong to the exact owned Job")
        if handoff:
            # Retain the actual worker object in the live supervisor, not a PID
            # reopened after parent exit. Do NOT release the payload barrier here.
            remote_worker = duplicate_to(native, process.process, handoff["observer_handle"])
            evidence = {"worker_handle": remote_worker, "process_id": process.pid,
                        "launcher_id": os.getpid(), "restricted": restricted,
                        "policy_at_creation": policy.value,
                        "creation_flags": FIXTURE_CREATION_FLAGS}
            publish_control(scratch / "handoff.json", json.dumps(evidence).encode("utf-8"))
            handed_off = True
            return evidence
        publish_control(scratch / "membership-checked", b"exact-job-checked")
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
        return {**evidence, "policy_at_creation": policy.value, "fixture_reaped": True,
                "creation_flags": FIXTURE_CREATION_FLAGS,
                "exact_owned_job_member_before_payload": True,
                "job_assigned_at_creation": True, "job_handle_inherited": False}
    finally:
        if owned_job and not handed_off:
            kill_job(owned_job, 1)
        if process.process:
            if not handed_off:
                terminate(process.process, 1)  # Only our handle; outer Job owns descendants.
                wait(process.process, 1000)
            close(process.process)
        if process.thread:
            close(process.thread)
        if owned_job:
            close(owned_job)
        if handoff:
            if remote_worker and not handed_off:
                duplicate = native_function(native, "DuplicateHandle", [
                    W.HANDLE, W.HANDLE, W.HANDLE, ctypes.POINTER(W.HANDLE),
                    W.DWORD, W.BOOL, W.DWORD])
                duplicate(handoff["observer_handle"], remote_worker, None, None, 0, False, 1)
            close(handoff["observer_handle"])
        delete(buffer)


def parent_exit_experiment(scratch: Path, restricted: bool) -> dict:
    """Own launcher exits; live supervisor retains Job and exact worker handle."""
    if not supported_machine():
        raise ValueError("Native Windows required")
    from codebase_atlas.windows_owned_process import WindowsOwnedProcess
    native = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
    current = native_function(native, "GetCurrentProcess", [], W.HANDLE)
    member = native_function(native, "IsProcessInJob", [
        W.HANDLE, W.HANDLE, ctypes.POINTER(W.BOOL)])
    get_pid = native_function(native, "GetProcessId", [W.HANDLE], W.DWORD)
    wait = native_function(native, "WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD)
    exit_code = native_function(native, "GetExitCodeProcess", [W.HANDLE, ctypes.POINTER(W.DWORD)])
    close = native_function(native, "CloseHandle", [W.HANDLE])
    launcher = WindowsOwnedProcess([
        sys.executable, str(Path(__file__).resolve()), "--handoff-launcher", str(scratch),
        "--restricted" if restricted else "--unrestricted"],
        cwd=scratch, env=controller_environment())
    worker = None
    evidence = None
    try:
        launcher.stdin.close()
        job_in_launcher = duplicate_to(native, launcher._job, launcher._process)
        observer_in_launcher = duplicate_to(native, current(), launcher._process)
        publish_control(scratch / "launcher-control.json", json.dumps({
            "job_handle": job_in_launcher, "observer_handle": observer_in_launcher
        }).encode("utf-8"))
        if launcher.wait(timeout=8) != 0:
            raise RuntimeError("Owned handoff launcher failed: " +
                               launcher.stderr.read(8192).decode("utf-8", errors="replace"))
        handoff = await_control(scratch / "handoff.json")
        worker = handoff["worker_handle"]
        if (handoff["launcher_id"] != launcher.pid or handoff["restricted"] != restricted
                or get_pid(worker) != handoff["process_id"]):
            raise ValueError("Parent-exit worker/launcher identity mismatch")
        exact_member = W.BOOL()
        if not member(worker, launcher._job, ctypes.byref(exact_member)) or not exact_member.value:
            raise RuntimeError("Worker is not in the supervisor's exact Job after parent exit")
        if wait(worker, 0) != 258:
            raise RuntimeError("Worker did not remain alive at the parent-exit barrier")
        publish_control(scratch / "membership-checked", b"exact-job-checked")
        if wait(worker, 8000) != 0:
            raise TimeoutError("Parent-exit fixture did not finish")
        code = W.DWORD()
        if not exit_code(worker, ctypes.byref(code)) or code.value != 0:
            raise RuntimeError("Parent-exit fixture failed")
        result = await_control(scratch / "fixture.json")
        if result.get("process_id") != handoff["process_id"] or result.get("restricted") != restricted:
            raise ValueError("Parent-exit fixture result identity mismatch")
        evidence = {**result, "launcher_id": launcher.pid,
                    "parent_exit_before_payload": True, "parent_exit_code": 0,
                    "worker_alive_after_parent_exit": True,
                    "exact_supervisor_job_member_after_parent_exit": True,
                    "policy_at_creation": handoff["policy_at_creation"],
                    "creation_flags": handoff["creation_flags"], "fixture_reaped": True,
                    "case": "launcher-exit-holder-alive"}
    finally:
        try:
            launcher.close_owned_job(10)
        finally:
            if worker:
                close(worker)
            for stream in (launcher.stdin, launcher.stdout, launcher.stderr):
                stream.close()
    return {**evidence, "owned_job_cleanup_completed": True}


def controller(scratch: Path) -> dict:
    results = []
    for restricted in (False, True):
        directory = scratch / ("restricted" if restricted else "baseline")
        directory.mkdir()
        try:
            results.append(launch_fixture(directory, restricted))
        except Exception as exc:
            return {"status": "failed", "process_id": os.getpid(), "fixtures": results,
                    "failed_restricted": restricted,
                    "error": {"type": type(exc).__name__, "message": str(exc)}}
    return {"status": "deny_all_probe_passed", "process_id": os.getpid(), "fixtures": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha")
    parser.add_argument("--target")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--controller", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--handoff-launcher", type=Path, help=argparse.SUPPRESS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--restricted", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--unrestricted", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.controller or args.fixture or args.handoff_launcher:
        if (args.source_sha or args.target or args.output
                or sum(bool(item) for item in (args.controller, args.fixture, args.handoff_launcher)) != 1):
            parser.error("Internal modes cannot accept evidence arguments")
        if args.handoff_launcher:
            if not (args.restricted or args.unrestricted):
                parser.error("Fixture policy required")
            scratch = args.handoff_launcher.resolve(strict=True)
            launch_fixture(scratch, args.restricted,
                           handoff=await_control(scratch / "launcher-control.json"))
            return 0
        if args.fixture:
            if not (args.restricted or args.unrestricted):
                parser.error("Fixture policy required")
            try:
                await_start_barrier(args.fixture.resolve(strict=True))
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
            evidence = controller(args.controller.resolve(strict=True))
            print(json.dumps(evidence))
            return 0 if evidence["status"] == "deny_all_probe_passed" else 1
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
                             "full parent-exit/controller-disconnect adversarial coverage",
                             "immutable source/tool views", "product qualification"],
              "experiments": [], "parent_exit_experiments": []}
    with args.output.open("x", encoding="utf-8") as output:
        try:
            if not supported_machine():
                raise ValueError("Native Windows required")
            for repeat in range(3):
                with tempfile.TemporaryDirectory(prefix="atlas-win-policy-i0-") as temporary:
                    result = run_owned([sys.executable, str(Path(__file__).resolve()),
                                        "--controller", temporary], cwd=temporary,
                                       env=controller_environment(),
                                       timeout=20, capture_output=True, text=True, check=False)
                    evidence = json.loads(result.stdout)
                    if evidence.get("status") not in {"deny_all_probe_passed", "failed"}:
                        raise ValueError("Unexpected controller evidence")
                    report["experiments"].append({"repeat": repeat + 1, **evidence,
                                                  "owned_job_cleanup_completed": True})
                    if result.returncode != 0 or evidence["status"] != "deny_all_probe_passed":
                        raise RuntimeError("Controller experiment failed; partial fixtures retained")
            for repeat in range(3):
                for restricted in (False, True):
                    with tempfile.TemporaryDirectory(prefix="atlas-win-parent-exit-i0-") as temporary:
                        evidence = parent_exit_experiment(Path(temporary), restricted)
                        report["parent_exit_experiments"].append({"repeat": repeat + 1, **evidence})
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
