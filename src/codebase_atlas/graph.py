"""Identity-safe in-memory graph and explicit-depth impact traversal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import Edge, Node


@dataclass(frozen=True)
class ImpactHit:
    node: Node
    depth: int
    path: tuple[Edge, ...]


class EvidenceGraph:
    def __init__(self, nodes: Iterable[Node] = (), edges: Iterable[Edge] = ()) -> None:
        self._nodes: dict[str, Node] = {}
        self._incoming: dict[str, list[Edge]] = {}
        self._outgoing: dict[str, list[Edge]] = {}
        for node in nodes:
            self.add_node(node)
        for edge in edges:
            self.add_edge(edge)

    def add_node(self, node: Node) -> None:
        existing = self._nodes.get(node.id)
        if existing is not None and existing != node:
            raise ValueError(f"conflicting node identity: {node.id}")
        self._nodes[node.id] = node

    def add_edge(self, edge: Edge) -> None:
        if edge.source_id not in self._nodes or edge.target_id not in self._nodes:
            raise ValueError("edge endpoints must be added before the edge")
        if edge not in self._outgoing.setdefault(edge.source_id, []):
            self._outgoing[edge.source_id].append(edge)
            self._incoming.setdefault(edge.target_id, []).append(edge)

    def impact(
        self,
        seed_ids: Iterable[str],
        *,
        direction: str,
        max_depth: int,
        include_heuristic: bool = False,
    ) -> tuple[ImpactHit, ...]:
        if direction not in {"upstream", "downstream"}:
            raise ValueError(f"unsupported impact direction: {direction}")
        if max_depth < 1:
            raise ValueError("max_depth must be positive")
        seeds = tuple(dict.fromkeys(seed_ids))
        missing = [node_id for node_id in seeds if node_id not in self._nodes]
        if missing:
            raise KeyError(f"unknown impact seed(s): {', '.join(missing)}")

        visited = {node_id: 0 for node_id in seeds}
        paths: dict[str, tuple[Edge, ...]] = {node_id: () for node_id in seeds}
        frontier = list(seeds)
        while frontier:
            current = frontier.pop(0)
            depth = visited[current]
            if depth >= max_depth:
                continue
            edges = self._incoming.get(current, ()) if direction == "upstream" else self._outgoing.get(current, ())
            for edge in sorted(edges, key=lambda item: (item.source_id, item.target_id, item.relation)):
                if edge.resolution != "exact" and not include_heuristic:
                    continue
                neighbor = edge.source_id if direction == "upstream" else edge.target_id
                candidate_depth = depth + 1
                if neighbor in visited and visited[neighbor] <= candidate_depth:
                    continue
                visited[neighbor] = candidate_depth
                paths[neighbor] = paths[current] + (edge,)
                frontier.append(neighbor)

        hits = [
            ImpactHit(self._nodes[node_id], depth, paths[node_id])
            for node_id, depth in visited.items()
            if node_id not in seeds
        ]
        hits.sort(
            key=lambda hit: (
                hit.depth,
                hit.node.location.path,
                hit.node.location.start_line,
                hit.node.id,
            )
        )
        return tuple(hits)
