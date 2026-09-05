#!/usr/bin/env python3
"""Deterministic same-repository, multi-MCP refresh stress acceptance."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from codebase_atlas.config import AtlasConfig, SHARED_PROVIDER_LAYOUT
from codebase_atlas.provider_layout import provider_environment, provider_project_identity
from codebase_atlas.python_registration_store import registration_index_path
from codebase_atlas.refresh_recovery import journal_path


class StressFailure(RuntimeError):
    pass


def remove_tree_with_retries(path: Path, *, attempts: int = 10) -> bool:
    """Best-effort cleanup without replacing a successful stress result."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt + 1 == attempts:
                return False
            time.sleep(0.2 * (attempt + 1))
    return False


class McpClient:
    def __init__(self, command: list[str], environment: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=environment,
        )
        self._next_id = 1
        self._lock = threading.Lock()
        self.stderr: list[str] = []
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr.append(line.rstrip())

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if self.process.poll() is not None:
                raise StressFailure(f"MCP exited {self.process.returncode}: {self.stderr[-20:]}")
            request_id = self._next_id
            self._next_id += 1
            message = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
            assert self.process.stdin is not None and self.process.stdout is not None
            self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
            line = self.process.stdout.readline()
            if not line:
                raise StressFailure(f"MCP closed stdout: {self.stderr[-20:]}")
            response = json.loads(line)
            if response.get("id") != request_id:
                raise StressFailure(f"MCP response id mismatch: {response}")
            if "error" in response:
                raise StressFailure(f"MCP JSON-RPC error: {response['error']}")
            return response["result"]

    def initialize(self) -> None:
        with self._lock:
            assert self.process.stdin is not None and self.process.stdout is not None
            self.process.stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
            }) + "\n")
            self.process.stdin.flush()
            response = json.loads(self.process.stdout.readline())
            if response.get("id") != 0 or "error" in response:
                raise StressFailure(f"MCP initialize failed: {response}")

    def close(self) -> None:
        process = self.process
        if process.poll() is not None:
            return
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def run_json(command: list[str], environment: dict[str, str], timeout: float = 180) -> dict[str, Any]:
    if os.name == "nt":
        # A Windows Provider daemon may briefly inherit its frontend's standard
        # handles. Pipe capture would then wait for descendant EOF even after
        # the direct CLI process exits, so bind capture to seekable files.
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            completed = subprocess.run(
                command,
                env=environment,
                check=False,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=timeout,
            )
            stdout_file.seek(0)
            stderr_file.seek(0)
            completed.stdout = stdout_file.read().decode("utf-8", "replace")
            completed.stderr = stderr_file.read().decode("utf-8", "replace")
    else:
        completed = subprocess.run(
            command,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    if completed.returncode != 0:
        raise StressFailure(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout={completed.stdout[-4000:]}\nstderr={completed.stderr[-4000:]}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise StressFailure(f"command returned non-JSON: {completed.stdout[-2000:]}") from exc


def git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments], check=True, capture_output=True
    )


def parse_windows_process_table(payload_text: str) -> dict[int, tuple[int, str]]:
    payload = json.loads(payload_text)
    rows = [payload] if isinstance(payload, dict) else payload
    return {
        int(row["ProcessId"]): (
            int(row["ParentProcessId"]), str(row.get("CommandLine") or "")
        )
        for row in rows
    }


def process_table() -> dict[int, tuple[int, str]]:
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            raise StressFailure("PowerShell is required for Windows process cleanup checks")
        completed = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "Select-Object ProcessId,ParentProcessId,CommandLine | "
                "ConvertTo-Json -Compress",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return parse_windows_process_table(completed.stdout)
    completed = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="], check=True, capture_output=True, text=True
    )
    result: dict[int, tuple[int, str]] = {}
    for line in completed.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) >= 2:
            result[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) == 3 else "")
    return result


def descendants(parent: int) -> set[int]:
    table = process_table()
    found: set[int] = set()
    frontier = {parent}
    while frontier:
        children = {pid for pid, (ppid, _) in table.items() if ppid in frontier}
        children -= found
        found.update(children)
        frontier = children
    return found


