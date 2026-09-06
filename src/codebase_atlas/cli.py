"""Codebase Atlas command-line entry point."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import shlex
import signal
import secrets
import stat
import sys
import time
import os
import subprocess
import webbrowser

from . import __version__
from .change_analysis import CHANGE_INTENTS, RESPONSE_MODES, analyze_change
from .codex_integration import PROJECT_RULE, codex_apply, codex_plan, codex_remove
from .config import (
    AtlasConfig,
    CONFIG_NAME,
    SHARED_PROVIDER_LAYOUT,
    _asset,
    diagnose,
)
from .index_state import (
    index_freshness,
    provider_database_health,
    record_index_state,
    repository_snapshot,
)
from .lifecycle import CodebaseMemoryDaemon, SharedCodebaseMemorySession
from .provider_process import run_provider_command
from .maintenance import apply_cleanup, cleanup_plan, inspect_installation, repair_plan
from .mcp import McpServer, run_stdio
from .operations import (
    STALE_POLICIES,
    attach_operational_status,
    operational_index_status,
    stale_policy_error,
    unknown_operational_status,
)
from .onboarding import OnboardingInputs, apply_plan, build_plan
from .providers import CodebaseMemoryImpactProvider, SerenaSemanticProvider, TypeScriptTestProvider
from .project_discovery import resolve_project
from .reloadable_mcp import ReloadingMcpServer
from .provider_layout import provider_environment
from .provider_transport import CodebaseMemoryMcpTransport
from .project_lifecycle import operational_lifecycle_status
from .refresh_coordinator import RefreshCoordinator, refresh_with_retry
from .provider_migration import (
    plan_provider_migration,
    prepare_shared_provider_root,
    shared_provider_config,
)
from .python_registration_store import (
    RegistrationIndexError,
    load_registration_index_state,
    registration_index_health,
    stage_registration_index,
)
from .refresh_planner import (
    RefreshPlanError,
    build_generation_manifest,
    generation_artifact_identity,
    manifest_path,
    plan_refresh,
    stage_generation_manifest_candidate,
)
from .runtime import required_checks_ok, runtime_checks
from .service import AtlasService, QueryRequest
from .session_update import disabled_session_update, session_start_update
from .version_check import VersionNotifier
from .web_ui import LocalUiServer


@contextmanager
def _graceful_termination():
    """Turn SIGTERM into normal unwinding so Provider ownership is released."""
    previous = signal.getsignal(signal.SIGTERM)

    def terminate(_signum, _frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _run_mcp_with_graceful_termination(server: McpServer) -> None:
    with _graceful_termination():
        try:
            run_stdio(server)
        except KeyboardInterrupt:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codebase-atlas")
    parser.add_argument("--version", action="store_true")
    commands = parser.add_subparsers(dest="command")
    initialize = commands.add_parser("init", help="create a project-local Atlas configuration")
    initialize.add_argument("--repo", type=Path, default=Path.cwd())
    initialize.add_argument("--config", type=Path)
    initialize.add_argument("--language", choices=("python", "typescript"))
    initialize.add_argument("--node", type=Path)
    initialize.add_argument("--cbm-binary", type=Path)
    initialize.add_argument("--serena-python", type=Path)
    initialize.add_argument("--node-bin-dir", type=Path)
    initialize.add_argument("--tsconfig", type=Path)
    initialize.add_argument("--data-dir", type=Path)
    setup = commands.add_parser(
        "setup", help="read-only compatibility check for required local runtimes"
    )
    setup.add_argument("--repo", type=Path, default=Path.cwd())
    setup.add_argument("--config", type=Path)
    setup.add_argument("--language", choices=("python", "typescript"))
    setup.add_argument("--node", type=Path)
    setup.add_argument("--cbm-binary", type=Path)
    setup.add_argument("--serena-python", type=Path)
    setup.add_argument("--node-bin-dir", type=Path)
    setup.add_argument("--tsconfig", type=Path)
    onboard = commands.add_parser("onboard", help="plan or explicitly apply a guided local onboarding flow")
    onboard.add_argument("--repo", type=Path, default=Path.cwd())
    onboard.add_argument("--config", type=Path)
    onboard.add_argument("--language", choices=("python", "typescript"))
    onboard.add_argument("--node", type=Path)
    onboard.add_argument("--cbm-binary", type=Path)
    onboard.add_argument("--serena-python", type=Path)
    onboard.add_argument("--node-bin-dir", type=Path)
    onboard.add_argument("--tsconfig", type=Path)
    onboard.add_argument("--data-dir", type=Path)
    onboard.add_argument("--mode", choices=("fast", "moderate", "full"), default="fast")
    onboard.add_argument("--apply", action="store_true")
    codex = commands.add_parser(
        "codex", help="plan, apply, or remove the local Codex MCP integration"
    )
    codex_commands = codex.add_subparsers(dest="codex_command", required=True)
    for codex_mode in ("plan", "apply", "remove"):
        codex_action = codex_commands.add_parser(codex_mode)
        codex_action.add_argument(
            "--config", type=Path, default=Path.cwd() / CONFIG_NAME
        )
        codex_action.add_argument("--name", default="codebase_atlas")
        codex_action.add_argument("--codex-binary", type=Path)
        codex_action.add_argument("--atlas-executable", type=Path)
        codex_action.add_argument(
            "--scope", choices=("global", "global-auto", "project"), default="global"
        )
        codex_action.add_argument("--codex-project-root", type=Path)
    doctor = commands.add_parser("doctor", help="check configured runtimes and index state")
    doctor.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    inspect = commands.add_parser("inspect", help="inspect index health and storage without modifying it")
    inspect.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    inspect.add_argument(
        "--deep", action="store_true",
        help="also run SQLite quick_check; this may take time for a large index",
    )
    migrate_provider = commands.add_parser(
        "migrate-provider",
        help="preview the legacy-to-shared Provider migration without modifying it",
    )
    migrate_provider.add_argument(
        "--config", type=Path, default=Path.cwd() / CONFIG_NAME
    )
    migrate_provider.add_argument(
        "--mode", choices=("fast", "moderate", "full"), default="fast"
    )
    migrate_provider.add_argument(
        "--apply", action="store_true",
        help="rebuild/verify the shared index, then publish the project switch",
    )
    repair = commands.add_parser(
        "repair", help="diagnose recovery; use --apply for an explicit safe Provider update"
    )
    repair.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    repair.add_argument("--mode", choices=("fast", "moderate", "full"), default="fast")
    repair.add_argument(
        "--apply", action="store_true",
        help="apply the proposed repair; without this flag the command is read-only",
    )
    clean = commands.add_parser(
        "clean", help="dry-run cleanup of recognized obsolete Atlas files"
    )
    clean.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    clean.add_argument(
        "--apply", action="store_true",
        help="remove exactly the files in the displayed in-memory plan",
    )
    index = commands.add_parser("index", help="build the configured structural index")
    index.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    index.add_argument("--mode", choices=("fast", "moderate", "full"), default="fast")
    update = commands.add_parser("update", help="safely update a configured structural index")
    update.add_argument("--config", type=Path, default=Path.cwd() / CONFIG_NAME)
    update.add_argument("--mode", choices=("fast", "moderate", "full"), default="fast")
    update.add_argument(
        "--force-provider",
        action="store_true",
        help="run the Provider even when Atlas source state is already current",
    )
    refresh_plan = commands.add_parser(
        "plan-refresh",
        help="read-only exact dirty-set plan for a future same-process refresh",
    )
    refresh_plan.add_argument(
        "--config", type=Path, default=Path.cwd() / CONFIG_NAME
    )
    related = commands.add_parser("related-tests")
    related.add_argument("--repo", type=Path, required=True)
    related.add_argument("--symbol", required=True)
    related.add_argument("--target-path", default="")
    related.add_argument("--target-owner", default="")
    related.add_argument("--node", type=Path, required=True)
    related.add_argument("--tsconfig", type=Path)
    related.add_argument(
        "--analyzer",
        type=Path,
        default=_asset("ts_test_analyzer.mjs"),
    )
    impact = commands.add_parser("impact")
    impact.add_argument("--repo", type=Path, required=True)
    impact.add_argument("--symbol", required=True)
    impact.add_argument("--direction", choices=("upstream", "downstream"), required=True)
    impact.add_argument("--depth", type=int, required=True)
    impact.add_argument("--binary", type=Path, required=True)
    impact.add_argument("--cache-dir", type=Path, required=True)
    impact.add_argument("--project", required=True)
    impact.add_argument("--target-path", default="")
    impact.add_argument("--target-owner", default="")
    impact.add_argument("--max-nodes", type=int, default=100)
    impact.add_argument("--max-edges", type=int, default=200)
    impact.add_argument("--timeout-ms", type=int, default=30_000)
    mcp = commands.add_parser(
        "mcp", help="run the query and explicit-refresh MCP server over stdio"
    )
    mcp.add_argument("--config", type=Path)
    mcp.add_argument("--repo", type=Path)
    mcp.add_argument("--node", type=Path)
    mcp.add_argument("--analyzer", type=Path, default=_asset("ts_test_analyzer.mjs"))
    mcp.add_argument("--binary", type=Path)
    mcp.add_argument("--cache-dir", type=Path)
    mcp.add_argument("--project")
    mcp.add_argument("--serena-python", type=Path)
    mcp.add_argument("--serena-runner", type=Path, default=_asset("serena_runner.py"))
    mcp.add_argument("--serena-home", type=Path)
    mcp.add_argument("--metadata-root", type=Path)
    mcp.add_argument("--language", choices=("python", "typescript"))
    mcp.add_argument("--node-bin-dir", type=Path)
    mcp.add_argument("--tsconfig", type=Path)
    mcp.add_argument("--stale-policy", choices=STALE_POLICIES, default="warn")
    mcp.add_argument(
        "--auto-update", choices=("off", "session-start", "on-query"), default="off"
    )
    mcp.add_argument("--auto-update-timeout", type=float, default=60.0)
    mcp.add_argument("--version-check", choices=("off", "notify"), default="off")
    mcp_auto = commands.add_parser(
        "mcp-auto", help="run a fail-closed MCP for the current Codex project"
    )
    mcp_auto.add_argument("--root", type=Path)
    mcp_auto.add_argument("--stale-policy", choices=STALE_POLICIES, default="warn")
    mcp_auto.add_argument(
        "--auto-update", choices=("off", "session-start", "on-query"), default="on-query"
    )
    mcp_auto.add_argument("--auto-update-timeout", type=float, default=60.0)
    mcp_auto.add_argument("--version-check", choices=("off", "notify"), default="notify")
    ui = commands.add_parser("ui", help="open the lightweight read-only local browser UI")
    ui.add_argument("--config", type=Path)
    ui.add_argument("--repo", type=Path)
    ui.add_argument("--node", type=Path)
    ui.add_argument("--analyzer", type=Path, default=_asset("ts_test_analyzer.mjs"))
    ui.add_argument("--binary", type=Path)
    ui.add_argument("--cache-dir", type=Path)
    ui.add_argument("--project")
    ui.add_argument("--serena-python", type=Path)
    ui.add_argument("--serena-runner", type=Path, default=_asset("serena_runner.py"))
    ui.add_argument("--serena-home", type=Path)
    ui.add_argument("--metadata-root", type=Path)
    ui.add_argument("--language", choices=("python", "typescript"))
    ui.add_argument("--node-bin-dir", type=Path)
    ui.add_argument("--tsconfig", type=Path)
    ui.add_argument("--stale-policy", choices=STALE_POLICIES, default="warn")
    ui.add_argument("--port", type=int, default=0, help="loopback port; 0 selects a free port")
    ui.add_argument("--no-open", action="store_true", help="do not open the system browser")
    query = commands.add_parser("query", help="run one query through the shared product service")
    query.add_argument("query_type", choices=("definition", "references", "callers", "callees", "related_tests", "impact"))
    query.add_argument("symbol")
    query.add_argument("--config", type=Path)
    query.add_argument("--repo", type=Path)
    query.add_argument("--node", type=Path)
    query.add_argument("--analyzer", type=Path, default=_asset("ts_test_analyzer.mjs"))
    query.add_argument("--binary", type=Path)
    query.add_argument("--cache-dir", type=Path)
    query.add_argument("--project")
    query.add_argument("--serena-python", type=Path)
    query.add_argument("--serena-runner", type=Path, default=_asset("serena_runner.py"))
    query.add_argument("--serena-home", type=Path)
    query.add_argument("--metadata-root", type=Path)
    query.add_argument("--language", choices=("python", "typescript"))
    query.add_argument("--node-bin-dir", type=Path)
    query.add_argument("--tsconfig", type=Path)
    query.add_argument("--target-path", default="")
    query.add_argument("--target-owner", default="")
    query.add_argument("--relation", choices=("registers",), default="")
    query.add_argument("--direction", choices=("upstream", "downstream"), default="upstream")
    query.add_argument("--depth", type=int, default=1)
    query.add_argument("--max-nodes", type=int, default=100)
    query.add_argument("--max-edges", type=int, default=200)
    query.add_argument("--timeout-ms", type=int, default=30_000)
    query.add_argument("--stale-policy", choices=STALE_POLICIES, default="warn")
    analyze = commands.add_parser(
        "analyze-change", help="build one exact, bounded change brief"
    )
    analyze.add_argument("symbol")
    analyze.add_argument("--intent", choices=CHANGE_INTENTS, default="change_behavior")
    analyze.add_argument("--response-mode", choices=RESPONSE_MODES, default="full")
    for action in query._actions:
        if action.dest in {"help", "query_type", "symbol", "relation"}:
            continue
        kwargs = {
            "dest": action.dest,
            "required": action.required,
            "default": (
                60_000 if action.dest == "timeout_ms"
                else 2 if action.dest == "depth"
                else action.default
            ),
        }
        if action.type is not None:
            kwargs["type"] = action.type
        if action.choices is not None:
            kwargs["choices"] = action.choices
        analyze.add_argument(*action.option_strings, **kwargs)
    batch = commands.add_parser(
        "query-batch", help="run JSON-lines queries through one long-lived product service"
    )
    for action in query._actions:
        if action.dest in {
            "help", "query_type", "symbol", "target_path", "target_owner", "relation", "direction", "depth",
            "max_nodes", "max_edges", "timeout_ms",
        }:
            continue
        options = action.option_strings
        kwargs = {
            "dest": action.dest,
            "required": action.required,
            "default": action.default,
        }
        if action.type is not None:
            kwargs["type"] = action.type
        if action.choices is not None:
            kwargs["choices"] = action.choices
        batch.add_argument(*options, **kwargs)
    args = parser.parse_args(argv)
    if args.version:
        print(json.dumps({"name": "codebase-atlas", "version": __version__}))
        return 0
    if args.command == "codex":
        operation = {
            "plan": codex_plan,
            "apply": codex_apply,
            "remove": codex_remove,
        }[args.codex_command]
        try:
            result = operation(
                args.config,
                name=args.name,
                codex_binary=args.codex_binary,
                atlas_executable=args.atlas_executable,
                scope=args.scope,
                codex_project_root=args.codex_project_root,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(json.dumps({
                "schema_version": 1,
                "status": "blocked",
                "mode": args.codex_command,
                "error": str(exc),
            }, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] != "blocked" else 2
    if args.command == "onboard":
        config_path = args.config or args.repo / CONFIG_NAME
        plan, config = build_plan(OnboardingInputs(
            args.repo, config_path, args.language, args.node, args.cbm_binary,
            args.serena_python, args.node_bin_dir, args.tsconfig, args.data_dir,
            args.mode,
        ))
        if not args.apply:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0 if plan["status"] == "planned" else 2
        result, code = apply_plan(plan, config, indexer=_index_repository, mode=args.mode)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    if args.command == "setup":
        candidate = args.config
        if candidate is None:
            local = args.repo / CONFIG_NAME
            candidate = local if local.is_file() else None
        if candidate is not None:
            configured = AtlasConfig.load(candidate)
            repository = configured.repository
            language = configured.language
            node = configured.node
            cbm_binary = configured.cbm_binary
            serena_python = configured.serena_python
            node_bin_dir = configured.node_bin_dir
            tsconfig = configured.tsconfig
        else:
            repository = args.repo
            language = args.language or (
                "typescript"
                if args.tsconfig is not None or (repository / "tsconfig.json").is_file()
                else "python"
            )
            node = args.node
            cbm_binary = args.cbm_binary
            serena_python = args.serena_python
            node_bin_dir = args.node_bin_dir
            tsconfig = args.tsconfig
        checks = runtime_checks(
            repository,
            language=language,
            node=node,
            cbm_binary=cbm_binary,
            serena_python=serena_python,
            node_bin_dir=node_bin_dir,
            tsconfig=tsconfig,
        )
        ok = required_checks_ok(checks)
        print(json.dumps({
            "status": "ready" if ok else "incomplete",
            "mode": "read_only",
            "config": str(candidate or ""),
            "language": language,
            "checks": checks,
        }, indent=2))
        return 0 if ok else 2
    if args.command == "init":
        config_path = (args.config or args.repo / CONFIG_NAME).resolve()
        config = AtlasConfig.discover(
            args.repo, language=args.language, node=args.node,
            cbm_binary=args.cbm_binary, serena_python=args.serena_python,
            node_bin_dir=args.node_bin_dir,
            tsconfig=args.tsconfig,
            data_dir=args.data_dir,
        )
        config.write(config_path)
        print(json.dumps({"status": "initialized", "config": str(config_path), "data_dir": str(config.data_dir)}, indent=2))
        return 0
    if args.command == "mcp-auto":
        server = ReloadingMcpServer(
            args.root or Path.cwd(),
            stale_policy=args.stale_policy,
            auto_update=args.auto_update,
            auto_update_timeout=args.auto_update_timeout,
            version_check=args.version_check,
        )
        try:
            _run_mcp_with_graceful_termination(server)
        finally:
            server.close()
        return 0
    if args.command == "inspect":
        config = AtlasConfig.load(args.config)
        report = inspect_installation(config, deep=args.deep)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    if args.command == "plan-refresh":
        config = AtlasConfig.load(args.config)
        try:
            result = plan_refresh(
                config.data_dir,
                config.repository,
                config.project,
                config.language,
            )
        except RefreshPlanError as exc:
            print(json.dumps({
                "schema_version": 1,
                "status": "blocked",
                "mode": "read_only",
                "repository": str(config.repository),
                "project": config.project,
                "reason": str(exc),
            }, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "migrate-provider":
        config = AtlasConfig.load(args.config)
        plan = plan_provider_migration(config)
        apply_command = (
            "codebase-atlas migrate-provider --config "
            f"{shlex.quote(str(args.config))} --mode {args.mode} --apply"
            if plan.status != "blocked" and plan.action != "already_active" else ""
        )
        if not args.apply or plan.status == "blocked" or plan.action == "already_active":
            payload = plan.as_dict() | {
                "mode": "read_only",
                "apply_command": apply_command,
                "note": "preview never creates, moves, adopts, or deletes an index",
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if plan.status != "blocked" else 2

        config_identity, config_bytes = _config_publication_snapshot(args.config)
        source_before = repository_snapshot(config.repository)
        candidate = shared_provider_config(config)
        root_created = False
        indexed = False
        config_published = False
        staged_registrations = None
        staged_manifest = None
        try:
            if plan.action in {"fresh_shared_index", "rebuild_into_shared"}:
                root_created = prepare_shared_provider_root(candidate.cache_dir)
                provider_result = _index_repository(candidate, args.mode)
                if str(provider_result.get("project", "")) != candidate.project:
                    raise RuntimeError("Provider returned a different shared project identity")
                indexed = True
            verified = plan_provider_migration(candidate)
            if verified.shared.get("status") != "healthy":
                raise RuntimeError(
                    "shared Provider database did not pass identity and quick-check validation"
                )
            source_after = repository_snapshot(config.repository)
            if (
                source_before.kind == "git"
                and source_after.kind == "git"
                and source_before.fingerprint != source_after.fingerprint
            ):
                raise RuntimeError(
                    "repository changed during Provider migration; legacy state was preserved"
                )
            if config.language == "python" and source_after.fingerprint:
                staged_registrations = stage_registration_index(
                    candidate.data_dir,
                    candidate.repository,
                    candidate.project,
                    source_after.fingerprint,
                )
            if source_after.kind == "git" and source_after.fingerprint:
                generation_id = secrets.token_hex(16)
                generation = build_generation_manifest(
                    candidate.repository,
                    candidate.project,
                    candidate.language,
                    generation_id=generation_id,
                    provider_identity=generation_artifact_identity(
                        candidate.cache_dir / f"{candidate.project}.db"
                    ),
                    sidecar_identity=(
                        generation_artifact_identity(staged_registrations.temporary)
                        if staged_registrations is not None
                        else {"status": "not_applicable"}
                    ),
                    created_at=f"generation:{generation_id}",
                )
                staged_manifest = stage_generation_manifest_candidate(
                    candidate.data_dir,
                    generation,
                    candidate.repository,
                    candidate.project,
                )
            if staged_registrations is not None:
                staged_registrations.publish()
            if staged_manifest is not None:
                staged_manifest.publish(manifest_path(candidate.data_dir))
            candidate.write_verified(args.config, config_identity)
            config_published = True
            record_index_state(
                candidate.data_dir,
                candidate.repository,
                candidate.project,
                args.mode,
                snapshot=source_after,
            )
            if staged_registrations is not None:
                staged_registrations.commit()
            if staged_manifest is not None:
                staged_manifest.commit()
        except BaseException as exc:
            if staged_manifest is not None:
                try:
                    staged_manifest.rollback()
                except OSError:
                    pass
            if staged_registrations is not None:
                try:
                    staged_registrations.rollback()
                except OSError:
                    pass
            if config_published:
                try:
                    AtlasConfig.restore_verified(args.config, config_identity, config_bytes)
                except (OSError, ValueError):
                    pass
            if isinstance(exc, KeyboardInterrupt):
                print(json.dumps({
                    "status": "interrupted",
                    "mode": "applied",
                    "legacy_preserved": True,
                    "shared_published": False,
                }, ensure_ascii=False, indent=2))
                return 130
            if not isinstance(exc, (OSError, RuntimeError, ValueError, RegistrationIndexError)):
                raise
            print(json.dumps({
                "status": "failed",
                "mode": "applied",
                "error": str(exc),
                "legacy_preserved": True,
                "shared_published": False,
                "root_created": root_created,
                "index_completed": indexed,
            }, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps({
            "status": "migrated",
            "mode": "applied",
            "repository": str(candidate.repository),
            "project": candidate.project,
            "shared_cache_dir": str(candidate.cache_dir),
            "legacy_cache_dir": str(candidate.legacy_cache_dir),
            "legacy_preserved": True,
            "root_created": root_created,
            "index_completed": indexed,
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "repair":
        config = AtlasConfig.load(args.config)
        before = inspect_installation(config)
        plan = repair_plan(before)
        if not args.apply or not plan["applicable"]:
            planned_status = (
                "planned" if plan["applicable"]
                else "blocked" if plan["action"] == "wait_and_retry"
                else "no_action"
            )
            print(json.dumps({
                "status": planned_status,
                "mode": "read_only",
                "inspection": before,
                "plan": plan,
                "apply_command": (
                    "codebase-atlas repair --config "
                    f"{shlex.quote(str(args.config))} --mode {args.mode} --apply"
                    if plan["applicable"] else ""
                ),
            }, ensure_ascii=False, indent=2))
            return 0 if before["ok"] else 2
        if plan["action"] == "registration_sidecar_rebuild":
            freshness = index_freshness(
                config.data_dir, config.repository, config.project
            )
            source_fingerprint = freshness.get("source_fingerprint")
            if freshness.get("status") != "fresh" or not source_fingerprint:
                raise RuntimeError(
                    "Python registration repair requires a fresh source index"
                )
            with stage_registration_index(
                config.data_dir,
                config.repository,
                config.project,
                source_fingerprint,
            ) as staged:
                staged.publish()
            after = inspect_installation(config)
            print(json.dumps({
                "status": "repaired" if after["ok"] else "repair_incomplete",
                "mode": "applied",
                "plan": plan,
                "project": config.project,
                "provider": {
                    "route": "atlas_source_current",
                    "status": "not_started",
                },
                "inspection": after,
            }, ensure_ascii=False, indent=2))
            return 0 if after["ok"] else 2
        if config.provider_layout == SHARED_PROVIDER_LAYOUT and config.project:
            result = _transactional_refresh(
                config,
                args.mode,
                force_provider=True,
            )
            after = inspect_installation(config)
            refreshed = result.get("status") == "refreshed"
            print(json.dumps({
                "status": "repaired" if refreshed and after["ok"] else "repair_incomplete",
                "mode": "applied",
                "plan": plan,
                "project": config.project,
                "generation": result,
                "inspection": after,
            }, ensure_ascii=False, indent=2))
            return 0 if refreshed and after["ok"] else 2
        config_identity, config_bytes = _config_publication_snapshot(args.config)
        source_before = repository_snapshot(config.repository)
        staged_registrations = None
        if config.language == "python" and config.project and source_before.fingerprint:
            previous_fingerprint = before.get("index", {}).get(
                "source_fingerprint"
            ) or before.get("index", {}).get("indexed_source_fingerprint")
            staged_registrations = stage_registration_index(
                config.data_dir,
                config.repository,
                config.project,
                source_before.fingerprint,
                previous_source_fingerprint=previous_fingerprint,
            )
        try:
            payload = _index_repository(config, args.mode)
        except RuntimeError as exc:
            if staged_registrations is not None:
                staged_registrations.close()
            print(json.dumps({
                "status": "failed",
                "mode": "applied",
                "plan": plan,
                "error": str(exc),
                "atlas_state_advanced": False,
                "provider_publication": "Provider staging/rollback boundary retained",
            }, ensure_ascii=False, indent=2))
            return 2
        source_after = repository_snapshot(config.repository)
        if (
            source_before.kind == "git"
            and source_after.kind == "git"
            and source_before.fingerprint != source_after.fingerprint
        ):
            if staged_registrations is not None:
                staged_registrations.close()
            print(json.dumps({
                "status": "failed",
                "mode": "applied",
                "plan": plan,
                "error": "repository changed while repairing; Atlas state was preserved; run repair again",
                "atlas_state_advanced": False,
            }, ensure_ascii=False, indent=2))
            return 2
        project = str(payload["project"])
        if config.language == "python" and source_after.fingerprint:
            if staged_registrations is None:
                staged_registrations = stage_registration_index(
                    config.data_dir,
                    config.repository,
                    project,
                    source_after.fingerprint,
                )
            elif project != config.project:
                staged_registrations.close()
                raise RuntimeError("Provider project identity changed during repair")
            staged_registrations.publish()
        try:
            config.with_project(project).write_verified(args.config, config_identity)
            state = record_index_state(
                config.data_dir, config.repository, project, args.mode, snapshot=source_after
            )
        except BaseException:
            if staged_registrations is not None:
                staged_registrations.rollback()
            try:
                AtlasConfig.restore_verified(
                    args.config, config_identity, config_bytes
                )
            except (OSError, ValueError):
                pass
            raise
        if staged_registrations is not None:
            staged_registrations.commit()
        after = inspect_installation(config.with_project(project))
        print(json.dumps({
            "status": "repaired" if after["ok"] else "repair_incomplete",
            "mode": "applied",
            "plan": plan,
            "project": project,
            "source_fingerprint": state.source_fingerprint,
            "provider": {
                "route": "provider_managed_staging",
                "status": payload.get("status"),
                "nodes": payload.get("nodes"),
                "edges": payload.get("edges"),
            },
            "inspection": after,
        }, ensure_ascii=False, indent=2))
        return 0 if after["ok"] else 2
    if args.command == "clean":
        config = AtlasConfig.load(args.config)
        plan = cleanup_plan(config)
        if args.apply and plan["refused"]:
            result = plan | {
                "status": "cleanup_blocked",
                "reason": "refused targets must be resolved before applying cleanup",
            }
        else:
            result = apply_cleanup(config, plan) if args.apply else plan
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result.get("refused") else 2
    if args.command in {"doctor", "index", "update"}:
        config = AtlasConfig.load(args.config)
        if args.command == "doctor":
            checks = diagnose(config)
            ok = required_checks_ok(checks)
            freshness = index_freshness(config.data_dir, config.repository, config.project)
            provider_database = provider_database_health(config.cache_dir, config.project)
            print(json.dumps({
                "status": "ready" if ok else "incomplete",
                "index": freshness,
                "provider_database": provider_database,
                "checks": checks,
            }, indent=2))
            return 0 if ok else 2
        config_identity, config_bytes = _config_publication_snapshot(args.config)
        if args.command == "update" and not args.force_provider:
            freshness = index_freshness(config.data_dir, config.repository, config.project)
            provider_database = provider_database_health(config.cache_dir, config.project)
            registration_health = registration_index_health(
                config.data_dir,
                config.repository,
                config.project,
                freshness.get("source_fingerprint"),
            ) if config.language == "python" else {"status": "not_applicable", "ok": True}
            source_and_provider_current = (
                freshness["status"] == "fresh"
                and freshness.get("mode") == args.mode
                and bool(provider_database["ok"])
            )
            if source_and_provider_current and config.provider_layout == SHARED_PROVIDER_LAYOUT:
                generation_plan = plan_refresh(
                    config.data_dir,
                    config.repository,
                    config.project,
                    config.language,
                )
                source_and_provider_current = (
                    generation_plan.get("status") == "planned"
                    and not generation_plan.get("dirty_paths")
                )
            if source_and_provider_current and not bool(registration_health["ok"]):
                source_fingerprint = freshness.get("source_fingerprint")
                if config.language == "python" and source_fingerprint:
                    with stage_registration_index(
                        config.data_dir,
                        config.repository,
                        config.project,
                        source_fingerprint,
                    ) as staged:
                        staged.publish()
                    registration_health = registration_index_health(
                        config.data_dir,
                        config.repository,
                        config.project,
                        source_fingerprint,
                    )
                    print(json.dumps({
                        "status": "current",
                        "project": config.project,
                        "config": str(args.config),
                        "index_state": str(config.data_dir / "index-state.json"),
                        "source_fingerprint": source_fingerprint,
                        "provider": {
                            "route": "atlas_source_current",
                            "status": "not_started",
                            "database": provider_database,
                        },
                        "python_registrations": registration_health | {
                            "action": "rebuilt"
                        },
                    }, indent=2))
                    return 0
            if (
                source_and_provider_current
                and bool(registration_health["ok"])
            ):
                print(json.dumps({
                    "status": "current",
                    "project": config.project,
                    "config": str(args.config),
                    "index_state": str(config.data_dir / "index-state.json"),
                    "source_fingerprint": freshness.get("source_fingerprint"),
                    "provider": {
                        "route": "atlas_source_current",
                        "status": "not_started",
                        "database": provider_database,
                    },
                    "python_registrations": registration_health,
                }, indent=2))
                return 0
        if config.provider_layout == SHARED_PROVIDER_LAYOUT and config.project:
            result = _transactional_refresh(
                config,
                args.mode,
                force_provider=(args.command == "index" or args.force_provider),
            )
            if result.get("status") == "refreshed":
                result["status"] = "indexed" if args.command == "index" else "updated"
            result.update({
                "project": config.project,
                "config": str(args.config),
                "index_state": str(config.data_dir / "index-state.json"),
            })
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("status") in {"current", "indexed", "updated"} else 2
        source_before = repository_snapshot(config.repository)
        staged_registrations = None
        if (
            config.language == "python"
            and config.project
            and source_before.fingerprint
        ):
            prior_freshness = (
                index_freshness(
                    config.data_dir, config.repository, config.project
                )
                if args.command == "update"
                else {}
            )
            staged_registrations = stage_registration_index(
                config.data_dir,
                config.repository,
                config.project,
                source_before.fingerprint,
                previous_source_fingerprint=(
                    prior_freshness.get("source_fingerprint")
                    or prior_freshness.get("indexed_source_fingerprint")
                ),
            )
        try:
            with _graceful_termination():
                payload = _index_repository(config, args.mode)
        except KeyboardInterrupt:
            if staged_registrations is not None:
                staged_registrations.close()
            print(json.dumps({
                "status": "failed",
                "error": "index update interrupted; the previous Atlas state was preserved",
                "atlas_state_advanced": False,
            }, indent=2))
            return 130
        except BaseException:
            if staged_registrations is not None:
                staged_registrations.close()
            raise
        source_after = repository_snapshot(config.repository)
        if (
            source_before.kind == "git"
            and source_after.kind == "git"
            and source_before.fingerprint != source_after.fingerprint
        ):
            if staged_registrations is not None:
                staged_registrations.close()
            raise RuntimeError(
                "repository changed while indexing; the previous Atlas state was preserved; run update again"
            )
        project = str(payload["project"])
        if config.language == "python" and source_after.fingerprint:
            if staged_registrations is None:
                staged_registrations = stage_registration_index(
                    config.data_dir,
                    config.repository,
                    project,
                    source_after.fingerprint,
                )
            elif project != config.project:
                staged_registrations.close()
                raise RuntimeError(
                    "Provider project identity changed; the previous Atlas state was preserved"
                )
            staged_registrations.publish()
        try:
            config.with_project(project).write_verified(args.config, config_identity)
            state = record_index_state(
                config.data_dir,
                config.repository,
                project,
                args.mode,
                snapshot=source_after,
            )
        except BaseException:
            if staged_registrations is not None:
                staged_registrations.rollback()
            try:
                AtlasConfig.restore_verified(
                    args.config, config_identity, config_bytes
                )
            except (OSError, ValueError):
                pass
            raise
        if staged_registrations is not None:
            staged_registrations.commit()
        print(json.dumps({
            "status": "indexed" if args.command == "index" else "updated",
            "project": project,
            "config": str(args.config),
            "index_state": str(config.data_dir / "index-state.json"),
            "source_fingerprint": state.source_fingerprint,
            "provider": {
                "route": "provider_managed",
                "status": payload.get("status"),
                "nodes": payload.get("nodes"),
                "edges": payload.get("edges"),
            },
            "python_registrations": (
                registration_index_health(
                    config.data_dir,
                    config.repository,
                    project,
                    source_after.fingerprint,
                ) if config.language == "python" else {
                    "status": "not_applicable", "ok": True
                }
            ),
        }, indent=2))
        return 0
    if args.command == "related-tests":
        provider = TypeScriptTestProvider(args.node, args.analyzer, args.tsconfig)
        results = provider.related_tests(
            args.repo,
            args.symbol,
            target_path=args.target_path,
            target_owner=args.target_owner,
        )
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": provider.name,
                    "results": [
                        {"node": asdict(node), "edge": asdict(edge)}
                        for node, edge in results
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "impact":
        provider = CodebaseMemoryImpactProvider(
            args.binary,
            args.repo,
            args.cache_dir,
            args.project,
        )
        lifecycle = _provider_lifecycle(
            args.binary, args.repo, args.cache_dir,
            getattr(args, "provider_layout", "legacy-project-v0"),
        )
        with lifecycle:
            hits = provider.impact(
                args.symbol,
                direction=args.direction,
                max_depth=args.depth,
                target_path=args.target_path,
                target_owner=args.target_owner,
                max_nodes=args.max_nodes,
                max_edges=args.max_edges,
                timeout_ms=args.timeout_ms,
            )
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": provider.name,
                    "results": [
                        {
                            "node": asdict(hit.node),
                            "depth": hit.depth,
                            "path": [asdict(edge) for edge in hit.path],
                        }
                        for hit in hits
                    ],
                    "truncated": hits.truncated,
                    "truncation": {
                        "reasons": hits.reasons,
                        "observed": {
                            "nodes": hits.examined_nodes,
                            "edges": hits.examined_edges,
                        },
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command in {"mcp", "query", "query-batch", "ui", "analyze-change"}:
        active_config_path = args.config
        if active_config_path is None:
            local_config = (args.repo or Path.cwd()) / CONFIG_NAME
            active_config_path = local_config if local_config.is_file() else None
        auto_update_status = disabled_session_update()
        if args.command == "mcp" and args.auto_update != "off":
            if args.auto_update_timeout <= 0 or args.auto_update_timeout > 300:
                raise SystemExit("--auto-update-timeout must be between 0 and 300 seconds")
        _apply_project_config(args)
        lifecycle_status = operational_lifecycle_status(
            args.data_dir, args.repo, args.project
        )
        if not lifecycle_status["ok"] and args.command != "mcp":
            print(json.dumps({
                "schema_version": 1,
                "status": "error",
                "code": lifecycle_status["status"],
                "message": "Codebase Atlas is not enabled for this project.",
                "project": lifecycle_status,
            }, ensure_ascii=False, indent=2))
            return 4
        if (
            args.command == "mcp"
            and lifecycle_status["ok"]
            and args.auto_update == "session-start"
        ):
            selected_config = args.config or Path.cwd() / CONFIG_NAME
            auto_update_status = session_start_update(
                selected_config, timeout_seconds=args.auto_update_timeout
            )
        if args.command == "mcp":
            args.index_status["identity"] = {
                "repository": str(args.repo.resolve()),
                "project": args.project,
                "config": str((args.config or args.repo / CONFIG_NAME).resolve()),
            }
            args.index_status["auto_update"] = auto_update_status
        if args.command in {"query", "analyze-change"}:
            policy_error = stale_policy_error(args.index_status, args.stale_policy)
            if policy_error:
                print(json.dumps(attach_operational_status(
                    {"status": "error", "message": policy_error},
                    args.index_status,
                    args.stale_policy,
                ), ensure_ascii=False, indent=2))
                return 3
        provider_layout = getattr(args, "provider_layout", "legacy-project-v0")
        transport = (
            CodebaseMemoryMcpTransport(
                args.binary, args.repo, args.cache_dir,
                exclusive=provider_layout != SHARED_PROVIDER_LAYOUT,
                client_version=__version__,
                managed_cache=provider_layout == SHARED_PROVIDER_LAYOUT,
            )
            if args.command == "mcp"
            else None
        )
        lifecycle = transport or _provider_lifecycle(
            args.binary, args.repo, args.cache_dir, provider_layout,
        )
        structural = CodebaseMemoryImpactProvider(
            args.binary,
            args.repo,
            args.cache_dir,
            args.project,
            transport=transport,
        )
        registration_index = None
        if args.language == "python" and getattr(args, "data_dir", None) is not None:
            source_fingerprint = args.index_status.get("source", {}).get(
                "source_fingerprint"
            )
            if source_fingerprint:
                try:
                    registration_index, registration_health = load_registration_index_state(
                        args.data_dir,
                        args.repo,
                        args.project,
                        source_fingerprint,
                    )
                except RegistrationIndexError as exc:
                    registration_index = None
                    registration_health = {
                        "status": "rebuild_required",
                        "ok": False,
                        "reason": str(exc),
                    }
            else:
                registration_health = registration_index_health(
                    args.data_dir, args.repo, args.project, source_fingerprint
                )
            args.index_status["python_registrations"] = registration_health
        service = AtlasService(
            repository=args.repo,
            structural_provider=structural,
            semantic_provider=SerenaSemanticProvider(
                args.serena_python,
                args.serena_runner,
                args.repo,
                args.serena_home,
                args.metadata_root,
                language=args.language,
                node_bin_dir=args.node_bin_dir,
            ),
            test_provider=TypeScriptTestProvider(args.node, args.analyzer, args.tsconfig),
            impact_provider=structural,
            lifecycle=lifecycle,
            registration_index=registration_index,
            session_continuations=args.command in {"mcp", "query-batch", "ui"},
        )
        refresh_coordinator = (
            RefreshCoordinator(
                AtlasConfig.load(active_config_path),
                transport,
                service,
                args.index_status,
            )
            if args.command == "mcp" and transport is not None and active_config_path is not None
            else None
        )
        with service:
            if args.command == "mcp":
                notifier = VersionNotifier(
                    __version__, args.data_dir,
                    enabled=args.version_check == "notify",
                )
                _run_mcp_with_graceful_termination(
                    McpServer(
                        service, args.index_status, args.stale_policy,
                        instructions=(
                            f"This server is only for repository {args.repo.resolve()}; "
                            f"never use it for another repository. {PROJECT_RULE}"
                        ),
                        version_notifier=notifier,
                        refresh_coordinator=refresh_coordinator,
                        auto_update=args.auto_update,
                        auto_update_timeout_ms=int(args.auto_update_timeout * 1000),
                        availability=lambda: operational_lifecycle_status(
                            args.data_dir, args.repo, args.project
                        ),
                    )
                )
            elif args.command == "query":
                response = service.query(
                    QueryRequest(
                        args.query_type,
                        args.symbol,
                        {
                            "target_path": args.target_path,
                            "target_owner": args.target_owner,
                            "relation": args.relation,
                            "direction": args.direction,
                            "depth": args.depth,
                            "max_nodes": args.max_nodes,
                            "max_edges": args.max_edges,
                            "timeout_ms": args.timeout_ms,
                        },
                    )
                )
                print(json.dumps(
                    _response_payload(response, args.index_status, args.stale_policy),
                    ensure_ascii=False,
                    indent=2,
                ))
            elif args.command == "analyze-change":
                print(json.dumps(
                    analyze_change(
                        service,
                        args.symbol,
                        intent=args.intent,
                        target_path=args.target_path,
                        target_owner=args.target_owner,
                        direction=args.direction,
                        depth=args.depth,
                        max_nodes=args.max_nodes,
                        max_edges=args.max_edges,
                        timeout_ms=args.timeout_ms,
                        index_status=args.index_status,
                        stale_policy=args.stale_policy,
                        response_mode=args.response_mode,
                    ),
                    ensure_ascii=False,
                    indent=2,
                ))
            elif args.command == "query-batch":
                _run_query_batch(service, args.index_status, args.stale_policy)
            else:
                if not 0 <= args.port <= 65535:
                    raise SystemExit("port must be between 0 and 65535")
                server = LocalUiServer(
                    service,
                    repository=str(args.repo),
                    language=args.language,
                    index_status=args.index_status,
                    stale_policy=args.stale_policy,
                    port=args.port,
                )
                print(json.dumps({
                    "status": "ready", "url": server.url,
                    "binding": server.authority, "mode": "read_only",
                }), flush=True)
                if not args.no_open:
                    webbrowser.open(server.url)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    server.httpd.server_close()
        return 0
    parser.print_help()
    return 0


def _response_payload(response, index_status=None, stale_policy: str = "ignore") -> dict:
    return attach_operational_status({
        "schema_version": 1,
        "query_type": response.query_type,
        "nodes": [asdict(node) for node in response.nodes],
        "edges": [asdict(edge) for edge in response.edges],
        "depths": response.depths,
        "paths": {
            node_id: [asdict(edge) for edge in path]
            for node_id, path in response.paths.items()
        },
        "truncated": response.truncated,
        "truncation": response.truncation,
    }, index_status, stale_policy)


def _config_publication_snapshot(path: Path) -> tuple[tuple[int, int], bytes]:
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("Atlas config must remain a regular file")
    identity = (metadata.st_dev, metadata.st_ino)
    payload = path.read_bytes()
    current = os.lstat(path)
    if (
        not stat.S_ISREG(current.st_mode)
        or (current.st_dev, current.st_ino) != identity
    ):
        raise RuntimeError("Atlas config changed before publication")
    return identity, payload


def _apply_project_config(args) -> None:
    candidate = args.config
    if candidate is None:
        base = args.repo if args.repo is not None else Path.cwd()
        local = base / CONFIG_NAME
        candidate = local if local.is_file() else None
    if candidate is not None:
        config = AtlasConfig.load(candidate)
        args.repo = config.repository
        args.node = config.node
        args.analyzer = config.analyzer
        args.binary = config.cbm_binary
        args.cache_dir = config.cache_dir
        args.project = config.project
        args.serena_python = config.serena_python
        args.serena_runner = config.serena_runner
        args.serena_home = config.serena_home
        args.metadata_root = config.metadata_root
        args.language = config.language
        args.node_bin_dir = config.node_bin_dir
        args.tsconfig = config.tsconfig
        args.index_status = operational_index_status(
            config.data_dir,
            config.repository,
            config.cache_dir,
            config.project,
        )
        args.data_dir = config.data_dir
        args.provider_layout = config.provider_layout
    else:
        args.index_status = unknown_operational_status()
        args.data_dir = None
        args.provider_layout = "legacy-project-v0"
    required = {
        "repo": args.repo, "node": args.node, "binary": args.binary,
        "cache_dir": args.cache_dir, "project": args.project,
        "serena_python": args.serena_python, "serena_home": args.serena_home,
        "metadata_root": args.metadata_root, "language": args.language,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(
            "missing runtime configuration: " + ", ".join(missing)
            + f"; run 'codebase-atlas init' and 'codebase-atlas index'"
        )


def _index_repository(config: AtlasConfig, mode: str) -> dict[str, object]:
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    environment = provider_environment(config.cache_dir, config.repository)
    # Own the daemon when indexing starts it, so a one-shot index/repair does
    # not leave a background Provider behind. A pre-existing daemon remains
    # unowned and is deliberately not stopped.
    with _provider_lifecycle(
        config.cbm_binary, config.repository, config.cache_dir, config.provider_layout
    ):
        command = [
            str(config.cbm_binary), "cli", "--json", "index_repository",
            "--repo-path", str(config.repository), "--mode", mode,
        ]
        if config.project:
            command.extend(["--name", config.project])
        completed = run_provider_command(command, env=environment)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Codebase Memory indexing failed")
    envelope = json.loads(completed.stdout)
    payload = envelope.get("structuredContent", {})
    project = payload.get("project")
    if envelope.get("isError") or not isinstance(project, str) or not project:
        raise RuntimeError(str(payload.get("error", "index result lacks project")))
    if payload.get("status") != "indexed":
        raise RuntimeError(str(payload.get("hint", f"index status is {payload.get('status', 'unknown')}")))
    return payload


def _transactional_refresh(
    config: AtlasConfig,
    mode: str,
    *,
    force_provider: bool = False,
    timeout_ms: int = 300_000,
) -> dict[str, object]:
    """Run one shared-layout CLI refresh through the MCP generation transaction."""
    status = operational_index_status(
        config.data_dir,
        config.repository,
        config.cache_dir,
        config.project,
    )
    transport = CodebaseMemoryMcpTransport(
        config.cbm_binary,
        config.repository,
        config.cache_dir,
        exclusive=False,
        client_version=__version__,
        managed_cache=True,
    )
    service = AtlasService(repository=config.repository, lifecycle=transport)
    coordinator = RefreshCoordinator(config, transport, service, status)
    with service:
        return refresh_with_retry(
            coordinator,
            mode=mode,
            timeout_ms=timeout_ms,
            force_provider=force_provider,
        )


def _provider_lifecycle(
    binary: Path, repository: Path, cache_dir: Path, provider_layout: str
):
    lifecycle = (
        SharedCodebaseMemorySession
        if provider_layout == SHARED_PROVIDER_LAYOUT
        else CodebaseMemoryDaemon
    )
    return lifecycle(binary, repository, cache_dir)


def _run_query_batch(
    service: AtlasService,
    index_status=None,
    stale_policy: str = "ignore",
) -> None:
    print(json.dumps(attach_operational_status(
        {"status": "ready"}, index_status, stale_policy
    )), flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        started = time.perf_counter()
        try:
            value = json.loads(line)
            if value.get("command") == "shutdown":
                print(json.dumps({"status": "closed"}), flush=True)
                return
            policy_error = stale_policy_error(index_status or {}, stale_policy)
            if policy_error:
                raise RuntimeError(policy_error)
            request = QueryRequest(
                value["query_type"], value["symbol"], dict(value.get("parameters", {}))
            )
            payload = _response_payload(
                service.query(request), index_status, stale_policy
            )
            payload.update(
                status="ok", duration_ms=(time.perf_counter() - started) * 1000.0
            )
        except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            payload = {
                "status": "error",
                "message": str(exc),
                "duration_ms": (time.perf_counter() - started) * 1000.0,
            }
            attach_operational_status(payload, index_status, stale_policy)
        print(json.dumps(payload, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
