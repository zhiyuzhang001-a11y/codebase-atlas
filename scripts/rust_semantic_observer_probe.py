"""Bounded Linux owned-child tracing probe, not semantic/build qualification."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat as stat_module
import signal
import subprocess
import sys
import tempfile
import time


TOKEN = "atlas-c0-short-lived-exec-control-v1"
FD_TOKEN = "atlas-c0-fd-exec-control-v2"
ARGUMENTS = ["", 'quote"backslash\\', "line\n\ttab\rreturn", "x" * 4096 + "-end"]
RSS_TOKEN = "atlas-c0-rss-v4"


def load_resource_sampler() -> tuple:
    """Load only the reviewed sibling from the exact-head checkout under -I -S."""
    path = Path(__file__).resolve().with_name("rust_semantic_linux_resources.py")
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if not 0 < len(raw) <= 65536:
        raise ValueError("bounded sampler source required")
    spec = importlib.util.spec_from_file_location("atlas_owned_linux_resources", path)
    module = importlib.util.module_from_spec(spec)
    # Execute the measured bytes, not a second path lookup or a cached pyc.
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module, {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def resource_marker(raw: bytes) -> tuple[int, int, int] | None:
    if len(raw) > 256:
        raise ValueError("oversized resource control marker")
    if not raw.endswith(b"\n"):
        return None
    match = re.fullmatch(RSS_TOKEN.encode() + rb" ([1-9][0-9]{0,9}) ([1-9][0-9]{0,9}) ([0-9]{1,5})\n", raw)
    if match is None:
        raise ValueError("unknown resource control marker")
    parent, child, held_fd = map(int, match.groups())
    if parent == child or max(parent, child) > 2**31 - 1 or not 3 <= held_fd <= 65535:
        raise ValueError("invalid resource control PIDs")
    return parent, child, held_fd


def control_object_samples(module, admission: list[dict], expected_cwds: dict,
                           held_fd: int, tool: dict) -> list[dict]:
    """Check fixed live controls only, never resolve an arbitrary execveat FD.

    proc cwd/fd magic links are intentionally followed inside an identity-bound
    proc directory. Their targets must match previously measured owned objects.
    This does not prove continuous identity or an earlier short-lived exec's FD.
    """
    result = []
    for identity in admission:
        pid = identity['pid']
        if pid not in expected_cwds:
            continue  # strace has no held-control FD; the two Python controls do.
        directory = os.open(f'/proc/{pid}', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            packet = {'pid': pid, 'starttime': identity['starttime'], 'checks': []}
            for phase in ('before', 'after'):
                observed = module.proc_identity(module._read_at(directory, 'stat', 8192))
                if any(observed[key] != identity[key] for key in ('pid', 'starttime', 'pgrp', 'session', 'ppid')) or observed['state'] in {'Z', 'X', 'x'}:
                    raise ValueError('object control process identity changed')
                cwd_path = os.readlink('cwd', dir_fd=directory)
                cwd_stat = os.stat('cwd', dir_fd=directory)
                fd_stat = os.stat(f'fd/{held_fd}', dir_fd=directory)
                info_dir = os.open('fdinfo', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    fdinfo = module._read_at(info_dir, str(held_fd), 4096)
                finally:
                    os.close(info_dir)
                flags = re.findall(rb'^flags:\s+([0-7]+)$', fdinfo, re.MULTILINE)
                expected = expected_cwds[pid]
                if (cwd_path != expected['path'] or (cwd_stat.st_dev, cwd_stat.st_ino) != (expected['device'], expected['inode'])
                        or (fd_stat.st_dev, fd_stat.st_ino, fd_stat.st_size) != (tool['device'], tool['inode'], tool['bytes'])
                        or len(flags) != 1 or int(flags[0], 8) & os.O_ACCMODE != os.O_RDONLY
                        or int(flags[0], 8) & os.O_PATH):
                    raise ValueError('owned cwd or held read-only tool FD identity mismatch')
                packet['checks'].append({'phase': phase, 'cwd': cwd_path,
                                         'cwd_device': cwd_stat.st_dev, 'cwd_inode': cwd_stat.st_ino,
                                         'fd': held_fd, 'fd_device': fd_stat.st_dev, 'fd_inode': fd_stat.st_ino,
                                         'fd_bytes': fd_stat.st_size, 'fdinfo': fdinfo.decode('ascii')})
            result.append(packet)
        finally:
            os.close(directory)
    if len(result) != 2:
        raise ValueError('two live cwd/FD controls required')
    return result


def admit_resource_controls(module, root_pid: int, parent: int, child: int) -> list[dict]:
    if len({root_pid, parent, child}) != 3:
        raise ValueError("distinct owned resource PIDs required")
    rows = []
    for pid in (root_pid, parent, child):
        directory = os.open(f"/proc/{pid}", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            row = module.proc_identity(module._read_at(directory, "stat", 8192))
        finally:
            os.close(directory)
        if (row['pid'] != pid or row['starttime'] <= 0 or row['session'] != root_pid
                or row['pgrp'] != root_pid or row['state'] in {'Z', 'X', 'x'}):
            raise ValueError("resource admission identity/group mismatch")
        rows.append(row)
    if rows[0]['ppid'] != os.getpid() or rows[1]['ppid'] != root_pid or rows[2]['ppid'] != parent:
        raise ValueError("resource control ancestry mismatch")
    return rows


def resource_sample_gate(sample: dict, parent: int, child: int) -> None:
    by_pid = {row['pid']: row['rss_bytes'] for row in sample['samples']}
    if len(by_pid) != 3 or len(sample['samples']) != 3:
        raise ValueError("exactly three distinct admitted RSS controls required")
    if any(by_pid.get(pid, 0) < 16 * 1024 * 1024 for pid in (parent, child)):
        raise ValueError("touched allocation RSS positive control missing")
    if sample['rss_bytes'] != sum(by_pid.values()) or sample['rss_bytes'] > 128 * 1024 * 1024:
        raise ValueError("128 MiB controlled admitted RSS gate")


def control_argv_literal(arguments: list[str]) -> str:
    """Exact ASCII control rendering, NOT a general strace/Cargo parser."""
    if any(not value.isascii() for value in arguments):
        raise ValueError("only fixed ASCII controls are supported")
    return "[" + ", ".join(json.dumps(value) for value in arguments) + "]"


def trace_contains_extended_controls(trace: str) -> bool:
    normal = 'execve("/usr/bin/true", ' + control_argv_literal(["/usr/bin/true", TOKEN] + ARGUMENTS)
    fd = r'execveat\([0-9]+, "", ' + re.escape(control_argv_literal(["/usr/bin/true", FD_TOKEN] + ARGUMENTS))
    lines = trace.splitlines()
    # Require complete, same-line successful calls. Interleaved/unknown format
    # is incomplete, not permission to guess a missing argv or execution result.
    normal_ok = any(normal + ", [" in line and re.search(r"\)\s*=\s*0$", line) for line in lines)
    fd_ok = any(re.search(fd + r', \[.*\], AT_EMPTY_PATH\)\s*=\s*0$', line) for line in lines)
    return trace_contains_control(trace) and normal_ok and fd_ok


def binary_identity(path: Path) -> dict:
    path = path.resolve(strict=True)
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        if not stat_module.S_ISREG(stat.st_mode) or stat.st_uid != 0 or stat.st_mode & 0o022:
            raise ValueError("probe tool must be a root-owned non-writable regular file")
        if stat.st_size > 64 * 1024 * 1024:
            raise ValueError("probe tool exceeds identity-read limit")
        raw = stream.read(64 * 1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
        if any(getattr(stat, key) != getattr(after, key) for key in
               ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
            raise ValueError("probe tool opened object changed during identity read")
    if len(raw) != stat.st_size or len(raw) > 64 * 1024 * 1024:
        raise ValueError("probe tool changed or exceeded bounded identity read")
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": stat.st_size, "device": stat.st_dev, "inode": stat.st_ino}


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
    result = {"schema": "atlas-rust-semantic-observer-probe-v4", "source_sha": source_sha,
              "status": "incomplete", "qualified": False,
              "scope": "Linux exec/creation/cwd/exit capture and fixed live cwd/FD/RSS controls only",
              "limitations": ["not a Cargo/build observer", "no general argv/cwd trace parser",
                              "no network isolation proof", "no semantic or platform qualification",
                              "not full-tree discovery, continuous RSS enforcement or peak measurement"]}
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
            resources, result['sampler_source'] = load_resource_sampler()
            result['resource_control'] = {'admission': [], 'samples': [], 'qualified': False}
            parent_cwd = root / 'cwd-control'
            child_cwd = parent_cwd / '目录'
            parent_cwd.mkdir(mode=0o700)
            child_cwd.mkdir(mode=0o700)
            cwd_objects = [{'path': str(path), 'device': path.stat().st_dev,
                            'inode': path.stat().st_ino} for path in (parent_cwd, child_cwd)]
            result['cwd_objects'] = cwd_objects
            trace_path = root / "exec.trace"
            # All arguments and code are Atlas-owned constants, never supplied
            # by a project. Python's fd exec must actually use execveat; a libc
            # fallback to /proc execve will fail the evidence predicate.
            code = (
                "import os, subprocess, time\n"
                f"subprocess.run({['/usr/bin/true', TOKEN]!r}, check=True)\n"
                f"subprocess.run({['/usr/bin/true', TOKEN] + ARGUMENTS!r}, check=True)\n"
                "fd = os.open('/usr/bin/true', os.O_RDONLY | os.O_CLOEXEC)\n"
                "pid = os.fork()\n"
                "if pid == 0:\n"
                f"    os.execve(fd, {['/usr/bin/true', FD_TOKEN] + ARGUMENTS!r}, dict(os.environ))\n"
                "    os._exit(125)\n"
                "os.close(fd)\n"
                "_, status = os.waitpid(pid, 0)\n"
                "if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:\n"
                "    raise RuntimeError('fd exec control failed')\n"
                f"os.chdir({str(parent_cwd)!r})\n"
                "held_fd = os.open('/usr/bin/true', os.O_RDONLY | os.O_CLOEXEC)\n"
                "allocation = bytearray(16 * 1024 * 1024)\n"
                "for i in range(0, len(allocation), 4096): allocation[i] = 1\n"
                "read_fd, write_fd = os.pipe()\n"
                "child = os.fork()\n"
                "if child == 0:\n"
                "    os.close(read_fd)\n"
                f"    os.chdir({str(child_cwd)!r})\n"
                "    own = bytearray(16 * 1024 * 1024)\n"
                "    for i in range(0, len(own), 4096): own[i] = 1\n"
                "    os.write(write_fd, b'R')\n"
                "    os.close(write_fd)\n"
                "    time.sleep(3)\n"
                "    os._exit(0)\n"
                "os.close(write_fd)\n"
                "if os.read(read_fd, 1) != b'R': raise RuntimeError('RSS readiness failed')\n"
                "os.close(read_fd)\n"
                f"print('{RSS_TOKEN}', os.getpid(), child, held_fd, flush=True)\n"
                "_, status = os.waitpid(child, 0)\n"
                "if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:\n"
                "    raise RuntimeError('RSS child failed')\n"
                "os.close(held_fd)\n"
            )
            argv = [tracer, "-f", "-q", "-v", "-s", "65536", "-e", "trace=execve,execveat,clone,clone3,fork,vfork,chdir",
                    "-o", str(trace_path), "/usr/bin/python3", "-I", "-S", "-c", code]
            # No user project, package source, compiler, shell, or inherited credentials.
            env = {"PATH": "/usr/bin:/bin", "HOME": str(root), "LC_ALL": "C"}
            result["argv"] = argv
            result["env_keys"] = sorted(env)
            with (root / "stdout").open("xb") as stdout, (root / "stderr").open("xb") as stderr:
                process = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, start_new_session=True)
                admitted = None
                next_sample = 0
                while process.poll() is None:
                    if time.monotonic() - start >= 20:
                        raise TimeoutError("20 second owned probe deadline")
                    if sum(p.stat().st_size for p in root.iterdir() if p.is_file()) > 1024 * 1024:
                        raise ValueError("1 MiB probe output gate")
                    control = result['resource_control']
                    if admitted is None:
                        with (root / 'stdout').open('rb') as marker_stream:
                            marker = resource_marker(marker_stream.read(257))
                        if marker is not None:
                            parent, child, held_fd = marker
                            control['admission'] = admit_resource_controls(resources, process.pid, parent, child)
                            admitted = {row['pid']: row['starttime'] for row in control['admission']}
                            expected_cwds = {parent: cwd_objects[0], child: cwd_objects[1]}
                    now = time.monotonic()
                    if admitted is not None and len(control['samples']) < 3 and now >= next_sample:
                        # Save the raw sampled values before evaluating positive/limit gates.
                        sample_start = now
                        sample = resources.sample_admitted(admitted, process.pid)
                        sample['start_seconds'] = sample_start - start
                        control['samples'].append(sample)
                        sample['objects'] = control_object_samples(resources, control['admission'], expected_cwds,
                                                                   held_fd, result['tools'][2])
                        sample['end_seconds'] = time.monotonic() - start
                        resource_sample_gate(sample, parent, child)
                        if sample['end_seconds'] - sample['start_seconds'] > 0.5:
                            raise ValueError('controlled RSS sample exceeded 0.5 second duration')
                        if len(control['samples']) > 1:
                            previous = control['samples'][-2]
                            if sample['start_seconds'] - previous['end_seconds'] > 0.5:
                                raise ValueError('controlled RSS sample gap exceeded 0.5 seconds')
                        next_sample = time.monotonic() + 0.1
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
                    if result["exit_code"] != 0 or not trace_contains_extended_controls(result["exec.trace"]):
                        raise ValueError("complete successful execve/execveat argument controls not observed")
                    if len(result['resource_control']['samples']) != 3:
                        raise ValueError('three controlled RSS samples not observed')
                    result["status"] = "short-lived-controls-observed"
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
    raise SystemExit(0 if receipt["status"] == "short-lived-controls-observed" else 1)
