from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest

from codebase_atlas.lifecycle import (
    CodebaseMemoryDaemon,
    GlobalCbmLock,
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
