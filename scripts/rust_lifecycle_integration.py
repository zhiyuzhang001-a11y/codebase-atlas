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
import sys
from codebase_atlas.rust_owned_command import run_owned


def git_audit_launch(executable, argv, git: str | None) -> bool:
    """Recognize only Git, including Windows Popen(None, commandline) audit."""
    allowed = {"git"}
    if git is not None:
        allowed.add(git)
    if executable is not None:
        return str(executable) in allowed
    if isinstance(argv, str):
        # CPython has already used list2cmdline. Compare the exact first token,
        # including quoting of a trusted absolute Git path; never allow shells
        # or basename-only matches for an arbitrary executable path.
        return any(argv == token or argv.startswith(token + " ")
                   for token in (subprocess.list2cmdline([path]) for path in allowed))
    return isinstance(argv, (tuple, list)) and bool(argv) and str(argv[0]) in allowed


def executable_trap(work: Path, name: str):
    """Harness-owned program with distinct positive/forbidden markers, no network."""
    if name not in {"wrapper", "rustup"}:
        raise ValueError("Unknown diagnostic executable trap")
    marker = work / ("forbidden-" + name)
    positive_marker = work / (name + "-positive-control")
    wrapper = work / (name + ".cmd" if os.name == "nt" else name)
    # Separate markers distinguish the intentional diagnostic execution from
    # forbidden normal-hook execution; never clear a marker to make a test pass.
    if marker.exists() or positive_marker.exists():
        raise RuntimeError(name + " marker already exists before qualification")
    payload = (f'@echo off\r\nif "%~1"=="--positive-control" (\r\n'
               f'echo executed>"{positive_marker}"\r\n) else (\r\n'
               f'echo executed>"{marker}"\r\n)\r\n' if os.name == "nt" else
               f'#!{sys.executable}\nimport sys\nfrom pathlib import Path\n'
               f'Path({str(positive_marker)!r} if sys.argv[1:] == ["--positive-control"] '
               f'else {str(marker)!r}).write_text("executed")\n')
    with wrapper.open("x", encoding="utf-8") as stream:
        stream.write(payload)
    if os.name != "nt":
        wrapper.chmod(0o700)
    # Only this harness-owned, non-project fixture is intentionally executed.
    # Windows batch launch is explicit, not an inherited COMSPEC or shell=True.
    command = ([str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"),
                "/d", "/c", str(wrapper), "--positive-control"] if os.name == "nt"
               else ([sys.executable, str(wrapper), "--positive-control"] if name == "rustup"
                     else [str(wrapper), "--positive-control"]))
    # Validate the rustup sentinel payload with an explicit interpreter; do not
    # relax the native observer's blanket ban on any actual rustup exec, even
    # for this control. Direct-entry execution remains forbidden in all hooks.
    control_env = {name: os.environ[name] for name in
                   ("SystemRoot", "WINDIR", "TEMP", "TMP") if name in os.environ}
    control = run_owned(command, cwd=work, env=control_env, timeout=5,
                        capture_output=True, text=True, check=True)
    if not positive_marker.is_file() or marker.exists():
        raise RuntimeError(name + " executable positive control failed")
    return wrapper, marker, {"executed": True, "exit_code": control.returncode, "argv": command,
                            "scope": "harness payload control; not permission to launch rustup"}


def hostile_hook_check(repository: Path, work: Path):
    """Real normal hooks with hostile env; Python audit only, NOT OS tracing."""
    from codebase_atlas.config import AtlasConfig, diagnose
    from codebase_atlas.rust_project import load_rust_service, _load_index
    from codebase_atlas.rust_mcp_refresh import RustMcpRefreshCoordinator
    from codebase_atlas.service import QueryRequest
    config_path = repository / ".codebase-atlas.toml"
    config = AtlasConfig.load(config_path)
    wrapper, marker, wrapper_control = executable_trap(work, "wrapper")
    rustup, rustup_marker, rustup_control = executable_trap(work, "rustup")
    service = load_rust_service(config)  # Cold, verified but no analyzer startup.
    service.start()  # Lazy frontend state only; T2 has not spawned.
    coordinator = RustMcpRefreshCoordinator(config, service, {}, config_path=config_path)
    observer = {"active": False, "forbidden": [], "git_argv": [], "forbidden_launches": []}
    git = shutil.which("git")
    def audit(event, args):
        if not observer["active"]:
            return
        if event == "subprocess.Popen":
            executable, argv = args[:2]
            if git_audit_launch(executable, argv, git):
                observer["git_argv"].append(argv if isinstance(argv, str) else list(argv))
                return
            observer["forbidden_launches"].append({"executable": str(executable), "argv": argv})
        elif not (event in {"os.system", "os.exec", "os.posix_spawn", "socket.connect", "socket.getaddrinfo"}
                  or event.startswith("os.spawn")):
            return
        observer["forbidden"].append(event)
        raise RuntimeError("forbidden Python execution/network attempt in hostile hook")
    sys.addaudithook(audit)
    original_config = config_path.read_bytes()
    original_generation = _load_index(config.data_dir, config.repository, config.project)[0]["generation_id"]
    rows = []
    try:
        traps = (("wrapper", {"RUSTC_WRAPPER": str(wrapper)}),
                 ("rustup-download-entry", {
                     "RUSTUP_TOOLCHAIN": "atlas-unavailable-download-trap",
                     "PATH": str(rustup.parent) + os.pathsep + os.environ.get("PATH", ""),
                 }))
        for case, environment in traps:
            with patch.dict(os.environ, environment):
                observer["active"] = True
                payload, code = enable_project(repository, language="rust")
                rows.append({"case": case, "hook": "enable", "exit_code": code, "result": payload})
                if code == 0:
                    raise RuntimeError("enable accepted hostile " + case)
                checks = diagnose(config)
                rows.append({"case": case, "hook": "doctor", "checks": checks})
                if not any(c["name"] == "rust_toolchain" and not c["ok"] for c in checks):
                    raise RuntimeError("doctor accepted hostile " + case)
                response = service.query(QueryRequest("definition", "run", {
                    "source_path": "src/lib.rs", "source_line": 4, "source_column": 28,
                    "target_path": "src/left.rs"}))
                rows.append({"case": case, "hook": "cold_query", "status": response.status,
                             "completeness": response.completeness, "node_count": len(response.nodes)})
                if response.status != "unavailable" or response.nodes:
                    raise RuntimeError("cold query accepted hostile " + case)
                result = coordinator.refresh(timeout_ms=60000)
                rows.append({"case": case, "hook": "refresh", "result": result})
                if result.get("status") != "failed" or not result.get("previous_generation_preserved"):
                    raise RuntimeError("refresh accepted hostile " + case)
    finally:
        observer["active"] = False
        service.close()
        evidence = {"hooks": rows, "observer": "Python audit only; NOT whole-tree or OS network",
                    "wrapper_positive_control": wrapper_control,
                    "rustup_positive_control": rustup_control,
                    "rustup_trap_scope": "executable download-entry marker, not actual network denial",
                    "rustup_download_entry_executed": rustup_marker.exists(),
                    "forbidden_events": observer["forbidden"], "git_argv": observer["git_argv"],
                    "forbidden_launches": observer["forbidden_launches"],
                    "wrapper_executed": marker.exists(),
                    "config_unchanged": config_path.read_bytes() == original_config,
                    "generation_preserved": _load_index(config.data_dir, config.repository, config.project)[0]["generation_id"] == original_generation}
        (work / "hostile-hooks.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    if (evidence["forbidden_events"] or evidence["wrapper_executed"] or evidence["rustup_download_entry_executed"]
            or not evidence["config_unchanged"] or not evidence["generation_preserved"]):
        raise RuntimeError("hostile hooks changed config or attempted forbidden execution")
    return {"status": "ready", **evidence}, 0

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


