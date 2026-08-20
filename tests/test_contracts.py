from __future__ import annotations

import unittest

from codebase_atlas.contracts import Edge, Node, SourceRange


HASH = "a" * 64


class ContractTests(unittest.TestCase):
    def test_normalizes_repository_path(self) -> None:
        location = SourceRange("src\\service.ts", 2, 4)
        self.assertEqual(location.path, "src/service.ts")

    def test_rejects_parent_path(self) -> None:
        with self.assertRaises(ValueError):
            SourceRange("../secret.ts", 1, 1)

    def test_accepts_exact_node_and_edge(self) -> None:
        node = Node(
            id="ts:test:tests/service.test.ts:5",
            kind="test",
            name="loads service",
            location=SourceRange("tests/service.test.ts", 5, 7),
            provider="atlas-ts-tests",
            confidence=1.0,
            evidence_hash=HASH,
        )
        edge = Edge(
            source_id=node.id,
            target_id="ts:function:src/service.ts:loadService",
            relation="calls",
            provider="atlas-ts-tests",
            confidence=1.0,
            evidence_hash=HASH,
        )
        self.assertEqual(edge.resolution, "exact")


if __name__ == "__main__":
    unittest.main()
