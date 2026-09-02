from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from codebase_atlas.lifecycle import (
    CodebaseMemoryDaemon,
    GlobalCbmLock,
    ProjectRefreshLease,
    SharedCodebaseMemorySession,
)
from codebase_atlas.cli import _provider_lifecycle


class FakeRunner:
    def __init__(self, running: bool) -> None:
        self.running = running
        self.actions: list[str] = []
        self.environments: list[dict[str, str]] = []

    def __call__(self, command, **kwargs):
        action = command[-1]
        self.actions.append(action)
        self.environments.append(kwargs["env"])
        if action == "status":
            message = "daemon: running" if self.running else "daemon: not running"
        elif action == "start":
            message = "daemon: already active" if self.running else "daemon: started"
            self.running = True
        else:
            self.running = False
            message = "daemon: stopping"
        return SimpleNamespace(
            returncode=1 if action == "status" and not self.running else 0,
            stdout=message,
            stderr="",
        )


class LifecycleTests(unittest.TestCase):
    def test_project_refresh_lease_is_nonblocking_and_project_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            data = Path(raw) / "data"
            repository = Path(raw) / "repo"
            repository.mkdir()
            first = ProjectRefreshLease(data, repository, "project-a")
            duplicate = ProjectRefreshLease(data, repository, "project-a")
            other = ProjectRefreshLease(data, repository, "project-b")
            self.assertTrue(first.acquire())
            try:
                self.assertFalse(duplicate.acquire())
                self.assertTrue(other.acquire())
            finally:
                first.release()
                other.release()
            self.assertTrue(duplicate.acquire())
            duplicate.release()

    def test_project_refresh_lease_rejects_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "repo"
            repository.mkdir()
            lease = ProjectRefreshLease(root / "data", repository, "project")
            lease.path.parent.mkdir(parents=True)
            target = root / "foreign"
            target.write_text("foreign")
            try:
                lease.path.symlink_to(target)
            except OSError:
                self.skipTest("symlinks are unavailable")
            # POSIX O_NOFOLLOW rejects during open; Windows opens the target and
            # the subsequent file-identity check rejects it with ValueError.
            with self.assertRaises((OSError, ValueError)):
                lease.acquire()

    def test_four_processes_have_exactly_one_project_refresh_owner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "repo"
            repository.mkdir()
            data = root / "data"
            start = root / "start"
            program = (
                "import sys,time\n"
                "from pathlib import Path\n"
                "from codebase_atlas.lifecycle import ProjectRefreshLease\n"
                "start=Path(sys.argv[1])\n"
                "while not start.exists(): time.sleep(0.005)\n"
                "lease=ProjectRefreshLease(Path(sys.argv[2]),Path(sys.argv[3]),'project')\n"
                "owned=lease.acquire()\n"
                "print('owner' if owned else 'non_owner',flush=True)\n"
                "time.sleep(0.5 if owned else 0.0)\n"
                "lease.release()\n"
            )
            environment = dict(os.environ)
            source = str(Path(__file__).parents[1] / "src")
            environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", program, str(start), str(data), str(repository)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=environment,
                )
                for _ in range(4)
            ]
            start.write_text("go")
            outputs = []
            try:
                for process in processes:
                    stdout, stderr = process.communicate(timeout=5.0)
                    self.assertEqual(process.returncode, 0, stderr)
                    outputs.append(stdout.strip())
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
            self.assertEqual(outputs.count("owner"), 1, outputs)
            self.assertEqual(outputs.count("non_owner"), 3, outputs)

    def test_starts_and_stops_only_owned_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runner = FakeRunner(False)
            daemon = CodebaseMemoryDaemon(
                Path("binary"), Path("repo"), Path("cache"), runner=runner,
                lock=GlobalCbmLock(Path(raw) / "cbm.lock"),
            )
            daemon.start()
            daemon.close()
            self.assertEqual(runner.actions, ["start", "stop"])
            self.assertTrue(all(
                environment["CBM_ALLOWED_ROOT"] == str(daemon.repository)
                and environment["CBM_CACHE_DIR"] == str(daemon.cache_dir)
                for environment in runner.environments
            ))

    def test_does_not_stop_preexisting_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runner = FakeRunner(True)
            daemon = CodebaseMemoryDaemon(
                Path("binary"), Path("repo"), Path("cache"), runner=runner,
                lock=GlobalCbmLock(Path(raw) / "cbm.lock"),
            )
            daemon.start()
            daemon.close()
            self.assertEqual(runner.actions, ["start"])

    def test_serializes_two_lifecycles_across_one_global_lock(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "cbm.lock"
            first = CodebaseMemoryDaemon(
                Path("binary"), Path("repo-a"), Path("cache-a"),
                runner=FakeRunner(False), lock=GlobalCbmLock(path),
            )
            second_runner = FakeRunner(False)
            second = CodebaseMemoryDaemon(
                Path("binary"), Path("repo-b"), Path("cache-b"),
                runner=second_runner, lock=GlobalCbmLock(path),
            )
            acquired = threading.Event()
            first.start()

            def start_second() -> None:
                second.start(timeout_seconds=1.0)
                acquired.set()

            thread = threading.Thread(target=start_second)
            thread.start()
            time.sleep(0.05)
            self.assertFalse(acquired.is_set())
            first.close()
            thread.join(timeout=1.0)
            self.assertTrue(acquired.is_set())
            second.close()
            self.assertEqual(second_runner.actions, ["start", "stop"])

    def test_reports_global_lock_wait_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "cbm.lock"
            first = GlobalCbmLock(path)
            second = GlobalCbmLock(path)
            first.acquire()
            try:
                with self.assertRaisesRegex(TimeoutError, "global CBM lock"):
                    second.acquire(timeout_seconds=0.05)
            finally:
                first.release()

    def test_shared_sessions_use_short_admission_and_never_stop_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "cbm.lock"
            cache = Path(raw) / "shared-cache"
            runner = FakeRunner(False)
            first = SharedCodebaseMemorySession(
                Path("binary"), Path("repo-a"), cache,
                runner=runner, lock=GlobalCbmLock(path),
            )
            second = SharedCodebaseMemorySession(
                Path("binary"), Path("repo-b"), cache,
                runner=runner, lock=GlobalCbmLock(path),
            )

            self.assertFalse(first.start())
            # Admission is already released: B joins before A closes.
            self.assertFalse(second.start(timeout_seconds=0.05))
            first.close()
            second.close()

            self.assertEqual(runner.actions, [])

    def test_shared_session_close_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runner = FakeRunner(False)
            session = SharedCodebaseMemorySession(
                Path("binary"), Path("repo"), Path("cache"), runner=runner,
                lock=GlobalCbmLock(Path(raw) / "cbm.lock"),
            )
            session.start()
            session.close()
            session.close()
            self.assertEqual(runner.actions, [])

    def test_product_selects_shared_lifecycle_only_for_published_layout(self) -> None:
        legacy = _provider_lifecycle(
            Path("binary"), Path("repo"), Path("cache"), "legacy-project-v0"
        )
        shared = _provider_lifecycle(
            Path("binary"), Path("repo"), Path("cache"), "shared-v1"
        )
        self.assertIsInstance(legacy, CodebaseMemoryDaemon)
        self.assertNotIsInstance(legacy, SharedCodebaseMemorySession)
        self.assertIsInstance(shared, SharedCodebaseMemorySession)


if __name__ == "__main__":
    unittest.main()
