from __future__ import annotations

import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
from time import perf_counter
import unittest
from unittest.mock import patch

from codebase_atlas import __version__
from codebase_atlas.config import AtlasConfig
from codebase_atlas.operations import operational_index_status
from codebase_atlas.provider_transport import CodebaseMemoryMcpTransport
from codebase_atlas.providers.cbm_impact import CodebaseMemoryImpactProvider
from codebase_atlas.refresh_coordinator import RefreshCoordinator
from codebase_atlas.service import AtlasService, QueryRequest


def git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args], check=True, capture_output=True
    )


@unittest.skipUnless(
    os.environ.get("ATLAS_M38_PROVIDER_BINARY"),
    "set ATLAS_M38_PROVIDER_BINARY for the isolated Stage 5 proof",
)
class ExplicitRefreshPerformanceIntegrationTests(unittest.TestCase):
    def test_noop_small_batches_warm_queries_and_long_task_replay(self) -> None:
        binary = Path(os.environ["ATLAS_M38_PROVIDER_BINARY"])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            runtime_temporary = tempfile.TemporaryDirectory(
                prefix="m38-stage5-runtime.", dir="/private/tmp"
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
            seed = repository / "seed.py"
            seed.write_text("def seed_symbol():\n    return 0\n")
            git(repository, "add", "seed.py")
            git(repository, "commit", "-qm", "initial")
            for name in ("node", "serena"):
                (root / name).touch()

            config = AtlasConfig(
                repository, "python", root / "node", binary, root / "serena",
                root / "data", "m38-stage5-explicit-fixture",
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
                binary, repository, config.cache_dir, config.project,
                transport=transport,
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

                noop = coordinator.refresh(timeout_ms=300_000)
                self.assertEqual(noop["status"], "current", noop)
                self.assertFalse(noop["provider_called"], noop)
                self.assertLess(noop["duration_ms"], 250.0, noop)

                seed.write_text("def seed_symbol():\n    return 1\n")
                one = coordinator.refresh(timeout_ms=300_000)
                self.assertEqual(one["dirty_paths"], ["seed.py"])

                for index in range(5):
                    (repository / f"batch_{index}.py").write_text(
                        f"def batch_symbol_{index}():\n    return {index}\n"
                    )
                five = coordinator.refresh(timeout_ms=300_000)
                self.assertEqual(len(five["dirty_paths"]), 5, five)

                for index in range(5):
                    (repository / f"batch_{index}.py").write_text(
                        f"def batch_symbol_{index}():\n    return {index + 10}\n"
                    )
                for index in range(5, 10):
                    (repository / f"batch_{index}.py").write_text(
                        f"def batch_symbol_{index}():\n    return {index}\n"
                    )
                ten = coordinator.refresh(timeout_ms=300_000)
                self.assertEqual(len(ten["dirty_paths"]), 10, ten)
                for result in (one, five, ten):
                    self.assertEqual(result["status"], "refreshed", result)
                    self.assertLess(result["duration_ms"], 6305.0, result)
                    self.assertEqual(transport.process.pid, child_pid)

                request = QueryRequest(
                    "definition", "batch_symbol_9", {"timeout_ms": 30_000}
                )
                samples = []
                for _ in range(50):
                    started = perf_counter()
                    with coordinator.query_snapshot(timeout_ms=2000):
                        response = service.query(request)
                    samples.append((perf_counter() - started) * 1000.0)
                    self.assertEqual([node.name for node in response.nodes], ["batch_symbol_9"])
                p95 = sorted(samples)[int(len(samples) * 0.95) - 1]
                self.assertLessEqual(p95, 2.022, samples)

                def normalized_batch_facts():
                    facts = []
                    for index in range(10):
                        result = service.query(QueryRequest(
                            "definition", f"batch_symbol_{index}",
                            {"timeout_ms": 30_000},
                        ))
                        facts.append([
                            (
                                node.id, node.kind, node.name,
                                node.location.path, node.location.start_line,
                                node.location.end_line, node.provider,
                                node.evidence_hash,
                            )
                            for node in result.nodes
                        ])
                    return facts

                incremental_facts = normalized_batch_facts()
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
                self.assertEqual(incremental_facts, normalized_batch_facts())

                old = repository / "batch_9.py"
                renamed = repository / "renamed_9.py"
                old.rename(renamed)
                rename_result = coordinator.refresh(timeout_ms=300_000)
                self.assertEqual(rename_result["status"], "refreshed", rename_result)
                renamed_response = service.query(request)
                self.assertEqual(
                    [node.location.path for node in renamed_response.nodes],
                    ["renamed_9.py"],
                )
                self.assertFalse(any(node.location.path == "batch_9.py" for node in renamed_response.nodes))

                renamed.unlink()
                delete_result = coordinator.refresh(timeout_ms=300_000)
                self.assertEqual(delete_result["status"], "refreshed", delete_result)
                self.assertEqual(service.query(request).nodes, ())
                self.assertEqual(transport.process.pid, child_pid)

                rss_kib = int(subprocess.run(
                    ["ps", "-o", "rss=", "-p", str(child_pid)],
                    check=True, capture_output=True, text=True,
                ).stdout.strip())
                print(json.dumps({
                    "status": "stage5_explicit_fixture_passed",
                    "same_child_pid": child_pid,
                    "baseline_ms": baseline["duration_ms"],
                    "noop_ms": noop["duration_ms"],
                    "one_file_ms": one["duration_ms"],
                    "five_file_ms": five["duration_ms"],
                    "ten_file_ms": ten["duration_ms"],
                    "warm_query_p50_ms": statistics.median(samples),
                    "warm_query_p95_ms": p95,
                    "rename_ms": rename_result["duration_ms"],
                    "delete_ms": delete_result["duration_ms"],
                    "provider_rss_kib_after": rss_kib,
                    "ten_file_incremental_clean_oracle_equal": True,
                    "old_path_and_deleted_facts_absent": True,
                }, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
