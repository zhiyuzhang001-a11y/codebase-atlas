"""Versioned product contracts for normalized structural evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
FACT_TIERS = frozenset({"T0", "T1", "T2", "T3", "T4"})
COMPLETENESS_STATUSES = frozenset({
    "complete_exact",
    "exact_hits_partial_scope",
    "syntactic_candidates",
    "unsupported",
    "unavailable",
    "stale",
})


def repository_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"path must be repository-relative: {value!r}")
    return str(path)


@dataclass(frozen=True)
class SourceRange:
    path: str
    start_line: int
    end_line: int
    start_column: int | None = None
    end_column: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", repository_path(self.path))
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("source range lines must be positive and ordered")
        if self.start_column is not None and self.start_column < 1:
            raise ValueError("start_column must be positive")
        if self.end_column is not None and self.end_column < 1:
            raise ValueError("end_column must be positive")


@dataclass(frozen=True)
class EvidenceProvenance:
    repository_identity: str
    generation_id: str
    fact_tier: str
    provider: str
    provider_version: str
    completeness: str

    def __post_init__(self) -> None:
        for name in (
            "repository_identity", "generation_id", "provider", "provider_version"
        ):
            if not getattr(self, name):
                raise ValueError(f"provenance {name} is required")
        if self.fact_tier not in FACT_TIERS:
            raise ValueError(f"unsupported fact tier: {self.fact_tier}")
        if self.completeness not in COMPLETENESS_STATUSES:
            raise ValueError(
                f"unsupported completeness status: {self.completeness}"
            )


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    name: str
    location: SourceRange
    provider: str
    confidence: float
    evidence_hash: str
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance: EvidenceProvenance | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.kind or not self.name or not self.provider:
            raise ValueError("node identity, kind, name, and provider are required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if len(self.evidence_hash) != 64:
            raise ValueError("evidence_hash must be a SHA-256 hex digest")
        if self.provenance is not None and self.provenance.provider != self.provider:
            raise ValueError("node and provenance providers must match")


@dataclass(frozen=True)
class Edge:
    source_id: str
    target_id: str
    relation: str
    provider: str
    confidence: float
    evidence_hash: str
    resolution: str = "exact"
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id or not self.target_id or not self.relation or not self.provider:
            raise ValueError("edge endpoints, relation, and provider are required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if len(self.evidence_hash) != 64:
            raise ValueError("evidence_hash must be a SHA-256 hex digest")
        if self.resolution not in {"exact", "heuristic", "unresolved"}:
            raise ValueError(f"unsupported resolution: {self.resolution}")


def contract_dict(value: Node | Edge) -> dict[str, Any]:
    """Serialize contract values without changing legacy Node response bytes."""
    payload = asdict(value)
    if isinstance(value, Node) and value.provenance is None:
        payload.pop("provenance", None)
    return payload
