"""Bounded Linux owned-child tracing probe, not semantic/build qualification."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time


TOKEN = "atlas-c0-short-lived-exec-control-v1"


def binary_identity(path: Path) -> dict:
    path = path.resolve(strict=True)
    stat = path.stat()
    if not path.is_file() or stat.st_uid != 0 or stat.st_mode & 0o022:
        raise ValueError("probe tool must be a root-owned non-writable regular file")
    if stat.st_size > 64 * 1024 * 1024:
        raise ValueError("probe tool exceeds identity-read limit")
    with path.open("rb") as stream:
        raw = stream.read(64 * 1024 * 1024 + 1)
    if len(raw) != stat.st_size or len(raw) > 64 * 1024 * 1024:
        raise ValueError("probe tool changed or exceeded bounded identity read")
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": stat.st_size}


def trace_contains_control(trace: str) -> bool:
    # Our control uses an exact simple ASCII argv, so successful exec is
    # unambiguous. Do not generalize this predicate into a Cargo trace parser.
    return any('execve("/usr/bin/true", ["/usr/bin/true", "' + TOKEN + '"]' in line
               and re.search(r"\)\s*=\s*0$", line) for line in trace.splitlines())


def final_gates(active_seconds: float, output_bytes: int) -> None:
    if active_seconds >= 20:
        raise TimeoutError("20 second owned probe deadline")
    if output_bytes > 1024 * 1024:
        raise ValueError("1 MiB aggregate probe output gate")


def capture_raw(root: Path, result: dict) -> int:
    """After cleanup, preserve a bounded prefix even when the output gate failed."""
    names = ("stderr", "stdout", "exec.trace")
    sizes = {name: (root / name).stat().st_size if (root / name).exists() else 0 for name in names}
    total = sum(sizes.values())
    result["output_bytes"] = sizes
    result["raw_missing"] = [name for name in names if not (root / name).exists()]
    remaining = 1024 * 1024
    omitted = {}
    for name in names:
        path = root / name
        if not path.exists():
            result[name] = ""
            continue
        with path.open("rb") as stream:
            raw = stream.read(min(sizes[name], remaining))
        remaining -= len(raw)
        result[name] = raw.decode("utf-8", errors="replace")
        if len(raw) != sizes[name]:
            omitted[name] = sizes[name] - len(raw)
    result["raw_omitted_bytes"] = omitted
    return total


def run_probe(source_sha: str, output: Path) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("exact source SHA required")
    if output.exists():
        raise ValueError("do not overwrite previous evidence")
    result = {"schema": "atlas-rust-semantic-observer-probe-v1", "source_sha": source_sha,
              "status": "incomplete", "qualified": False,
              "scope": "Linux tracing capability for a fixed trusted short-lived child only",
              "limitations": ["not a Cargo/build observer", "no execveat positive control",
                              "no network isolation proof", "no semantic or platform qualification"]}
    if sys.platform != "linux":
        result["failure"] = "Linux required; no platform skip counted as success"
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result
    tracer = shutil.which("strace", path="/usr/bin:/bin")
    if tracer is None:
        result["failure"] = "strace unavailable; no installation or privilege escalation"
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result
    start = time.monotonic()
    process = None
    # Keep raw output until cleanup and bounded capture. If cleanup fails, leave
    # the private directory intact instead of deleting while a child may live.
    root = Path(tempfile.mkdtemp(prefix="atlas-c0-observer-"))
    os.chmod(root, 0o700)
    try:
        try:
            result["tools"] = [binary_identity(Path(tracer)), binary_identity(Path("/usr/bin/python3")),
                               binary_identity(Path("/usr/bin/true"))]
            trace_path = root / "exec.trace"
            code = "import subprocess; subprocess.run(['/usr/bin/true', '" + TOKEN + "'], check=True)"
            argv = [tracer, "-f", "-qq", "-v", "-s", "65536", "-e", "trace=execve,execveat",
                    "-o", str(trace_path), "/usr/bin/python3", "-I", "-S", "-c", code]
            # No user project, package source, compiler, shell, or inherited credentials.
            env = {"PATH": "/usr/bin:/bin", "HOME": str(root), "LC_ALL": "C"}
            result["argv"] = argv
            result["env_keys"] = sorted(env)
            with (root / "stdout").open("xb") as stdout, (root / "stderr").open("xb") as stderr:
                process = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, start_new_session=True)
                while process.poll() is None:
                    if time.monotonic() - start >= 20:
                        raise TimeoutError("20 second owned probe deadline")
                    if sum(p.stat().st_size for p in root.iterdir() if p.is_file()) > 1024 * 1024:
                        raise ValueError("1 MiB probe output gate")
                    time.sleep(0.02)
            result["exit_code"] = process.returncode
            result["status"] = "probe-exited"
        except Exception as exc:
            result.update(status="incomplete", failure=str(exc))
        finally:
            cleanup_start = time.monotonic()
            result["active_seconds"] = cleanup_start - start
            if process is not None:
                cleanup_deadline = cleanup_start + 10
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    result.update(status="incomplete", cleanup_failure="kill failed: " + str(exc))
                try:
                    process.wait(timeout=max(0, cleanup_deadline - time.monotonic()))
                    while True:
                        try:
                            os.killpg(process.pid, 0)
                        except ProcessLookupError:
                            result["cleanup"] = "owned process group absent; parent reaped"
                            break
                        if time.monotonic() >= cleanup_deadline:
                            raise TimeoutError("owned process group remains")
                        time.sleep(0.02)
                except Exception as exc:
                    result.update(status="incomplete", cleanup_failure=str(exc))
            result["cleanup_seconds"] = time.monotonic() - cleanup_start
            result["elapsed_seconds"] = time.monotonic() - start
            try:
                total = capture_raw(root, result)
                final_gates(result["active_seconds"], total)
                if result["status"] == "probe-exited":
                    if result["exit_code"] != 0 or not trace_contains_control(result["exec.trace"]):
                        raise ValueError("complete successful short-lived control exec not observed")
                    result["status"] = "short-lived-control-observed"
            except Exception as exc:
                result.update(status="incomplete", failure=str(exc))
    except Exception as exc:
        result.update(status="incomplete", failure="probe finalization failed: " + str(exc),
                      cleanup_failure="private evidence retained after finalization failure")
    if "cleanup_failure" in result:
        result["retained_private_directory"] = str(root)
    else:
        shutil.rmtree(root)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    receipt = run_probe(arguments.source_sha, arguments.output)
    print(json.dumps({k: receipt[k] for k in ("source_sha", "status", "qualified")}))
    raise SystemExit(0 if receipt["status"] == "short-lived-control-observed" else 1)
