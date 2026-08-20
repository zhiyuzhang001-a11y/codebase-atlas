from __future__ import annotations

import unittest

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
    def impact(self, _symbol, *, direction, max_depth):
        target = Node("target", "function", "target", SourceRange("src/x.py", 1, 1), "fake", 1.0, HASH)
        caller = Node("caller", "function", "caller", SourceRange("src/x.py", 2, 2), "fake", 1.0, HASH)
        edge = Edge("caller", "target", "calls", "fake", 1.0, HASH)
        return (ImpactHit(caller, min(max_depth, 1), (edge,)),)


class ServiceTests(unittest.TestCase):
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
