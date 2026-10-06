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
import math
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


def await_start_barrier(scratch: Path, timeout: float = 5, *, deadline=None) -> None:
    """Owned fixture only: do not execute the payload before native membership check."""
    deadline = monotonic() + timeout if deadline is None else deadline
    barrier = scratch / "membership-checked"
    while not barrier.exists():
        if monotonic() >= deadline:
            raise TimeoutError("Exact Job membership barrier was not released")
        sleep(0.01)
    if monotonic() >= deadline:
        raise TimeoutError("Exact Job membership barrier arrived after deadline")
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


def duplicate_to(native, source_handle, target_process, *, desired_access=None) -> int:
    current = native_function(native, "GetCurrentProcess", [], W.HANDLE)
    duplicate = native_function(native, "DuplicateHandle", [
        W.HANDLE, W.HANDLE, W.HANDLE, ctypes.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD])
    result = W.HANDLE()
    if not duplicate(current(), source_handle, target_process, ctypes.byref(result),
                     0 if desired_access is None else desired_access, False,
                     2 if desired_access is None else 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return result.value


def sanitized_control(record: dict) -> dict:
    """Retain own fixture exchanges without transferable native handle values."""
    return {key: value for key, value in record.items()
            if key not in {"worker_handle", "observer_handle", "job_handle"}}


def armed_remaining(armed: dict, now: float, minimum: float) -> float:
    start, deadline = armed.get("armed_at"), armed.get("barrier_deadline")
    if (type(start) not in (float, int) or type(deadline) not in (float, int)
            or not math.isfinite(start) or not math.isfinite(deadline)
            or deadline != start + 5 or now < start or deadline - now <= minimum):
        raise ValueError("Worker barrier lifetime/remaining deadline not proven")
    return deadline - now


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
        holder_loss = bool(handoff and handoff.get("holder_loss"))
        new_job = not handoff or holder_loss
        owned_job = create_job(None, None) if new_job else handoff["job_handle"]
        if not owned_job:
            raise ctypes.WinError(ctypes.get_last_error())
        limit = ExtendedLimit()
        limit.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE only.
        if new_job and not set_job(owned_job, 9, ctypes.byref(limit), ctypes.sizeof(limit)):
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
        if holder_loss:
            args.append("--disconnect-worker")
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
                        "creation_flags": FIXTURE_CREATION_FLAGS,
                        "exact_owned_job_member_before_payload": True,
                        "tested_job_created_by_holder": holder_loss,
                        "tested_job_handle_transferred": False,
                        "exact_job_checked_at": monotonic()}
            publish_control(scratch / "handoff.json", json.dumps(evidence).encode("utf-8"))
            handed_off = True
            if holder_loss:
                # Keep the sole tested-Job handle here. Forced process termination
                # must close it via the OS, NOT an explicit cleanup call.
                finish = await_control(scratch / "holder-finish.json", timeout=15)
                if finish != {"worker_finished": True}:
                    raise ValueError("Invalid holder completion control")
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
        observer_in_launcher = duplicate_to(native, current(), launcher._process, desired_access=0x40)
        publish_control(scratch / "launcher-control.json", json.dumps({
            "job_handle": job_in_launcher, "observer_handle": observer_in_launcher,
            "observer_process_access": 0x40
        }).encode("utf-8"))
        if launcher.wait(timeout=8) != 0:
            raise RuntimeError("Owned handoff launcher failed: " +
                               launcher.stderr.read(8192).decode("utf-8", errors="replace"))
        parent_exited_at = monotonic()
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
        released_at = monotonic()
        if wait(worker, 8000) != 0:
            raise TimeoutError("Parent-exit fixture did not finish")
        signaled_at = monotonic()
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
                    "case": "launcher-exit-holder-alive",
                    "control_receipts": {"handoff": sanitized_control(handoff),
                                         "observer_process_access": 0x40},
                    "timeline": {"parent_exited_at": parent_exited_at,
                                 "barrier_released_at": released_at, "worker_signaled_at": signaled_at}}
    finally:
        try:
            if evidence is not None:
                evidence["timeline"]["outer_cleanup_started_at"] = monotonic()
            launcher.close_owned_job(10)
            if evidence is not None:
                evidence["timeline"]["outer_cleanup_completed_at"] = monotonic()
        finally:
            if worker:
                close(worker)
            for stream in (launcher.stdin, launcher.stdout, launcher.stderr):
                stream.close()
    return {**evidence, "owned_job_cleanup_completed": True}


