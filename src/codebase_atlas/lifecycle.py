"""Owned lifecycle management for reusable external provider processes."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Callable, Any

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on Windows
    fcntl = None
    import msvcrt


Runner = Callable[..., Any]


def default_cbm_lock_path() -> Path:
    runtime = Path(
        os.environ.get("ATLAS_RUNTIME_DIR")
        or os.environ.get("XDG_RUNTIME_DIR")
        or tempfile.gettempdir()
    )
    user = os.getuid() if hasattr(os, "getuid") else os.environ.get("USERNAME", "user")
    return runtime / f"codebase-atlas-cbm-{user}.lock"


class GlobalCbmLock:
    """Cross-process lock for the upstream Provider's single global daemon."""

    def __init__(self, path: Path | None = None, *, timeout_seconds: float = 30.0) -> None:
        self.path = (path or default_cbm_lock_path()).absolute()
        self.timeout_seconds = timeout_seconds
        self._handle = None

    def acquire(self, *, timeout_seconds: float | None = None) -> None:
        if self._handle is not None:
            return
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if fcntl is None and handle.tell() == 0:  # pragma: no cover - Windows only
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:  # pragma: no cover - Windows only
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                self._handle = handle
                return
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError(
                        f"timed out waiting {timeout:.1f}s for global CBM lock {self.path}"
                    )
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover - Windows only
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> "GlobalCbmLock":
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


class CodebaseMemoryDaemon:
    def __init__(
        self,
        binary: Path,
        repository: Path,
        cache_dir: Path,
        *,
        runner: Runner = subprocess.run,
        lock: GlobalCbmLock | None = None,
    ) -> None:
        self.binary = binary.resolve()
        self.repository = repository.resolve()
        self.cache_dir = cache_dir.resolve()
        self.runner = runner
        self.lock = lock or GlobalCbmLock()
        self.owned = False
        self.active = False

    def _run(self, action: str):
        environment = os.environ.copy()
        environment["CBM_CACHE_DIR"] = str(self.cache_dir)
        environment["CBM_ALLOWED_ROOT"] = str(self.repository.parent)
        completed = self.runner(
            [str(self.binary), "daemon", action],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        combined = f"{completed.stdout}\n{completed.stderr}".lower()
        if completed.returncode != 0 and not (
            action == "status" and "not running" in combined
        ):
            raise RuntimeError(
                completed.stderr.strip() or completed.stdout.strip() or f"daemon {action} failed"
            )
        return completed

    def start(self, *, timeout_seconds: float | None = None) -> bool:
        if self.active:
            return False
        self.lock.acquire(timeout_seconds=timeout_seconds)
        try:
            status = self._run("status")
            output = f"{status.stdout}\n{status.stderr}".lower()
            if "not running" not in output:
                self.owned = False
                self.active = True
                return False
            self._run("start")
            self.owned = True
            self.active = True
            return True
        except Exception:
            self.lock.release()
            raise

    def close(self) -> None:
        try:
            if self.owned:
                self._run("stop")
        finally:
            self.owned = False
            self.active = False
            self.lock.release()

    def __enter__(self) -> "CodebaseMemoryDaemon":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
