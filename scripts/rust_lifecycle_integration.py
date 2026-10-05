#!/usr/bin/env python3
"""Internal real-tool lifecycle integration, NOT installed-wheel qualification.

Only temporary fixture projects are written. The closed product gate is patched
inside this diagnostic process; no source, global connection or Release changes.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from types import MappingProxyType
from unittest.mock import patch
import argparse

from codebase_atlas import languages
from codebase_atlas.rust_installation import release_lock, verify_existing_toolchain, save_toolchain_receipt, toolchain_store
from codebase_atlas.rust_scanner_installation import install_scanner_asset
from codebase_atlas.release_installation import current_platform_target
from codebase_atlas.simple_cli import enable_project, status_project, verify_project, stop_project, remove_project


def mcp_check(repository):
    from codebase_atlas.config import AtlasConfig
    from codebase_atlas.rust_project import load_rust_service
    from codebase_atlas.rust_mcp_refresh import RustMcpRefreshCoordinator
    from codebase_atlas.mcp import McpServer
    from codebase_atlas.simple_cli import _rust_verification_query
    config = AtlasConfig.load(repository / ".codebase-atlas.toml")
    service = load_rust_service(config)
    status = {"identity": {"repository": str(repository), "project": config.project}}
    coordinator = RustMcpRefreshCoordinator(config, service, status)
    server = McpServer(service, index_status=status, refresh_coordinator=coordinator)
    processes = []
    def call(name, arguments):
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": name, "arguments": arguments}})
        if response.get("error") or response.get("result", {}).get("isError"):
            raise RuntimeError("MCP " + name + " failed: " + json.dumps(response))
        return response["result"]["structuredContent"]
    def query(symbol, path, position):
        result = call("definition", {"symbol": symbol, "target_path": path, **position})
        process = service.rust_provider._process
        if process is not None and process not in processes:
            processes.append(process)
        return result
    with service:
        first = call("project_status", {})
        before = first["generation_id"]
        _rust_verification_query(config, repository / ".codebase-atlas.toml", query=query)
        refreshed = call("refresh_index", {"mode": "fast", "timeout_ms": 60000})
        if refreshed["status"] != "refreshed":
            raise RuntimeError("MCP refresh did not publish")
        after = call("project_status", {})
        if after["generation_id"] == before or after["identity"] != status["identity"]:
            raise RuntimeError("MCP generation/identity binding failed")
        _rust_verification_query(config, repository / ".codebase-atlas.toml", query=query)
    if not processes or any(process.poll() is None for process in processes):
        raise RuntimeError("MCP owned child cleanup failed")
    return {"status": "ready", "generation_before": before, "generation_after": after["generation_id"],
            "owned_process_cleanup": "pass", "process_ids": [process.pid for process in processes],
            "live_codex_task_tested": False}, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--archives", type=Path, required=True)
    parser.add_argument("--scanner", type=Path, required=True)
    parser.add_argument("--scanner-sha256", required=True)
    parser.add_argument("--scanner-commit", required=True)
    args = parser.parse_args()
    work = args.work_dir.resolve()
    source = Path(__file__).resolve().parents[1]
    if work.is_relative_to(source) or (work.exists() and any(work.iterdir())):
        raise RuntimeError("integration work directory must be empty and outside the checkout")
    work.mkdir(parents=True, mode=0o700, exist_ok=True)
    repository = work / "project"
    shutil.copytree(source / "tests/fixtures/rust-product/crate", repository)
    for argv in (["git", "init", "-q"], ["git", "add", "."],
                 ["git", "-c", "user.name=Atlas Integration", "-c", "user.email=atlas@example.invalid",
                  "commit", "-qm", "frozen fixture"]):
        subprocess.run(argv, cwd=repository, check=True, capture_output=True)
    before = subprocess.check_output(["git", "diff", "HEAD"], cwd=repository)
    results = []
    candidate = dict(languages._LANGUAGES)
    candidate["rust"] = replace(candidate["rust"], public_enabled=True)
    environment = {"XDG_DATA_HOME": str(work / "data")}
    with patch.dict(os.environ, environment), patch.object(languages, "_LANGUAGES", MappingProxyType(candidate)):
        lock = release_lock()
        target = current_platform_target()
        components = dict(lock["targets"][target]["components"])
        components["rust-src"] = lock["rust_src"]
        archives = {name: args.archives / Path(identity["url"]).name for name, identity in components.items()}
        document = verify_existing_toolchain(args.toolchain.resolve(), archives, target)
        save_toolchain_receipt(document, toolchain_store())
        install_scanner_asset(args.scanner, sha256=args.scanner_sha256, commit=args.scanner_commit, target=target)
        for operation, function in (("enable", lambda: enable_project(repository, language="rust")),
                                    ("status", lambda: status_project(repository)),
                                    ("mcp", lambda: mcp_check(repository)),
                                    ("verify", lambda: verify_project(repository)),
                                    ("stop", lambda: stop_project(repository)),
                                    ("resume", lambda: enable_project(repository, language="rust")),
                                    ("remove", lambda: remove_project(repository)),
                                    ("remove_again", lambda: remove_project(repository))):
            started = time.monotonic()
            payload, code = function()
            result = {"operation": operation, "exit_code": code, "elapsed_seconds": time.monotonic() - started,
                      "result": payload}
            results.append(result)
            print(json.dumps(result), flush=True)
            (work / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            expected = {"enable": "ready", "status": "ready", "verify": "PASS",
                        "stop": "stopped", "resume": "ready", "remove": "removed",
                        "remove_again": "removed", "mcp": "ready"}[operation]
            if code != 0 or payload.get("status") != expected:
                raise RuntimeError(operation + " did not pass")
        if subprocess.check_output(["git", "diff", "HEAD"], cwd=repository) != before:
            raise RuntimeError("tracked fixture source changed")
    print(json.dumps({"status": "pass", "qualification": "internal_real_tool_only",
                      "installed_wheel_tested": False, "public_enabled": languages.get_language("rust").public_enabled}))


if __name__ == "__main__":
    main()
