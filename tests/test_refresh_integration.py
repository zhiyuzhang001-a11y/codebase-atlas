from __future__ import annotations

import os
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas import __version__
from codebase_atlas.config import AtlasConfig, SHARED_PROVIDER_LAYOUT
from codebase_atlas.provider_layout import provider_project_identity
from codebase_atlas.operations import operational_index_status
from codebase_atlas.provider_transport import CodebaseMemoryMcpTransport
from codebase_atlas.providers.cbm_impact import CodebaseMemoryImpactProvider
from codebase_atlas.refresh_coordinator import RefreshCoordinator
from codebase_atlas.index_state import state_path
from codebase_atlas.python_registration_store import registration_index_path
from codebase_atlas.refresh_planner import manifest_path
from codebase_atlas.service import AtlasService, QueryRequest


def git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args], check=True, capture_output=True
    )


@unittest.skipUnless(
    os.environ.get("ATLAS_M38_PROVIDER_BINARY"),
    "set ATLAS_M38_PROVIDER_BINARY for the isolated managed-Provider proof",
)
class ManagedProviderRefreshIntegrationTests(unittest.TestCase):
    def test_two_projects_refresh_without_foreign_facts_or_lifecycle_coupling(self) -> None:
        binary = Path(os.environ["ATLAS_M38_PROVIDER_BINARY"])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            runtime_temporary = tempfile.TemporaryDirectory(
                prefix="m38-stage3-two-projects.", dir="/private/tmp"
            )
            self.addCleanup(runtime_temporary.cleanup)
            runtime = Path(runtime_temporary.name)
            subprocess.run(["chmod", "-N", str(runtime)], check=True)
            runtime.chmod(0o700)
            environment = patch.dict(os.environ, {
                "CBM_RUNTIME_DIR": str(runtime.resolve()),
                "XDG_DATA_HOME": str((root / "xdg-data").resolve()),
            })
            environment.start()
            self.addCleanup(environment.stop)
            services = []
            coordinators = []
            transports = []
            symbols = ("project_alpha_symbol", "project_beta_symbol")
            for index, symbol in enumerate(symbols):
                repository = root / f"repo-{index}"
                repository.mkdir()
                git(repository, "init", "-q")
                git(repository, "config", "user.email", "atlas@example.invalid")
                git(repository, "config", "user.name", "Atlas Test")
                (repository / "sample.py").write_text(
                    f"def {symbol}():\n    return {index}\n"
                )
                git(repository, "add", "sample.py")
                git(repository, "commit", "-qm", "initial")
                for name in (f"node-{index}", f"serena-{index}"):
                    (root / name).touch()
                project = provider_project_identity(repository)
                config = AtlasConfig(
                    repository, "python", root / f"node-{index}", binary,
                    root / f"serena-{index}", root / f"data-{index}", project,
                    provider_layout=SHARED_PROVIDER_LAYOUT,
                )
                status = operational_index_status(
                    config.data_dir, config.repository, config.cache_dir, config.project
                )
                status["identity"] = {
                    "repository": str(repository.resolve()), "project": project
                }
                transport = CodebaseMemoryMcpTransport(
                    binary, repository, config.cache_dir,
                    exclusive=False, client_version=__version__,
                )
                structural = CodebaseMemoryImpactProvider(
                    binary, repository, config.cache_dir, project, transport=transport
                )
                service = AtlasService(
                    repository=repository, structural_provider=structural,
                    impact_provider=structural, lifecycle=transport,
                    session_continuations=True,
                )
                service.start()
                services.append(service)
                transports.append(transport)
                coordinators.append(RefreshCoordinator(config, transport, service, status))
            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(
                        lambda coordinator: coordinator.refresh(timeout_ms=300_000),
                        coordinators,
                    ))
                self.assertEqual([item["status"] for item in results], ["refreshed", "refreshed"])
                for owner, foreign, service in (
                    (symbols[0], symbols[1], services[0]),
                    (symbols[1], symbols[0], services[1]),
                ):
                    own = service.query(QueryRequest(
                        "definition", owner, {"timeout_ms": 30_000}
                    ))
                    absent = service.query(QueryRequest(
                        "definition", foreign, {"timeout_ms": 30_000}
                    ))
                    self.assertEqual([node.name for node in own.nodes], [owner])
                    self.assertEqual(absent.nodes, ())
                first_pid = transports[0].process.pid
                second_pid = transports[1].process.pid
                services[0].close()
                still_alive = services[1].query(QueryRequest(
                    "definition", symbols[1], {"timeout_ms": 30_000}
                ))
                self.assertEqual([node.name for node in still_alive.nodes], [symbols[1]])
                self.assertIsNotNone(transports[1].process)
                self.assertEqual(transports[1].process.pid, second_pid)
                print(json.dumps({
                    "status": "two_project_isolation_passed",
                    "project_a_pid": first_pid,
                    "project_b_pid": second_pid,
                    "refresh_durations_ms": [item["duration_ms"] for item in results],
                    "foreign_fact_counts": [0, 0],
                    "second_alive_after_first_close": True,
                }, sort_keys=True))
            finally:
                for service in services:
                    service.close()

    def test_post_provider_state_failure_restores_queryable_old_generation(self) -> None:
        binary = Path(os.environ["ATLAS_M38_PROVIDER_BINARY"])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            runtime_temporary = tempfile.TemporaryDirectory(
                prefix="m38-stage3-runtime.", dir="/private/tmp"
            )
            self.addCleanup(runtime_temporary.cleanup)
            runtime = Path(runtime_temporary.name)
            subprocess.run(["chmod", "-N", str(runtime)], check=True)
            runtime.chmod(0o700)
            environment = patch.dict(
                os.environ, {"CBM_RUNTIME_DIR": str(runtime.resolve())}
            )
            environment.start()
            self.addCleanup(environment.stop)
            repository = root / "repo"
            repository.mkdir()
            git(repository, "init", "-q")
            git(repository, "config", "user.email", "atlas@example.invalid")
            git(repository, "config", "user.name", "Atlas Test")
            source = repository / "sample.py"
            source.write_text("def old_generation_symbol():\n    return 1\n")
            git(repository, "add", "sample.py")
            git(repository, "commit", "-qm", "initial")
            for name in ("node", "serena"):
                (root / name).touch()
            config = AtlasConfig(
                repository, "python", root / "node", binary, root / "serena",
                root / "data", "m38-stage3-rollback-fixture",
            )
            status = operational_index_status(
                config.data_dir, config.repository, config.cache_dir, config.project
            )
            status["identity"] = {
                "repository": str(repository.resolve()), "project": config.project
            }
            transport = CodebaseMemoryMcpTransport(
                binary, repository, config.cache_dir,
                exclusive=False, client_version=__version__,
            )
            structural = CodebaseMemoryImpactProvider(
                binary, repository, config.cache_dir, config.project, transport=transport
            )
            service = AtlasService(
                repository=repository, structural_provider=structural,
                impact_provider=structural, lifecycle=transport,
                session_continuations=True,
            )
            coordinator = RefreshCoordinator(config, transport, service, status)
            with service:
                baseline = coordinator.refresh(timeout_ms=300_000)
                self.assertEqual(baseline["status"], "refreshed", baseline)
                child_pid = transport.process.pid
                paths = (
                    config.cache_dir / f"{config.project}.db",
                    registration_index_path(config.data_dir),
                    manifest_path(config.data_dir),
                    state_path(config.data_dir),
                )
                before = {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in paths
                }
                source.write_text("def rejected_generation_symbol():\n    return 2\n")
                with patch(
                    "codebase_atlas.refresh_coordinator.record_index_state",
                    side_effect=OSError("injected state failure"),
                ):
                    failed = coordinator.refresh(timeout_ms=300_000)
                self.assertEqual(failed["status"], "failed", failed)
                self.assertTrue(failed["previous_generation_preserved"], failed)
                self.assertEqual(
                    {
                        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in paths
                    },
                    before,
                )
                old = service.query(QueryRequest(
                    "definition", "old_generation_symbol", {"timeout_ms": 30_000}
                ))
                rejected = service.query(QueryRequest(
                    "definition", "rejected_generation_symbol", {"timeout_ms": 30_000}
                ))
                self.assertEqual([node.name for node in old.nodes], ["old_generation_symbol"])
                self.assertEqual(rejected.nodes, ())
                self.assertEqual(transport.process.pid, child_pid)
                self.assertEqual(status["status"], "stale")
                print(json.dumps({
                    "status": "rollback_passed",
                    "same_child_pid": child_pid,
                    "failure_duration_ms": failed["duration_ms"],
                    "old_nodes": [node.name for node in old.nodes],
                    "rejected_nodes": len(rejected.nodes),
                    "hashes_restored": True,
                }, sort_keys=True))

    def test_same_child_baseline_mutation_refresh_and_query(self) -> None:
        binary = Path(os.environ["ATLAS_M38_PROVIDER_BINARY"])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            runtime_temporary = tempfile.TemporaryDirectory(
                prefix="m38-stage2-runtime.", dir="/private/tmp"
            )
            self.addCleanup(runtime_temporary.cleanup)
            runtime = Path(runtime_temporary.name)
            subprocess.run(["chmod", "-N", str(runtime)], check=True)
            runtime.chmod(0o700)
            environment = patch.dict(
                os.environ, {"CBM_RUNTIME_DIR": str(runtime.resolve())}
            )
            environment.start()
            self.addCleanup(environment.stop)
            repository = root / "repo"
            repository.mkdir()
            git(repository, "init", "-q")
            git(repository, "config", "user.email", "atlas@example.invalid")
            git(repository, "config", "user.name", "Atlas Test")
            source = repository / "sample.py"
            source.write_text("def baseline_stage2():\n    return 1\n")
            git(repository, "add", "sample.py")
            git(repository, "commit", "-qm", "initial")
            for name in ("node", "serena"):
                (root / name).touch()
            config = AtlasConfig(
                repository,
                "python",
                root / "node",
                binary,
                root / "serena",
                root / "data",
                "m38-stage2-fixture",
            )
            status = operational_index_status(
                config.data_dir, config.repository, config.cache_dir, config.project
            )
            status["identity"] = {
                "repository": str(repository.resolve()), "project": config.project
            }
            transport = CodebaseMemoryMcpTransport(
                binary,
                repository,
                config.cache_dir,
                exclusive=False,
                client_version=__version__,
            )
            structural = CodebaseMemoryImpactProvider(
                binary,
                repository,
                config.cache_dir,
                config.project,
                transport=transport,
            )
            service = AtlasService(
                repository=repository,
                structural_provider=structural,
                impact_provider=structural,
                lifecycle=transport,
                session_continuations=True,
            )
            coordinator = RefreshCoordinator(config, transport, service, status)
            with service:
                baseline = coordinator.refresh(timeout_ms=300_000)
                self.assertEqual(baseline["status"], "refreshed", baseline)
                self.assertIsNotNone(transport.process)
                child_pid = transport.process.pid

                source.write_text(
                    "def baseline_stage2():\n    return 1\n\n"
                    "def added_stage2():\n    return baseline_stage2()\n"
                )
                refreshed = coordinator.refresh(timeout_ms=300_000)
                self.assertEqual(refreshed["status"], "refreshed", refreshed)
                self.assertEqual(refreshed["dirty_paths"], ["sample.py"])
                self.assertIsNotNone(transport.process)
                self.assertEqual(transport.process.pid, child_pid)
                self.assertLess(refreshed["duration_ms"], 6305.0)

                response = service.query(QueryRequest(
                    "definition", "added_stage2", {"timeout_ms": 30_000}
                ))
                self.assertEqual(
                    [(node.name, node.location.path) for node in response.nodes],
                    [("added_stage2", "sample.py")],
                )
                self.assertEqual(status["status"], "fresh")
                self.assertEqual(
                    status["generation_id"], refreshed["generation_after"]
                )

                oracle_requests = (
                    QueryRequest("definition", "added_stage2", {"timeout_ms": 30_000}),
                    QueryRequest("callers", "baseline_stage2", {"timeout_ms": 30_000}),
                    QueryRequest("callees", "added_stage2", {"timeout_ms": 30_000}),
                )

                def normalized(request):
                    result = service.query(request)
                    return {
                        "nodes": [
                            (
                                node.id, node.kind, node.name, node.location.path,
                                node.location.start_line, node.location.end_line,
                                node.provider, node.evidence_hash,
                            )
                            for node in result.nodes
                        ],
                        "edges": [
                            (edge.source_id, edge.target_id, edge.relation, edge.evidence_hash)
                            for edge in result.edges
                        ],
                    }

                incremental_oracle = [normalized(request) for request in oracle_requests]
                clean = transport.call(
                    "index_repository",
                    {
                        "repo_path": str(repository.resolve()),
                        "name": config.project,
                        "mode": "full",
                    },
                    timeout_ms=300_000,
                )
                self.assertEqual(clean["status"], "indexed", clean)
                service.activate_generation(service.registration_index)
                clean_oracle = [normalized(request) for request in oracle_requests]
                self.assertEqual(incremental_oracle, clean_oracle)

                renamed_source = repository / "renamed.py"
                source.rename(renamed_source)
                renamed = coordinator.refresh(timeout_ms=300_000)
                self.assertEqual(renamed["status"], "refreshed", renamed)
                self.assertEqual(
                    renamed["dirty_paths"], ["renamed.py", "sample.py"]
                )
                self.assertLess(renamed["duration_ms"], 6305.0)
                renamed_query = service.query(QueryRequest(
                    "definition", "added_stage2", {"timeout_ms": 30_000}
                ))
                self.assertEqual(
                    [node.location.path for node in renamed_query.nodes],
                    ["renamed.py"],
                )

                renamed_source.unlink()
                deleted = coordinator.refresh(timeout_ms=300_000)
                self.assertEqual(deleted["status"], "refreshed", deleted)
                self.assertEqual(deleted["dirty_paths"], ["renamed.py"])
                self.assertLess(deleted["duration_ms"], 6305.0)
                deleted_query = service.query(QueryRequest(
                    "definition", "added_stage2", {"timeout_ms": 30_000}
                ))
                self.assertEqual(deleted_query.nodes, ())
                self.assertIsNotNone(transport.process)
                self.assertEqual(transport.process.pid, child_pid)
                print(json.dumps({
                    "status": "passed",
                    "same_child_pid": child_pid,
                    "baseline_duration_ms": baseline["duration_ms"],
                    "mutation_duration_ms": refreshed["duration_ms"],
                    "rename_duration_ms": renamed["duration_ms"],
                    "delete_duration_ms": deleted["duration_ms"],
                    "mutation_dirty_paths": refreshed["dirty_paths"],
                    "query_nodes": [node.name for node in response.nodes],
                    "incremental_clean_oracle_equal": True,
                    "generation_after": refreshed["generation_after"],
                }, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
