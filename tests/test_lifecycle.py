from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

from codebase_atlas.lifecycle import CodebaseMemoryDaemon


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
            self.running = True
            message = "daemon: started"
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
        runner = FakeRunner(False)
        daemon = CodebaseMemoryDaemon(Path("binary"), Path("repo"), Path("cache"), runner=runner)
        daemon.start()
        daemon.close()
        self.assertEqual(runner.actions, ["status", "start", "stop"])

    def test_does_not_stop_preexisting_daemon(self) -> None:
        runner = FakeRunner(True)
        daemon = CodebaseMemoryDaemon(Path("binary"), Path("repo"), Path("cache"), runner=runner)
        daemon.start()
        daemon.close()
        self.assertEqual(runner.actions, ["status"])


if __name__ == "__main__":
    unittest.main()
