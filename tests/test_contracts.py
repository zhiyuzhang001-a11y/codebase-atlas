from __future__ import annotations

import unittest

from codebase_atlas.contracts import (
    Edge,
    EvidenceProvenance,
    Node,
    SourceRange,
    contract_dict,
)


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

    def test_optional_provenance_preserves_legacy_serialization(self) -> None:
        legacy = Node(
            "node", "function", "run", SourceRange("src/run.py", 1, 1),
            "provider", 1.0, HASH,
        )
        self.assertNotIn("provenance", contract_dict(legacy))
        provenance = EvidenceProvenance(
            "repo-id", "generation-1", "T2", "provider", "1.0.0",
            "complete_exact",
        )
        enriched = Node(
            "node", "function", "run", SourceRange("src/run.rs", 1, 1),
            "provider", 1.0, HASH, provenance=provenance,
        )
        self.assertEqual(
            contract_dict(enriched)["provenance"]["fact_tier"], "T2"
        )

    def test_provenance_rejects_bad_tier_status_and_provider_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "fact tier"):
            EvidenceProvenance(
                "repo", "generation", "TX", "provider", "1", "complete_exact"
            )
        with self.assertRaisesRegex(ValueError, "completeness"):
            EvidenceProvenance(
                "repo", "generation", "T1", "provider", "1", "complete-ish"
            )
        provenance = EvidenceProvenance(
            "repo", "generation", "T1", "provider-a", "1", "syntactic_candidates"
        )
        with self.assertRaisesRegex(ValueError, "providers must match"):
            Node(
                "node", "function", "run", SourceRange("src/run.rs", 1, 1),
                "provider-b", 1.0, HASH, provenance=provenance,
            )


if __name__ == "__main__":
    unittest.main()
