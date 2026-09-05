"""Owned lifecycle management for reusable external provider processes."""

from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import time
from typing import Callable, Any

from .provider_layout import provider_environment
from .provider_process import run_provider_command

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on Windows
    fcntl = None
    import msvcrt


Runner = Callable[..., Any]


def _project_lease_identity(repository: Path, project: str) -> str:
    return hashlib.sha256(
        f"{repository.resolve()}\0{project}".encode("utf-8")
    ).hexdigest()[:24]


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


class ProjectRefreshLease:
    """Nonblocking cross-process writer lease for one exact Atlas project."""

    def __init__(self, data_dir: Path, repository: Path, project: str) -> None:
        identity = _project_lease_identity(repository, project)
        self.path = data_dir.resolve() / f"refresh-{identity}.lock"
        self.repository = repository.resolve()
        self.project = project
        self._handle = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT | nofollow,
            0o600,
        )
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            opened = os.fstat(descriptor)
            literal = os.lstat(self.path)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(literal.st_mode)
                or (opened.st_dev, opened.st_ino) != (literal.st_dev, literal.st_ino)
            ):
                raise ValueError("refresh lease is not a safe regular file")
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:  # pragma: no cover - Windows only
                    if opened.st_size == 0:
                        handle.write(b"\0")
                    handle.seek(0)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except (BlockingIOError, OSError):
                handle.close()
                return False
            payload = json.dumps({
                "schema_version": 1,
                "pid": os.getpid(),
                "repository": str(self.repository),
                "project": self.project,
            }, sort_keys=True).encode("utf-8")
            handle.seek(0)
            handle.truncate()
            handle.write(payload)
            os.fsync(descriptor)
            self._handle = handle
            return True
        except BaseException:
            handle.close()
            raise

    def acquire_shared(self, *, timeout_seconds: float = 30.0) -> bool:
        """Acquire a reader lease; Windows conservatively serializes readers."""
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            self.path, os.O_RDWR | os.O_CREAT | nofollow, 0o600
        )
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            opened = os.fstat(descriptor)
            literal = os.lstat(self.path)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(literal.st_mode)
                or (opened.st_dev, opened.st_ino) != (literal.st_dev, literal.st_ino)
            ):
                raise ValueError("refresh lease is not a safe regular file")
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    if fcntl is not None:
                        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    else:  # pragma: no cover - Windows only
                        if opened.st_size == 0:
                            handle.write(b"\0")
                        handle.seek(0)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    self._handle = handle
                    return True
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        handle.close()
                        return False
                    time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        except BaseException:
            handle.close()
            raise

    def owner_status(self) -> dict[str, Any]:
        """Best-effort diagnostics for the process currently holding the lease."""
        try:
            metadata = os.lstat(self.path)
            if not stat.S_ISREG(metadata.st_mode):
                return {"status": "unsafe", "path": str(self.path)}
            value = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            if not isinstance(value, dict):
                raise ValueError("lease payload is not an object")
            return {
                "status": "observed",
                "pid": value.get("pid"),
                "repository": value.get("repository"),
                "project": value.get("project"),
            }
        except FileNotFoundError:
            return {"status": "absent"}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return {"status": "unreadable", "detail": str(exc)}

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

    def __enter__(self) -> "ProjectRefreshLease":
        if not self.acquire():
            raise TimeoutError("refresh is owned by another Atlas process")
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


class ProjectOperationLease(ProjectRefreshLease):
    """Exclusive cross-process lease for one project's lifecycle operations."""

    def __init__(self, data_dir: Path, repository: Path, project: str) -> None:
        super().__init__(data_dir, repository, project)
        identity = _project_lease_identity(repository, project)
        self.path = data_dir.resolve() / f"operation-{identity}.lock"


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
        environment = provider_environment(self.cache_dir, self.repository)
        command = [str(self.binary), "daemon", action]
        if self.runner is subprocess.run:
            completed = run_provider_command(command, env=environment)
        else:
            completed = self.runner(
                command,
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
            started = self._run("start")
            output = f"{started.stdout}\n{started.stderr}".lower()
            if "already active" in output or "already running" in output:
                self.owned = False
                self.active = True
                return False
            if "started" not in output:
                raise RuntimeError("daemon start did not report ownership state")
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


class SharedCodebaseMemorySession(CodebaseMemoryDaemon):
    """Admit work to the Provider's shared, frontend-owned daemon generation.

    Atlas deliberately does not run ``daemon start`` here because that creates
    a permanent generation. Provider CLI frontends bootstrap one shared,
    non-permanent generation and its final disconnect performs bounded cleanup.
    The Atlas lock is therefore only a short local admission barrier.
    """

    def start(self, *, timeout_seconds: float | None = None) -> bool:
        if self.active:
            return False
        self.lock.acquire(timeout_seconds=timeout_seconds)
        try:
            self.owned = False
            self.active = True
            return False
        finally:
            self.lock.release()

    def close(self) -> None:
        # Frontend disconnects, not Atlas's structural lifetime, determine the
        # non-permanent Provider generation's bounded retirement.
        self.owned = False
        self.active = False
        self.lock.release()
