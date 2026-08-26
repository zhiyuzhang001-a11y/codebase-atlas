"""Shared query service used by product interfaces."""

from __future__ import annotations

import base64
from collections import OrderedDict
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, fields, is_dataclass
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sys
from time import monotonic
from typing import TYPE_CHECKING, Any

from .contracts import Edge, Node, SourceRange, repository_path
from .graph import ImpactHit, ImpactTraversal
from .index_state import repository_snapshot
from .providers.python_callers import PythonExactCallerProvider
from .providers.python_references import PythonExactReferenceProvider
from .providers.python_registrations import RegistrationIndex

if TYPE_CHECKING:
    from .lifecycle import CodebaseMemoryDaemon
    from .providers.cbm_impact import CodebaseMemoryImpactProvider
    from .providers.serena import SerenaSemanticProvider
    from .providers.ts_tests import TypeScriptTestProvider


DEFAULT_MAX_NODES = 100
DEFAULT_MAX_EDGES = 200
DEFAULT_TIMEOUT_MS = 30_000
MAX_SESSION_CACHE_ENTRIES = 128
MAX_CONTINUATION_LENGTH = 512
MAX_CONTINUATION_ENTRY_BYTES = 16 * 1024 * 1024
MAX_CONTINUATION_CACHE_BYTES = 64 * 1024 * 1024
MAX_CONTINUATION_CACHE_ENTRIES = 32
CONTINUATION_PREFIX = "atlas-cont-v1"
CONTINUATION_ORDER = "typescript-references-v1"


