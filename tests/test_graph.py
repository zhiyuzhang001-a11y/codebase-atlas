from __future__ import annotations

import unittest

from codebase_atlas.contracts import Edge, Node, SourceRange
from codebase_atlas.graph import EvidenceGraph


HASH = "b" * 64


def node(node_id: str, name: str, line: int) -> Node:
    return Node(node_id, "function", name, SourceRange("src/x.py", line, line), "fixture", 1.0, HASH)


def calls(source: str, target: str) -> Edge:
    return Edge(source, target, "calls", "fixture", 1.0, HASH)


class EvidenceGraphTests(unittest.TestCase):
    def test_unions_same_name_seeds_by_identity_and_preserves_hops(self) -> None:
        nodes = (
            node("text.format", "format", 5),
            node("json.format", "format", 10),
            node("render_text", "render_text", 14),
            node("render_json", "render_json", 19),
            node("test_text", "test_render_text", 4),
            node("test_json", "test_render_json", 8),
        )
        graph = EvidenceGraph(
            nodes,
            (
                calls("render_text", "text.format"),
                calls("render_json", "json.format"),
                calls("test_text", "render_text"),
                calls("test_json", "render_json"),
            ),
        )
        hits = graph.impact(("text.format", "json.format"), direction="upstream", max_depth=2)
        self.assertEqual({hit.node.id for hit in hits}, {"render_text", "render_json", "test_text", "test_json"})
        self.assertEqual({hit.depth for hit in hits if hit.node.id.startswith("test")}, {2})
        self.assertTrue(all(len(hit.path) == hit.depth for hit in hits))

    def test_cycle_does_not_reemit_seed(self) -> None:
        graph = EvidenceGraph(
            (node("a", "a", 1), node("b", "b", 2)),
            (calls("a", "b"), calls("b", "a")),
        )
        hits = graph.impact(("a",), direction="downstream", max_depth=5)
        self.assertEqual([hit.node.id for hit in hits], ["b"])

    def test_excludes_unresolved_edges_by_default(self) -> None:
        graph = EvidenceGraph(
            (node("a", "a", 1), node("b", "b", 2)),
            (Edge("a", "b", "calls", "fixture", 0.5, HASH, resolution="heuristic"),),
        )
        self.assertEqual(graph.impact(("a",), direction="downstream", max_depth=1), ())


if __name__ == "__main__":
    unittest.main()
