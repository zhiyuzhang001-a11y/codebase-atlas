"""Codebase Atlas command-line entry point."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shlex
import sys
import time
import os
import subprocess

from . import __version__
from .config import AtlasConfig, CONFIG_NAME, _asset, diagnose
from .index_state import (
    index_freshness,
    provider_database_health,
    record_index_state,
    repository_snapshot,
)
from .lifecycle import CodebaseMemoryDaemon
from .maintenance import apply_cleanup, cleanup_plan, inspect_installation, repair_plan
from .mcp import McpServer, run_stdio
from .operations import (
    STALE_POLICIES,
    attach_operational_status,
    operational_index_status,
    stale_policy_error,
    unknown_operational_status,
)
from .onboarding import OnboardingInputs, apply_plan, build_plan
from .providers import CodebaseMemoryImpactProvider, SerenaSemanticProvider, TypeScriptTestProvider
from .runtime import required_checks_ok, runtime_checks
from .service import AtlasService, QueryRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codebase-atlas")
    parser.add_argument("--version", action="store_true")
    commands = parser.add_subparsers(dest="command")
    initialize = commands.add_parser("init", help="create a project-local Atlas configuration")
    initialize.add_argument("--repo", type=Path, default=Path.cwd())
    initialize.add_argument("--config", type=Path)
    initialize.add_argument("--language", choices=("python", "typescript"))
    initialize.add_argument("--node", type=Path)
    initialize.add_argument("--cbm-binary", type=Path)
    initialize.add_argument("--serena-python", type=Path)
    initialize.add_argument("--node-bin-dir", type=Path)
    initialize.add_argument("--tsconfig", type=Path)
    initialize.add_argument("--data-dir", type=Path)
    setup = commands.add_parser(
        "setup", help="read-only compatibility check for required local runtimes"
    )
    setup.add_argument("--repo", type=Path, default=Path.cwd())
    setup.add_argument("--config", type=Path)
    setup.add_argument("--language", choices=("python", "typescript"))
    setup.add_argument("--node", type=Path)
    setup.add_argument("--cbm-binary", type=Path)
    setup.add_argument("--serena-python", type=Path)
    setup.add_argument("--node-bin-dir", type=Path)
    setup.add_argument("--tsconfig", type=Path)
    onboard = commands.add_parser("onboard", help="plan or explicitly apply a guided local onboarding flow")
    onboard.add_argument("--repo", type=Path, default=Path.cwd())
    onboard.add_argument("--config", type=Path)
    onboard.add_argument("--language", choices=("python", "typescript"))
    onboard.add_argument("--node", type=Path)
    onboard.add_argument("--cbm-binary", type=Path)
    onboard.add_argument("--serena-python", type=Path)
    onboard.add_argument("--node-bin-dir", type=Path)
    onboard.add_argument("--tsconfig", type=Path)
    onboard.add_argument("--data-dir", type=Path)
    onboard.add_argument("--mode", choices=("fast", "moderate", "full"), default="fast")
    onboard.add_argument("--apply", action="store_true")
    doctor = commands.add_parser("doctor", help="check configured runtimes and index state")
    doctor.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    inspect = commands.add_parser("inspect", help="inspect index health and storage without modifying it")
    inspect.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    inspect.add_argument(
        "--deep", action="store_true",
        help="also run SQLite quick_check; this may take time for a large index",
    )
    repair = commands.add_parser(
        "repair", help="diagnose recovery; use --apply for an explicit safe Provider update"
    )
    repair.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    repair.add_argument("--mode", choices=("fast", "moderate", "full"), default="fast")
    repair.add_argument(
        "--apply", action="store_true",
        help="apply the proposed repair; without this flag the command is read-only",
    )
    clean = commands.add_parser(
        "clean", help="dry-run cleanup of recognized obsolete Atlas files"
    )
    clean.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    clean.add_argument(
        "--apply", action="store_true",
        help="remove exactly the files in the displayed in-memory plan",
    )
    index = commands.add_parser("index", help="build the configured structural index")
    index.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    index.add_argument("--mode", choices=("fast", "moderate", "full"), default="fast")
    update = commands.add_parser("update", help="safely update a configured structural index")
    update.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    update.add_argument("--mode", choices=("fast", "moderate", "full"), default="fast")
    update.add_argument(
        "--force-provider",
        action="store_true",
        help="run the Provider even when Atlas source state is already current",
    )
    related = commands.add_parser("related-tests")
    related.add_argument("--repo", type=Path, required=True)
    related.add_argument("--symbol", required=True)
    related.add_argument("--target-path", default="")
    related.add_argument("--target-owner", default="")
    related.add_argument("--node", type=Path, required=True)
    related.add_argument("--tsconfig", type=Path)
    related.add_argument(
        "--analyzer",
        type=Path,
        default=_asset("ts_test_analyzer.mjs"),
    )
    impact = commands.add_parser("impact")
    impact.add_argument("--repo", type=Path, required=True)
    impact.add_argument("--symbol", required=True)
    impact.add_argument("--direction", choices=("upstream", "downstream"), required=True)
    impact.add_argument("--depth", type=int, required=True)
    impact.add_argument("--binary", type=Path, required=True)
    impact.add_argument("--cache-dir", type=Path, required=True)
    impact.add_argument("--project", required=True)
    impact.add_argument("--target-path", default="")
    impact.add_argument("--target-owner", default="")
    impact.add_argument("--max-nodes", type=int, default=100)
    impact.add_argument("--max-edges", type=int, default=200)
    impact.add_argument("--timeout-ms", type=int, default=30_000)
    mcp = commands.add_parser("mcp", help="run the read-only MCP server over stdio")
    mcp.add_argument("--config", type=Path)
    mcp.add_argument("--repo", type=Path)
    mcp.add_argument("--node", type=Path)
    mcp.add_argument("--analyzer", type=Path, default=_asset("ts_test_analyzer.mjs"))
    mcp.add_argument("--binary", type=Path)
    mcp.add_argument("--cache-dir", type=Path)
    mcp.add_argument("--project")
    mcp.add_argument("--serena-python", type=Path)
    mcp.add_argument("--serena-runner", type=Path, default=_asset("serena_runner.py"))
    mcp.add_argument("--serena-home", type=Path)
    mcp.add_argument("--metadata-root", type=Path)
    mcp.add_argument("--language", choices=("python", "typescript"))
    mcp.add_argument("--node-bin-dir", type=Path)
    mcp.add_argument("--tsconfig", type=Path)
    mcp.add_argument("--stale-policy", choices=STALE_POLICIES, default="warn")
    query = commands.add_parser("query", help="run one query through the shared product service")
    query.add_argument("query_type", choices=("definition", "references", "callers", "callees", "related_tests", "impact"))
    query.add_argument("symbol")
    query.add_argument("--config", type=Path)
    query.add_argument("--repo", type=Path)
    query.add_argument("--node", type=Path)
    query.add_argument("--analyzer", type=Path, default=_asset("ts_test_analyzer.mjs"))
    query.add_argument("--binary", type=Path)
    query.add_argument("--cache-dir", type=Path)
    query.add_argument("--project")
    query.add_argument("--serena-python", type=Path)
    query.add_argument("--serena-runner", type=Path, default=_asset("serena_runner.py"))
    query.add_argument("--serena-home", type=Path)
    query.add_argument("--metadata-root", type=Path)
    query.add_argument("--language", choices=("python", "typescript"))
    query.add_argument("--node-bin-dir", type=Path)
    query.add_argument("--tsconfig", type=Path)
    query.add_argument("--target-path", default="")
    query.add_argument("--target-owner", default="")
    query.add_argument("--direction", choices=("upstream", "downstream"), default="upstream")
    query.add_argument("--depth", type=int, default=1)
    query.add_argument("--max-nodes", type=int, default=100)
    query.add_argument("--max-edges", type=int, default=200)
    query.add_argument("--timeout-ms", type=int, default=30_000)
    query.add_argument("--stale-policy", choices=STALE_POLICIES, default="warn")
    batch = commands.add_parser(
        "query-batch", help="run JSON-lines queries through one long-lived product service"
    )
    for action in query._actions:
        if action.dest in {
            "help", "query_type", "symbol", "target_path", "target_owner", "direction", "depth",
            "max_nodes", "max_edges", "timeout_ms",
        }:
            continue
        options = action.option_strings
        kwargs = {
            "dest": action.dest,
            "required": action.required,
            "default": action.default,
        }
        if action.type is not None:
            kwargs["type"] = action.type
        if action.choices is not None:
            kwargs["choices"] = action.choices
        batch.add_argument(*options, **kwargs)
    args = parser.parse_args(argv)
    if args.version:
        print(json.dumps({"name": "codebase-atlas", "version": __version__}))
        return 0
    if args.command == "onboard":
        config_path = args.config or args.repo / CONFIG_NAME
        plan, config = build_plan(OnboardingInputs(
            args.repo, config_path, args.language, args.node, args.cbm_binary,
            args.serena_python, args.node_bin_dir, args.tsconfig, args.data_dir,
            args.mode,
        ))
        if not args.apply:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0 if plan["status"] == "planned" else 2
        result, code = apply_plan(plan, config, indexer=_index_repository, mode=args.mode)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    if args.command == "setup":
        candidate = args.config
        if candidate is None:
            local = args.repo / CONFIG_NAME
            candidate = local if local.is_file() else None
        if candidate is not None:
            configured = AtlasConfig.load(candidate)
            repository = configured.repository
            language = configured.language
            node = configured.node
            cbm_binary = configured.cbm_binary
            serena_python = configured.serena_python
            node_bin_dir = configured.node_bin_dir
            tsconfig = configured.tsconfig
        else:
            repository = args.repo
            language = args.language or (
                "typescript"
                if args.tsconfig is not None or (repository / "tsconfig.json").is_file()
                else "python"
            )
            node = args.node
            cbm_binary = args.cbm_binary
            serena_python = args.serena_python
            node_bin_dir = args.node_bin_dir
            tsconfig = args.tsconfig
        checks = runtime_checks(
            repository,
            language=language,
            node=node,
            cbm_binary=cbm_binary,
            serena_python=serena_python,
            node_bin_dir=node_bin_dir,
            tsconfig=tsconfig,
        )
        ok = required_checks_ok(checks)
        print(json.dumps({
            "status": "ready" if ok else "incomplete",
            "mode": "read_only",
            "config": str(candidate or ""),
            "language": language,
            "checks": checks,
        }, indent=2))
        return 0 if ok else 2
    if args.command == "init":
        config_path = (args.config or args.repo / CONFIG_NAME).resolve()
        config = AtlasConfig.discover(
            args.repo, language=args.language, node=args.node,
            cbm_binary=args.cbm_binary, serena_python=args.serena_python,
            node_bin_dir=args.node_bin_dir,
            tsconfig=args.tsconfig,
            data_dir=args.data_dir,
        )
        config.write(config_path)
        print(json.dumps({"status": "initialized", "config": str(config_path), "data_dir": str(config.data_dir)}, indent=2))
        return 0
    if args.command == "inspect":
        config = AtlasConfig.load(args.config)
        report = inspect_installation(config, deep=args.deep)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    if args.command == "repair":
        config = AtlasConfig.load(args.config)
        before = inspect_installation(config)
        plan = repair_plan(before)
        if not args.apply or not plan["applicable"]:
            planned_status = (
                "planned" if plan["applicable"]
                else "blocked" if plan["action"] == "wait_and_retry"
                else "no_action"
            )
            print(json.dumps({
                "status": planned_status,
                "mode": "read_only",
                "inspection": before,
                "plan": plan,
                "apply_command": (
                    "codebase-atlas repair --config "
                    f"{shlex.quote(str(args.config))} --mode {args.mode} --apply"
                    if plan["applicable"] else ""
                ),
            }, ensure_ascii=False, indent=2))
            return 0 if before["ok"] else 2
        source_before = repository_snapshot(config.repository)
        try:
            payload = _index_repository(config, args.mode)
        except RuntimeError as exc:
            print(json.dumps({
                "status": "failed",
                "mode": "applied",
                "plan": plan,
                "error": str(exc),
                "atlas_state_advanced": False,
                "provider_publication": "Provider staging/rollback boundary retained",
            }, ensure_ascii=False, indent=2))
            return 2
        source_after = repository_snapshot(config.repository)
        if (
            source_before.kind == "git"
            and source_after.kind == "git"
            and source_before.fingerprint != source_after.fingerprint
        ):
            print(json.dumps({
                "status": "failed",
                "mode": "applied",
                "plan": plan,
                "error": "repository changed while repairing; Atlas state was preserved; run repair again",
                "atlas_state_advanced": False,
            }, ensure_ascii=False, indent=2))
            return 2
        project = str(payload["project"])
        config.with_project(project).write(args.config)
        state = record_index_state(
            config.data_dir, config.repository, project, args.mode, snapshot=source_after
        )
        after = inspect_installation(config.with_project(project))
        print(json.dumps({
            "status": "repaired" if after["ok"] else "repair_incomplete",
            "mode": "applied",
            "plan": plan,
            "project": project,
            "source_fingerprint": state.source_fingerprint,
            "provider": {
                "route": "provider_managed_staging",
                "status": payload.get("status"),
                "nodes": payload.get("nodes"),
                "edges": payload.get("edges"),
            },
            "inspection": after,
        }, ensure_ascii=False, indent=2))
        return 0 if after["ok"] else 2
    if args.command == "clean":
        config = AtlasConfig.load(args.config)
        plan = cleanup_plan(config)
        if args.apply and plan["refused"]:
            result = plan | {
                "status": "cleanup_blocked",
                "reason": "refused targets must be resolved before applying cleanup",
            }
        else:
            result = apply_cleanup(config, plan) if args.apply else plan
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result.get("refused") else 2
    if args.command in {"doctor", "index", "update"}:
        config = AtlasConfig.load(args.config)
        if args.command == "doctor":
            checks = diagnose(config)
            ok = required_checks_ok(checks)
            freshness = index_freshness(config.data_dir, config.repository, config.project)
            provider_database = provider_database_health(config.cache_dir, config.project)
            print(json.dumps({
                "status": "ready" if ok else "incomplete",
                "index": freshness,
                "provider_database": provider_database,
                "checks": checks,
            }, indent=2))
            return 0 if ok else 2
        if args.command == "update" and not args.force_provider:
            freshness = index_freshness(config.data_dir, config.repository, config.project)
            provider_database = provider_database_health(config.cache_dir, config.project)
            if (
                freshness["status"] == "fresh"
                and freshness.get("mode") == args.mode
                and bool(provider_database["ok"])
            ):
                print(json.dumps({
                    "status": "current",
                    "project": config.project,
                    "config": str(args.config),
                    "index_state": str(config.data_dir / "index-state.json"),
                    "source_fingerprint": freshness.get("source_fingerprint"),
                    "provider": {
                        "route": "atlas_source_current",
                        "status": "not_started",
                        "database": provider_database,
                    },
                }, indent=2))
                return 0
        source_before = repository_snapshot(config.repository)
        payload = _index_repository(config, args.mode)
        source_after = repository_snapshot(config.repository)
        if (
            source_before.kind == "git"
            and source_after.kind == "git"
            and source_before.fingerprint != source_after.fingerprint
        ):
            raise RuntimeError(
                "repository changed while indexing; the previous Atlas state was preserved; run update again"
            )
        project = str(payload["project"])
        config.with_project(project).write(args.config)
        state = record_index_state(
            config.data_dir,
            config.repository,
            project,
            args.mode,
            snapshot=source_after,
        )
        print(json.dumps({
            "status": "indexed" if args.command == "index" else "updated",
            "project": project,
            "config": str(args.config),
            "index_state": str(config.data_dir / "index-state.json"),
            "source_fingerprint": state.source_fingerprint,
            "provider": {
                "route": "provider_managed",
                "status": payload.get("status"),
                "nodes": payload.get("nodes"),
                "edges": payload.get("edges"),
            },
        }, indent=2))
        return 0
    if args.command == "related-tests":
        provider = TypeScriptTestProvider(args.node, args.analyzer, args.tsconfig)
        results = provider.related_tests(
            args.repo,
            args.symbol,
            target_path=args.target_path,
            target_owner=args.target_owner,
        )
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": provider.name,
                    "results": [
                        {"node": asdict(node), "edge": asdict(edge)}
                        for node, edge in results
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "impact":
        provider = CodebaseMemoryImpactProvider(
            args.binary,
            args.repo,
            args.cache_dir,
            args.project,
        )
        lifecycle = CodebaseMemoryDaemon(args.binary, args.repo, args.cache_dir)
        with lifecycle:
            hits = provider.impact(
                args.symbol,
                direction=args.direction,
                max_depth=args.depth,
                target_path=args.target_path,
                target_owner=args.target_owner,
                max_nodes=args.max_nodes,
                max_edges=args.max_edges,
                timeout_ms=args.timeout_ms,
            )
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": provider.name,
                    "results": [
                        {
                            "node": asdict(hit.node),
                            "depth": hit.depth,
                            "path": [asdict(edge) for edge in hit.path],
                        }
                        for hit in hits
                    ],
                    "truncated": hits.truncated,
                    "truncation": {
                        "reasons": hits.reasons,
                        "observed": {
                            "nodes": hits.examined_nodes,
                            "edges": hits.examined_edges,
                        },
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command in {"mcp", "query", "query-batch"}:
        _apply_project_config(args)
        if args.command == "query":
            policy_error = stale_policy_error(args.index_status, args.stale_policy)
            if policy_error:
                print(json.dumps(attach_operational_status(
                    {"status": "error", "message": policy_error},
                    args.index_status,
                    args.stale_policy,
                ), ensure_ascii=False, indent=2))
                return 3
        lifecycle = CodebaseMemoryDaemon(args.binary, args.repo, args.cache_dir)
        structural = CodebaseMemoryImpactProvider(
            args.binary,
            args.repo,
            args.cache_dir,
            args.project,
        )
        service = AtlasService(
            repository=args.repo,
            structural_provider=structural,
            semantic_provider=SerenaSemanticProvider(
                args.serena_python,
                args.serena_runner,
                args.repo,
                args.serena_home,
                args.metadata_root,
                language=args.language,
                node_bin_dir=args.node_bin_dir,
            ),
            test_provider=TypeScriptTestProvider(args.node, args.analyzer, args.tsconfig),
            impact_provider=structural,
            lifecycle=lifecycle,
        )
        with service:
            if args.command == "mcp":
                run_stdio(McpServer(service, args.index_status, args.stale_policy))
            elif args.command == "query":
                response = service.query(
                    QueryRequest(
                        args.query_type,
                        args.symbol,
                        {
                            "target_path": args.target_path,
                            "target_owner": args.target_owner,
                            "direction": args.direction,
                            "depth": args.depth,
                            "max_nodes": args.max_nodes,
                            "max_edges": args.max_edges,
                            "timeout_ms": args.timeout_ms,
                        },
                    )
                )
                print(json.dumps(
                    _response_payload(response, args.index_status, args.stale_policy),
                    ensure_ascii=False,
                    indent=2,
                ))
            else:
                _run_query_batch(service, args.index_status, args.stale_policy)
        return 0
    parser.print_help()
    return 0


def _response_payload(response, index_status=None, stale_policy: str = "ignore") -> dict:
    return attach_operational_status({
        "schema_version": 1,
        "query_type": response.query_type,
        "nodes": [asdict(node) for node in response.nodes],
        "edges": [asdict(edge) for edge in response.edges],
        "depths": response.depths,
        "paths": {
            node_id: [asdict(edge) for edge in path]
            for node_id, path in response.paths.items()
        },
        "truncated": response.truncated,
        "truncation": response.truncation,
    }, index_status, stale_policy)


def _apply_project_config(args) -> None:
    candidate = args.config
    if candidate is None:
        base = args.repo if args.repo is not None else Path.cwd()
        local = base / CONFIG_NAME
        candidate = local if local.is_file() else None
    if candidate is not None:
        config = AtlasConfig.load(candidate)
        args.repo = config.repository
        args.node = config.node
        args.analyzer = config.analyzer
        args.binary = config.cbm_binary
        args.cache_dir = config.cache_dir
        args.project = config.project
        args.serena_python = config.serena_python
        args.serena_runner = config.serena_runner
        args.serena_home = config.serena_home
        args.metadata_root = config.metadata_root
        args.language = config.language
        args.node_bin_dir = config.node_bin_dir
        args.tsconfig = config.tsconfig
        args.index_status = operational_index_status(
            config.data_dir,
            config.repository,
            config.cache_dir,
            config.project,
        )
    else:
        args.index_status = unknown_operational_status()
    required = {
        "repo": args.repo, "node": args.node, "binary": args.binary,
        "cache_dir": args.cache_dir, "project": args.project,
        "serena_python": args.serena_python, "serena_home": args.serena_home,
        "metadata_root": args.metadata_root, "language": args.language,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(
            "missing runtime configuration: " + ", ".join(missing)
            + f"; run 'codebase-atlas init' and 'codebase-atlas index'"
        )


def _index_repository(config: AtlasConfig, mode: str) -> dict[str, object]:
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["CBM_CACHE_DIR"] = str(config.cache_dir)
    environment["CBM_ALLOWED_ROOT"] = str(config.repository.parent)
    # Own the daemon when indexing starts it, so a one-shot index/repair does
    # not leave a background Provider behind. A pre-existing daemon remains
    # unowned and is deliberately not stopped.
    with CodebaseMemoryDaemon(
        config.cbm_binary, config.repository, config.cache_dir
    ):
        completed = subprocess.run(
            [
                str(config.cbm_binary), "cli", "--json", "index_repository",
                "--repo-path", str(config.repository), "--mode", mode,
            ],
            check=False, capture_output=True, text=True, env=environment,
        )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Codebase Memory indexing failed")
    envelope = json.loads(completed.stdout)
    payload = envelope.get("structuredContent", {})
    project = payload.get("project")
    if envelope.get("isError") or not isinstance(project, str) or not project:
        raise RuntimeError(str(payload.get("error", "index result lacks project")))
    if payload.get("status") != "indexed":
        raise RuntimeError(str(payload.get("hint", f"index status is {payload.get('status', 'unknown')}")))
    return payload


def _run_query_batch(
    service: AtlasService,
    index_status=None,
    stale_policy: str = "ignore",
) -> None:
    print(json.dumps(attach_operational_status(
        {"status": "ready"}, index_status, stale_policy
    )), flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        started = time.perf_counter()
        try:
            value = json.loads(line)
            if value.get("command") == "shutdown":
                print(json.dumps({"status": "closed"}), flush=True)
                return
            policy_error = stale_policy_error(index_status or {}, stale_policy)
            if policy_error:
                raise RuntimeError(policy_error)
            request = QueryRequest(
                value["query_type"], value["symbol"], dict(value.get("parameters", {}))
            )
            payload = _response_payload(
                service.query(request), index_status, stale_policy
            )
            payload.update(
                status="ok", duration_ms=(time.perf_counter() - started) * 1000.0
            )
        except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            payload = {
                "status": "error",
                "message": str(exc),
                "duration_ms": (time.perf_counter() - started) * 1000.0,
            }
            attach_operational_status(payload, index_status, stale_policy)
        print(json.dumps(payload, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