def matching_processes(fragment: str) -> set[int]:
    if os.name == "nt":
        fragment = fragment.casefold()
    return {
        pid for pid, (_, command) in process_table().items()
        if fragment in (command.casefold() if os.name == "nt" else command)
        and pid != os.getpid()
    }


def structured(result: dict[str, Any], *, allow_error: bool = False) -> dict[str, Any]:
    if result.get("isError") and not allow_error:
        raise StressFailure(f"tool returned isError: {result.get('structuredContent')}")
    value = result.get("structuredContent")
    if not isinstance(value, dict):
        raise StressFailure(f"tool lacks structuredContent: {result}")
    return value


def validate_query(
    result: dict[str, Any], symbol: str, *, present: bool
) -> tuple[str, float]:
    value = structured(result)
    names = [node.get("name") for node in value.get("nodes", [])]
    if (symbol in names) != present:
        raise StressFailure(f"query presence mismatch for {symbol}: {names}")
    generation = value.get("generation_id")
    if not isinstance(generation, str) or not generation:
        raise StressFailure(f"query has no generation: {value}")
    automatic = value.get("index", {}).get("auto_update", {})
    if automatic.get("policy") != "on-query" or not automatic.get("ok"):
        raise StressFailure(f"automatic update mismatch: {automatic}")
    if automatic.get("generation") not in {None, generation}:
        raise StressFailure(f"automatic/query generation mismatch: {automatic} / {generation}")
    wait_ms = float(automatic.get("timings_ms", {}).get("wait_for_owner", 0.0))
    duration_ms = float(automatic.get("duration_ms", 0.0))
    if wait_ms > duration_ms + 1.0:
        raise StressFailure(f"wait exceeds wall duration: {automatic}")
    return generation, wait_ms


def parallel_definitions(
    clients: list[McpClient], symbols: list[str], *, present: bool
) -> tuple[str, float]:
    with ThreadPoolExecutor(max_workers=len(symbols)) as executor:
        futures = [
            executor.submit(client.call, "definition", {"symbol": symbol, "timeout_ms": 60000})
            for client, symbol in zip(clients, symbols)
        ]
        results = [future.result(timeout=90) for future in futures]
    observations = [
        validate_query(result, symbol, present=present)
        for result, symbol in zip(results, symbols)
    ]
    generations = {generation for generation, _ in observations}
    if len(generations) != 1:
        raise StressFailure(f"generation divergence: {sorted(generations)}")
    return next(iter(generations)), max(wait for _, wait in observations)


