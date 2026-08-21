"""Shared query service used by product interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any

from .contracts import Edge, Node
from .graph import ImpactHit, ImpactTraversal


DEFAULT_MAX_NODES = 100
DEFAULT_MAX_EDGES = 200
DEFAULT_TIMEOUT_MS = 30_000


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
        for name in ("target_path", "target_owner"):
            value = self.parameters.get(name, "")
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
        for name, default, maximum in (
            ("max_nodes", DEFAULT_MAX_NODES, 10_000),
            ("max_edges", DEFAULT_MAX_EDGES, 20_000),
            ("timeout_ms", DEFAULT_TIMEOUT_MS, 300_000),
        ):
            value = self.parameters.get(name, default)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer between 1 and {maximum}")
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
    truncated: bool = False
    truncation: dict[str, Any] = field(default_factory=dict)


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

    def _ensure_structural(self, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bool:
        if self._structural_started:
            return True
        if self.lifecycle is not None:
            try:
                self.lifecycle.start(timeout_seconds=timeout_ms / 1000.0)
            except TimeoutError:
                return False
        self._structural_started = True
        return True

    def _ensure_semantic(self, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bool:
        if self._semantic_started:
            return True
        if self.semantic_provider is not None and hasattr(self.semantic_provider, "start"):
            try:
                self.semantic_provider.start(timeout_seconds=timeout_ms / 1000.0)
            except TimeoutError:
                return False
        self._semantic_started = True
        return True

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
        started = monotonic()
        limits = self._limits(request)
        if request.query_type == "definition":
            if self.structural_provider is None:
                raise RuntimeError("structural provider is not configured")
            if not self._ensure_structural(limits["timeout_ms"]):
                return self._time_budget_response(request.query_type, limits, started)
            return self._bounded_response(
                request.query_type,
                tuple(self.structural_provider.definitions(
                    request.symbol,
                    target_path=str(request.parameters.get("target_path", "")),
                    target_owner=str(request.parameters.get("target_owner", "")),
                )),
                (),
                limits,
                started,
            )
        if request.query_type == "references":
            repository = request.parameters.get("repository", self.repository)
            nodes_list: list[Node] = []
            if (
                repository is not None
                and self._has_ts_project(repository)
                and hasattr(self.test_provider, "references")
            ):
                remaining_timeout = self._remaining_timeout(limits, started)
                if remaining_timeout is None:
                    return self._partial_time_response(
                        request.query_type, tuple(nodes_list), (), limits, started
                    )
                try:
                    nodes_list.extend(self.test_provider.references(
                        repository,
                        request.symbol,
                        target_path=str(request.parameters.get("target_path", "")),
                        target_owner=str(request.parameters.get("target_owner", "")),
                        timeout_ms=remaining_timeout,
                    ))
                except TimeoutError:
                    return self._partial_time_response(
                        request.query_type, tuple(nodes_list), (), limits, started
                    )
            if self.semantic_provider is None and not nodes_list:
                raise RuntimeError("semantic reference provider is not configured")
            if self.semantic_provider is None:
                return self._bounded_response(
                    request.query_type, tuple(nodes_list), (), limits, started
                )
            remaining_timeout = self._remaining_timeout(limits, started)
            if remaining_timeout is None or not self._ensure_semantic(remaining_timeout):
                return self._partial_time_response(
                    request.query_type, tuple(nodes_list), (), limits, started
                )
            remaining_timeout = self._remaining_timeout(limits, started)
            if remaining_timeout is None:
                return self._partial_time_response(
                    request.query_type, tuple(nodes_list), (), limits, started
                )
            try:
                semantic_nodes = tuple(self.semantic_provider.query(
                    "references", request.symbol,
                    target_path=str(request.parameters.get("target_path", "")),
                    target_owner=str(request.parameters.get("target_owner", "")),
                    timeout_ms=remaining_timeout,
                ))
            except TimeoutError:
                self._semantic_started = False
                return self._partial_time_response(
                    request.query_type, tuple(nodes_list), (), limits, started
                )
            seen = {
                (node.location.path, node.location.start_line, node.location.start_column)
                for node in nodes_list
            }
            nodes_list.extend(
                node for node in semantic_nodes
                if (node.location.path, node.location.start_line, node.location.start_column)
                not in seen
            )
            return self._bounded_response(
                request.query_type,
                tuple(nodes_list),
                (),
                limits,
                started,
            )
        if request.query_type in {"callers", "callees"}:
            if self.structural_provider is None:
                raise RuntimeError("structural provider is not configured")
            if not self._ensure_structural(limits["timeout_ms"]):
                return self._time_budget_response(request.query_type, limits, started)
            method = getattr(self.structural_provider, request.query_type)
            remaining_timeout = self._remaining_timeout(limits, started)
            if remaining_timeout is None:
                return self._impact_response(
                    request.query_type,
                    ImpactTraversal((), True, ("time_budget_exceeded",)),
                    limits,
                    started,
                )
            return self._impact_response(
                request.query_type,
                method(
                    request.symbol,
                    target_path=str(request.parameters.get("target_path", "")),
                    target_owner=str(request.parameters.get("target_owner", "")),
                    max_nodes=limits["max_nodes"],
                    max_edges=limits["max_edges"],
                    timeout_ms=remaining_timeout,
                ),
                limits,
                started,
            )
        if request.query_type == "related_tests":
            repository = request.parameters.get("repository", self.repository)
            if repository is None:
                raise RuntimeError("repository is required for related-tests")
            if self._has_ts_project(repository):
                remaining_timeout = self._remaining_timeout(limits, started)
                if remaining_timeout is None:
                    return self._time_budget_response(request.query_type, limits, started)
                try:
                    results = self.test_provider.related_tests(
                        repository,
                        request.symbol,
                        target_path=str(request.parameters.get("target_path", "")),
                        target_owner=str(request.parameters.get("target_owner", "")),
                        timeout_ms=remaining_timeout,
                    )
                except TimeoutError:
                    return self._time_budget_response(request.query_type, limits, started)
                return self._bounded_response(
                    query_type=request.query_type,
                    nodes=tuple(node for node, _edge in results),
                    edges=tuple(edge for _node, edge in results),
                    limits=limits,
                    started=started,
                )
            if self.structural_provider is None:
                raise RuntimeError("related-tests provider is not configured")
            if not self._ensure_structural(limits["timeout_ms"]):
                return self._time_budget_response(request.query_type, limits, started)
            remaining_timeout = self._remaining_timeout(limits, started)
            if remaining_timeout is None:
                return self._impact_response(
                    request.query_type,
                    ImpactTraversal((), True, ("time_budget_exceeded",)),
                    limits,
                    started,
                )
            return self._impact_response(
                request.query_type,
                self.structural_provider.related_tests(
                    request.symbol,
                    target_path=str(request.parameters.get("target_path", "")),
                    target_owner=str(request.parameters.get("target_owner", "")),
                    max_nodes=limits["max_nodes"],
                    max_edges=limits["max_edges"],
                    timeout_ms=remaining_timeout,
                ),
                limits,
                started,
            )
        if self.impact_provider is None:
            raise RuntimeError("impact provider is not configured")
        if not self._ensure_structural(limits["timeout_ms"]):
            return self._time_budget_response(request.query_type, limits, started)
        remaining_timeout = self._remaining_timeout(limits, started)
        if remaining_timeout is None:
            return self._impact_response(
                request.query_type,
                ImpactTraversal((), True, ("time_budget_exceeded",)),
                limits,
                started,
            )
        traversal = self.impact_provider.impact(
            request.symbol,
            direction=str(request.parameters.get("direction", "upstream")),
            max_depth=int(request.parameters.get("depth", 1)),
            target_path=str(request.parameters.get("target_path", "")),
            target_owner=str(request.parameters.get("target_owner", "")),
            max_nodes=limits["max_nodes"],
            max_edges=limits["max_edges"],
            timeout_ms=remaining_timeout,
        )
        hits_list = list(traversal)
        repository = request.parameters.get("repository", self.repository)
        extra_reasons: list[str] = []
        if (
            repository is not None
            and self._has_ts_project(repository)
            and request.parameters.get("direction", "upstream") == "upstream"
            and "time_budget_exceeded" not in getattr(traversal, "reasons", ())
        ):
            remaining_timeout = self._remaining_timeout(limits, started)
            if remaining_timeout is None:
                test_results = ()
                extra_reasons.append("time_budget_exceeded")
            else:
                try:
                    test_results = self.test_provider.related_tests(
                        repository,
                        request.symbol,
                        target_path=str(request.parameters.get("target_path", "")),
                        target_owner=str(request.parameters.get("target_owner", "")),
                        timeout_ms=remaining_timeout,
                    )
                except TimeoutError:
                    test_results = ()
                    extra_reasons.append("time_budget_exceeded")
            existing_ids = {hit.node.id for hit in hits_list}
            for node, edge in test_results:
                if node.id not in existing_ids:
                    hits_list.append(ImpactHit(node, 1, (edge,)))
                    existing_ids.add(node.id)
        if isinstance(traversal, ImpactTraversal):
            traversal = ImpactTraversal(
                tuple(hits_list),
                traversal.truncated,
                tuple(dict.fromkeys((*traversal.reasons, *extra_reasons))),
                max(traversal.examined_nodes, len(hits_list)),
                max(
                    traversal.examined_edges,
                    len(tuple(dict.fromkeys(
                        (path_edge.source_id, path_edge.target_id, path_edge.relation)
                        for hit in hits_list for path_edge in hit.path
                    ))),
                ),
            )
        else:
            traversal = tuple(hits_list)
        return self._impact_response(request.query_type, traversal, limits, started)

    def _has_ts_project(self, repository: Path) -> bool:
        if self.test_provider is None:
            return False
        selected = getattr(self.test_provider, "tsconfig", None)
        return bool(selected and (repository / selected).is_file()) or (repository / "tsconfig.json").is_file()

    @staticmethod
    def _limits(request: QueryRequest) -> dict[str, int]:
        return {
            "max_nodes": int(request.parameters.get("max_nodes", DEFAULT_MAX_NODES)),
            "max_edges": int(request.parameters.get("max_edges", DEFAULT_MAX_EDGES)),
            "timeout_ms": int(request.parameters.get("timeout_ms", DEFAULT_TIMEOUT_MS)),
        }

    @staticmethod
    def _remaining_timeout(limits: dict[str, int], started: float) -> int | None:
        remaining = limits["timeout_ms"] - int((monotonic() - started) * 1000.0)
        return remaining if remaining >= 1 else None

    @classmethod
    def _time_budget_response(
        cls, query_type: str, limits: dict[str, int], started: float
    ) -> QueryResponse:
        elapsed_ms = (monotonic() - started) * 1000.0
        truncation = cls._truncation(
            ["time_budget_exceeded"],
            limits,
            observed_nodes=0,
            observed_edges=0,
            returned_nodes=0,
            returned_edges=0,
            elapsed_ms=elapsed_ms,
        )
        return QueryResponse(
            query_type, (), (), truncated=True, truncation=truncation
        )

    @classmethod
    def _partial_time_response(
        cls,
        query_type: str,
        nodes: tuple[Node, ...],
        edges: tuple[Edge, ...],
        limits: dict[str, int],
        started: float,
    ) -> QueryResponse:
        response = cls._bounded_response(
            query_type, nodes, edges, limits, started
        )
        truncation = dict(response.truncation)
        truncation["reasons"] = tuple(dict.fromkeys(
            (*truncation.get("reasons", ()), "time_budget_exceeded")
        ))
        return QueryResponse(
            response.query_type,
            response.nodes,
            response.edges,
            response.depths,
            response.paths,
            True,
            truncation,
        )

    @staticmethod
    def _truncation(
        reasons: list[str],
        limits: dict[str, int],
        *,
        observed_nodes: int,
        observed_edges: int,
        returned_nodes: int,
        returned_edges: int,
        elapsed_ms: float,
    ) -> dict[str, Any]:
        return {
            "reasons": tuple(dict.fromkeys(reasons)),
            "limits": dict(limits),
            "observed": {
                "nodes": observed_nodes,
                "edges": observed_edges,
                "elapsed_ms": elapsed_ms,
            },
            "returned": {"nodes": returned_nodes, "edges": returned_edges},
            "continuation": None,
            "resumable": False,
        }

    @classmethod
    def _bounded_response(
        cls,
        query_type: str,
        nodes: tuple[Node, ...],
        edges: tuple[Edge, ...],
        limits: dict[str, int],
        started: float,
    ) -> QueryResponse:
        reasons: list[str] = []
        selected_nodes = nodes[:limits["max_nodes"]]
        if len(nodes) > len(selected_nodes):
            reasons.append("node_budget_exceeded")
        selected_ids = {node.id for node in selected_nodes}
        relevant_edges = tuple(
            edge for edge in edges
            if edge.source_id in selected_ids or edge.target_id in selected_ids
        )
        selected_edges = relevant_edges[:limits["max_edges"]]
        if len(relevant_edges) > len(selected_edges):
            reasons.append("edge_budget_exceeded")
        elapsed_ms = (monotonic() - started) * 1000.0
        if elapsed_ms > limits["timeout_ms"]:
            reasons.append("time_budget_exceeded")
        truncation = cls._truncation(
            reasons,
            limits,
            observed_nodes=len(nodes),
            observed_edges=len(edges),
            returned_nodes=len(selected_nodes),
            returned_edges=len(selected_edges),
            elapsed_ms=elapsed_ms,
        )
        return QueryResponse(
            query_type,
            selected_nodes,
            selected_edges,
            truncated=bool(reasons),
            truncation=truncation,
        )

    @classmethod
    def _impact_response(
        cls,
        query_type: str,
        traversal,
        limits: dict[str, int],
        started: float,
    ) -> QueryResponse:
        hits = tuple(traversal)
        reasons = list(traversal.reasons) if isinstance(traversal, ImpactTraversal) else []
        selected_hits: list[ImpactHit] = []
        edges: list[Edge] = []
        for hit in hits:
            if len(selected_hits) >= limits["max_nodes"]:
                reasons.append("node_budget_exceeded")
                break
            new_edges = [edge for edge in hit.path if edge not in edges]
            if len(edges) + len(new_edges) > limits["max_edges"]:
                reasons.append("edge_budget_exceeded")
                break
            selected_hits.append(hit)
            edges.extend(new_edges)
        elapsed_ms = (monotonic() - started) * 1000.0
        if elapsed_ms > limits["timeout_ms"]:
            reasons.append("time_budget_exceeded")
        observed_nodes = (
            traversal.examined_nodes if isinstance(traversal, ImpactTraversal)
            else len(hits)
        )
        observed_edges = (
            traversal.examined_edges if isinstance(traversal, ImpactTraversal)
            else len(tuple(dict.fromkeys(
                (edge.source_id, edge.target_id, edge.relation)
                for hit in hits for edge in hit.path
            )))
        )
        truncation = cls._truncation(
            reasons,
            limits,
            observed_nodes=observed_nodes,
            observed_edges=observed_edges,
            returned_nodes=len(selected_hits),
            returned_edges=len(edges),
            elapsed_ms=elapsed_ms,
        )
        return QueryResponse(
            query_type=query_type,
            nodes=tuple(hit.node for hit in selected_hits),
            edges=tuple(edges),
            depths={hit.node.id: hit.depth for hit in selected_hits},
            paths={hit.node.id: hit.path for hit in selected_hits},
            truncated=bool(reasons),
            truncation=truncation,
        )

    def __enter__(self) -> "AtlasService":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
