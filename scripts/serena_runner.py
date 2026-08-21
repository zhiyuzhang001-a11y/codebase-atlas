#!/usr/bin/env python3
"""JSON-lines bridge loaded only by the pinned Serena virtual environment."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

from serena.agent import SerenaAgent
from serena.config.serena_config import ProjectConfig, RegisteredProject, SerenaConfig
from serena.tools import FindReferencingSymbolsTool, FindSymbolTool
from solidlsp.ls_config import LanguageServerId


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def definitions(
    tool: FindSymbolTool,
    query: str,
    target_path: str = "",
    target_owner: str = "",
) -> list[dict[str, Any]]:
    rows = json.loads(tool.apply(name_path_pattern=query))
    if not isinstance(rows, list):
        raise ValueError("Serena find_symbol returned a non-list result")
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("name_path"), str)
        and row["name_path"].split("/")[-1] == query
        and (
            not target_owner
            or len(row["name_path"].split("/")) >= 2
            and row["name_path"].split("/")[-2] == target_owner
        )
        and isinstance(row.get("relative_path"), str)
        and (not target_path or row["relative_path"] == target_path)
    ]


def query(
    agent: SerenaAgent,
    query_type: str,
    symbol: str,
    target_path: str = "",
    target_owner: str = "",
) -> list[dict[str, Any]]:
    matches = definitions(
        agent.get_tool(FindSymbolTool), symbol, target_path, target_owner
    )
    if query_type == "definition":
        return [
            {
                "path": row["relative_path"],
                "symbol": symbol,
                "start_line": row["body_location"]["start_line"] + 1,
                "end_line": row["body_location"]["end_line"] + 1,
                "provider_id": row["name_path"],
                "provenance": {
                    "provider": "serena",
                    "operation": "find_symbol",
                    "kind": row.get("kind"),
                    "source_line_base": 0,
                },
            }
            for row in matches
            if isinstance(row.get("body_location"), dict)
            and isinstance(row["body_location"].get("start_line"), int)
            and isinstance(row["body_location"].get("end_line"), int)
        ]
    if query_type != "references":
        raise ValueError(f"unsupported query type: {query_type}")
    retriever = agent.get_tool(FindReferencingSymbolsTool).create_language_server_symbol_retriever()
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for match in matches:
        references = retriever.find_referencing_symbols(
            match["name_path"], relative_file_path=match["relative_path"]
        )
        for reference in references:
            path = reference.get_relative_path()
            if path is None:
                continue
            key = (path, reference.line, reference.character)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "path": path,
                    "symbol": symbol,
                    "start_line": reference.line + 1,
                    "end_line": reference.line + 1,
                    "start_column": reference.character + 1,
                    "end_column": reference.character + len(symbol) + 1,
                    "provider_id": match["name_path"],
                    "provenance": {
                        "provider": "serena",
                        "operation": "find_referencing_symbols",
                        "definition_path": match["relative_path"],
                        "reference_character": reference.character,
                        "source_line_base": 0,
                    },
                }
            )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--metadata-root", type=Path, required=True)
    parser.add_argument("--language", choices=("python", "typescript"), required=True)
    args = parser.parse_args()
    repository = args.repo.resolve()
    metadata_root = args.metadata_root.resolve()
    metadata_root.mkdir(parents=True, exist_ok=True)
    config = SerenaConfig(log_level=logging.WARNING).with_headless_mode_overrides()
    config.project_serena_folder_location = str(metadata_root / "$projectFolderName" / ".serena")
    configured_dir = Path(config.get_configured_project_serena_folder(repository))
    configured_dir.mkdir(parents=True, exist_ok=True)
    project_yml = Path(config.get_project_yml_location(repository))
    language = LanguageServerId.PYTHON if args.language == "python" else LanguageServerId.TYPESCRIPT
    if not project_yml.exists():
        project_config = ProjectConfig.autogenerate(
            repository,
            config,
            languages=[language],
            save_to_disk=True,
            interactive=False,
        )
    else:
        project_config = ProjectConfig.load(repository, serena_config=config)
    config.projects = [RegisteredProject(str(repository), project_config)]
    started = perf_counter()
    agent = SerenaAgent(project=str(repository), serena_config=config)
    agent.execute_task(lambda: None)
    if language not in agent.get_active_language_server_ids():
        raise RuntimeError("Serena project started without an active language server")
    emit({"status": "ready", "startup_ms": (perf_counter() - started) * 1000})
    try:
        for line in sys.stdin:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                emit({"status": "stopping"})
                break
            query_started = perf_counter()
            try:
                results = query(
                    agent, request["query_type"], request["query"],
                    str(request.get("target_path", "")),
                    str(request.get("target_owner", "")),
                )
                emit(
                    {
                        "status": "ok",
                        "results": results,
                        "duration_ms": (perf_counter() - query_started) * 1000,
                    }
                )
            except Exception as exc:
                emit(
                    {
                        "status": "error",
                        "message": f"{type(exc).__name__}: {exc}",
                        "duration_ms": (perf_counter() - query_started) * 1000,
                    }
                )
    finally:
        agent.on_shutdown(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