def active_config_change_check(repository: Path, work: Path, service, query):
    """Mutate only the isolated Cargo home after a real T2 session is warm.

    Not a continuous mutation race or OS denial proof. Never modify a preexisting
    config; keep the positive-controlled wrapper marker as forbidden evidence.
    """
    from codebase_atlas.config import AtlasConfig
    from codebase_atlas.rust_project import _load_index
    provider = service.rust_provider
    process = provider._process
    if process is None or process.poll() is not None or not provider.running:
        raise RuntimeError("active config check requires a running analyzer")
    config_path = repository / ".codebase-atlas.toml"
    original_config = config_path.read_bytes()
    config = AtlasConfig.load(config_path)
    generation = _load_index(config.data_dir, config.repository, config.project)[0]["generation_id"]
    wrapper = work / ("wrapper.cmd" if os.name == "nt" else "wrapper")
    marker = work / "forbidden-wrapper"
    if not (work / "wrapper-positive-control").is_file() or marker.exists():
        raise RuntimeError("active config check lacks a clean wrapper positive control")
    path = provider.runtime.cargo_home / "config.toml"
    payload = ('[build]\nrustc-wrapper = ' + json.dumps(str(wrapper)) + '\n').encode()
    with path.open("xb") as stream:
        stream.write(payload)
    try:
        result = query()
        evidence = {"status": result.get("status"), "node_count": len(result.get("nodes", [])),
                    "process_id": process.pid, "process_exit_code": process.poll(),
                    "analyzer_stopped": not provider.running,
                    "wrapper_executed": marker.exists(),
                    "hostile_config_unchanged": path.read_bytes() == payload,
                    "project_config_unchanged": config_path.read_bytes() == original_config,
                    "generation_preserved": _load_index(config.data_dir, config.repository,
                                                         config.project)[0]["generation_id"] == generation,
                    "scope": "warm source-API MCP boundary; not continuous mutation or OS denial"}
        if (evidence["status"] != "unavailable" or evidence["node_count"]
                or not evidence["analyzer_stopped"] or evidence["process_exit_code"] is None
                or evidence["wrapper_executed"] or not evidence["hostile_config_unchanged"]
                or not evidence["project_config_unchanged"] or not evidence["generation_preserved"]):
            raise RuntimeError("active Cargo configuration rejection failed: " + json.dumps(evidence))
        return evidence
    finally:
        # Only the exact fixture bytes exclusively created above are ours to remove.
        if path.read_bytes() != payload:
            raise RuntimeError("active fixture config changed; refusing cleanup")
        path.unlink()


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
        active_config = active_config_change_check(repository, repository.parent, service,
            lambda: call("definition", {"symbol": "run", "target_path": "src/left.rs",
                "source_path": "src/lib.rs", "source_line": 4, "source_column": 28}))
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
            "active_cargo_config_change": active_config,
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
                                    ("hostile_hooks", lambda: hostile_hook_check(repository, work)),
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
                        "remove_again": "removed", "mcp": "ready", "hostile_hooks": "ready"}[operation]
            if code != 0 or payload.get("status") != expected:
                raise RuntimeError(operation + " did not pass")
        if subprocess.check_output(["git", "diff", "HEAD"], cwd=repository) != before:
            raise RuntimeError("tracked fixture source changed")
    summary = {"status": "pass", "qualification": "internal_real_tool_only",
               "installed_wheel_tested": False, "tracked_fixture_source_unchanged": True,
               "public_enabled": languages.get_language("rust").public_enabled}
    print(json.dumps(summary))
    (work / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    main()
