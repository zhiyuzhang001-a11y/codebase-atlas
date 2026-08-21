"""Exact TypeScript/Javascript test callback mapping."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from ..contracts import Edge, Node, SourceRange


class TypeScriptTestProvider:
    name = "atlas-ts-tests"

    def __init__(self, node: Path, analyzer: Path, tsconfig: Path | None = None) -> None:
        self.node = node.resolve()
        self.analyzer = analyzer.resolve()
        self.tsconfig = tsconfig

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
        target_owner: str = "",
        timeout_ms: int | None = None,
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
        if target_owner:
            command.extend(("--target-owner", target_owner))
        if self.tsconfig is not None:
            command.extend(("--tsconfig", str(self.tsconfig)))
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_ms / 1000.0 if timeout_ms is not None else None,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("TypeScript test analysis exceeded the query time budget") from exc
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "TypeScript test analyzer failed")
        payload = json.loads(completed.stdout)
        if payload.get("schema_version") != 1 or not isinstance(payload.get("results"), list):
            raise ValueError("invalid TypeScript test analyzer result")
        return tuple(
            (self._node(item["node"]), self._edge(item["edge"]))
            for item in payload["results"]
        )

    def references(
        self,
        repository: Path,
        symbol: str,
        *,
        target_path: str = "",
        target_owner: str = "",
        timeout_ms: int | None = None,
    ) -> tuple[Node, ...]:
        command = [
            str(self.node), str(self.analyzer),
            "--repo", str(repository.resolve()),
            "--symbol", symbol,
            "--query-type", "references",
        ]
        if target_path:
            command.extend(("--target-path", target_path))
        if target_owner:
            command.extend(("--target-owner", target_owner))
        if self.tsconfig is not None:
            command.extend(("--tsconfig", str(self.tsconfig)))
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_ms / 1000.0 if timeout_ms is not None else None,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("TypeScript reference analysis exceeded the query time budget") from exc
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "TypeScript reference analyzer failed")
        payload = json.loads(completed.stdout)
        if (
            payload.get("schema_version") != 1
            or payload.get("query_type") != "references"
            or not isinstance(payload.get("results"), list)
        ):
            raise ValueError("invalid TypeScript reference analyzer result")
        return tuple(self._node(item) for item in payload["results"])
