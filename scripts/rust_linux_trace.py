"""Internal Linux syscall observation. Other platforms remain unqualified.

Preparation/downloads are outside the trace. Raw exec argv require independent
allow-list audit; collecting them alone does not close the full phase-2 gate.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
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


def trace_summary(path: Path, *, initial_cwd: str | None = None) -> dict:
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
    summary = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "execution_argv_raw": executions, "non_unix_socket_attempts": network,
            "execution_results": execution_results(text),
            "exit_records": sum("+++ exited with" in line or "+++ killed by" in line
                                for line in text.splitlines())}
    if initial_cwd is not None:
        summary["execution_cwd_records"] = cwd_execution_records(text, initial_cwd)
    return summary


def bootstrap_environment() -> dict[str, str]:
    # -v exposes environments; never inherit CI tokens or arbitrary overrides.
    names = ("HOME", "USERPROFILE", "PATH", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP",
             "XDG_DATA_HOME", "CARGO_HOME", "RUSTUP_HOME")
    return {name: os.environ[name] for name in names if name in os.environ}


def cwd_execution_records(text: str, initial_cwd: str) -> list[dict]:
    """Reconstruct recorded absolute cwd strings, not filesystem admission.

    Pair creation results before replay so a child running before its parent's
    vfork/clone return inherits at creation, not at the later return line.
    Unknown namespace/fd/relative or shared-cwd race cases fail closed.
    """
    if "superseded by execve" in text:
        raise ValueError("Native exec thread identity replacement is unsupported")
    def absolute(value):
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts or str(path) != value:
            raise ValueError("Native cwd must be an unambiguous absolute POSIX path")
        return value
    initial_cwd = absolute(initial_cwd)
    names = "execve|execveat|clone|clone3|fork|vfork|chdir|fchdir|unshare|chroot|setns|pivot_root"
    pending = {}
    events = []
    for row, line in enumerate(text.splitlines()):
        start = re.match(r"^(\d+)\s+(" + names + r")\(", line)
        resume = re.match(r"^(\d+)\s+<\.\.\. (" + names + r") resumed>", line)
        exited = re.match(r"^(\d+)\s+\+\+\+ (?:exited with|killed by)", line)
        if exited:
            events.append((row, row, int(exited[1]), "exit", line, ""))
            continue
        if start:
            key = (int(start[1]), start[2])
            if key in pending:
                raise ValueError("Native cwd syscall missing its previous result")
            if line.endswith("<unfinished ...>"):
                pending[key] = (row, line)
                continue
            begin, raw = row, line
        elif resume:
            key = (int(resume[1]), resume[2])
            if key not in pending:
                raise ValueError("Native cwd syscall resumed without its start")
            begin, raw = pending.pop(key)
        else:
            continue
        result = re.search(r"\)\s+=\s+(.+)$", line)
        if result is None:
            raise ValueError("Native cwd syscall completion missing")
        events.append((begin, row, key[0], key[1], raw, result[1]))
    if pending or not events:
        raise ValueError("Native cwd trace empty or incomplete")
    events.sort(key=lambda event: event[0])
    contexts = {events[0][2]: {"cwd": initial_cwd}}
    records = []
    for begin, end, pid, name, raw, result in events:
        if pid not in contexts:
            raise ValueError("Native cwd has an unobserved process parent")
        context = contexts[pid]
        if name == "exit":
            del contexts[pid]
        elif name in {"unshare", "chroot", "setns", "pivot_root"}:
            raise ValueError("Native cwd namespace mutation is unsupported")
        elif name in {"clone", "clone3", "fork", "vfork"}:
            if result.startswith("-1 "):
                continue
            if not re.fullmatch(r"[1-9][0-9]*", result):
                raise ValueError("Native child PID result is unsupported")
            child = int(result)
            if child in contexts or "CLONE_NEW" in raw:
                raise ValueError("Native child identity/namespace is ambiguous")
            if name in {"clone", "clone3"} and "flags=" not in raw:
                raise ValueError("Native clone flags missing")
            contexts[child] = context if "CLONE_FS" in raw else dict(context)
        elif name in {"chdir", "fchdir"}:
            if result.startswith("-1 "):
                continue
            if result != "0" or name == "fchdir":
                raise ValueError("Native cwd fd/result cannot be resolved")
            if sum(value is context for value in contexts.values()) > 1:
                raise ValueError("Native shared cwd mutation ordering is ambiguous")
            arguments = raw.split("chdir(", 1)[1]
            try:
                value, stop = json.JSONDecoder().raw_decode(arguments)
            except ValueError as error:
                raise ValueError("Native cwd path encoding unsupported") from error
            if not isinstance(value, str) or not re.match(r"\s*(?:\)|<unfinished)", arguments[stop:]):
                raise ValueError("Native cwd path truncated or ambiguous")
            context["cwd"] = absolute(value)
        elif result == "0":
            executable, argv = decode_exec_argv({"syscall": name, "argv_raw": raw})
            records.append({"pid": pid, "executable": executable,
                            "argv0": argv[0], "cwd": context["cwd"]})
            # exec unshares the filesystem context, even after CLONE_FS.
            contexts[pid] = dict(context)
        elif not result.startswith("-1 "):
            raise ValueError("Native exec completion result is unsupported")
    if not records:
        raise ValueError("Native cwd trace lacks successful exec records")
    return records


def decode_exec_argv(entry: dict) -> tuple[str, list[str]]:
    """Decode one native exec without guessing at unsupported encodings."""
    if entry["syscall"] != "execve":
        raise ValueError("Unsupported native exec syscall for argv audit")
    decoder = json.JSONDecoder()
    try:
        arguments = entry["argv_raw"].split("execve(", 1)[1]
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
    return executable, argv


def require_verified_tool_paths(summary: dict, verified_tools: dict) -> list[dict]:
    """Bind launched Rust tool paths to a retained, production-verified receipt.

    This is not argv/cwd admission, exec interception, or a file-mutation guard.
    Non-Rust executable roles still require their separate full-tree audit.
    """
    roles = {"cargo", "rustc", "rust-analyzer"}
    if len(verified_tools) != 3 or {v.get("role") for v in verified_tools.values()} != roles:
        raise ValueError("Verified Rust tool map must contain the exact three roles")
    for executable, identity in verified_tools.items():
        path = PurePosixPath(executable)
        if not path.is_absolute() or path.name != identity["role"] or \
                str(path) != executable or ".." in path.parts or \
                not re.fullmatch(r"[0-9a-f]{64}", identity.get("sha256", "")):
            raise ValueError("Verified Rust tool map has an invalid path/hash")
    matched = []
    for entry in summary["execution_results"]:
        if not entry["launched"]:
            continue
        executable, argv = decode_exec_argv(entry)
        if PurePosixPath(executable).name == "rustup":
            raise ValueError("Native lifecycle launched forbidden rustup")
        if PurePosixPath(executable).name not in roles:
            continue
        if executable not in verified_tools or argv[0] != executable:
            raise ValueError("Launched Rust tool path/argv0 differs from verified receipt")
        matched.append({"pid": entry["pid"], "executable": executable,
                        **verified_tools[executable]})
    if {entry["role"] for entry in matched} != roles:
        raise ValueError("Native trace lacks one or more verified Rust tool roles")
    return matched


def require_offline_metadata(summary: dict) -> int:
    """Check real launched argv, never an environment string or failed attempt.

    This narrow gate does not qualify other probes or the complete allow-list.
    Unsupported strace string encodings/syscalls fail closed rather than guessing.
    """
    count = 0
    for entry in summary["execution_results"]:
        if not entry["launched"]:
            continue
        executable, argv = decode_exec_argv(entry)
        # These are Linux strace paths, regardless of the audit host OS.
        if PurePosixPath(executable).name != "cargo" or argv[1:2] != ["metadata"]:
            continue
        if argv[0] != executable or not PurePosixPath(executable).is_absolute():
            raise ValueError("Cargo metadata executable/argv0 identity mismatch")
        flags = {"--offline", "--no-deps", "--all-features"}
        operands = {"--format-version", "--manifest-path", "--filter-platform"}
        seen: dict[str, str | None] = {}
        position = 2
        while position < len(argv):
            option = argv[position]
            if option in seen or option not in flags | operands:
                raise ValueError("Unsupported or duplicate cargo metadata option")
            position += 1
            value = None
            if option in operands:
                if position == len(argv) or argv[position].startswith(("-", "@")):
                    raise ValueError("Cargo metadata option operand missing or ambiguous")
                value = argv[position]
                position += 1
            seen[option] = value
        if not {"--offline", "--no-deps", "--format-version", "--manifest-path"} <= seen.keys():
            raise ValueError("Cargo metadata lacks required explicit options")
        if seen["--format-version"] != "1":
            raise ValueError("Unsupported cargo metadata format")
        manifest = PurePosixPath(seen["--manifest-path"])
        if not manifest.is_absolute() or manifest.name != "Cargo.toml":
            raise ValueError("Cargo metadata manifest must be an absolute Cargo.toml path")
        target = seen.get("--filter-platform")
        if target is not None and not re.fullmatch(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+){2,}", target):
            raise ValueError("Cargo metadata target must be a native target triple")
        count += 1
    if count == 0:
        raise ValueError("Native trace has no launched cargo metadata evidence")
    return count


def observe(argv: list[str], *, cwd: Path, directory: Path, verified_tools: dict) -> dict:
    if not sys.platform.startswith("linux"):
        raise ValueError("Linux native observer cannot qualify another OS")
    tracer = shutil.which("strace")
    if tracer is None:
        raise ValueError("Linux native strace observer missing")
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    environment = bootstrap_environment()
    def traced(command, name, timeout):
        trace = directory / (name + ".trace")
        # Use normal ptrace stops rather than seccomp-assisted filtering. The
        # ARM runner produced an undecodable/incomplete exec during thread exit;
        # that evidence remains rejected, never inferred from an exit record.
        arguments = [tracer, "-f", "-s", "65535", "-v", "-e",
                     "trace=%process,%network,chdir,fchdir,unshare,chroot,setns,pivot_root,io_uring_setup,io_uring_enter", "-o", str(trace), *command]
        result = run_owned(arguments, cwd=cwd, env=environment, timeout=timeout,
                           capture_output=True, text=True)
        (directory / (name + ".stdout")).write_text(result.stdout, encoding="utf-8")
        (directory / (name + ".stderr")).write_text(result.stderr, encoding="utf-8")
        result.check_returncode()
        return trace_summary(trace, initial_cwd=str(cwd.resolve()))
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
    verified_launches = require_verified_tool_paths(actual, verified_tools)
    return {"status": "observed_no_non_unix_socket_attempts", "positive_control": positive,
            "lifecycle": actual, "environment_names": sorted(environment),
            "initial_cwd": str(cwd.resolve()),
            "offline_metadata_launches": metadata_count,
            "verified_rust_tool_launches": verified_launches,
            "not_proven": ["independent full argv allow-list audit", "cwd filesystem identity/context admission",
                           "observed executable bytes/immutability", "non-Rust executable role admission",
                           "network denial", "other OS native tracing",
                           "installed-wheel", "resource gates"]}
