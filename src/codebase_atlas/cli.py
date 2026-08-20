"""Codebase Atlas command-line entry point."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from . import __version__
from .lifecycle import CodebaseMemoryDaemon
from .mcp import McpServer, run_stdio
from .providers import CodebaseMemoryImpactProvider, TypeScriptTestProvider
from .service import AtlasService


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
    if args.command == "mcp":
        lifecycle = CodebaseMemoryDaemon(args.binary, args.repo, args.cache_dir)
        service = AtlasService(
            repository=args.repo,
            test_provider=TypeScriptTestProvider(args.node, args.analyzer),
            impact_provider=CodebaseMemoryImpactProvider(
                args.binary,
                args.repo,
                args.cache_dir,
                args.project,
            ),
            lifecycle=lifecycle,
        )
        with service:
            run_stdio(McpServer(service))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
