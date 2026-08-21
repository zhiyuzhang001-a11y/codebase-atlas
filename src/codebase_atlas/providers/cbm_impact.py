"""Identity-safe impact expansion over exact Codebase Memory graph edges."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from ..contracts import Edge, Node, SourceRange
from ..graph import EvidenceGraph, ImpactHit


CODE_EXTENSIONS = {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"}


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
    ) -> None:
        self.binary = binary.resolve()
        self.repository = repository.resolve()
        self.cache_dir = cache_dir.resolve()
        self.project = project
        self._node_cache: dict[str, Node] = {}

    def _run(self, tool: str, *args: str) -> dict[str, Any]:
        environment = os.environ.copy()
        environment["CBM_CACHE_DIR"] = str(self.cache_dir)
        environment["CBM_ALLOWED_ROOT"] = str(self.repository.parent)
        completed = subprocess.run(
            [str(self.binary), "cli", "--json", tool, *args],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
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

    def _search_name(self, symbol: str, *, target_path: str = "") -> tuple[Node, ...]:
        payload = self._run(
            "search_graph",
            "--project",
            self.project,
            "--name-pattern",
            f"^{re.escape(symbol)}$",
            "--format",
            "json",
            "--limit",
            "100",
        )
        return tuple(
            node for node in self._nodes_from_search(payload)
            if node.name == symbol and (not target_path or node.location.path == target_path)
        )

    def definitions(self, symbol: str, *, target_path: str = "") -> tuple[Node, ...]:
        """Return every exact-name definition with its stable qualified identity."""
        return self._search_name(symbol, target_path=target_path)

    def callers(self, symbol: str, *, target_path: str = "") -> tuple[ImpactHit, ...]:
        return self.impact(symbol, direction="upstream", max_depth=1, target_path=target_path)

    def callees(self, symbol: str, *, target_path: str = "") -> tuple[ImpactHit, ...]:
        return self.impact(symbol, direction="downstream", max_depth=1, target_path=target_path)

    def related_tests(self, symbol: str, *, target_path: str = "") -> tuple[ImpactHit, ...]:
        return tuple(
            hit
            for hit in self.impact(
                symbol,
                direction="upstream",
                max_depth=1,
                target_path=target_path,
            )
            if "tests" in Path(hit.node.location.path).parts
            or ".test." in Path(hit.node.location.path).name
            or Path(hit.node.location.path).name.startswith("test_")
        )

    def _search_identity(self, node_id: str) -> Node | None:
        cached = self._node_cache.get(node_id)
        if cached is not None:
            return cached
        payload = self._run(
            "search_graph",
            "--project",
            self.project,
            "--qn-pattern",
            f"^{re.escape(node_id)}$",
            "--format",
            "json",
            "--limit",
            "10",
        )
        matches = [node for node in self._nodes_from_search(payload) if node.id == node_id]
        if not matches:
            # Traces can contain external/library pseudo-nodes that have no
            # repository source location. They are not product results.
            return None
        if len(matches) != 1:
            raise RuntimeError(f"expected one exact node for {node_id}, found {len(matches)}")
        return matches[0]

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

    def impact(self, symbol: str, *, direction: str, max_depth: int, target_path: str = "") -> tuple[ImpactHit, ...]:
        if direction not in {"upstream", "downstream"}:
            raise ValueError(f"unsupported direction: {direction}")
        seeds = self._search_name(symbol, target_path=target_path)
        if not seeds:
            return ()
        graph = EvidenceGraph(seeds)
        frontier = {node.id for node in seeds}
        expanded: set[str] = set()
        for _depth in range(1, max_depth + 1):
            next_frontier: set[str] = set()
            for current_id in sorted(frontier):
                if current_id in expanded:
                    continue
                expanded.add(current_id)
                cbm_direction = "inbound" if direction == "upstream" else "outbound"
                section = "callers" if direction == "upstream" else "callees"
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
                )
                for row in self._trace_rows(payload, section):
                    neighbor_id = row["id"]
                    neighbor = self._search_identity(neighbor_id)
                    if neighbor is None:
                        continue
                    graph.add_node(neighbor)
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
                    next_frontier.add(neighbor_id)
            frontier = next_frontier
            if not frontier:
                break
        return graph.impact(
            (node.id for node in seeds),
            direction=direction,
            max_depth=max_depth,
        )