def disconnect_worker(scratch: Path, restricted: bool) -> dict:
    """Arm in the same token/scratch, then block until observer releases payload."""
    positive = scratch / "armed-positive"
    positive.write_bytes(b"armed-positive")
    if positive.read_bytes() != b"armed-positive":
        raise RuntimeError("Disconnect scratch positive failed")
    armed_at = monotonic()
    deadline = armed_at + 5
    publish_control(scratch / "worker-armed.json", json.dumps({
        "process_id": os.getpid(), "restricted": restricted,
        "same_domain_armed_positive": True,
        "armed_at": armed_at, "barrier_deadline": deadline}).encode("utf-8"))
    await_start_barrier(scratch, deadline=deadline)
    (scratch / "after-disconnect-barrier").write_bytes(b"executed-after-barrier")
    return {**fixture(scratch, restricted), "after_barrier_executed": True}


def holder_loss_experiment(scratch: Path, restricted: bool, crash: bool) -> dict:
    """Outside observer owns worker PROCESS handle, never the tested Job handle.

    A distinct outer safety Job stays alive until verification ends. Crash uses
    TerminateProcess(holder), never TerminateJobObject or close_owned_job first.
    """
    if not supported_machine():
        raise ValueError("Native Windows required")
    from codebase_atlas.windows_owned_process import WindowsOwnedProcess
    native = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
    current = native_function(native, "GetCurrentProcess", [], W.HANDLE)
    get_pid = native_function(native, "GetProcessId", [W.HANDLE], W.DWORD)
    wait = native_function(native, "WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD)
    exit_code = native_function(native, "GetExitCodeProcess", [W.HANDLE, ctypes.POINTER(W.DWORD)])
    terminate = native_function(native, "TerminateProcess", [W.HANDLE, W.UINT])
    close = native_function(native, "CloseHandle", [W.HANDLE])
    holder = WindowsOwnedProcess([
        sys.executable, str(Path(__file__).resolve()), "--loss-holder", str(scratch),
        "--restricted" if restricted else "--unrestricted"],
        cwd=scratch, env=controller_environment())
    worker = None
    evidence = None
    try:
        holder.stdin.close()
        observer_in_holder = duplicate_to(native, current(), holder._process, desired_access=0x40)
        publish_control(scratch / "launcher-control.json", json.dumps({
            "observer_handle": observer_in_holder, "holder_loss": True,
            "observer_process_access": 0x40
        }).encode("utf-8"))  # Deliberately NO Job handle in either direction.
        handoff = await_control(scratch / "handoff.json")
        worker = handoff["worker_handle"]
        if (handoff["launcher_id"] != holder.pid or get_pid(worker) != handoff["process_id"]
                or handoff["restricted"] != restricted
                or not handoff["exact_owned_job_member_before_payload"]
                or not handoff["tested_job_created_by_holder"]
                or handoff["tested_job_handle_transferred"]):
            raise ValueError("Holder/worker/tested-Job identity mismatch")
        armed = await_control(scratch / "worker-armed.json")
        armed_observed_at = monotonic()
        remaining = armed_remaining(armed, armed_observed_at, 3 if crash else 0)
        if (armed.get("process_id") != handoff["process_id"]
                or armed.get("restricted") != restricted
                or armed.get("same_domain_armed_positive") is not True
                or (scratch / "armed-positive").read_bytes() != b"armed-positive"
                or wait(worker, 0) != 258 or holder.poll() is not None
                or (scratch / "membership-checked").exists()
                or (scratch / "after-disconnect-barrier").exists()):
            raise RuntimeError("Live holder/worker armed barrier not proven")
        if crash:
            started = monotonic()
            armed_remaining(armed, started, 3)
            if not terminate(holder._process, 93):
                raise ctypes.WinError(ctypes.get_last_error())
            if holder.wait(timeout=3) != 93:
                raise RuntimeError("Holder did not exit by the owned process termination")
            if wait(worker, 3000) != 0:
                raise TimeoutError("Last-handle loss did not terminate the worker")
            signaled_at = monotonic()
            elapsed = signaled_at - started
            if elapsed > 3:
                raise TimeoutError("Holder-loss termination exceeded three-second budget")
            code = W.DWORD()
            if not exit_code(worker, ctypes.byref(code)) or code.value == 259:
                raise RuntimeError("Worker termination code unavailable")
            # Worker is already signaled before release; no sampling/sleep-only
            # absence claim and no cleanup call has touched the outer safety Job.
            publish_control(scratch / "membership-checked", b"exact-job-checked")
            released_at = monotonic()
            if (scratch / "after-disconnect-barrier").exists():
                raise RuntimeError("Worker executed past disconnect barrier")
            evidence = {"worker_exit_code": code.value, "holder_exit_code": 93,
                        "worker_signaled_before_barrier_release": True,
                        "termination_seconds": elapsed, "after_barrier_executed": False}
        else:
            publish_control(scratch / "membership-checked", b"exact-job-checked")
            released_at = monotonic()
            if wait(worker, 8000) != 0:
                raise TimeoutError("Live-holder positive control did not finish")
            signaled_at = monotonic()
            code = W.DWORD()
            if not exit_code(worker, ctypes.byref(code)) or code.value != 0:
                raise RuntimeError("Live-holder worker positive control failed")
            result = await_control(scratch / "fixture.json")
            if (result.get("process_id") != handoff["process_id"]
                    or result.get("restricted") != restricted
                    or result.get("after_barrier_executed") is not True
                    or (scratch / "after-disconnect-barrier").read_bytes() != b"executed-after-barrier"):
                raise ValueError("Live-holder barrier observer control failed")
            publish_control(scratch / "holder-finish.json", b'{"worker_finished":true}')
            if holder.wait(timeout=3) != 0:
                raise RuntimeError("Live holder did not finish normally")
            evidence = {**result, "worker_exit_code": 0, "holder_exit_code": 0}
        evidence.update({"case": "last-holder-crash" if crash else "live-holder-control",
                         "restricted": restricted, "process_id": handoff["process_id"],
                         "holder_id": holder.pid, "same_domain_armed_positive": True,
                         "exact_holder_job_member_before_payload": True,
                         "observer_has_tested_job_handle": False,
                         "worker_has_tested_job_handle": False,
                         "policy_at_creation": handoff["policy_at_creation"],
                         "outer_cleanup_only_after_worker_signaled": True,
                         "fixture_reaped": True,
                         "control_receipts": {"handoff": sanitized_control(handoff),
                                              "armed": sanitized_control(armed),
                                              "observer_process_access": 0x40},
                         "barrier_remaining_at_observation": remaining,
                         "timeline": {"armed_observed_at": armed_observed_at,
                                      "worker_signaled_at": signaled_at,
                                      "barrier_released_at": released_at}})
        if crash:
            evidence["timeline"]["holder_termination_requested_at"] = started
    except Exception as exc:
        code = holder.poll()
        if code is not None:
            detail = holder.stderr.read(8192).decode("utf-8", errors="replace")
            raise RuntimeError(f"Holder-loss fixture failed: {exc}; holder exit={code}; {detail}") from exc
        raise
    finally:
        try:
            if evidence is not None:
                evidence["timeline"]["outer_cleanup_started_at"] = monotonic()
            holder.close_owned_job(10)
            if evidence is not None:
                evidence["timeline"]["outer_cleanup_completed_at"] = monotonic()
        finally:
            if worker:
                close(worker)
            for stream in (holder.stdin, holder.stdout, holder.stderr):
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
    parser.add_argument("--loss-holder", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--disconnect-worker", action="store_true", help=argparse.SUPPRESS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--restricted", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--unrestricted", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.disconnect_worker and not args.fixture:
        parser.error("Disconnect worker requires fixture mode")
    if args.controller or args.fixture or args.handoff_launcher or args.loss_holder:
        if (args.source_sha or args.target or args.output
                or sum(bool(item) for item in (args.controller, args.fixture,
                                              args.handoff_launcher, args.loss_holder)) != 1):
            parser.error("Internal modes cannot accept evidence arguments")
        if args.handoff_launcher or args.loss_holder:
            if not (args.restricted or args.unrestricted):
                parser.error("Fixture policy required")
            scratch = (args.handoff_launcher or args.loss_holder).resolve(strict=True)
            launch_fixture(scratch, args.restricted,
                           handoff=await_control(scratch / "launcher-control.json"))
            return 0
        if args.fixture:
            if not (args.restricted or args.unrestricted):
                parser.error("Fixture policy required")
            try:
                if args.disconnect_worker:
                    evidence = disconnect_worker(args.fixture.resolve(strict=True), args.restricted)
                else:
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
              "experiments": [], "parent_exit_experiments": [], "holder_loss_experiments": []}
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
            for repeat in range(3):
                for restricted in (False, True):
                    for crash in (False, True):
                        with tempfile.TemporaryDirectory(prefix="atlas-win-holder-loss-i0-") as temporary:
                            evidence = holder_loss_experiment(Path(temporary), restricted, crash)
                            report["holder_loss_experiments"].append({"repeat": repeat + 1, **evidence})
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
