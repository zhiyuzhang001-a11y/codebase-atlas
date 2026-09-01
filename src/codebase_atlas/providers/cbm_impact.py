"""Identity-safe impact expansion over exact Codebase Memory graph edges."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from time import monotonic
from typing import Any

from ..contracts import Edge, Node, SourceRange
from ..graph import EvidenceGraph, ImpactHit, ImpactTraversal
from ..provider_layout import provider_environment
from ..provider_transport import CodebaseMemoryMcpTransport


CODE_EXTENSIONS = {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"}
LOCATE_FILES_MAX_FILES = 2
LOCATE_FILES_MAX_INTERNAL_ROWS = 60


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _line_range(value: Any) -> tuple[int, int] | None:
    if isinstance(value, int) and value >= 1:
        return value, value
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", value)
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2) or match.group(1))
    return (start, end) if start >= 1 and end >= start else None


class CodebaseMemoryImpactProvider:
    name = "atlas-cbm-impact"

    def __init__(
        self,
        binary: Path,
        repository: Path,
        cache_dir: Path,
        project: str,
        transport: CodebaseMemoryMcpTransport | None = None,
    ) -> None:
        self.binary = binary.resolve()
        self.repository = repository.resolve()
        self.cache_dir = cache_dir.resolve()
        self.project = project
        self.transport = transport
        self._node_cache: dict[str, Node] = {}
        self._definition_cache: dict[tuple[str, str, str], tuple[Node, ...]] = {}
        self._impact_cache: dict[
            tuple[str, str, int, str, str, int, int, int], ImpactTraversal
        ] = {}
        self._cache_fingerprint = self._index_fingerprint()

    def _index_fingerprint(self) -> tuple[int, int] | None:
        index = self.cache_dir / f"{self.project}.db"
        try:
            stat = index.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _invalidate_if_index_changed(self) -> None:
        fingerprint = self._index_fingerprint()
        if fingerprint == self._cache_fingerprint:
            return
        self._node_cache.clear()
        self._definition_cache.clear()
        self._impact_cache.clear()
        self._cache_fingerprint = fingerprint

    def _run(self, tool: str, *args: str, timeout_seconds: float | None = None) -> dict[str, Any]:
        environment = provider_environment(self.cache_dir, self.repository)
        try:
            completed = subprocess.run(
                [str(self.binary), "cli", "--json", tool, *args],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Codebase Memory {tool} exceeded the query time budget") from exc
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "Codebase Memory exited with an error")
        envelope = json.loads(completed.stdout)
        if envelope.get("isError"):
            payload = envelope.get("structuredContent", {})
            raise RuntimeError(str(payload.get("error", "Codebase Memory returned an error")))
        payload = envelope.get("structuredContent")
        if not isinstance(payload, dict):
            raise ValueError("Codebase Memory result lacks structuredContent")
        return payload

    @staticmethod
    def _safe_located_path(value: Any) -> str:
        if not isinstance(value, str) or not value or "\\" in value:
            raise ValueError("Provider returned an invalid file path")
        path = PurePosixPath(value)
        if path.is_absolute() or value != path.as_posix() or ".." in path.parts:
            raise ValueError("Provider returned a non-repository-relative file path")
        return value

    def locate_files(
        self,
        intent: str,
        *,
        max_files: int = LOCATE_FILES_MAX_FILES,
        max_internal_rows: int = LOCATE_FILES_MAX_INTERNAL_ROWS,
        timeout_ms: int = 30_000,
    ) -> dict[str, Any]:
        """Return Provider-owned bounded heuristic file projection."""
        if not isinstance(intent, str) or not intent or len(intent.encode("utf-8")) > 1000:
            raise ValueError("intent must contain 1 to 1000 bytes")
        if not isinstance(max_files, int) or isinstance(max_files, bool) or not 1 <= max_files <= 2:
            raise ValueError("max_files must be an integer between 1 and 2")
        if (
            not isinstance(max_internal_rows, int)
            or isinstance(max_internal_rows, bool)
            or not 1 <= max_internal_rows <= 60
        ):
            raise ValueError("max_internal_rows must be an integer between 1 and 60")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 1 <= timeout_ms <= 300_000:
            raise ValueError("timeout_ms must be an integer between 1 and 300000")

        self._invalidate_if_index_changed()
        if self.transport is None:
            payload = self._run(
                "locate_files",
                "--project", self.project,
                "--intent", intent,
                "--max-files", str(max_files),
                "--max-internal-rows", str(max_internal_rows),
                timeout_seconds=timeout_ms / 1000.0,
            )
        else:
            payload = self.transport.call(
                "locate_files",
                {
                    "project": self.project,
                    "intent": intent,
                    "max_files": max_files,
                    "max_internal_rows": max_internal_rows,
                },
                timeout_ms=timeout_ms,
            )
        status = payload.get("status")
        if status not in {"ok", "no_matches"}:
            raise ValueError("Provider returned an invalid locate_files status")
        files = payload.get("files")
        if not isinstance(files, list) or len(files) > max_files:
            raise ValueError("Provider exceeded the locate_files file budget")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("Provider returned an invalid file entry")
            path = self._safe_located_path(item.get("path"))
            if path in seen:
                raise ValueError("Provider returned duplicate locate_files paths")
            rank = item.get("rank")
            evidence_count = item.get("evidence_count")
            if not isinstance(rank, (int, float)) or isinstance(rank, bool):
                raise ValueError("Provider returned an invalid file rank")
            if not isinstance(evidence_count, int) or isinstance(evidence_count, bool) or evidence_count < 1:
                raise ValueError("Provider returned an invalid evidence count")
            seen.add(path)
            normalized.append({"path": path, "rank": float(rank), "evidence_count": evidence_count})
        terms = payload.get("matched_terms")
        if not isinstance(terms, list) or not all(isinstance(term, str) for term in terms):
            raise ValueError("Provider returned invalid matched terms")
        budget = payload.get("budget")
        expected_budget = {
            "provider_queries": 1,
            "max_internal_rows": max_internal_rows,
            "max_files": max_files,
        }
        if budget != expected_budget:
            raise ValueError("Provider returned a mismatched locate_files budget")
        return {
            "status": status,
            "files": normalized,
            "matched_terms": terms,
            "budget": expected_budget,
        }

    def _nodes_from_search(self, payload: dict[str, Any]) -> tuple[Node, ...]:
        columns = payload.get("cols", [])
        if not isinstance(columns, list):
            raise ValueError("Codebase Memory search columns must be a list")
        nodes: list[Node] = []
        for group in payload.get("groups", []):
            if not isinstance(group, dict):
                continue
            prefix = group.get("qn_prefix")
            filename = group.get("file")
            if not isinstance(prefix, str) or not isinstance(filename, str):
                continue
            if (
                Path(filename).is_absolute()
                or filename.startswith("<")
                or Path(filename).suffix.lower() not in CODE_EXTENSIONS
            ):
                continue
            for row in group.get("rows", []):
                if not isinstance(row, list):
                    continue
                values = dict(zip(columns, row))
                name = values.get("name")
                lines = _line_range(values.get("lines"))
                if not isinstance(name, str) or not lines:
                    continue
                node_id = f"{prefix}.{name}" if prefix else name
                node = Node(
                    id=node_id,
                    kind=str(values.get("label", "symbol")).lower(),
                    name=name,
                    location=SourceRange(filename, lines[0], lines[1]),
                    provider=self.name,
                    confidence=1.0,
                    evidence_hash=_hash({"id": node_id, "file": filename, "lines": lines}),
                    attributes={"project": self.project},
                )
                self._node_cache[node_id] = node
                nodes.append(node)
        return tuple(nodes)

    def _search_name(
        self,
        symbol: str,
        *,
        target_path: str = "",
        target_owner: str = "",
        timeout_seconds: float | None = None,
    ) -> tuple[Node, ...]:
        selector = (
            ("--qn-pattern", rf"(^|\.){re.escape(target_owner)}\.{re.escape(symbol)}$")
            if target_owner
            else ("--name-pattern", f"^{re.escape(symbol)}$")
        )
        payload = self._run(
            "search_graph",
            "--project",
            self.project,
            *selector,
            "--format",
            "json",
            "--limit",
            "100",
            timeout_seconds=timeout_seconds,
        )
        return tuple(
            node for node in self._nodes_from_search(payload)
            if node.name == symbol
            and (not target_path or node.location.path == target_path)
            and (
                not target_owner
                or node.id == f"{target_owner}.{symbol}"
                or node.id.endswith(f".{target_owner}.{symbol}")
            )
        )

    def definitions(
        self, symbol: str, *, target_path: str = "", target_owner: str = ""
    ) -> tuple[Node, ...]:
        """Return every exact-name definition with its stable qualified identity."""
        self._invalidate_if_index_changed()
        key = (symbol, target_path, target_owner)
        cached = self._definition_cache.get(key)
        if cached is not None:
            return cached
        result = self._search_name(
            symbol, target_path=target_path, target_owner=target_owner
        )
        self._definition_cache[key] = result
        return result

    def callers(
        self,
        symbol: str,
        *,
        target_path: str = "",
        target_owner: str = "",
        max_nodes: int = 100,
        max_edges: int = 200,
        timeout_ms: int = 30_000,
    ) -> ImpactTraversal:
        return self.impact(
            symbol, direction="upstream", max_depth=1, target_path=target_path,
            target_owner=target_owner,
            max_nodes=max_nodes, max_edges=max_edges, timeout_ms=timeout_ms,
        )

    def callees(
        self,
        symbol: str,
        *,
        target_path: str = "",
        target_owner: str = "",
        max_nodes: int = 100,
        max_edges: int = 200,
        timeout_ms: int = 30_000,
    ) -> ImpactTraversal:
        return self.impact(
            symbol, direction="downstream", max_depth=1, target_path=target_path,
            target_owner=target_owner,
            max_nodes=max_nodes, max_edges=max_edges, timeout_ms=timeout_ms,
        )

    def related_tests(
        self,
        symbol: str,
        *,
        target_path: str = "",
        target_owner: str = "",
        max_nodes: int = 100,
        max_edges: int = 200,
        timeout_ms: int = 30_000,
    ) -> ImpactTraversal:
        traversal = self.impact(
            symbol,
            direction="upstream",
            max_depth=1,
            target_path=target_path,
            target_owner=target_owner,
            max_nodes=max_nodes,
            max_edges=max_edges,
            timeout_ms=timeout_ms,
        )
        hits = tuple(
            hit
            for hit in traversal
            if "tests" in Path(hit.node.location.path).parts
            or ".test." in Path(hit.node.location.path).name
            or Path(hit.node.location.path).name.startswith("test_")
        )
        return ImpactTraversal(
            hits,
            traversal.truncated,
            traversal.reasons,
            traversal.examined_nodes,
            traversal.examined_edges,
        )

    def _search_identity(
        self, node_id: str, *, timeout_seconds: float | None = None
    ) -> Node | None:
        return self._search_identities(
            (node_id,), timeout_seconds=timeout_seconds
        ).get(node_id)

    def _search_identities(
        self, node_ids: tuple[str, ...], *, timeout_seconds: float | None = None
    ) -> dict[str, Node]:
        requested = tuple(dict.fromkeys(node_ids))
        matches = {
            node_id: self._node_cache[node_id]
            for node_id in requested
            if node_id in self._node_cache
        }
        missing = tuple(node_id for node_id in requested if node_id not in matches)
        if not missing:
            return matches
        payload = self._run(
            "search_graph",
            "--project",
            self.project,
            "--qn-pattern",
            "^(" + "|".join(re.escape(node_id).replace(r"\-", "-") for node_id in missing) + ")$",
            "--format",
            "json",
            "--limit",
            str(len(missing)),
            timeout_seconds=timeout_seconds,
        )
        requested_set = set(missing)
        for node in self._nodes_from_search(payload):
            if node.id not in requested_set:
                continue
            if node.id in matches:
                raise RuntimeError(f"expected one exact node for {node.id}, found multiple")
            matches[node.id] = node
        # Traces can contain external/library pseudo-nodes that have no
        # repository source location. They intentionally remain absent.
        return matches

    @staticmethod
    def _trace_rows(payload: dict[str, Any], section: str) -> tuple[dict[str, Any], ...]:
        trace = payload.get(section, {})
        if not isinstance(trace, dict):
            return ()
        columns = trace.get("cols", [])
        rows: list[dict[str, Any]] = []
        for group in trace.get("groups", []):
            if not isinstance(group, dict) or not isinstance(group.get("qn_prefix"), str):
                continue
            for raw in group.get("rows", []):
                if not isinstance(raw, list):
                    continue
                row = dict(zip(columns, raw))
                name = row.get("name")
                if isinstance(name, str):
                    row["id"] = f"{group['qn_prefix']}.{name}"
                    rows.append(row)
        return tuple(rows)

    def impact(
        self,
        symbol: str,
        *,
        direction: str,
        max_depth: int,
        target_path: str = "",
        target_owner: str = "",
        max_nodes: int = 100,
        max_edges: int = 200,
        timeout_ms: int = 30_000,
    ) -> ImpactTraversal:
        if direction not in {"upstream", "downstream"}:
            raise ValueError(f"unsupported direction: {direction}")
        self._invalidate_if_index_changed()
        cache_key = (
            symbol, direction, max_depth, target_path, target_owner,
            max_nodes, max_edges, timeout_ms,
        )
        cached = self._impact_cache.get(cache_key)
        if cached is not None:
            return cached
        deadline = monotonic() + timeout_ms / 1000.0
        reasons: list[str] = []

        def remaining() -> float:
            value = deadline - monotonic()
            if value <= 0:
                raise TimeoutError("impact traversal exceeded the query time budget")
            return value

        try:
            definition_key = (symbol, target_path, target_owner)
            seeds = self._definition_cache.get(definition_key)
            if seeds is None:
                seeds = self._search_name(
                    symbol,
                    target_path=target_path,
                    target_owner=target_owner,
                    timeout_seconds=remaining(),
                )
                self._definition_cache[definition_key] = seeds
        except TimeoutError:
            return ImpactTraversal((), True, ("time_budget_exceeded",))
        if not seeds:
            return ImpactTraversal(())
        graph = EvidenceGraph(seeds)
        frontier = {node.id for node in seeds}
        expanded: set[str] = set()
        discovered = set(frontier)
        edge_ids: set[tuple[str, str, str]] = set()
        examined_neighbor_ids: set[str] = set()
        examined_edge_ids: set[tuple[str, str, str]] = set()
        stop = False
        for _depth in range(1, max_depth + 1):
            next_frontier: set[str] = set()
            for current_id in sorted(frontier):
                if current_id in expanded:
                    continue
                expanded.add(current_id)
                cbm_direction = "inbound" if direction == "upstream" else "outbound"
                section = "callers" if direction == "upstream" else "callees"
                try:
                    payload = self._run(
                        "trace_path",
                        "--project",
                        self.project,
                        "--function-name",
                        current_id,
                        "--direction",
                        cbm_direction,
                        "--depth",
                        "1",
                        "--limit",
                        "100",
                        "--include-tests",
                        "true",
                        "--include-evidence",
                        "true",
                        "--format",
                        "json",
                        timeout_seconds=remaining(),
                    )
                except TimeoutError:
                    reasons.append("time_budget_exceeded")
                    stop = True
                    break
                rows = self._trace_rows(payload, section)
                if len(rows) >= 100:
                    reasons.append("provider_result_limit")
                exact_rows = tuple(
                    row
                    for row in rows
                    if row.get("strategy") == "lsp"
                    and (
                        not isinstance(row.get("confidence"), (int, float))
                        or float(row["confidence"]) >= 0.9
                    )
                )
                remaining_node_slots = max_nodes - (len(discovered) - len(seeds))
                remaining_edge_slots = max_edges - len(edge_ids)
                selected_rows: list[dict[str, Any]] = []
                pending_nodes: set[str] = set()
                pending_edges: set[tuple[str, str, str]] = set()
                for row in exact_rows:
                    # CBM can include low-confidence name-based guesses beside
                    # LSP-resolved edges. The default Atlas graph contract is
                    # exact-only, so guesses must not consume result budgets or
                    # enter paths labeled as exact.
                    neighbor_id = row["id"]
                    examined_neighbor_ids.add(neighbor_id)
                    if (
                        neighbor_id not in discovered
                        and neighbor_id not in pending_nodes
                        and len(pending_nodes) >= remaining_node_slots
                    ):
                        reasons.append("node_budget_exceeded")
                        stop = True
                        break
                    edge_id = (
                        neighbor_id if direction == "upstream" else current_id,
                        current_id if direction == "upstream" else neighbor_id,
                        "calls",
                    )
                    examined_edge_ids.add(edge_id)
                    if (
                        edge_id not in edge_ids
                        and edge_id not in pending_edges
                        and len(pending_edges) >= remaining_edge_slots
                    ):
                        reasons.append("edge_budget_exceeded")
                        stop = True
                        break
                    selected_rows.append(row)
                    if neighbor_id not in discovered:
                        pending_nodes.add(neighbor_id)
                    if edge_id not in edge_ids:
                        pending_edges.add(edge_id)
                try:
                    resolved = self._search_identities(
                        tuple(row["id"] for row in selected_rows),
                        timeout_seconds=remaining(),
                    )
                except TimeoutError:
                    reasons.append("time_budget_exceeded")
                    stop = True
                    resolved = {}
                for row in selected_rows:
                    neighbor_id = row["id"]
                    neighbor = resolved.get(neighbor_id)
                    if neighbor is None:
                        continue
                    edge_id = (
                        neighbor_id if direction == "upstream" else current_id,
                        current_id if direction == "upstream" else neighbor_id,
                        "calls",
                    )
                    graph.add_node(neighbor)
                    discovered.add(neighbor_id)
                    confidence = row.get("confidence")
                    edge_confidence = float(confidence) if isinstance(confidence, (int, float)) else 1.0
                    source_id, target_id = (
                        (neighbor_id, current_id)
                        if direction == "upstream"
                        else (current_id, neighbor_id)
                    )
                    graph.add_edge(
                        Edge(
                            source_id=source_id,
                            target_id=target_id,
                            relation="calls",
                            provider=self.name,
                            confidence=edge_confidence,
                            evidence_hash=_hash({"source": source_id, "target": target_id, "row": row}),
                            resolution="exact",
                            attributes={
                                "cbm_strategy": row.get("strategy", "unknown"),
                                "direction": cbm_direction,
                            },
                        )
                    )
                    edge_ids.add(edge_id)
                    next_frontier.add(neighbor_id)
                if stop:
                    break
            if stop:
                break
            frontier = next_frontier
            if not frontier:
                break
        hits = graph.impact(
            (node.id for node in seeds), direction=direction, max_depth=max_depth
        )
        traversal = ImpactTraversal(
            hits,
            bool(reasons),
            tuple(dict.fromkeys(reasons)),
            len(examined_neighbor_ids),
            len(examined_edge_ids),
        )
        if "time_budget_exceeded" not in traversal.reasons:
            self._impact_cache[cache_key] = traversal
        return traversal
