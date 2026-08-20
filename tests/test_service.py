from __future__ import annotations

import unittest
from pathlib import Path

from codebase_atlas.contracts import Edge, Node, SourceRange
from codebase_atlas.graph import ImpactHit
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
    def definitions(self, symbol):
        return (Node("target", "function", symbol, SourceRange("src/x.py", 1, 1), "fake", 1.0, HASH),)

    def callers(self, symbol):
        return self.impact(symbol, direction="upstream", max_depth=1)

    def callees(self, symbol):
        return self.impact(symbol, direction="downstream", max_depth=1)

    def related_tests(self, symbol):
        return self.impact(symbol, direction="upstream", max_depth=1)

    def impact(self, _symbol, *, direction, max_depth):
        target = Node("target", "function", "target", SourceRange("src/x.py", 1, 1), "fake", 1.0, HASH)
        caller = Node("caller", "function", "caller", SourceRange("src/x.py", 2, 2), "fake", 1.0, HASH)
        edge = Edge("caller", "target", "calls", "fake", 1.0, HASH)
        return (ImpactHit(caller, min(max_depth, 1), (edge,)),)


class FakeSemanticProvider:
    def query(self, query_type, symbol):
        return (Node("reference", query_type, symbol, SourceRange("src/x.py", 3, 3), "semantic", 1.0, HASH),)


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

    def test_requires_started_service(self) -> None:
        service = AtlasService(impact_provider=FakeImpactProvider())
        with self.assertRaisesRegex(RuntimeError, "start"):
            service.query(QueryRequest("impact", "target"))


if __name__ == "__main__":
    unittest.main()
