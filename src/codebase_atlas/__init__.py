"""Codebase Atlas product package."""

from .contracts import Edge, Node, SourceRange
from .graph import EvidenceGraph, ImpactHit
from .service import AtlasService, QueryRequest, QueryResponse

__all__ = [
    "AtlasService",
    "Edge",
    "EvidenceGraph",
    "ImpactHit",
    "Node",
    "QueryRequest",
    "QueryResponse",
    "SourceRange",
]
__version__ = "0.21.0"
