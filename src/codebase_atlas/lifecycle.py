"""Owned lifecycle management for reusable external provider processes."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Callable, Any


Runner = Callable[..., Any]


class CodebaseMemoryDaemon:
    def __init__(
        self,
        binary: Path,
        repository: Path,
        cache_dir: Path,
        *,
        runner: Runner = subprocess.run,
    ) -> None:
        self.binary = binary.resolve()
        self.repository = repository.resolve()
        self.cache_dir = cache_dir.resolve()
        self.runner = runner
        self.owned = False

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

    def start(self) -> bool:
        status = self._run("status")
        output = f"{status.stdout}\n{status.stderr}".lower()
        if "not running" not in output:
            self.owned = False
            return False
        self._run("start")
        self.owned = True
        return True

    def close(self) -> None:
        if self.owned:
            try:
                self._run("stop")
            finally:
                self.owned = False

    def __enter__(self) -> "CodebaseMemoryDaemon":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
