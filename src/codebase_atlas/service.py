"""Shared query service used by product interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import Edge, Node
from .graph import ImpactHit


@dataclass(frozen=True)
class QueryRequest:
    query_type: str
    symbol: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.query_type not in {"related_tests", "impact"}:
            raise ValueError(f"query type is not implemented by the gap service: {self.query_type}")
        if not self.symbol:
            raise ValueError("query symbol is required")
        if self.query_type == "impact":
            direction = self.parameters.get("direction", "upstream")
            depth = self.parameters.get("depth", 1)
            if direction not in {"upstream", "downstream"}:
                raise ValueError(f"unsupported impact direction: {direction}")
            if not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= 10:
                raise ValueError("impact depth must be an integer between 1 and 10")


@dataclass(frozen=True)
class QueryResponse:
    query_type: str
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    depths: dict[str, int] = field(default_factory=dict)


class AtlasService:
    def __init__(
        self,
        *,
        repository: Path | None = None,
        test_provider=None,
        impact_provider=None,
        lifecycle=None,
    ) -> None:
        self.repository = repository.resolve() if repository is not None else None
        self.test_provider = test_provider
        self.impact_provider = impact_provider
        self.lifecycle = lifecycle
        self.started = False

    def start(self) -> None:
        if self.started:
            return
        if self.lifecycle is not None:
            self.lifecycle.start()
        self.started = True

    def close(self) -> None:
        if not self.started:
            return
        if self.lifecycle is not None:
            self.lifecycle.close()
        self.started = False

    def query(self, request: QueryRequest) -> QueryResponse:
        if not self.started:
            raise RuntimeError("AtlasService.start() must be called before query()")
        if request.query_type == "related_tests":
            if self.test_provider is None:
                raise RuntimeError("related-tests provider is not configured")
            repository = request.parameters.get("repository", self.repository)
            if repository is None:
                raise RuntimeError("repository is required for related-tests")
            results = self.test_provider.related_tests(
                repository,
                request.symbol,
                target_path=str(request.parameters.get("target_path", "")),
            )
            return QueryResponse(
                query_type=request.query_type,
                nodes=tuple(node for node, _edge in results),
                edges=tuple(edge for _node, edge in results),
            )
        if self.impact_provider is None:
            raise RuntimeError("impact provider is not configured")
        hits: tuple[ImpactHit, ...] = self.impact_provider.impact(
            request.symbol,
            direction=str(request.parameters.get("direction", "upstream")),
            max_depth=int(request.parameters.get("depth", 1)),
        )
        edges: list[Edge] = []
        for hit in hits:
            for edge in hit.path:
                if edge not in edges:
                    edges.append(edge)
        return QueryResponse(
            query_type=request.query_type,
            nodes=tuple(hit.node for hit in hits),
            edges=tuple(edges),
            depths={hit.node.id: hit.depth for hit in hits},
        )

    def __enter__(self) -> "AtlasService":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
