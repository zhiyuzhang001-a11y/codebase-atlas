"""Codebase Atlas command-line entry point."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

from . import __version__
from .lifecycle import CodebaseMemoryDaemon
from .mcp import McpServer, run_stdio
from .providers import CodebaseMemoryImpactProvider, SerenaSemanticProvider, TypeScriptTestProvider
from .service import AtlasService, QueryRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codebase-atlas")
    parser.add_argument("--version", action="store_true")
    commands = parser.add_subparsers(dest="command")
    related = commands.add_parser("related-tests")
    related.add_argument("--repo", type=Path, required=True)
    related.add_argument("--symbol", required=True)
    related.add_argument("--target-path", default="")
    related.add_argument("--node", type=Path, required=True)
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
    mcp = commands.add_parser("mcp", help="run the read-only MCP server over stdio")
    mcp.add_argument("--repo", type=Path, required=True)
    mcp.add_argument("--node", type=Path, required=True)
    mcp.add_argument("--analyzer", type=Path, default=Path(__file__).resolve().parents[2] / "scripts/ts_test_analyzer.mjs")
    mcp.add_argument("--binary", type=Path, required=True)
    mcp.add_argument("--cache-dir", type=Path, required=True)
    mcp.add_argument("--project", required=True)
    mcp.add_argument("--serena-python", type=Path, required=True)
    mcp.add_argument("--serena-runner", type=Path, default=Path(__file__).resolve().parents[2] / "scripts/serena_runner.py")
    mcp.add_argument("--serena-home", type=Path, required=True)
    mcp.add_argument("--metadata-root", type=Path, required=True)
    mcp.add_argument("--language", choices=("python", "typescript"), required=True)
    mcp.add_argument("--node-bin-dir", type=Path)
    query = commands.add_parser("query", help="run one query through the shared product service")
    query.add_argument("query_type", choices=("definition", "references", "callers", "callees", "related_tests", "impact"))
    query.add_argument("symbol")
    query.add_argument("--repo", type=Path, required=True)
    query.add_argument("--node", type=Path, required=True)
    query.add_argument("--analyzer", type=Path, default=Path(__file__).resolve().parents[2] / "scripts/ts_test_analyzer.mjs")
    query.add_argument("--binary", type=Path, required=True)
    query.add_argument("--cache-dir", type=Path, required=True)
    query.add_argument("--project", required=True)
    query.add_argument("--serena-python", type=Path, required=True)
    query.add_argument("--serena-runner", type=Path, default=Path(__file__).resolve().parents[2] / "scripts/serena_runner.py")
    query.add_argument("--serena-home", type=Path, required=True)
    query.add_argument("--metadata-root", type=Path, required=True)
    query.add_argument("--language", choices=("python", "typescript"), required=True)
    query.add_argument("--node-bin-dir", type=Path)
    query.add_argument("--target-path", default="")
    query.add_argument("--direction", choices=("upstream", "downstream"), default="upstream")
    query.add_argument("--depth", type=int, default=1)
    batch = commands.add_parser(
        "query-batch", help="run JSON-lines queries through one long-lived product service"
    )
    for action in query._actions:
        if action.dest in {"help", "query_type", "symbol", "target_path", "direction", "depth"}:
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
    if args.command == "related-tests":
        provider = TypeScriptTestProvider(args.node, args.analyzer)
        results = provider.related_tests(
            args.repo,
            args.symbol,
            target_path=args.target_path,
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
        hits = provider.impact(args.symbol, direction=args.direction, max_depth=args.depth)
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
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command in {"mcp", "query", "query-batch"}:
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
            test_provider=TypeScriptTestProvider(args.node, args.analyzer),
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
                            "direction": args.direction,
                            "depth": args.depth,
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
    }


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
