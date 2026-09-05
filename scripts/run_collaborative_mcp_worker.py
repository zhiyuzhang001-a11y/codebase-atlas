#!/usr/bin/env python3
"""One independently launched worker for the same-repository MCP acceptance gate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

from run_multi_mcp_stress import McpClient, StressFailure, structured, validate_query


def wait_for(barrier: Path, pattern: str, count: int, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(list(barrier.glob(pattern))) >= count:
            return
        time.sleep(0.05)
    raise StressFailure(f"barrier timed out: {pattern}")


def mark(barrier: Path, name: str, worker: int) -> None:
    (barrier / f"{name}-{worker}").write_text("ready\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--barrier", type=Path, required=True)
    parser.add_argument("--worker", type=int, choices=(0, 1, 2), required=True)
    args = parser.parse_args()
    args.barrier.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "codebase_atlas.cli", "mcp",
        "--config", str(args.config), "--auto-update", "on-query",
        "--auto-update-timeout", "90", "--version-check", "off",
    ]
    client = McpClient(command, dict(os.environ))
    path = args.repository / f"collaborative_agent_{args.worker}.py"
    created = f"atlas_collaborative_{args.worker}_created"
    modified = f"atlas_collaborative_{args.worker}_modified"
    observations: dict[str, str] = {}
    try:
        client.initialize()
        path.write_text(f"def {created}():\n    return 'created'\n", encoding="utf-8")
        mark(args.barrier, "created", args.worker)
        wait_for(args.barrier, "created-*", 3)
        create_result = client.call(
            "definition", {"symbol": created, "timeout_ms": 90000}
        )
        (args.barrier / f"create-response-{args.worker}.json").write_text(
            json.dumps(create_result, sort_keys=True), encoding="utf-8"
        )
        observations["create_generation"] = validate_query(
            create_result, created, present=True
        )[0]
        mark(args.barrier, "create-query", args.worker)
        wait_for(args.barrier, "create-query-*", 3)

        path.write_text(f"def {modified}():\n    return 'modified'\n", encoding="utf-8")
        mark(args.barrier, "modified", args.worker)
        wait_for(args.barrier, "modified-*", 3)
        observations["modify_generation"] = validate_query(
            client.call("definition", {"symbol": modified, "timeout_ms": 90000}),
            modified,
            present=True,
        )[0]
        validate_query(
            client.call("definition", {"symbol": created, "timeout_ms": 90000}),
            created,
            present=False,
        )
        mark(args.barrier, "modify-query", args.worker)
        wait_for(args.barrier, "modify-query-*", 3)

        path.unlink()
        mark(args.barrier, "deleted", args.worker)
        wait_for(args.barrier, "deleted-*", 3)
        observations["delete_generation"] = validate_query(
            client.call("definition", {"symbol": modified, "timeout_ms": 90000}),
            modified,
            present=False,
        )[0]
        status = structured(client.call("project_status"))
        if status.get("generation_id") != observations["delete_generation"]:
            raise StressFailure(f"status generation diverged: {status}")
        baseline = structured(client.call(
            "definition", {"symbol": "atlas_collaborative_baseline", "timeout_ms": 90000}
        ))
        if [node.get("location", {}).get("path") for node in baseline.get("nodes", [])] != [
            "baseline.py"
        ]:
            raise StressFailure(f"target baseline mismatch: {baseline}")
        foreign = structured(client.call(
            "definition", {"symbol": "symbol_from_an_unrelated_project", "timeout_ms": 90000}
        ))
        if foreign.get("nodes"):
            raise StressFailure(f"foreign result leaked: {foreign}")
        if len(set(observations.values())) != 3:
            raise StressFailure(f"source phases reused a generation: {observations}")
        result = {"status": "passed", "worker": args.worker, **observations}
        (args.barrier / f"result-{args.worker}.json").write_text(
            json.dumps(result, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
