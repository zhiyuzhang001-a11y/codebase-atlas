"""Exact TypeScript/Javascript test callback mapping."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from ..contracts import Edge, Node, SourceRange


class TypeScriptTestProvider:
    name = "atlas-ts-tests"

    def __init__(self, node: Path, analyzer: Path) -> None:
        self.node = node.resolve()
        self.analyzer = analyzer.resolve()

    @staticmethod
    def _node(payload: dict[str, Any]) -> Node:
        value = dict(payload)
        value["location"] = SourceRange(**value["location"])
        return Node(**value)

    @staticmethod
    def _edge(payload: dict[str, Any]) -> Edge:
        return Edge(**payload)

    def related_tests(
        self,
        repository: Path,
        symbol: str,
        *,
        target_path: str = "",
    ) -> tuple[tuple[Node, Edge], ...]:
        command = [
            str(self.node),
            str(self.analyzer),
            "--repo",
            str(repository.resolve()),
            "--symbol",
            symbol,
        ]
        if target_path:
            command.extend(("--target-path", target_path))
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "TypeScript test analyzer failed")
        payload = json.loads(completed.stdout)
        if payload.get("schema_version") != 1 or not isinstance(payload.get("results"), list):
            raise ValueError("invalid TypeScript test analyzer result")
        return tuple(
            (self._node(item["node"]), self._edge(item["edge"]))
            for item in payload["results"]
        )
