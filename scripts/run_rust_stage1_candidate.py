#!/usr/bin/env python3
"""Replay one frozen Rust corpus repository through the internal Stage 1 candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import monotonic

from codebase_atlas.providers.rust_analyzer import RustAnalyzerProvider
from codebase_atlas.refresh_planner import build_generation_manifest


def generation(repository: Path, project: str) -> dict:
    return build_generation_manifest(
        repository,
        project,
        "rust",
        generation_id=f"candidate-{repository.name}",
        provider_identity={"status": "candidate-t1"},
        sidecar_identity={"status": "candidate-t1"},
        created_at=f"candidate:{repository.name}",
    )


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("scope", "t2"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--analyzer", type=Path)
    args = parser.parse_args()
    repository = args.repo.resolve()
    candidate = generation(repository, args.project)
    if args.mode == "scope":
        write_json(args.output, candidate["source_scope"])
        return 0
    if args.queries is None or args.analyzer is None:
        parser.error("t2 mode requires --queries and --analyzer")
    query_document = json.loads(args.queries.read_text(encoding="utf-8"))
    results = []
    started = monotonic()
    provider = RustAnalyzerProvider(
        args.analyzer, repository, args.project, candidate, readiness_seconds=60
    )
    try:
        provider.start(timeout_seconds=30)
        for spec in query_document["queries"]:
            line, column = spec["position"]
            nodes = provider.query(
                spec["kind"],
                spec["id"],
                source_path=spec["source"],
                source_line=line,
                source_column=column,
                timeout_ms=60_000,
            )
            locations = [
                {
                    "path": node.location.path,
                    "start_line": node.location.start_line,
                    "start_column": node.location.start_column,
                    "end_line": node.location.end_line,
                    "end_column": node.location.end_column,
                }
                for node in nodes
            ]
            paths = {item["path"] for item in locations}
            if spec["kind"] == "definition":
                passed = (
                    len(locations) == spec.get("location_count", 1)
                    and spec["target"] in paths
                )
            else:
                passed = (
                    len(locations) >= spec["minimum_locations"]
                    and set(spec.get("required_paths", [])) <= paths
                )
            results.append({"spec": spec, "locations": locations, "passed": passed})
    finally:
        provider.close()
    value = {
        "schema_version": 1,
        "repository": repository.name,
        "status": "complete_exact" if all(row["passed"] for row in results) else "exact_hits_partial_scope",
        "source_scope_status": candidate["source_scope"]["status"],
        "results": results,
        "elapsed_seconds": round(monotonic() - started, 3),
        "rust_analyzer_running_after_close": provider.running,
    }
    write_json(args.output, value)
    passed = sum(row["passed"] for row in results)
    print(json.dumps({"repository": repository.name, "passed": passed, "total": len(results)}))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