def assert_clean_staging(config: AtlasConfig) -> None:
    prefixes = (
        ".python-registrations-", ".generation-manifest-candidate-",
        ".generation-manifest-backup-", ".refresh-journal-",
        ".provider-generation-backup-", ".refresh-recovery-",
    )
    leftovers = [
        str(path)
        for directory in (config.data_dir, config.cache_dir)
        if directory.exists()
        for path in directory.iterdir()
        if path.name.startswith(prefixes)
    ]
    if leftovers:
        raise StressFailure(f"staging residue: {leftovers}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-binary", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--serena-python", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--language", choices=("python", "typescript"), default="python")
    parser.add_argument("--clients", type=int, default=4)
    parser.add_argument("--writers", type=int, default=3)
    parser.add_argument("--files-per-writer", type=int, default=1)
    parser.add_argument("--ledger-out", type=Path)
    args = parser.parse_args()
    if args.rounds < 1:
        raise SystemExit("--rounds must be positive")
    if args.writers < 1 or args.clients <= args.writers:
        raise SystemExit("--clients must be greater than positive --writers")
    if args.files_per_writer < 1:
        raise SystemExit("--files-per-writer must be positive")

    temporary_parent = "/private/tmp" if sys.platform == "darwin" else None
    root = Path(tempfile.mkdtemp(
        prefix="atlas-multi-mcp-stress-", dir=temporary_parent
    ))
    if sys.platform == "darwin":
        subprocess.run(["chmod", "-N", str(root)], check=True)
    root.chmod(0o700)
    ledger: dict[str, Any] = {"status": "running", "root": str(root), "rounds": []}
    clients: list[McpClient] = []
    observed_children: set[int] = set()
    baseline_provider_pids = matching_processes(str(args.provider_binary.resolve()))
    exit_code = 1
    try:
        repository = root / "repository"
        repository.mkdir()
        git(repository, "init", "-q")
        git(repository, "config", "user.email", "atlas@example.invalid")
        git(repository, "config", "user.name", "Atlas Stress")
        extension = ".py" if args.language == "python" else ".ts"

        def source(symbol: str, value: str) -> str:
            if args.language == "python":
                return f"def {symbol}():\n    return {value!r}\n"
            return f"export function {symbol}(): string {{ return {value!r}; }}\n"

        baseline_name = f"baseline{extension}"
        (repository / baseline_name).write_text(
            source("atlas_stress_baseline", "baseline"), encoding="utf-8"
        )
        committed = [baseline_name]
        tsconfig = None
        if args.language == "typescript":
            tsconfig = repository / "tsconfig.json"
            tsconfig.write_text(json.dumps({
                "compilerOptions": {
                    "target": "ES2022", "module": "ESNext", "strict": True,
                },
                "include": ["*.ts"],
            }, indent=2) + "\n", encoding="utf-8")
            (repository / "consumer.ts").write_text(
                "import { atlas_stress_baseline } from './baseline';\n"
                "export function atlas_stress_consumer(): string {\n"
                "  return atlas_stress_baseline();\n"
                "}\n",
                encoding="utf-8",
            )
            (repository / "baseline.ts").write_text(
                "export function atlas_stress_leaf(): string { return 'leaf'; }\n"
                "export function atlas_stress_baseline(): string {\n"
                "  return atlas_stress_leaf();\n"
                "}\n",
                encoding="utf-8",
            )
            (repository / "baseline.test.ts").write_text(
                "import { atlas_stress_baseline } from './baseline';\n"
                "declare function test(name: string, callback: () => unknown): void;\n"
                "test('baseline', () => atlas_stress_baseline());\n",
                encoding="utf-8",
            )
            committed.extend(["tsconfig.json", "consumer.ts", "baseline.test.ts"])
        git(repository, "add", *committed)
        git(repository, "commit", "-qm", "baseline")
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        if sys.platform == "darwin":
            subprocess.run(["chmod", "-N", str(runtime)], check=True)
        config_path = root / "project.toml"
        with_env = {
            **os.environ,
            "XDG_DATA_HOME": str((root / "xdg-data").resolve()),
            "XDG_RUNTIME_DIR": str(runtime.resolve()),
            "ATLAS_RUNTIME_DIR": str(runtime.resolve()),
            "CBM_RUNTIME_DIR": str(runtime.resolve()),
        }
        for name in (
            "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "ATLAS_RUNTIME_DIR", "CBM_RUNTIME_DIR"
        ):
            os.environ[name] = with_env[name]
        source_root = str(Path(__file__).resolve().parents[1] / "src")
        with_env["PYTHONPATH"] = source_root + os.pathsep + with_env.get("PYTHONPATH", "")
        project = provider_project_identity(repository)
        config = AtlasConfig(
            repository,
            args.language,
            args.node,
            args.provider_binary,
            args.serena_python,
            root / "atlas-data",
            project,
            node_bin_dir=args.node.parent,
            tsconfig=tsconfig,
            provider_layout=SHARED_PROVIDER_LAYOUT,
        )
        config.write(config_path)
        python = sys.executable
        base_command = [python, "-m", "codebase_atlas.cli"]
        initial = run_json(base_command + ["index", "--config", str(config_path)], with_env)
        if initial.get("status") not in {"indexed", "refreshed"}:
            raise StressFailure(f"initial index failed: {initial}")

        mcp_command = base_command + [
            "mcp", "--config", str(config_path), "--auto-update", "on-query",
            "--auto-update-timeout", "60", "--version-check", "off",
        ]

        def new_client() -> McpClient:
            client = McpClient(mcp_command, with_env)
            client.initialize()
            return client

        clients = [new_client() for _ in range(args.clients)]
        for client in clients:
            status = structured(client.call("project_status"))
            if status.get("identity", {}).get("repository") != str(repository.resolve()):
                raise StressFailure(f"repository identity mismatch: {status}")
            if status.get("auto_update", {}).get("policy") != "on-query":
                raise StressFailure(f"auto-update policy mismatch: {status}")

        if args.language == "typescript":
            if registration_index_path(config.data_dir).exists():
                raise StressFailure("TypeScript refresh published a Python sidecar")
            parity_generations: set[str] = set()
            references = structured(clients[0].call(
                "references", {
                    "symbol": "atlas_stress_baseline",
                    "max_nodes": 1,
                    "timeout_ms": 60000,
                }
            ))
            parity_generations.add(str(references.get("generation_id")))
            continuation = references.get("truncation", {}).get("continuation")
            if not isinstance(continuation, str) or not continuation:
                raise StressFailure(f"TypeScript continuation missing: {references}")
            reference_paths = {
                node.get("location", {}).get("path")
                for node in references.get("nodes", [])
            }
            while continuation:
                page = structured(clients[0].call(
                    "references", {
                        "symbol": "atlas_stress_baseline",
                        "max_nodes": 1,
                        "continuation": continuation,
                        "timeout_ms": 60000,
                    }
                ))
                parity_generations.add(str(page.get("generation_id")))
                reference_paths.update(
                    node.get("location", {}).get("path")
                    for node in page.get("nodes", [])
                )
                continuation = page.get("truncation", {}).get("continuation")
            if not {"consumer.ts", "baseline.test.ts"}.issubset(reference_paths):
                raise StressFailure(f"TypeScript reference parity failed: {references}")
            callers = structured(clients[0].call(
                "callers", {"symbol": "atlas_stress_baseline", "timeout_ms": 60000}
            ))
            parity_generations.add(str(callers.get("generation_id")))
            caller_paths = {
                node.get("location", {}).get("path") for node in callers.get("nodes", [])
            }
            if "consumer.ts" not in caller_paths:
                raise StressFailure(f"TypeScript caller parity failed: {callers}")
            related = structured(clients[0].call(
                "related_tests", {
                    "symbol": "atlas_stress_baseline", "timeout_ms": 60000,
                }
            ))
            parity_generations.add(str(related.get("generation_id")))
            if "baseline.test.ts" not in {
                node.get("location", {}).get("path") for node in related.get("nodes", [])
            }:
                raise StressFailure(f"TypeScript related-test parity failed: {related}")
            callees = structured(clients[0].call(
                "callees", {
                    "symbol": "atlas_stress_baseline", "timeout_ms": 60000,
                }
            ))
            parity_generations.add(str(callees.get("generation_id")))
            if "atlas_stress_leaf" not in {
                node.get("name") for node in callees.get("nodes", [])
            }:
                raise StressFailure(f"TypeScript callee parity failed: {callees}")
            impact = structured(clients[0].call(
                "impact", {
                    "symbol": "atlas_stress_baseline",
                    "direction": "upstream",
                    "depth": 2,
                    "timeout_ms": 60000,
                }
            ))
            parity_generations.add(str(impact.get("generation_id")))
            if not {"consumer.ts", "baseline.test.ts"}.intersection({
                node.get("location", {}).get("path")
                for node in impact.get("nodes", [])
            }):
                raise StressFailure(f"TypeScript impact parity failed: {impact}")
            if len(parity_generations) != 1 or "None" in parity_generations:
                raise StressFailure(
                    f"TypeScript parity generation divergence: {parity_generations}"
                )

            first_page = structured(clients[0].call(
                "references", {
                    "symbol": "atlas_stress_baseline",
                    "max_nodes": 1,
                    "timeout_ms": 60000,
                }
            ))
            stale_token = first_page.get("truncation", {}).get("continuation")
            if not isinstance(stale_token, str) or not stale_token:
                raise StressFailure(f"TypeScript stale-token fixture missing: {first_page}")
            continuation_path = repository / "continuation_mutation.ts"
            continuation_symbol = "atlas_continuation_mutation"
            continuation_path.write_text(
                "import { atlas_stress_baseline } from './baseline';\n"
                f"export function {continuation_symbol}(): number {{\n"
                "  return atlas_stress_baseline();\n"
                "}\n",
                encoding="utf-8",
            )
            validate_query(
                clients[1].call(
                    "definition", {"symbol": continuation_symbol, "timeout_ms": 60000}
                ),
                continuation_symbol,
                present=True,
            )
            stale_result = clients[0].call(
                "references", {
                    "symbol": "atlas_stress_baseline",
                    "max_nodes": 1,
                    "continuation": stale_token,
                    "timeout_ms": 60000,
                },
            )
            stale_payload = structured(stale_result, allow_error=True)
            continuation_error = str(stale_payload.get("error", ""))
            if not stale_result.get("isError") or continuation_error not in {
                "continuation_stale",
                "continuation_unavailable",
            }:
                raise StressFailure(
                    f"TypeScript prior-generation continuation was not rejected: {stale_result}"
                )
            continuation_path.unlink()
            validate_query(
                clients[1].call(
                    "definition", {"symbol": continuation_symbol, "timeout_ms": 60000}
                ),
                continuation_symbol,
                present=False,
            )

        # Fault injection: kill an MCP after it has published a recovery journal.
        crash_path = repository / f"crash_owner{extension}"
        crash_symbol = "atlas_crash_owner_symbol"
        crash_path.write_text(source(crash_symbol, "crash"), encoding="utf-8")
        with ThreadPoolExecutor(max_workers=1) as executor:
            crashed_call = executor.submit(
                clients[0].call, "definition", {"symbol": crash_symbol, "timeout_ms": 60000}
            )
            deadline = time.monotonic() + 15
            while not journal_path(config.data_dir).exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            if not journal_path(config.data_dir).exists():
                raise StressFailure("fault injection never observed an active refresh journal")
            observed_children.update(descendants(clients[0].process.pid))
            clients[0].process.kill()
            try:
                crashed_call.result(timeout=15)
            except Exception:
                pass
        clients[0].close()
        recovered = clients[1].call(
            "definition", {"symbol": crash_symbol, "timeout_ms": 60000}
        )
        validate_query(recovered, crash_symbol, present=True)
        crash_path.unlink()
        deleted_after_crash = clients[1].call(
            "definition", {"symbol": crash_symbol, "timeout_ms": 60000}
        )
        validate_query(deleted_after_crash, crash_symbol, present=False)
        clients[0] = new_client()

        for round_index in range(args.rounds):
            paths = [
                repository / f"agent_{writer}_{file_index}{extension}"
                for writer in range(args.writers)
                for file_index in range(args.files_per_writer)
            ]
            created_all = [
                f"atlas_agent_{writer}_file_{file_index}_round_{round_index}_created"
                for writer in range(args.writers)
                for file_index in range(args.files_per_writer)
            ]
            modified_all = [
                f"atlas_agent_{writer}_file_{file_index}_round_{round_index}_modified"
                for writer in range(args.writers)
                for file_index in range(args.files_per_writer)
            ]
            created = [
                created_all[writer * args.files_per_writer]
                for writer in range(args.writers)
            ]
            modified = [
                modified_all[writer * args.files_per_writer]
                for writer in range(args.writers)
            ]
            for path, symbol in zip(paths, created_all):
                path.write_text(source(symbol, "created"), encoding="utf-8")

            with ThreadPoolExecutor(max_workers=args.clients) as executor:
                query_futures = [
                    executor.submit(
                        client.call, "definition", {"symbol": symbol, "timeout_ms": 60000}
                    )
                    for client, symbol in zip(clients[:args.writers], created)
                ]
                time.sleep(0.1)
                status_result = clients[-1].call("project_status")
                status_value = structured(status_result)
                if status_value.get("status") == "refresh_wait_timeout":
                    if status_value.get("coordination", {}).get("waited_ms", 0) < 1500:
                        raise StressFailure(f"status wait timing missing: {status_value}")
                create_results = [future.result(timeout=90) for future in query_futures]
            create_observations = [
                validate_query(result, symbol, present=True)
                for result, symbol in zip(create_results, created)
            ]
            create_generations = {item[0] for item in create_observations}
            if len(create_generations) != 1:
                raise StressFailure(f"create generation divergence: {create_generations}")
            create_generation = next(iter(create_generations))

            for path, symbol in zip(paths, modified_all):
                path.write_text(source(symbol, "modified"), encoding="utf-8")
            modify_generation, modify_wait = parallel_definitions(
                clients[:args.writers], modified, present=True
            )
            if modify_generation == create_generation:
                raise StressFailure("modified sources reused the create generation")
            parallel_definitions(clients[:args.writers], created, present=False)

            renamed_paths = [
                path.with_name(f"renamed_{path.name}") for path in paths
            ]
            for path, renamed in zip(paths, renamed_paths):
                path.rename(renamed)
            rename_generation, rename_wait = parallel_definitions(
                clients[:args.writers], modified, present=True
            )
            if rename_generation == modify_generation:
                raise StressFailure("renamed sources reused the modify generation")
            sentinel_paths = [
                paths[writer * args.files_per_writer]
                for writer in range(args.writers)
            ]
            renamed_sentinel_paths = [
                renamed_paths[writer * args.files_per_writer]
                for writer in range(args.writers)
            ]
            for client, symbol, old_path, renamed_path in zip(
                clients[:args.writers], modified, sentinel_paths, renamed_sentinel_paths
            ):
                renamed_result = structured(client.call(
                    "definition", {"symbol": symbol, "timeout_ms": 60000}
                ))
                observed_paths = {
                    node.get("location", {}).get("path")
                    for node in renamed_result.get("nodes", [])
                }
                if old_path.name in observed_paths or renamed_path.name not in observed_paths:
                    raise StressFailure(
                        f"rename path replacement failed for {symbol}: {renamed_result}"
                    )

            for path in renamed_paths:
                path.unlink()
            delete_generation, delete_wait = parallel_definitions(
                clients[:args.writers], modified, present=False
            )
            if delete_generation == rename_generation:
                raise StressFailure("deleted sources reused the rename generation")
            final_statuses = [structured(client.call("project_status")) for client in clients]
            if {item.get("generation_id") for item in final_statuses} != {delete_generation}:
                raise StressFailure(f"final generation divergence: {final_statuses}")
            if any(client.process.poll() is not None for client in clients):
                raise StressFailure("an MCP process exited during a completed round")
            errors = "\n".join(line for client in clients for line in client.stderr).lower()
            forbidden = ("traceback", "global cbm lock", "no such file", "timeout error")
            if any(token in errors for token in forbidden):
                raise StressFailure(f"forbidden MCP diagnostics: {errors[-4000:]}")
            assert_clean_staging(config)
            ledger["rounds"].append({
                "round": round_index + 1,
                "create_generation": create_generation,
                "modify_generation": modify_generation,
                "rename_generation": rename_generation,
                "delete_generation": delete_generation,
                "max_wait_ms": max(
                    [item[1] for item in create_observations]
                    + [modify_wait, rename_wait, delete_wait]
                ),
                "status_during_refresh": status_value.get("status"),
            })

        doctor = run_json(base_command + ["doctor", "--config", str(config_path)], with_env)
        inspect = run_json(
            base_command + ["inspect", "--config", str(config_path), "--deep"], with_env
        )
        target = structured(clients[0].call(
            "definition", {"symbol": "atlas_stress_baseline", "timeout_ms": 60000}
        ))
        foreign = structured(clients[1].call(
            "definition", {"symbol": "symbol_from_an_unrelated_project", "timeout_ms": 60000}
        ))
        if [node.get("location", {}).get("path") for node in target.get("nodes", [])] != [baseline_name]:
            raise StressFailure(f"target symbol acceptance failed: {target}")
        if foreign.get("nodes"):
            raise StressFailure(f"foreign symbol leaked into target: {foreign}")
        if doctor.get("status") != "ready" or doctor.get("index", {}).get("status") != "fresh":
            raise StressFailure(f"doctor acceptance failed: {doctor}")
        provider_health = inspect.get("provider_database", inspect.get("provider", {}))
        if not provider_health.get("ok") or provider_health.get("quick_check") != ["ok"]:
            raise StressFailure(f"deep Provider acceptance failed: {inspect}")
        for client in clients:
            observed_children.update(descendants(client.process.pid))
            client.close()
        config_values = {}
        for key in ("auto_watch", "watcher_enabled"):
            completed = subprocess.run(
                [str(args.provider_binary), "config", "get", key],
                cwd=repository,
                env=provider_environment(config.cache_dir, repository, with_env),
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if completed.returncode != 0:
                raise StressFailure(
                    f"managed watcher config read failed: {key}: {completed.stderr}"
                )
            lines = [line.strip().lower() for line in completed.stdout.splitlines()]
            config_values[key] = lines[-1] if lines else ""
        if config_values != {"auto_watch": "false", "watcher_enabled": "false"}:
            raise StressFailure(f"managed watcher configuration mismatch: {config_values}")
        if subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain"],
            check=True, capture_output=True, text=True
        ).stdout:
            raise StressFailure("fixture source was not restored to its committed baseline")
        assert_clean_staging(config)
        if args.language == "typescript" and registration_index_path(
            config.data_dir
        ).exists():
            raise StressFailure("TypeScript run retained a Python sidecar")
        ledger.update({
            "status": "passed",
            "language": args.language,
            "clients": args.clients,
            "writers": args.writers,
            "files_per_writer": args.files_per_writer,
            "doctor": doctor.get("status"),
            "index": doctor.get("index", {}).get("status"),
            "provider_deep": provider_health.get("status", "healthy"),
            "target_hit": baseline_name,
            "foreign_hits": 0,
            "watcher": config_values,
        })
        (root / "ledger.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        exit_code = 0
    except BaseException as exc:
        ledger.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        (root / "ledger.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    finally:
        for client in clients:
            observed_children.update(descendants(client.process.pid))
            client.close()
        deadline = time.monotonic() + 20
        live: set[int] = set()
        new_provider_pids: set[int] = set()
        rooted_processes: set[int] = set()
        while time.monotonic() < deadline:
            table = process_table()
            live = {pid for pid in observed_children if pid in table}
            new_provider_pids = matching_processes(
                str(args.provider_binary.resolve())
            ) - baseline_provider_pids
            rooted_processes = {
                pid for pid, (_, command) in table.items()
                if (
                    (str(root).casefold() in command.casefold())
                    if os.name == "nt" else (str(root) in command)
                )
                and pid != os.getpid()
            }
            if not live and not new_provider_pids and not rooted_processes:
                break
            time.sleep(0.2)
        else:
            ledger["status"] = "failed"
            ledger["error"] = (
                f"residual processes: children={sorted(live)} "
                f"provider={sorted(new_provider_pids)} rooted={sorted(rooted_processes)}"
            )
            (root / "ledger.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")
            exit_code = 1
        if args.ledger_out is not None:
            destination = args.ledger_out.resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if ledger.get("status") == "passed":
            rounds = ledger.get("rounds", [])
            summary = {
                key: ledger[key]
                for key in (
                    "status", "doctor", "index", "provider_deep",
                    "target_hit", "foreign_hits", "watcher", "language",
                    "clients", "writers", "files_per_writer",
                )
            }
            summary.update({
                "rounds": len(rounds),
                "max_wait_ms": max(
                    (float(item.get("max_wait_ms", 0.0)) for item in rounds),
                    default=0.0,
                ),
                "status_during_refresh": sorted({
                    str(item.get("status_during_refresh")) for item in rounds
                }),
            })
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            if not remove_tree_with_retries(root):
                print(
                    f"warning: passed diagnostics could not be removed: {root}",
                    file=sys.stderr,
                )
        elif exit_code == 1 and "error" in ledger:
            print(json.dumps(ledger, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            print(f"diagnostics retained at {root}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
