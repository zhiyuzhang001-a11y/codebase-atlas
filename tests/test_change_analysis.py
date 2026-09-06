from __future__ import annotations

import json
import unittest

from codebase_atlas.change_analysis import _compact_result, analyze_change
from codebase_atlas.contracts import Node, SourceRange
from codebase_atlas.service import QueryResponse


HASH = "a" * 64


class FakeService:
    def __init__(self, definitions=1, truncated_definition=False, fail="") -> None:
        self.definitions = definitions
        self.truncated_definition = truncated_definition
        self.fail = fail
        self.requests = []

    def query(self, request):
        self.requests.append(request)
        if request.query_type == self.fail:
            raise RuntimeError("provider failed")
        if request.query_type == "definition":
            nodes = tuple(
                Node(
                    f"target-{index}", "function", "target",
                    SourceRange(f"src/target_{index}.py", 10, 12),
                    "provider", 1.0, HASH,
                )
                for index in range(self.definitions)
            )
            return QueryResponse(
                "definition", nodes, (), truncated=self.truncated_definition,
                truncation={"reasons": ("time_budget_exceeded",)} if self.truncated_definition else {},
            )
        path = "tests/test_target.py" if request.query_type == "related_tests" else "src/use.py"
        node = Node(
            f"{request.query_type}-node", "function", request.query_type,
            SourceRange(path, 20, 21), "provider", 1.0, HASH,
        )
        return QueryResponse(request.query_type, (node,), ())


class ChangeAnalysisTests(unittest.TestCase):
    def test_compact_relationship_keeps_edge_identity_and_provenance(self) -> None:
        compact = _compact_result({
            "nodes": [],
            "edges": [{
                "source_id": "caller", "target_id": "target", "relation": "calls",
                "provider": "provider", "evidence_hash": HASH,
                "attributes": {"repeated": "omitted"},
            }],
            "truncated": False,
        })
        self.assertEqual(compact["edges"][0], {
            "source_id": "caller", "target_id": "target", "relation": "calls",
            "provider": "provider", "evidence_hash": HASH,
        })

    def test_exact_brief_uses_one_shared_service_and_preserves_evidence(self) -> None:
        service = FakeService()
        brief = analyze_change(
            service, "target", intent="fix_bug",
            index_status={"status": "fresh", "ok": True},
        )
        self.assertEqual(brief["status"], "exact")
        self.assertEqual(brief["target"]["location"]["path"], "src/target_0.py")
        self.assertEqual(brief["implementation"][0], "src/target_0.py")
        self.assertEqual(brief["recommended_test_targets"], ["tests/test_target.py"])
        self.assertEqual(brief["budget"]["service_calls"], 6)
        self.assertEqual(brief["index"]["status"], "fresh")
        timeouts = [request.parameters["timeout_ms"] for request in service.requests]
        self.assertTrue(all(left >= right for left, right in zip(timeouts, timeouts[1:])))

    def test_compact_brief_preserves_decision_evidence_and_reduces_bytes(self) -> None:
        status = {"status": "fresh", "ok": True, "generation_id": "generation-1"}
        full = analyze_change(FakeService(), "target", index_status=status)
        compact = analyze_change(
            FakeService(), "target", index_status=status, response_mode="compact"
        )
        self.assertEqual(compact["response_mode"], "compact")
        self.assertEqual(compact["target"], full["target"])
        self.assertEqual(compact["completeness"], full["completeness"])
        self.assertEqual(compact["index"], full["index"])
        self.assertEqual(
            compact["evidence"]["callers"]["nodes"][0]["evidence_hash"], HASH
        )
        self.assertLess(
            len(json.dumps(compact, separators=(",", ":"))),
            len(json.dumps(full, separators=(",", ":"))),
        )

    def test_fix_bug_prioritizes_test_evidence_after_definition(self) -> None:
        service = FakeService()
        analyze_change(service, "target", intent="fix_bug")
        self.assertEqual(
            [request.query_type for request in service.requests[:3]],
            ["definition", "related_tests", "callers"],
        )

    def test_ambiguous_definition_stops_without_relationship_guessing(self) -> None:
        service = FakeService(definitions=2)
        brief = analyze_change(service, "target")
        self.assertEqual(brief["status"], "needs_disambiguation")
        self.assertEqual(len(brief["candidates"]), 2)
        self.assertEqual(len(service.requests), 1)
        self.assertEqual(brief["completeness"]["callers"]["status"], "not_run")

    def test_explicit_owner_member_shorthand_is_not_fuzzy_search(self) -> None:
        service = FakeService()
        brief = analyze_change(service, "Owner.target", target_path="src/target_0.py")
        first = service.requests[0]
        self.assertEqual(first.symbol, "target")
        self.assertEqual(first.parameters["target_owner"], "Owner")
        self.assertEqual(brief["request"]["symbol"], "Owner.target")

    def test_empty_truncated_definition_is_partial_not_unresolved(self) -> None:
        brief = analyze_change(
            FakeService(definitions=0, truncated_definition=True), "target"
        )
        self.assertEqual(brief["status"], "partial")
        self.assertEqual(brief["completeness"]["definition"]["status"], "partial")

    def test_subquery_error_is_explicit_and_does_not_erase_other_results(self) -> None:
        brief = analyze_change(FakeService(fail="callers"), "target")
        self.assertEqual(brief["status"], "partial")
        self.assertEqual(brief["completeness"]["callers"]["status"], "error")
        self.assertEqual(brief["completeness"]["callees"]["status"], "complete")
        self.assertIsNotNone(brief["callees"])


if __name__ == "__main__":
    unittest.main()
