"""Bounded one-shot Rust commands with owned process-tree cleanup."""
from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
from time import monotonic, sleep

MAX_OUTPUT_BYTES = 1024 * 1024
CLEANUP_SECONDS = 10.0


def run_owned(args, *, cwd=None, env=None, timeout, check=False,
              capture_output=False, text=False, stdin=None, stdout=None, stderr=None):
    """Narrow run-compatible API; callers must supply a verified environment.

    Successful parent exit does not release workers: cleanup owns the POSIX
    session or atomic Windows Job even after the parent has exited.
    """
    if (not math.isfinite(timeout) or timeout <= 0 or env is None
            or stdin not in (None, subprocess.DEVNULL)
            or (not capture_output and (stdout != subprocess.PIPE or stderr != subprocess.PIPE))):
        raise ValueError("Rust command requires bounded capture and an explicit environment")
    deadline = monotonic() + timeout
    if os.name == "nt":
        from .windows_owned_process import WindowsOwnedProcess
        process = WindowsOwnedProcess(args, cwd=cwd or os.getcwd(), env=env)
    else:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, bufsize=0)
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    read_errors = []

    def drain(stream, destination):
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    return
                if len(destination) + len(chunk) > MAX_OUTPUT_BYTES:
                    overflow.set()
                    return
                destination.extend(chunk)
        except OSError as exc:
            read_errors.append(exc)

    threads = [threading.Thread(target=drain, args=(stream, buffer), daemon=True)
               for stream, buffer in zip((process.stdout, process.stderr), buffers)]
    try:
        if process.stdin is not None:
            process.stdin.close()
        for thread in threads:
            thread.start()
        while process.poll() is None:
            if overflow.is_set():
                raise ValueError("Rust command output exceeded capture limit")
            if monotonic() >= deadline:
                raise subprocess.TimeoutExpired(args, timeout)
            sleep(min(0.01, max(0, deadline - monotonic())))
        if monotonic() >= deadline:
            raise subprocess.TimeoutExpired(args, timeout)
        returncode = process.returncode
    finally:
        cleanup_deadline = monotonic() + CLEANUP_SECONDS
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
                raise TimeoutError("Rust command pipes did not close within cleanup grace")
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    if overflow.is_set():
        raise ValueError("Rust command output exceeded capture limit")
    if read_errors:
        raise OSError("Rust command output could not be captured") from read_errors[0]
    output, error = (bytes(buffer) for buffer in buffers)
    if text:
        output, error = output.decode("utf-8", errors="replace"), error.decode("utf-8", errors="replace")
    completed = subprocess.CompletedProcess(args, returncode, output, error)
    if check:
        completed.check_returncode()
    return completed
