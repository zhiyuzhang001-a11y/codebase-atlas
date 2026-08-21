from __future__ import annotations

import unittest
from pathlib import Path

from codebase_atlas.contracts import Edge, Node, SourceRange
from codebase_atlas.graph import ImpactHit, ImpactTraversal
from codebase_atlas.service import AtlasService, QueryRequest


HASH = "c" * 64


class FakeLifecycle:
    def __init__(self) -> None:
        self.starts = 0
        self.closes = 0

    def start(self) -> None:
        self.starts += 1

    def close(self) -> None:
        self.closes += 1


class FakeImpactProvider:
    def definitions(self, symbol, *, target_path=""):
        return (Node("target", "function", symbol, SourceRange("src/x.py", 1, 1), "fake", 1.0, HASH),)

    def callers(self, symbol, *, target_path="", **budget):
        return self.impact(
            symbol, direction="upstream", max_depth=1,
            target_path=target_path, **budget,
        )

    def callees(self, symbol, *, target_path="", **budget):
        return self.impact(
            symbol, direction="downstream", max_depth=1,
            target_path=target_path, **budget,
        )

    def related_tests(self, symbol, *, target_path="", **budget):
        return self.impact(
            symbol,
            direction="upstream",
            max_depth=1,
            target_path=target_path,
            **budget,
        )

    def impact(self, _symbol, *, direction, max_depth, target_path="", **_budget):
        target = Node("target", "function", "target", SourceRange("src/x.py", 1, 1), "fake", 1.0, HASH)
        caller = Node("caller", "function", "caller", SourceRange("src/x.py", 2, 2), "fake", 1.0, HASH)
        edge = Edge("caller", "target", "calls", "fake", 1.0, HASH)
        return (ImpactHit(caller, min(max_depth, 1), (edge,)),)


class FakeSemanticProvider:
    def __init__(self):
        self.starts = 0
        self.closes = 0

    def start(self):
        self.starts += 1

    def close(self):
        self.closes += 1

    def query(self, query_type, symbol, *, target_path=""):
        return (
            Node("reference-1", query_type, symbol, SourceRange("src/x.py", 3, 3), "semantic", 1.0, HASH),
            Node("reference-2", query_type, symbol, SourceRange("src/y.py", 5, 5), "semantic", 1.0, HASH),
        )


class FakeTestProvider:
    def related_tests(self, _repository, symbol, *, target_path=""):
        target = Node("target", "function", symbol, SourceRange(target_path or "src/x.ts", 1, 1), "fake", 1.0, HASH)
        test = Node("test", "test", "works", SourceRange("tests/x.test.ts", 4, 5), "tests", 1.0, HASH)
        return ((test, Edge("test", target.id, "calls", "tests", 1.0, HASH)),)


class ServiceTests(unittest.TestCase):
    def test_routes_all_six_query_types(self) -> None:
        structural = FakeImpactProvider()
        service = AtlasService(
            repository=Path(__file__).resolve().parents[1] / "fixtures/ts-tests",
            structural_provider=structural,
            semantic_provider=FakeSemanticProvider(),
            test_provider=FakeTestProvider(),
            impact_provider=structural,
        )
        with service:
            responses = {
                query_type: service.query(
                    QueryRequest(
                        query_type,
                        "target",
                        {"target_path": "src/x.ts", "depth": 1} if query_type == "impact" else {},
                    )
                )
                for query_type in (
                    "definition",
                    "references",
                    "callers",
                    "callees",
                    "related_tests",
                    "impact",
                )
            }
        self.assertTrue(all(response.nodes for response in responses.values()))
        self.assertEqual(responses["references"].nodes[0].kind, "references")
        self.assertEqual(responses["related_tests"].nodes[0].kind, "test")
        self.assertTrue(responses["impact"].paths["caller"])

    def test_reuses_one_lifecycle_for_multiple_queries(self) -> None:
        lifecycle = FakeLifecycle()
        service = AtlasService(impact_provider=FakeImpactProvider(), lifecycle=lifecycle)
        with service:
            first = service.query(QueryRequest("impact", "target", {"depth": 2}))
            second = service.query(QueryRequest("impact", "target", {"depth": 2}))
        self.assertEqual(first.nodes, second.nodes)
        self.assertEqual((lifecycle.starts, lifecycle.closes), (1, 1))

    def test_starts_only_provider_required_by_query(self) -> None:
        lifecycle = FakeLifecycle()
        semantic = FakeSemanticProvider()
        structural = FakeImpactProvider()
        service = AtlasService(
            structural_provider=structural,
            impact_provider=structural,
            semantic_provider=semantic,
            lifecycle=lifecycle,
        )
        with service:
            service.query(QueryRequest("definition", "target"))
            self.assertEqual((lifecycle.starts, semantic.starts), (1, 0))
        self.assertEqual((lifecycle.closes, semantic.closes), (1, 0))

        lifecycle = FakeLifecycle()
        semantic = FakeSemanticProvider()
        service = AtlasService(semantic_provider=semantic, lifecycle=lifecycle)
        with service:
            service.query(QueryRequest("references", "target"))
            self.assertEqual((lifecycle.starts, semantic.starts), (0, 1))
        self.assertEqual((lifecycle.closes, semantic.closes), (0, 1))

    def test_requires_started_service(self) -> None:
        service = AtlasService(impact_provider=FakeImpactProvider())
        with self.assertRaisesRegex(RuntimeError, "start"):
            service.query(QueryRequest("impact", "target"))

    def test_applies_result_budget_and_reports_explicit_truncation(self) -> None:
        service = AtlasService(semantic_provider=FakeSemanticProvider())
        with service:
            response = service.query(QueryRequest(
                "references", "target", {"max_nodes": 1, "max_edges": 2, "timeout_ms": 1000}
            ))
        self.assertEqual(len(response.nodes), 1)
        self.assertTrue(response.truncated)
        self.assertEqual(response.truncation["reasons"], ("node_budget_exceeded",))
        self.assertEqual(response.truncation["observed"]["nodes"], 2)
        self.assertEqual(response.truncation["returned"]["nodes"], 1)
        self.assertIsNone(response.truncation["continuation"])
        self.assertFalse(response.truncation["resumable"])

    def test_preserves_provider_side_time_truncation(self) -> None:
        class TruncatedProvider(FakeImpactProvider):
            def impact(self, *args, **kwargs):
                hits = super().impact(*args, **kwargs)
                return ImpactTraversal(
                    hits, True, ("time_budget_exceeded",), 1, 1
                )

        provider = TruncatedProvider()
        service = AtlasService(impact_provider=provider)
        with service:
            response = service.query(QueryRequest("impact", "target"))
        self.assertTrue(response.truncated)
        self.assertIn("time_budget_exceeded", response.truncation["reasons"])

    def test_rejects_invalid_query_budget(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_nodes"):
            QueryRequest("impact", "target", {"max_nodes": 0})


if __name__ == "__main__":
    unittest.main()
