"""Codebase Atlas command-line entry point."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
import os
import subprocess

from . import __version__
from .config import AtlasConfig, CONFIG_NAME, diagnose
from .index_state import index_freshness, record_index_state, repository_snapshot
from .lifecycle import CodebaseMemoryDaemon, GlobalCbmLock
from .mcp import McpServer, run_stdio
from .providers import CodebaseMemoryImpactProvider, SerenaSemanticProvider, TypeScriptTestProvider
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
    doctor = commands.add_parser("doctor", help="check configured runtimes and index state")
    doctor.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    index = commands.add_parser("index", help="build the configured structural index")
    index.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    index.add_argument("--mode", choices=("fast", "moderate", "full"), default="fast")
    update = commands.add_parser("update", help="safely update a configured structural index")
    update.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    update.add_argument("--mode", choices=("fast", "moderate", "full"), default="fast")
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
        default=Path(__file__).resolve().parents[2] / "scripts/ts_test_analyzer.mjs",
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
    mcp.add_argument("--analyzer", type=Path, default=Path(__file__).resolve().parents[2] / "scripts/ts_test_analyzer.mjs")
    mcp.add_argument("--binary", type=Path)
    mcp.add_argument("--cache-dir", type=Path)
    mcp.add_argument("--project")
    mcp.add_argument("--serena-python", type=Path)
    mcp.add_argument("--serena-runner", type=Path, default=Path(__file__).resolve().parents[2] / "scripts/serena_runner.py")
    mcp.add_argument("--serena-home", type=Path)
    mcp.add_argument("--metadata-root", type=Path)
    mcp.add_argument("--language", choices=("python", "typescript"))
    mcp.add_argument("--node-bin-dir", type=Path)
    mcp.add_argument("--tsconfig", type=Path)
    query = commands.add_parser("query", help="run one query through the shared product service")
    query.add_argument("query_type", choices=("definition", "references", "callers", "callees", "related_tests", "impact"))
    query.add_argument("symbol")
    query.add_argument("--config", type=Path)
    query.add_argument("--repo", type=Path)
    query.add_argument("--node", type=Path)
    query.add_argument("--analyzer", type=Path, default=Path(__file__).resolve().parents[2] / "scripts/ts_test_analyzer.mjs")
    query.add_argument("--binary", type=Path)
    query.add_argument("--cache-dir", type=Path)
    query.add_argument("--project")
    query.add_argument("--serena-python", type=Path)
    query.add_argument("--serena-runner", type=Path, default=Path(__file__).resolve().parents[2] / "scripts/serena_runner.py")
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
    if args.command in {"doctor", "index", "update"}:
        config = AtlasConfig.load(args.config)
        if args.command == "doctor":
            checks = diagnose(config)
            ok = all(bool(item["ok"]) for item in checks)
            freshness = index_freshness(config.data_dir, config.repository, config.project)
            print(json.dumps({"status": "ready" if ok else "incomplete", "index": freshness, "checks": checks}, indent=2))
            return 0 if ok else 2
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
                run_stdio(McpServer(service))
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
                print(json.dumps(_response_payload(response), ensure_ascii=False, indent=2))
            else:
                _run_query_batch(service)
        return 0
    parser.print_help()
    return 0


def _response_payload(response) -> dict:
    return {
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
    }


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
    with GlobalCbmLock(timeout_seconds=300.0):
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


def _run_query_batch(service: AtlasService) -> None:
    print(json.dumps({"status": "ready"}), flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        started = time.perf_counter()
        try:
            value = json.loads(line)
            if value.get("command") == "shutdown":
                print(json.dumps({"status": "closed"}), flush=True)
                return
            request = QueryRequest(
                value["query_type"], value["symbol"], dict(value.get("parameters", {}))
            )
            payload = _response_payload(service.query(request))
            payload.update(
                status="ok", duration_ms=(time.perf_counter() - started) * 1000.0
            )
        except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            payload = {
                "status": "error",
                "message": str(exc),
                "duration_ms": (time.perf_counter() - started) * 1000.0,
            }
        print(json.dumps(payload, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
