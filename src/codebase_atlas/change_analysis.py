"""One bounded, read-only change brief over the shared Atlas service."""

from __future__ import annotations

from dataclasses import asdict
from time import monotonic
from typing import Any

from .operations import attach_operational_status
from .service import AtlasService, QueryRequest, QueryResponse


CHANGE_INTENTS = (
    "change_contract",
    "change_behavior",
    "refactor_internal",
    "fix_bug",
)
SUBQUERIES = (
    "definition",
    "references",
    "callers",
    "callees",
    "impact",
    "related_tests",
)


def _response(response: QueryResponse) -> dict[str, Any]:
    return {
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


def _completion(response: QueryResponse) -> dict[str, Any]:
    return {
        "status": "partial" if response.truncated else "complete",
        "reasons": list(response.truncation.get("reasons", ())),
        "continuation": response.truncation.get("continuation"),
        "error": "",
    }


def _empty_completion(status: str = "not_run", error: str = "") -> dict[str, Any]:
    return {"status": status, "reasons": [], "continuation": None, "error": error}


def _test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{normalized}"
        or name.startswith("test_")
        or name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
    )


def _path_suggestions(
    target: dict[str, Any] | None,
    results: dict[str, dict[str, Any] | None],
) -> tuple[list[dict[str, Any]], list[str]]:
    reads: list[dict[str, Any]] = []
    tests: list[str] = []
    seen_reads: set[tuple[str, int | None, int | None]] = set()
    seen_tests: set[str] = set()

    def add_node(node: dict[str, Any]) -> None:
        location = node.get("location") or {}
        path = str(location.get("path", ""))
        if not path:
            return
        if _test_path(path):
            if path not in seen_tests:
                seen_tests.add(path)
                tests.append(path)
            return
        key = (path, location.get("start_line"), location.get("end_line"))
        if key not in seen_reads:
            seen_reads.add(key)
            reads.append({
                "path": path,
                "start_line": location.get("start_line"),
                "end_line": location.get("end_line"),
                "symbol": node.get("name", ""),
                "provider": node.get("provider", ""),
                "evidence_hash": node.get("evidence_hash", ""),
            })

    if target is not None:
        add_node(target)
    for name in ("callers", "callees", "references", "impact", "related_tests"):
        result = results.get(name)
        if result is None:
            continue
        for node in result.get("nodes", []):
            add_node(node)
    return reads, tests


def analyze_change(
    service: AtlasService,
    symbol: str,
    *,
    intent: str = "change_behavior",
    target_path: str = "",
    target_owner: str = "",
    direction: str = "upstream",
    depth: int = 2,
    max_nodes: int = 100,
    max_edges: int = 200,
    timeout_ms: int = 60_000,
    index_status: dict[str, Any] | None = None,
    stale_policy: str = "ignore",
) -> dict[str, Any]:
    """Resolve one exact target and return a single evidence-preserving brief."""
    requested_symbol = symbol
    if not target_owner and "." in symbol:
        explicit_owner, explicit_member = symbol.rsplit(".", 1)
        if explicit_owner and explicit_member:
            target_owner = explicit_owner.rsplit(".", 1)[-1]
            symbol = explicit_member
    if intent not in CHANGE_INTENTS:
        raise ValueError(f"unsupported change intent: {intent}")
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 1 <= timeout_ms <= 300_000:
        raise ValueError("timeout_ms must be an integer between 1 and 300000")

    started = monotonic()
    completeness = {name: _empty_completion() for name in SUBQUERIES}
    results: dict[str, dict[str, Any] | None] = {name: None for name in SUBQUERIES}
    calls = 0

    def remaining() -> int:
        elapsed = int((monotonic() - started) * 1000.0)
        return max(0, timeout_ms - elapsed)

    def run(name: str, **extra: Any) -> QueryResponse | None:
        nonlocal calls
        available = remaining()
        if available <= 0:
            completeness[name] = _empty_completion("not_run", "shared_timeout_exhausted")
            return None
        parameters = {
            "target_path": target_path,
            "target_owner": target_owner,
            "max_nodes": max_nodes,
            "max_edges": max_edges,
            "timeout_ms": available,
            **extra,
        }
        try:
            calls += 1
            response = service.query(QueryRequest(name, symbol, parameters))
        except (RuntimeError, TypeError, ValueError) as exc:
            completeness[name] = _empty_completion("error", str(exc))
            return None
        results[name] = _response(response)
        completeness[name] = _completion(response)
        return response

    definition = run("definition")
    target: dict[str, Any] | None = None
    if definition is None:
        status = "partial" if completeness["definition"]["status"] != "error" else "error"
    elif not definition.nodes:
        status = "partial" if definition.truncated else "unresolved"
    elif len(definition.nodes) != 1:
        status = "needs_disambiguation"
    else:
        target = asdict(definition.nodes[0])
        target["resolution"] = "exact"
        exact_location = target.get("location") or {}
        exact_path = str(exact_location.get("path", "")) or target_path
        exact_owner = str((target.get("attributes") or {}).get("owner", "")) or target_owner
        target_path, target_owner = exact_path, exact_owner

        run("callers")
        run("callees")
        run("related_tests")
        run("references")
        if intent != "refactor_internal":
            run("impact", direction=direction, depth=depth)
        material = (
            "definition", "callers", "callees", "related_tests", "references"
        ) + (() if intent == "refactor_internal" else ("impact",))
        status = "partial" if any(
            completeness[name]["status"] != "complete" for name in material
        ) else "exact"

    reads, test_targets = _path_suggestions(target, results)
    implementation = list(dict.fromkeys(item["path"] for item in reads))
    brief = {
        "schema_version": 1,
        "analysis_type": "change_brief",
        "status": status,
        "intent": intent,
        "request": {
            "symbol": requested_symbol,
            "resolved_symbol": symbol,
            "target_path": target_path,
            "target_owner": target_owner,
        },
        "target": target,
        "candidates": (results["definition"] or {}).get("nodes", []),
        "implementation": implementation,
        "references": results["references"],
        "callers": results["callers"],
        "callees": results["callees"],
        "impact": results["impact"],
        "tests": results["related_tests"],
        "recommended_reads": reads,
        "recommended_test_targets": test_targets,
        "completeness": completeness,
        "budget": {
            "timeout_ms": timeout_ms,
            "max_nodes_per_query": max_nodes,
            "max_edges_per_query": max_edges,
            "remaining_ms": remaining(),
            "service_calls": calls,
        },
        "timing": {"elapsed_ms": (monotonic() - started) * 1000.0},
    }
    return attach_operational_status(brief, index_status, stale_policy)
