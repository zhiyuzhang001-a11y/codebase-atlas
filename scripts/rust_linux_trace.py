"""Internal Linux syscall observation. Other platforms remain unqualified.

Preparation/downloads are outside the trace. Raw exec argv require independent
allow-list audit; collecting them alone does not close the full phase-2 gate.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

from codebase_atlas.rust_owned_command import run_owned


def execution_results(text: str) -> list[dict]:
    """Pair interleaved exec results; an attempt is not a successful launch."""
    pending: dict[tuple[str, str], str] = {}
    completed = []
    for line in text.splitlines():
        start = re.match(r"^(\d+)\s+(execve(?:at)?)\(", line)
        resume = re.match(r"^(\d+)\s+<\.\.\. (execve(?:at)?) resumed>", line)
        if start:
            key = (start[1], start[2])
            if key in pending:
                raise ValueError("Native exec result missing before next attempt")
            if line.endswith("<unfinished ...>"):
                pending[key] = line
                continue
            argv_raw = line
        elif resume:
            key = (resume[1], resume[2])
            if key not in pending:
                raise ValueError("Native exec resumed without its complete argv")
            argv_raw = pending.pop(key)
        else:
            continue
        result = re.search(r"\)\s+=\s+(.+)$", line)
        if result is None:
            raise ValueError("Native exec completion result missing")
        completed.append({"pid": int(key[0]), "syscall": key[1],
                          "argv_raw": argv_raw, "result": result[1],
                          "launched": result[1] == "0"})
    if pending:
        raise ValueError("Native trace ended with unresolved exec attempts")
    return completed


def trace_summary(path: Path) -> dict:
    if not path.is_file() or not 0 < path.stat().st_size <= 64 * 1024 * 1024:
        raise ValueError("Native trace missing, empty or oversized")
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="strict")
    executions = [line for line in text.splitlines() if re.search(r"\bexecve(?:at)?\(", line)]
    if not executions or any("..." in line.replace("<unfinished ...>", "") for line in executions):
        raise ValueError("Native execution argv missing or truncated/incomplete")
    if re.search(r"detached|Operation not permitted|ptrace:|io_uring_(?:setup|enter)\(", text):
        raise ValueError("Native observer lost coverage or encountered unobserved io_uring")
    network = [line for line in text.splitlines()
               if re.search(r"\bsocket\(AF_(?!UNIX\b)", line)]
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "execution_argv_raw": executions, "non_unix_socket_attempts": network,
            "execution_results": execution_results(text),
            "exit_records": sum("+++ exited with" in line or "+++ killed by" in line
                                for line in text.splitlines())}


def bootstrap_environment() -> dict[str, str]:
    # -v exposes environments; never inherit CI tokens or arbitrary overrides.
    names = ("HOME", "USERPROFILE", "PATH", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP",
             "XDG_DATA_HOME", "CARGO_HOME", "RUSTUP_HOME")
    return {name: os.environ[name] for name in names if name in os.environ}


def require_offline_metadata(summary: dict) -> int:
    """Check real launched argv, never an environment string or failed attempt.

    This narrow gate does not qualify other probes or the complete allow-list.
    Unsupported strace string encodings/syscalls fail closed rather than guessing.
    """
    decoder = json.JSONDecoder()
    count = 0
    for entry in summary["execution_results"]:
        if not entry["launched"]:
            continue
        if entry["syscall"] != "execve":
            raise ValueError("Unsupported native exec syscall for metadata audit")
        arguments = entry["argv_raw"].split("execve(", 1)[1]
        try:
            executable, end = decoder.raw_decode(arguments)
            remainder = arguments[end:].lstrip()
            if not remainder.startswith(","):
                raise ValueError("Native exec argv separator missing")
            vector = remainder[1:].lstrip()
            argv, end = decoder.raw_decode(vector)
            if not vector[end:].lstrip().startswith(","):
                raise ValueError("Native exec argv terminator missing")
        except (ValueError, IndexError) as error:
            raise ValueError("Native exec argv cannot be decoded completely") from error
        if not isinstance(executable, str) or not isinstance(argv, list) or not argv or \
                not all(isinstance(value, str) for value in argv):
            raise ValueError("Native exec argv has an unsupported shape")
        if Path(executable).name != "cargo" or argv[1:2] != ["metadata"]:
            continue
        if "--offline" not in argv[2:] or "--no-deps" not in argv[2:]:
            raise ValueError("Launched cargo metadata lacks explicit --offline/--no-deps")
        count += 1
    if count == 0:
        raise ValueError("Native trace has no launched cargo metadata evidence")
    return count


def observe(argv: list[str], *, cwd: Path, directory: Path) -> dict:
    if not sys.platform.startswith("linux"):
        raise ValueError("Linux native observer cannot qualify another OS")
    tracer = shutil.which("strace")
    if tracer is None:
        raise ValueError("Linux native strace observer missing")
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    environment = bootstrap_environment()
    def traced(command, name, timeout):
        trace = directory / (name + ".trace")
        arguments = [tracer, "-f", "--seccomp-bpf", "-s", "65535", "-v", "-e",
                     "trace=%process,%network,io_uring_setup,io_uring_enter", "-o", str(trace), *command]
        result = run_owned(arguments, cwd=cwd, env=environment, timeout=timeout,
                           capture_output=True, text=True)
        (directory / (name + ".stdout")).write_text(result.stdout, encoding="utf-8")
        (directory / (name + ".stderr")).write_text(result.stderr, encoding="utf-8")
        result.check_returncode()
        return trace_summary(trace)
    # Controlled loopback only, not an external network request or project code.
    control = "import socket,subprocess,sys; s=socket.socket(); s.settimeout(1); s.connect_ex(('127.0.0.1',9)); s.close(); subprocess.run([sys.executable,'-c','pass'],check=True)"
    positive = traced([sys.executable, "-c", control], "positive-control", 20)
    if not positive["non_unix_socket_attempts"] or len(positive["execution_argv_raw"]) < 2:
        raise ValueError("Native positive control failed to observe socket and child exec")
    actual = traced(argv, "lifecycle", 600)
    if actual["non_unix_socket_attempts"]:
        raise ValueError("Lifecycle attempted non-Unix network socket")
    if actual["exit_records"] == 0:
        raise ValueError("Native trace lacks process exit evidence")
    metadata_count = require_offline_metadata(actual)
    return {"status": "observed_no_non_unix_socket_attempts", "positive_control": positive,
            "lifecycle": actual, "environment_names": sorted(environment),
            "offline_metadata_launches": metadata_count,
            "not_proven": ["independent full argv allow-list audit", "other OS native tracing",
                           "installed-wheel", "resource gates"]}
