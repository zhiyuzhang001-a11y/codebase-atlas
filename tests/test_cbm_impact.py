from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from codebase_atlas.contracts import Node, SourceRange
from codebase_atlas.providers.cbm_impact import CodebaseMemoryImpactProvider


HASH = "d" * 64


def node(node_id: str, line: int) -> Node:
    return Node(
        node_id, "function", node_id.rsplit(".", 1)[-1],
        SourceRange("src/x.py", line, line), "fake", 1.0, HASH,
    )


class WideProvider(CodebaseMemoryImpactProvider):
    def __init__(self, cache_dir: Path = Path("/tmp/cache")) -> None:
        super().__init__(Path("/tmp/cbm"), Path("/tmp/repo"), cache_dir, "p")
        self.trace_calls = 0
        self.identity_batches: list[tuple[str, ...]] = []
        self.nodes = {
            "pkg.target": node("pkg.target", 1),
            "pkg.a": node("pkg.a", 2),
            "pkg.b": node("pkg.b", 3),
            "pkg.c": node("pkg.c", 4),
        }

    def _search_name(
        self, symbol, *, target_path="", target_owner="", timeout_seconds=None
    ):
        return (self.nodes["pkg.target"],)

    def _search_identity(self, node_id, *, timeout_seconds=None):
        return self.nodes[node_id]

    def _search_identities(self, node_ids, *, timeout_seconds=None):
        self.identity_batches.append(tuple(node_ids))
        return {node_id: self.nodes[node_id] for node_id in node_ids}

    def _run(self, tool, *args, timeout_seconds=None):
        if tool != "trace_path":
            raise AssertionError(f"unexpected tool: {tool}")
        self.trace_calls += 1
        return {
            "callers": {
                "cols": ["name", "confidence", "strategy"],
                "groups": [{
                    "qn_prefix": "pkg",
                    "rows": [["a", 1.0, "lsp"], ["b", 1.0, "lsp"], ["c", 1.0, "lsp"]],
                }],
            }
        }

class TimeoutProvider(WideProvider):
    def _search_name(
        self, symbol, *, target_path="", target_owner="", timeout_seconds=None
    ):
        raise TimeoutError("budget")


class MixedResolutionProvider(WideProvider):
    def _run(self, tool, *args, timeout_seconds=None):
        return {
            "callers": {
                "cols": ["name", "confidence", "strategy"],
                "groups": [{
                    "qn_prefix": "pkg",
                    "rows": [
                        ["a", 0.01, "heuristic"],
                        ["b", 0.95, "lsp"],
                        ["c", 0.88, "lsp"],
                    ],
                }],
            }
        }


class OwnerSearchProvider(CodebaseMemoryImpactProvider):
    def __init__(self) -> None:
        super().__init__(Path("/tmp/cbm"), Path("/tmp/repo"), Path("/tmp/cache"), "p")
        self.arguments = ()

    def _run(self, tool, *args, timeout_seconds=None):
        self.arguments = args
        return {
            "cols": ["name", "label", "lines"],
            "groups": [
                {
                    "file": "src/members.ts",
                    "qn_prefix": "p.src.members.PrimaryWorker",
                    "rows": [["run", "Method", "2-4"]],
                },
                {
                    "file": "src/members.ts",
                    "qn_prefix": "p.src.members.SecondaryWorker",
                    "rows": [["run", "Method", "8-10"]],
                },
            ],
        }


class CodebaseMemoryBudgetTests(unittest.TestCase):
    def test_excludes_heuristic_provider_edges_from_exact_results(self) -> None:
        traversal = MixedResolutionProvider().impact(
            "target", direction="upstream", max_depth=1, timeout_ms=1000,
        )
        self.assertEqual([hit.node.id for hit in traversal], ["pkg.b"])
        self.assertTrue(all(
            edge.resolution == "exact" for hit in traversal for edge in hit.path
        ))

    def test_owner_uses_qualified_search_and_selects_one_same_file_member(self) -> None:
        provider = OwnerSearchProvider()
        result = provider.definitions(
            "run", target_path="src/members.ts", target_owner="PrimaryWorker"
        )
        self.assertEqual([node.id for node in result], [
            "p.src.members.PrimaryWorker.run"
        ])
        self.assertIn("--qn-pattern", provider.arguments)

    def test_stops_before_resolving_node_beyond_budget(self) -> None:
        provider = WideProvider()
        traversal = provider.impact(
            "target", direction="upstream", max_depth=1,
            max_nodes=2, max_edges=10, timeout_ms=1000,
        )
        self.assertEqual([hit.node.id for hit in traversal], ["pkg.a", "pkg.b"])
        self.assertTrue(traversal.truncated)
        self.assertEqual(traversal.reasons, ("node_budget_exceeded",))
        self.assertEqual(traversal.examined_nodes, 3)
        self.assertEqual(traversal.examined_edges, 2)
        self.assertEqual(provider.identity_batches, [("pkg.a", "pkg.b")])

    def test_returns_explicit_partial_contract_on_timeout(self) -> None:
        traversal = TimeoutProvider().impact(
            "target", direction="upstream", max_depth=1, timeout_ms=10,
        )
        self.assertEqual(tuple(traversal), ())
        self.assertTrue(traversal.truncated)
        self.assertEqual(traversal.reasons, ("time_budget_exceeded",))

    def test_stops_before_adding_edge_beyond_budget(self) -> None:
        traversal = WideProvider().impact(
            "target", direction="upstream", max_depth=1,
            max_nodes=10, max_edges=1, timeout_ms=1000,
        )
        self.assertEqual([hit.node.id for hit in traversal], ["pkg.a"])
        self.assertTrue(traversal.truncated)
        self.assertEqual(traversal.reasons, ("edge_budget_exceeded",))
        self.assertEqual(traversal.examined_edges, 2)

    def test_reuses_traversal_until_index_fingerprint_changes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cache_dir = Path(raw)
            index = cache_dir / "p.db"
            index.write_bytes(b"v1")
            provider = WideProvider(cache_dir)
            arguments = {
                "direction": "upstream", "max_depth": 1,
                "max_nodes": 10, "max_edges": 10, "timeout_ms": 1000,
            }
            first = provider.impact("target", **arguments)
            second = provider.impact("target", **arguments)
            self.assertIs(first, second)
            self.assertEqual(provider.trace_calls, 1)

            index.write_bytes(b"version-two")
            third = provider.impact("target", **arguments)
            self.assertIsNot(third, second)
            self.assertEqual(provider.trace_calls, 2)


if __name__ == "__main__":
    unittest.main()
