from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest

from codebase_atlas.lifecycle import CodebaseMemoryDaemon, GlobalCbmLock


class FakeRunner:
    def __init__(self, running: bool) -> None:
        self.running = running
        self.actions: list[str] = []

    def __call__(self, command, **_kwargs):
        action = command[-1]
        self.actions.append(action)
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


if __name__ == "__main__":
    unittest.main()
