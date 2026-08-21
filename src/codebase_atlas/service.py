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
        if self.query_type not in {
            "definition",
            "references",
            "callers",
            "callees",
            "related_tests",
            "impact",
        }:
            raise ValueError(f"unsupported query type: {self.query_type}")
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
    paths: dict[str, tuple[Edge, ...]] = field(default_factory=dict)


class AtlasService:
    def __init__(
        self,
        *,
        repository: Path | None = None,
        structural_provider=None,
        semantic_provider=None,
        test_provider=None,
        impact_provider=None,
        lifecycle=None,
    ) -> None:
        self.repository = repository.resolve() if repository is not None else None
        self.structural_provider = structural_provider or impact_provider
        self.semantic_provider = semantic_provider
        self.test_provider = test_provider
        self.impact_provider = impact_provider
        self.lifecycle = lifecycle
        self.started = False
        self._structural_started = False
        self._semantic_started = False

    def start(self) -> None:
        if self.started:
            return
        self.started = True

    def _ensure_structural(self) -> None:
        if self._structural_started:
            return
        if self.lifecycle is not None:
            self.lifecycle.start()
        self._structural_started = True

    def _ensure_semantic(self) -> None:
        if self._semantic_started:
            return
        if self.semantic_provider is not None and hasattr(self.semantic_provider, "start"):
            self.semantic_provider.start()
        self._semantic_started = True

    def close(self) -> None:
        if not self.started:
            return
        try:
            if self._semantic_started and self.semantic_provider is not None and hasattr(self.semantic_provider, "close"):
                self.semantic_provider.close()
        finally:
            if self._structural_started and self.lifecycle is not None:
                self.lifecycle.close()
        self._semantic_started = False
        self._structural_started = False
        self.started = False

    def query(self, request: QueryRequest) -> QueryResponse:
        if not self.started:
            raise RuntimeError("AtlasService.start() must be called before query()")
        if request.query_type == "definition":
            if self.structural_provider is None:
                raise RuntimeError("structural provider is not configured")
            self._ensure_structural()
            return QueryResponse(
                request.query_type,
                tuple(self.structural_provider.definitions(
                    request.symbol,
                    target_path=str(request.parameters.get("target_path", "")),
                )),
                (),
            )
        if request.query_type == "references":
            if self.semantic_provider is None:
                raise RuntimeError("semantic reference provider is not configured")
            self._ensure_semantic()
            return QueryResponse(
                request.query_type,
                tuple(self.semantic_provider.query(
                    "references", request.symbol,
                    target_path=str(request.parameters.get("target_path", "")),
                )),
                (),
            )
        if request.query_type in {"callers", "callees"}:
            if self.structural_provider is None:
                raise RuntimeError("structural provider is not configured")
            self._ensure_structural()
            method = getattr(self.structural_provider, request.query_type)
            return self._impact_response(
                request.query_type,
                tuple(method(
                    request.symbol,
                    target_path=str(request.parameters.get("target_path", "")),
                )),
            )
        if request.query_type == "related_tests":
            repository = request.parameters.get("repository", self.repository)
            if repository is None:
                raise RuntimeError("repository is required for related-tests")
            if self._has_ts_project(repository):
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
            if self.structural_provider is None:
                raise RuntimeError("related-tests provider is not configured")
            self._ensure_structural()
            return self._impact_response(
                request.query_type,
                tuple(self.structural_provider.related_tests(
                    request.symbol,
                    target_path=str(request.parameters.get("target_path", "")),
                )),
            )
        if self.impact_provider is None:
            raise RuntimeError("impact provider is not configured")
        self._ensure_structural()
        hits: tuple[ImpactHit, ...] = self.impact_provider.impact(
            request.symbol,
            direction=str(request.parameters.get("direction", "upstream")),
            max_depth=int(request.parameters.get("depth", 1)),
            target_path=str(request.parameters.get("target_path", "")),
        )
        hits_list = list(hits)
        repository = request.parameters.get("repository", self.repository)
        if (
            repository is not None
            and self._has_ts_project(repository)
            and request.parameters.get("direction", "upstream") == "upstream"
        ):
            test_results = self.test_provider.related_tests(
                repository,
                request.symbol,
                target_path=str(request.parameters.get("target_path", "")),
            )
            existing_ids = {hit.node.id for hit in hits_list}
            for node, edge in test_results:
                if node.id not in existing_ids:
                    hits_list.append(ImpactHit(node, 1, (edge,)))
                    existing_ids.add(node.id)
        return self._impact_response(request.query_type, tuple(hits_list))

    def _has_ts_project(self, repository: Path) -> bool:
        if self.test_provider is None:
            return False
        selected = getattr(self.test_provider, "tsconfig", None)
        return bool(selected and (repository / selected).is_file()) or (repository / "tsconfig.json").is_file()

    @staticmethod
    def _impact_response(query_type: str, hits: tuple[ImpactHit, ...]) -> QueryResponse:
        edges: list[Edge] = []
        for hit in hits:
            for edge in hit.path:
                if edge not in edges:
                    edges.append(edge)
        return QueryResponse(
            query_type=query_type,
            nodes=tuple(hit.node for hit in hits),
            edges=tuple(edges),
            depths={hit.node.id: hit.depth for hit in hits},
            paths={hit.node.id: hit.path for hit in hits},
        )

    def __enter__(self) -> "AtlasService":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