@dataclass
class _ContinuationEntry:
    entry_id: str
    query_key: tuple[str, ...]
    source_fingerprint: str
    nodes: tuple[Node, ...]
    weight: int = 0


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
        relation = self.parameters.get("relation", "")
        if not isinstance(relation, str) or relation not in {"", "registers"}:
            raise ValueError("relation must be empty or 'registers'")
        if relation and self.query_type not in {"callers", "callees"}:
            raise ValueError("relation is supported only for callers and callees")
        if "continuation" in self.parameters:
            continuation = self.parameters["continuation"]
            if (
                self.query_type != "references"
                or not isinstance(continuation, str)
                or not continuation
                or len(continuation) > MAX_CONTINUATION_LENGTH
            ):
                raise ValueError("invalid_continuation")
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
        structural_provider: CodebaseMemoryImpactProvider | None = None,
        semantic_provider: SerenaSemanticProvider | None = None,
        test_provider: TypeScriptTestProvider | None = None,
        impact_provider: CodebaseMemoryImpactProvider | None = None,
        lifecycle: CodebaseMemoryDaemon | None = None,
        registration_index: RegistrationIndex | None = None,
        direct_provider: Any | None = None,
        session_continuations: bool = False,
    ) -> None:
        self.repository = repository.resolve() if repository is not None else None
        self.structural_provider = structural_provider or impact_provider
        self.semantic_provider = semantic_provider
        self.test_provider = test_provider
        self.impact_provider = impact_provider
        self.lifecycle = lifecycle
        self.registration_index = registration_index
        self.direct_provider = direct_provider
        self.session_continuations = session_continuations
        self.started = False
        self._structural_started = False
        self._semantic_started = False
        self._direct_started = False
        self._python_reference_cache: OrderedDict[
            tuple[Path, str, str], tuple[Node, ...]
        ] = OrderedDict()
        self._python_complete_reference_cache: OrderedDict[
            tuple[Path, str, str, str], tuple[Node, ...]
        ] = OrderedDict()
        self._python_caller_cache: OrderedDict[
            tuple[Path, str, str, str, str], ImpactTraversal
        ] = OrderedDict()
        self._continuation_secret: bytes | None = None
        self._ts_continuation_cache: OrderedDict[
            str, _ContinuationEntry
        ] = OrderedDict()
        self._ts_continuation_queries: dict[tuple[str, ...], str] = {}
        self._ts_continuation_bytes = 0

    def start(self) -> None:
        if self.started:
            return
        if self.session_continuations:
            self._continuation_secret = secrets.token_bytes(32)
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

    def _ensure_direct(self, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bool:
        if self._direct_started:
            return True
        if self.direct_provider is None:
            return False
        try:
            self.direct_provider.start(timeout_seconds=timeout_ms / 1000.0)
        except TimeoutError:
            return False
        self._direct_started = True
        return True

    def close(self) -> None:
        if not self.started:
            return
        try:
            try:
                if self._direct_started and self.direct_provider is not None:
                    self.direct_provider.close()
                if self._semantic_started and self.semantic_provider is not None and hasattr(self.semantic_provider, "close"):
                    self.semantic_provider.close()
            finally:
                if self._structural_started and self.lifecycle is not None:
                    self.lifecycle.close()
        finally:
            self._semantic_started = False
            self._direct_started = False
            self._structural_started = False
            self._python_reference_cache.clear()
            self._python_complete_reference_cache.clear()
            self._python_caller_cache.clear()
            self._clear_ts_continuations()
            self._continuation_secret = None
            self.started = False

    def query(self, request: QueryRequest) -> QueryResponse:
        if not self.started:
            raise RuntimeError("AtlasService.start() must be called before query()")
        started = monotonic()
        limits = self._limits(request)
        if self.direct_provider is not None:
            if request.parameters.get("relation"):
                raise ValueError("relation is not supported by the configured language")
            if not self._ensure_direct(limits["timeout_ms"]):
                return self._time_budget_response(request.query_type, limits, started)
            raw = self.direct_provider.query_product(
                request.query_type,
                request.symbol,
                target_path=str(request.parameters.get("target_path", "")),
                target_owner=str(request.parameters.get("target_owner", "")),
                parameters=request.parameters,
            )
            nodes = tuple(Node(
                id=str(item["id"]),
                kind=str(item["kind"]),
                name=str(item["name"]),
                location=SourceRange(
                    path=str(item["location"]["path"]),
                    start_line=int(item["location"]["line"]),
                    end_line=int(item["location"].get("end_line", item["location"]["line"])),
                    start_column=int(item["location"].get("column", 1)),
                    end_column=int(item["location"].get("end_column", item["location"].get("column", 1))),
                ),
                provider=str(item["provider"]),
                confidence=float(item["confidence"]),
                evidence_hash=str(item["evidence_hash"]),
                attributes=dict(item.get("attributes", {})),
            ) for item in raw["nodes"])
            edges = tuple(Edge(
                source_id=str(item["source_id"]),
                target_id=str(item["target_id"]),
                relation=str(item["relation"]),
                provider=str(item["provider"]),
                confidence=float(item["confidence"]),
                evidence_hash=str(item["evidence_hash"]),
                resolution=str(item.get("resolution", "exact")),
                attributes=dict(item.get("attributes", {})),
            ) for item in raw["edges"])
            truncation = dict(raw.get("truncation", {}))
            truncation["warnings"] = list(raw.get("warnings", []))
            truncation["capability"] = raw.get("capability", "complete")
            depths: dict[str, int] = {}
            paths: dict[str, tuple[Edge, ...]] = {}
            if request.query_type == "impact" and edges:
                node_ids = {node.id for node in nodes}
                direction = str(request.parameters.get("direction", "upstream"))
                endpoint_ids = {
                    endpoint for edge in edges
                    for endpoint in (edge.source_id, edge.target_id)
                }
                roots = sorted(endpoint_ids - node_ids)
                if roots:
                    frontier = [(roots[0], 0, ())]
                    seen = {roots[0]}
                    while frontier:
                        parent, depth, path = frontier.pop(0)
                        candidates = (
                            ((edge.source_id, edge) for edge in edges if edge.target_id == parent)
                            if direction == "upstream"
                            else ((edge.target_id, edge) for edge in edges if edge.source_id == parent)
                        )
                        for child, edge in candidates:
                            if child in seen:
                                continue
                            seen.add(child)
                            depths[child] = depth + 1
                            paths[child] = path + (edge,)
                            frontier.append((child, depth + 1, paths[child]))
            return QueryResponse(
                request.query_type,
                nodes,
                edges,
                depths=depths,
                paths=paths,
                truncated=bool(truncation.get("reasons")),
                truncation=truncation,
            )
        if request.parameters.get("relation") == "registers":
            if self.registration_index is None:
                return self._impact_response(
                    request.query_type,
                    ImpactTraversal(
                        (), True, ("registration_index_unavailable",)
                    ),
                    limits,
                    started,
                )
            target_path = str(request.parameters.get("target_path", ""))
            target_owner = str(request.parameters.get("target_owner", ""))
            traversal = (
                self.registration_index.callees(
                    request.symbol,
                    target_path=target_path,
                    target_owner=target_owner,
                )
                if request.query_type == "callees"
                else self.registration_index.callers_for(
                    request.symbol,
                    target_path=target_path,
                    target_owner=target_owner,
                )
            )
            return self._impact_response(
                request.query_type, traversal, limits, started
            )
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
            repository_value = request.parameters.get("repository", self.repository)
            repository = (
                Path(repository_value).resolve()
                if repository_value is not None else None
            )
            nodes_list: list[Node] = []
            target_path = str(request.parameters.get("target_path", ""))
            if "continuation" in request.parameters:
                return self._query_ts_continuation(
                    request, repository, limits, started
                )
            ts_query_key: tuple[str, ...] | None = None
            ts_source_fingerprint: str | None = None
            if (
                self.session_continuations
                and repository is not None
                and Path(target_path).suffix != ".py"
                and self._has_ts_project(repository)
                and hasattr(self.test_provider, "references")
            ):
                ts_query_key = self._ts_query_key(request, repository)
                if ts_query_key in self._ts_continuation_queries:
                    ts_source_fingerprint = self._source_fingerprint(repository)
                    if ts_source_fingerprint is not None:
                        cached_response = self._cached_ts_initial_response(
                            ts_query_key,
                            ts_source_fingerprint,
                            limits,
                            started,
                        )
                        if cached_response is not None:
                            return cached_response
            python_cache_key: tuple[Path, str, str, str] | None = None
            if repository is not None and Path(target_path).suffix == ".py":
                python_cache_key = (
                    Path(repository).resolve(),
                    request.symbol,
                    target_path,
                    str(request.parameters.get("target_owner", "")),
                )
                cached_references = self._cache_get(
                    self._python_complete_reference_cache, python_cache_key
                )
                if cached_references is not None:
                    return self._bounded_response(
                        request.query_type, cached_references, (), limits, started
                    )
                remaining_timeout = self._remaining_timeout(limits, started)
                if remaining_timeout is None:
                    return self._partial_time_response(
                        request.query_type, (), (), limits, started
                    )
                try:
                    nodes_list.extend(self._python_exact_references(
                        Path(repository),
                        request.symbol,
                        target_path=target_path,
                        timeout_ms=remaining_timeout,
                    ))
                except TimeoutError:
                    return self._partial_time_response(
                        request.query_type, tuple(nodes_list), (), limits, started
                    )
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
                    ts_nodes = tuple(self.test_provider.references(
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
                pure_ts_answer = not nodes_list
                nodes_list.extend(ts_nodes)
                if ts_nodes:
                    if pure_ts_answer and ts_query_key is not None:
                        return self._cache_ts_initial_response(
                            ts_query_key,
                            ts_source_fingerprint,
                            repository,
                            tuple(nodes_list),
                            limits,
                            started,
                        )
                    return self._bounded_response(
                        request.query_type, tuple(nodes_list), (), limits, started
                    )
            if self.semantic_provider is None and not nodes_list:
                raise RuntimeError("semantic reference provider is not configured")
            if self.semantic_provider is None:
                if python_cache_key is not None:
                    self._cache_put(
                        self._python_complete_reference_cache,
                        python_cache_key,
                        tuple(nodes_list),
                    )
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
            if python_cache_key is not None:
                self._cache_put(
                    self._python_complete_reference_cache,
                    python_cache_key,
                    tuple(nodes_list),
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
            method = getattr(self.structural_provider, request.query_type)

            def structural_relation_query() -> ImpactTraversal:
                remaining_timeout = self._remaining_timeout(limits, started)
                if (
                    remaining_timeout is None
                    or not self._ensure_structural(remaining_timeout)
                ):
                    return ImpactTraversal(
                        (), True, ("time_budget_exceeded",)
                    )
                remaining_timeout = self._remaining_timeout(limits, started)
                if remaining_timeout is None:
                    return ImpactTraversal(
                        (), True, ("time_budget_exceeded",)
                    )
                return method(
                    request.symbol,
                    target_path=str(request.parameters.get("target_path", "")),
                    target_owner=str(request.parameters.get("target_owner", "")),
                    max_nodes=limits["max_nodes"],
                    max_edges=limits["max_edges"],
                    timeout_ms=remaining_timeout,
                )

            if request.query_type == "callers":
                traversal = self._query_with_python_caller_supplement(
                    request, limits, started, structural_relation_query
                )
            else:
                traversal = structural_relation_query()
            traversal = self._query_with_python_registration_supplement(
                request, limits, started, traversal
            )
            repository = request.parameters.get("repository", self.repository)
            if (
                repository is not None
                and self._has_ts_project(Path(repository))
                and hasattr(self.test_provider, request.query_type)
            ):
                traversal = self._exact_ts_relation_supplement(
                    request, traversal, limits, started, Path(repository)
                )
            return self._impact_response(
                request.query_type,
                traversal,
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

            def structural_test_query() -> ImpactTraversal:
                remaining_timeout = self._remaining_timeout(limits, started)
                if (
                    remaining_timeout is None
                    or not self._ensure_structural(remaining_timeout)
                ):
                    return ImpactTraversal(
                        (), True, ("time_budget_exceeded",)
                    )
                remaining_timeout = self._remaining_timeout(limits, started)
                if remaining_timeout is None:
                    return ImpactTraversal(
                        (), True, ("time_budget_exceeded",)
                    )
                return self.structural_provider.related_tests(
                    request.symbol,
                    target_path=str(request.parameters.get("target_path", "")),
                    target_owner=str(request.parameters.get("target_owner", "")),
                    max_nodes=limits["max_nodes"],
                    max_edges=limits["max_edges"],
                    timeout_ms=remaining_timeout,
                )

            traversal = self._query_with_python_caller_supplement(
                request,
                limits,
                started,
                structural_test_query,
                only_tests=True,
            )
            traversal = self._query_with_python_registration_supplement(
                request, limits, started, traversal, only_tests=True
            )
            return self._impact_response(
                request.query_type,
                traversal,
                limits,
                started,
            )
        if self.impact_provider is None:
            raise RuntimeError("impact provider is not configured")

        def structural_impact_query() -> ImpactTraversal:
            remaining_timeout = self._remaining_timeout(limits, started)
            if (
                remaining_timeout is None
                or not self._ensure_structural(remaining_timeout)
            ):
                return ImpactTraversal((), True, ("time_budget_exceeded",))
            remaining_timeout = self._remaining_timeout(limits, started)
            if remaining_timeout is None:
                return ImpactTraversal((), True, ("time_budget_exceeded",))
            return self.impact_provider.impact(
                request.symbol,
                direction=str(request.parameters.get("direction", "upstream")),
                max_depth=int(request.parameters.get("depth", 1)),
                target_path=str(request.parameters.get("target_path", "")),
                target_owner=str(request.parameters.get("target_owner", "")),
                max_nodes=limits["max_nodes"],
                max_edges=limits["max_edges"],
                timeout_ms=remaining_timeout,
            )

        if request.parameters.get("direction", "upstream") == "upstream":
            traversal = self._query_with_python_caller_supplement(
                request, limits, started, structural_impact_query
            )
            traversal = self._query_with_python_registration_supplement(
                request, limits, started, traversal
            )
        else:
            traversal = structural_impact_query()
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

    def _query_with_python_caller_supplement(
        self,
        request: QueryRequest,
        limits: dict[str, int],
        started: float,
        structural_query: Callable[[], ImpactTraversal],
        *,
        only_tests: bool = False,
    ) -> ImpactTraversal:
        context = self._python_caller_context(request)
        if context is None:
            return structural_query()
        repository, project, target_path, cache_key = context
        cached_callers = self._cache_get(self._python_caller_cache, cache_key)
        if cached_callers is not None:
            return self._merge_python_caller_evidence(
                structural_query(), cached_callers, only_tests=only_tests
            )

        with ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="atlas-python-evidence"
        ) as executor:
            evidence_future = executor.submit(
                self._collect_python_reference_evidence,
                request,
                limits,
                started,
                repository,
                target_path,
            )
            structural = structural_query()
            references, evidence_timed_out = evidence_future.result()

        if "time_budget_exceeded" in tuple(getattr(structural, "reasons", ())):
            return structural
        seeds = tuple(self.structural_provider.definitions(
            request.symbol,
            target_path=target_path,
            target_owner=str(request.parameters.get("target_owner", "")),
        ))
        if len(seeds) != 1:
            return structural
        semantic = PythonExactCallerProvider(repository, project).callers(
            seeds[0], references
        )
        if not evidence_timed_out:
            self._cache_put(self._python_caller_cache, cache_key, semantic)
        return self._merge_python_caller_evidence(
            structural,
            semantic,
            only_tests=only_tests,
            evidence_timed_out=evidence_timed_out,
        )

    def _python_caller_context(
        self, request: QueryRequest
    ) -> tuple[Path, str, str, tuple[Path, str, str, str, str]] | None:
        target_path = str(request.parameters.get("target_path", ""))
        repository = request.parameters.get("repository", self.repository)
        if (
            repository is None
            or Path(target_path).suffix != ".py"
            or self.structural_provider is None
        ):
            return None
        repository = Path(repository).resolve()
        project = str(getattr(self.structural_provider, "project", ""))
        if not project:
            return None
        cache_key = (
            repository,
            project,
            request.symbol,
            target_path,
            str(request.parameters.get("target_owner", "")),
        )
        return repository, project, target_path, cache_key

    def _query_with_python_registration_supplement(
        self,
        request: QueryRequest,
        limits: dict[str, int],
        started: float,
        structural: ImpactTraversal,
        *,
        only_tests: bool = False,
    ) -> ImpactTraversal:
        if "time_budget_exceeded" in tuple(getattr(structural, "reasons", ())):
            return structural
        context = self._python_caller_context(request)
        if context is None:
            return structural
        _repository, _project, target_path, _caller_cache_key = context
        registration = ImpactTraversal(())
        if self.registration_index is not None:
            if request.query_type == "callees":
                registration = self.registration_index.callees(
                    request.symbol,
                    target_path=target_path,
                    target_owner=str(request.parameters.get("target_owner", "")),
                )
            else:
                registration = self.registration_index.callers_for(
                    request.symbol,
                    target_path=target_path,
                    target_owner=str(request.parameters.get("target_owner", "")),
                )
        return self._merge_python_registration_evidence(
            structural,
            registration,
            only_tests=only_tests,
        )

    def _merge_python_registration_evidence(
        self,
        structural: ImpactTraversal,
        registration: ImpactTraversal,
        *,
        only_tests: bool = False,
        evidence_timed_out: bool = False,
    ) -> ImpactTraversal:
        merged = list(structural)
        positions = {hit.node.id: index for index, hit in enumerate(merged)}
        for hit in registration:
            if only_tests and not self._is_python_test_node(hit.node):
                continue
            position = positions.get(hit.node.id)
            if position is None:
                positions[hit.node.id] = len(merged)
                merged.append(hit)
                continue
            existing = merged[position]
            combined_path = tuple(dict.fromkeys((*existing.path, *hit.path)))
            merged[position] = ImpactHit(
                existing.node, min(existing.depth, hit.depth), combined_path
            )
        observed_edges = len({
            (edge.source_id, edge.target_id, edge.relation, edge.provider)
            for hit in merged for edge in hit.path
        })
        return ImpactTraversal(
            tuple(merged),
            bool(getattr(structural, "truncated", False)) or evidence_timed_out,
            tuple(dict.fromkeys((
                *tuple(getattr(structural, "reasons", ())),
                *(("time_budget_exceeded",) if evidence_timed_out else ()),
            ))),
            max(getattr(structural, "examined_nodes", 0), len(merged)),
            max(getattr(structural, "examined_edges", 0), observed_edges),
        )

    def _collect_python_reference_evidence(
        self,
        request: QueryRequest,
        limits: dict[str, int],
        started: float,
        repository: Path,
        target_path: str,
    ) -> tuple[tuple[Node, ...], bool]:
        references: list[Node] = []
        semantic_timed_out = False
        if self.semantic_provider is not None:
            remaining_timeout = self._remaining_timeout(limits, started)
            if remaining_timeout is None or not self._ensure_semantic(remaining_timeout):
                semantic_timed_out = True
            else:
                remaining_timeout = self._remaining_timeout(limits, started)
                if remaining_timeout is None:
                    semantic_timed_out = True
                else:
                    try:
                        semantic_references = tuple(self.semantic_provider.query(
                            "references",
                            request.symbol,
                            target_path=target_path,
                            target_owner=str(request.parameters.get("target_owner", "")),
                            timeout_ms=remaining_timeout,
                        ))
                    except TimeoutError:
                        self._semantic_started = False
                        semantic_timed_out = True
                    else:
                        references.extend(semantic_references)
        exact_timed_out = False
        remaining_timeout = self._remaining_timeout(limits, started)
        if remaining_timeout is None:
            exact_timed_out = True
        else:
            try:
                exact_references = self._python_exact_references(
                    repository,
                    request.symbol,
                    target_path=target_path,
                    timeout_ms=remaining_timeout,
                )
            except TimeoutError:
                exact_timed_out = True
            else:
                seen_locations = {
                    (node.location.path, node.location.start_line, node.location.start_column)
                    for node in references
                }
                references.extend(
                    node for node in exact_references
                    if (node.location.path, node.location.start_line, node.location.start_column)
                    not in seen_locations
                )
        return tuple(references), semantic_timed_out or exact_timed_out

    def _merge_python_caller_evidence(
        self,
        structural: ImpactTraversal,
        semantic: ImpactTraversal,
        *,
        only_tests: bool = False,
        evidence_timed_out: bool = False,
    ) -> ImpactTraversal:
        merged = list(structural)
        seen_ids = {hit.node.id for hit in merged}
        for hit in semantic:
            if only_tests and not self._is_python_test_node(hit.node):
                continue
            if hit.node.id not in seen_ids:
                merged.append(hit)
                seen_ids.add(hit.node.id)
        return ImpactTraversal(
            tuple(merged),
            bool(getattr(structural, "truncated", False)) or evidence_timed_out,
            tuple(dict.fromkeys((
                *tuple(getattr(structural, "reasons", ())),
                *(("time_budget_exceeded",) if evidence_timed_out else ()),
            ))),
            max(getattr(structural, "examined_nodes", 0), len(merged)),
            max(getattr(structural, "examined_edges", 0), len(merged)),
        )

    @staticmethod
    def _is_python_test_node(node: Node) -> bool:
        path = Path(node.location.path)
        return (
            node.name.startswith("test")
            and (path.name.startswith("test_") or "tests" in path.parts)
        )

    def _python_exact_references(
        self,
        repository: Path,
        symbol: str,
        *,
        target_path: str,
        timeout_ms: int,
    ) -> tuple[Node, ...]:
        key = (repository.resolve(), symbol, target_path)
        cached = self._cache_get(self._python_reference_cache, key)
        if cached is not None:
            return cached
        results = PythonExactReferenceProvider(key[0]).references(
            symbol,
            target_path=target_path,
            timeout_ms=timeout_ms,
        )
        self._cache_put(self._python_reference_cache, key, results)
        return results

    @staticmethod
    def _cache_get(cache: OrderedDict, key):
        try:
            value = cache.pop(key)
        except KeyError:
            return None
        cache[key] = value
        return value

    @staticmethod
    def _cache_put(cache: OrderedDict, key, value) -> None:
        cache.pop(key, None)
        cache[key] = value
        while len(cache) > MAX_SESSION_CACHE_ENTRIES:
            cache.popitem(last=False)

    @staticmethod
    def _deep_size(value: Any, seen: set[int] | None = None) -> int:
        seen = seen or set()
        identity = id(value)
        if identity in seen:
            return 0
        seen.add(identity)
        size = sys.getsizeof(value)
        if is_dataclass(value) and not isinstance(value, type):
            size += sum(
                AtlasService._deep_size(getattr(value, item.name), seen)
                for item in fields(value)
            )
        elif isinstance(value, Mapping):
            size += sum(
                AtlasService._deep_size(key, seen)
                + AtlasService._deep_size(item, seen)
                for key, item in value.items()
            )
        elif isinstance(value, (tuple, list, set, frozenset)):
            size += sum(AtlasService._deep_size(item, seen) for item in value)
        return size

    def _clear_ts_continuations(self) -> None:
        self._ts_continuation_cache.clear()
        self._ts_continuation_queries.clear()
        self._ts_continuation_bytes = 0

    def _remove_ts_continuation(self, entry_id: str) -> None:
        entry = self._ts_continuation_cache.pop(entry_id, None)
        if entry is None:
            return
        self._ts_continuation_bytes = max(
            0, self._ts_continuation_bytes - entry.weight
        )
        if self._ts_continuation_queries.get(entry.query_key) == entry_id:
            self._ts_continuation_queries.pop(entry.query_key, None)

    def _ts_query_key(
        self, request: QueryRequest, repository: Path
    ) -> tuple[str, ...]:
        repository = repository.resolve()
        selected = getattr(self.test_provider, "tsconfig", None)
        selected_path = Path(selected) if selected is not None else Path("tsconfig.json")
        if not selected_path.is_absolute():
            selected_path = repository / selected_path
        target_path = str(request.parameters.get("target_path", ""))
        normalized_target = repository_path(target_path) if target_path else ""
        return (
            str(repository),
            str(selected_path.resolve()),
            CONTINUATION_ORDER,
            "references",
            request.symbol,
            normalized_target,
            str(request.parameters.get("target_owner", "")),
        )

    @staticmethod
    def _source_fingerprint(repository: Path) -> str | None:
        snapshot = repository_snapshot(repository)
        if snapshot.kind != "git" or snapshot.fingerprint is None:
            return None
        return snapshot.fingerprint

    @staticmethod
    def _urlsafe_encode(payload: bytes) -> str:
        return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

    @staticmethod
    def _urlsafe_decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        try:
            decoded = base64.b64decode(
                value + padding, altchars=b"-_", validate=True
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid_continuation") from exc
        if AtlasService._urlsafe_encode(decoded) != value:
            raise ValueError("invalid_continuation")
        return decoded

    def _continuation_token(self, entry_id: str, offset: int) -> str:
        if self._continuation_secret is None:
            raise ValueError("invalid_continuation")
        payload = json.dumps(
            {"entry": entry_id, "offset": offset, "v": 1},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        signature = hmac.new(
            self._continuation_secret, payload, hashlib.sha256
        ).digest()
        return ".".join((
            CONTINUATION_PREFIX,
            self._urlsafe_encode(payload),
            self._urlsafe_encode(signature),
        ))

    def _parse_continuation(self, token: str) -> tuple[str, int]:
        if self._continuation_secret is None:
            raise ValueError("invalid_continuation")
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != CONTINUATION_PREFIX:
            raise ValueError("invalid_continuation")
        payload = self._urlsafe_decode(parts[1])
        signature = self._urlsafe_decode(parts[2])
        expected = hmac.new(
            self._continuation_secret, payload, hashlib.sha256
        ).digest()
        if len(signature) != len(expected) or not hmac.compare_digest(
            signature, expected
        ):
            raise ValueError("invalid_continuation")
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_continuation") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"entry", "offset", "v"}
            or value.get("v") != 1
            or not isinstance(value.get("entry"), str)
            or len(value["entry"]) != 32
            or any(character not in "0123456789abcdef" for character in value["entry"])
            or not isinstance(value.get("offset"), int)
            or isinstance(value["offset"], bool)
            or value["offset"] <= 0
        ):
            raise ValueError("invalid_continuation")
        return value["entry"], value["offset"]

    def _store_ts_continuation(
        self,
        query_key: tuple[str, ...],
        source_fingerprint: str,
        nodes: tuple[Node, ...],
    ) -> _ContinuationEntry | None:
        entry_id = secrets.token_hex(16)
        while entry_id in self._ts_continuation_cache:
            entry_id = secrets.token_hex(16)
        entry = _ContinuationEntry(
            entry_id, query_key, source_fingerprint, nodes
        )
        entry.weight = self._deep_size(entry)
        if entry.weight > MAX_CONTINUATION_ENTRY_BYTES:
            return None
        previous_id = self._ts_continuation_queries.get(query_key)
        if previous_id is not None:
            self._remove_ts_continuation(previous_id)
        while self._ts_continuation_cache and (
            len(self._ts_continuation_cache) >= MAX_CONTINUATION_CACHE_ENTRIES
            or self._ts_continuation_bytes + entry.weight
            > MAX_CONTINUATION_CACHE_BYTES
        ):
            oldest_id = next(iter(self._ts_continuation_cache))
            self._remove_ts_continuation(oldest_id)
        if (
            len(self._ts_continuation_cache) >= MAX_CONTINUATION_CACHE_ENTRIES
            or self._ts_continuation_bytes + entry.weight
            > MAX_CONTINUATION_CACHE_BYTES
        ):
            return None
        self._ts_continuation_cache[entry.entry_id] = entry
        self._ts_continuation_queries[query_key] = entry.entry_id
        self._ts_continuation_bytes += entry.weight
        return entry

    def _continuation_timeout_response(
        self,
        entry: _ContinuationEntry,
        offset: int,
        token: str,
        limits: dict[str, int],
        started: float,
    ) -> QueryResponse:
        elapsed_ms = (monotonic() - started) * 1000.0
        truncation = self._truncation(
            ["time_budget_exceeded"],
            limits,
            observed_nodes=len(entry.nodes),
            observed_edges=0,
            returned_nodes=0,
            returned_edges=0,
            elapsed_ms=elapsed_ms,
        )
        truncation.update({
            "continuation": token,
            "resumable": True,
            "page": {
                "offset": offset,
                "next_offset": offset,
                "total_nodes": len(entry.nodes),
            },
        })
        return QueryResponse(
            "references", (), (), truncated=True, truncation=truncation
        )

    def _ts_continuation_page(
        self,
        entry: _ContinuationEntry,
        offset: int,
        limits: dict[str, int],
        started: float,
        *,
        retry_token: str | None = None,
    ) -> QueryResponse:
        if (monotonic() - started) * 1000.0 > limits["timeout_ms"]:
            if retry_token is not None:
                return self._continuation_timeout_response(
                    entry, offset, retry_token, limits, started
                )
            return self._time_budget_response("references", limits, started)
        end = min(len(entry.nodes), offset + limits["max_nodes"])
        nodes = entry.nodes[offset:end]
        has_more = end < len(entry.nodes)
        elapsed_ms = (monotonic() - started) * 1000.0
        reasons = ["node_budget_exceeded"] if has_more else []
        truncation = self._truncation(
            reasons,
            limits,
            observed_nodes=len(entry.nodes),
            observed_edges=0,
            returned_nodes=len(nodes),
            returned_edges=0,
            elapsed_ms=elapsed_ms,
        )
        truncation.update({
            "continuation": (
                self._continuation_token(entry.entry_id, end)
                if has_more else None
            ),
            "resumable": has_more,
            "page": {
                "offset": offset,
                "next_offset": end if has_more else None,
                "total_nodes": len(entry.nodes),
            },
        })
        return QueryResponse(
            "references", nodes, (), truncated=has_more, truncation=truncation
        )

    @staticmethod
    def _continuation_unavailable(
        response: QueryResponse, reason: str
    ) -> QueryResponse:
        truncation = dict(response.truncation)
        truncation["continuation_unavailable_reason"] = reason
        return QueryResponse(
            response.query_type,
            response.nodes,
            response.edges,
            response.depths,
            response.paths,
            response.truncated,
            truncation,
        )

    def _query_ts_continuation(
        self,
        request: QueryRequest,
        repository: Path | None,
        limits: dict[str, int],
        started: float,
    ) -> QueryResponse:
        if not self.session_continuations or repository is None:
            raise ValueError("invalid_continuation")
        token = str(request.parameters["continuation"])
        entry_id, offset = self._parse_continuation(token)
        entry = self._ts_continuation_cache.get(entry_id)
        if entry is None:
            raise ValueError("continuation_unavailable")
        if offset >= len(entry.nodes):
            raise ValueError("invalid_continuation")
        query_key = self._ts_query_key(request, repository)
        if query_key != entry.query_key:
            raise ValueError("continuation_query_mismatch")
        current_fingerprint = self._source_fingerprint(repository)
        if current_fingerprint is None:
            raise ValueError("continuation_unavailable")
        if current_fingerprint != entry.source_fingerprint:
            self._remove_ts_continuation(entry.entry_id)
            raise ValueError("continuation_stale")
        if (monotonic() - started) * 1000.0 > limits["timeout_ms"]:
            return self._continuation_timeout_response(
                entry, offset, token, limits, started
            )
        self._ts_continuation_cache.move_to_end(entry.entry_id)
        return self._ts_continuation_page(
            entry, offset, limits, started, retry_token=token
        )

    def _cached_ts_initial_response(
        self,
        query_key: tuple[str, ...],
        source_fingerprint: str,
        limits: dict[str, int],
        started: float,
    ) -> QueryResponse | None:
        entry_id = self._ts_continuation_queries.get(query_key)
        if entry_id is None:
            return None
        entry = self._ts_continuation_cache.get(entry_id)
        if entry is None:
            self._ts_continuation_queries.pop(query_key, None)
            return None
        if entry.source_fingerprint != source_fingerprint:
            self._remove_ts_continuation(entry.entry_id)
            return None
        if (monotonic() - started) * 1000.0 > limits["timeout_ms"]:
            return self._time_budget_response("references", limits, started)
        self._ts_continuation_cache.move_to_end(entry.entry_id)
        return self._ts_continuation_page(entry, 0, limits, started)

    def _cache_ts_initial_response(
        self,
        query_key: tuple[str, ...],
        source_fingerprint: str | None,
        repository: Path,
        nodes: tuple[Node, ...],
        limits: dict[str, int],
        started: float,
    ) -> QueryResponse:
        ordinary = self._bounded_response(
            "references", nodes, (), limits, started
        )
        if (
            len(nodes) <= limits["max_nodes"]
            or (monotonic() - started) * 1000.0 > limits["timeout_ms"]
        ):
            return ordinary
        final_fingerprint = self._source_fingerprint(repository)
        if (
            final_fingerprint is None
            or (
                source_fingerprint is not None
                and final_fingerprint != source_fingerprint
            )
            or (monotonic() - started) * 1000.0 > limits["timeout_ms"]
        ):
            return ordinary
        entry = self._store_ts_continuation(
            query_key, final_fingerprint, nodes
        )
        if entry is None:
            return self._continuation_unavailable(ordinary, "entry_too_large")
        if (monotonic() - started) * 1000.0 > limits["timeout_ms"]:
            self._remove_ts_continuation(entry.entry_id)
            return ordinary
        return self._ts_continuation_page(entry, 0, limits, started)

    def _exact_ts_relation_supplement(
        self,
        request: QueryRequest,
        structural: ImpactTraversal,
        limits: dict[str, int],
        started: float,
        repository: Path,
    ) -> ImpactTraversal:
        hits = list(structural)
        reasons = tuple(getattr(structural, "reasons", ()))
        remaining_timeout = self._remaining_timeout(limits, started)
        timed_out = remaining_timeout is None
        results: tuple[tuple[Node, Edge], ...] = ()
        if not timed_out:
            try:
                results = getattr(self.test_provider, request.query_type)(
                    repository,
                    request.symbol,
                    target_path=str(request.parameters.get("target_path", "")),
                    target_owner=str(request.parameters.get("target_owner", "")),
                    timeout_ms=remaining_timeout,
                )
            except TimeoutError:
                timed_out = True
        seen_ids = {hit.node.id for hit in hits}
        seen_identities = {
            (hit.node.name, hit.node.location.path, hit.node.location.start_line)
            for hit in hits
        }
        for node, edge in results:
            identity = (node.name, node.location.path, node.location.start_line)
            if node.id not in seen_ids and identity not in seen_identities:
                hits.append(ImpactHit(node, 1, (edge,)))
                seen_ids.add(node.id)
                seen_identities.add(identity)
        return ImpactTraversal(
            tuple(hits),
            bool(getattr(structural, "truncated", False)) or timed_out,
            tuple(dict.fromkeys((
                *reasons,
                *(("time_budget_exceeded",) if timed_out else ()),
            ))),
            max(getattr(structural, "examined_nodes", 0), len(hits)),
            max(getattr(structural, "examined_edges", 0), len(hits)),
        )

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
