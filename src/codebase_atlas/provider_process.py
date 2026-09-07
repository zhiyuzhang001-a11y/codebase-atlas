"""Safe subprocess capture for Provider frontends that may spawn a daemon."""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Mapping, Sequence


def run_provider_command(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: os.PathLike[str] | str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    arguments = {
        "cwd": cwd,
        "env": env,
        "check": False,
        "timeout": timeout,
    }
    if os.name != "nt":
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            **arguments,
        )

    # Windows Provider frontends may bootstrap a background daemon which
    # briefly inherits their standard handles. Pipes then wait for descendant
    # EOF after the frontend exits; seekable files do not have that coupling.
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        completed = subprocess.run(
            command,
            stdout=stdout_file,
            stderr=stderr_file,
            **arguments,
        )
        stdout_file.seek(0)
        stderr_file.seek(0)
        return subprocess.CompletedProcess(
            completed.args,
            completed.returncode,
            stdout_file.read().decode("utf-8", "replace"),
            stderr_file.read().decode("utf-8", "replace"),
        )
