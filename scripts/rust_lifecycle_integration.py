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


def install_execution_sentinels(repository: Path, work: Path) -> None:
    """Temporary safety fixture only; neither sentinel is intentionally run."""
    build_marker = json.dumps(str(work / "forbidden-build-script"), ensure_ascii=False)
    macro_marker = json.dumps(str(work / "forbidden-proc-macro"), ensure_ascii=False)
    (repository / "build.rs").write_text(
        'fn main() { std::fs::write(' + build_marker + ', b"executed").unwrap(); }\n', encoding="utf-8")
    macro = repository / "sentinel-macro"
    (macro / "src").mkdir(parents=True)
    (macro / "Cargo.toml").write_text(
        '[package]\nname="atlas-sentinel-macro"\nversion="0.1.0"\nedition="2021"\n'
        '[lib]\nproc-macro=true\n', encoding="utf-8")
    (macro / "src/lib.rs").write_text(
        'extern crate proc_macro;\n#[proc_macro_derive(Sentinel)]\n'
        'pub fn sentinel(_: proc_macro::TokenStream) -> proc_macro::TokenStream {\n'
        'std::fs::write(' + macro_marker + ', b"executed").unwrap();\n'
        'proc_macro::TokenStream::new()\n}\n', encoding="utf-8")
    with (repository / "Cargo.toml").open("a", encoding="utf-8") as output:
        output.write('\n[dependencies]\natlas-sentinel-macro = { path="sentinel-macro" }\n')
    with (repository / "src/lib.rs").open("a", encoding="utf-8") as output:
        output.write('\n#[derive(atlas_sentinel_macro::Sentinel)]\npub struct SentinelProbe;\n')


def execution_sentinel_state(work: Path) -> dict[str, bool]:
    return {name: (work / name).exists() for name in ("forbidden-build-script", "forbidden-proc-macro")}


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
    frozen_results = []
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
    def frozen_definition():
        contract = json.loads((Path(__file__).resolve().parents[1] / "cases/rust-product-enablement.v1.json").read_text())
        request = next(item for item in contract["fixture_queries"] if item["id"] == "crate-left")
        result = query(request["symbol"], request["expected_path"],
                       {key: request[key] for key in ("source_path", "source_line", "source_column")})
        frozen_results.append(result)
        if (result.get("status") not in {"complete_exact", "exact_hits_partial_scope"}
                or not any(node.get("location", {}).get("path") == request["expected_path"]
                           and node.get("location", {}).get("start_line") == request["expected_line"]
                           for node in result.get("nodes", []))):
            raise RuntimeError("frozen crate-left query did not return its exact target")
    with service:
        first = call("project_status", {})
        before = first["generation_id"]
        _rust_verification_query(config, repository / ".codebase-atlas.toml", query=query)
        frozen_definition()
        refreshed = call("refresh_index", {"mode": "fast", "timeout_ms": 60000})
        if refreshed["status"] != "refreshed":
            raise RuntimeError("MCP refresh did not publish")
        after = call("project_status", {})
        if after["generation_id"] == before or after["identity"] != status["identity"]:
            raise RuntimeError("MCP generation/identity binding failed")
        _rust_verification_query(config, repository / ".codebase-atlas.toml", query=query)
        frozen_definition()
    if not processes or any(process.poll() is None for process in processes):
        raise RuntimeError("MCP owned child cleanup failed")
    return {"status": "ready", "generation_before": before, "generation_after": after["generation_id"],
            "owned_process_cleanup": "pass", "process_ids": [process.pid for process in processes],
            "frozen_crate_left_before_after": frozen_results,
            "live_codex_task_tested": False}, 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    archives_group = parser.add_mutually_exclusive_group(required=True)
    archives_group.add_argument("--archives", type=Path)
    archives_group.add_argument("--archive-map", type=Path)
    parser.add_argument("--scanner", type=Path, required=True)
    parser.add_argument("--scanner-sha256", required=True)
    parser.add_argument("--scanner-commit", required=True)
    parser.add_argument("--execution-sentinels", action="store_true")
    args = parser.parse_args(argv)
    work = args.work_dir.resolve()
    source = Path(__file__).resolve().parents[1]
    source_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True,
                                text=True, check=True, timeout=5).stdout.strip()
    if args.scanner_commit != source_sha:
        raise RuntimeError("internal lifecycle requires the exact current-source scanner")
    if work.is_relative_to(source) or (work.exists() and any(work.iterdir())):
        raise RuntimeError("integration work directory must be empty and outside the checkout")
    work.mkdir(parents=True, mode=0o700, exist_ok=True)
    repository = work / "project"
    shutil.copytree(source / "tests/fixtures/rust-product/crate", repository)
    if args.execution_sentinels:
        install_execution_sentinels(repository, work)
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
        if args.archive_map is not None:
            paths = json.loads(args.archive_map.read_text(encoding="utf-8"))
            if set(paths) != set(components):
                raise RuntimeError("archive map must contain the exact official component inventory")
            archives = {name: Path(path) for name, path in paths.items()}
        else:
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
                      "result": payload, "atlas_source_sha": source_sha,
                      "scanner_source_sha": args.scanner_commit}
            if args.execution_sentinels:
                result["project_execution_sentinels"] = execution_sentinel_state(work)
            results.append(result)
            print(json.dumps(result), flush=True)
            (work / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            if any(result.get("project_execution_sentinels", {}).values()):
                raise RuntimeError(operation + " executed forbidden fixture code")
            expected = {"enable": "ready", "status": "ready", "verify": "PASS",
                        "stop": "stopped", "resume": "ready", "remove": "removed",
                        "remove_again": "removed", "mcp": "ready"}[operation]
            if code != 0 or payload.get("status") != expected:
                raise RuntimeError(operation + " did not pass")
        if subprocess.check_output(["git", "diff", "HEAD"], cwd=repository) != before:
            raise RuntimeError("tracked fixture source changed")
    summary = {"status": "pass", "qualification": "internal_real_tool_only",
               "installed_wheel_tested": False, "tracked_fixture_source_unchanged": True,
               "public_enabled": languages.get_language("rust").public_enabled}
    print(json.dumps(summary))
    return summary


if __name__ == "__main__":
    main()
