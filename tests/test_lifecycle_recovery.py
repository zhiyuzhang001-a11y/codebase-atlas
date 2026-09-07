from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.lifecycle_recovery import (
    LifecycleRecoveryJournal, journal_path, recover_lifecycle_transaction,
)
from codebase_atlas.routing_transaction import RoutingTransaction
from codebase_atlas.simple_cli import status_project, stop_project
from tests.test_simple_cli import configured_project


class LifecycleRecoveryTests(unittest.TestCase):
    def test_interrupted_publication_restores_authorized_project_files(self):
        with tempfile.TemporaryDirectory() as raw:
            repository, config, config_path = configured_project(Path(raw))
            original = config_path.read_bytes()
            routing = RoutingTransaction(repository)
            journal = LifecycleRecoveryJournal.begin(
                config, config_path, operation="enable", operation_id="op-1",
                routing=routing,
            )
            for plan in routing.plans:
                journal.allow(plan.path, plan.after)
            routing.apply()
            candidate = config.with_project("changed")
            journal.allow(config_path, candidate.render().encode())
            candidate.write(config_path)
            recovered = recover_lifecycle_transaction(repository)
            self.assertEqual(recovered["action"], "restored_previous_state")
            self.assertEqual(config_path.read_bytes(), original)
            self.assertFalse((repository / "AGENTS.md").exists())
            self.assertFalse((repository / ".agents/skills/codebase-atlas/SKILL.md").exists())
            self.assertFalse(journal_path(repository).exists())

    def test_external_repository_edit_is_preserved_and_blocks_recovery(self):
        with tempfile.TemporaryDirectory() as raw:
            repository, config, config_path = configured_project(Path(raw))
            routing = RoutingTransaction(repository)
            LifecycleRecoveryJournal.begin(
                config, config_path, operation="update", operation_id="op-2",
                routing=routing,
            )
            config_path.write_bytes(b"user edit")
            with self.assertRaisesRegex(RuntimeError, "external modification preserved"):
                recover_lifecycle_transaction(repository)
            self.assertEqual(config_path.read_bytes(), b"user edit")
            self.assertTrue(journal_path(repository).exists())

    def test_accepted_operation_is_not_rolled_back_after_cleanup_crash(self):
        with tempfile.TemporaryDirectory() as raw:
            repository, config, config_path = configured_project(Path(raw))
            routing = RoutingTransaction(repository)
            journal = LifecycleRecoveryJournal.begin(
                config, config_path, operation="update", operation_id="op-3",
                routing=routing,
            )
            candidate = config.with_project("changed")
            payload = candidate.render().encode()
            journal.allow(config_path, payload)
            config_path.write_bytes(payload)
            journal.accept()
            recovered = recover_lifecycle_transaction(repository)
            self.assertEqual(recovered["action"], "accepted_completed_operation")
            self.assertEqual(config_path.read_bytes(), payload)

    def test_next_lifecycle_command_recovers_after_external_process_exit(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository, config, config_path = configured_project(root)
            original = config_path.read_bytes()
            recovery_data = root / "xdg-data"
            script = """
import os
from dataclasses import replace
from pathlib import Path
from codebase_atlas.config import AtlasConfig
from codebase_atlas.lifecycle_recovery import LifecycleRecoveryJournal
from codebase_atlas.routing_transaction import RoutingTransaction
repository = Path(os.environ['ATLAS_TEST_REPOSITORY'])
config_path = Path(os.environ['ATLAS_TEST_CONFIG'])
config = AtlasConfig.load(config_path)
routing = RoutingTransaction(repository)
journal = LifecycleRecoveryJournal.begin(
    config, config_path, operation='update', operation_id='killed-operation',
    routing=routing,
)
routing.apply()
candidate = replace(config, cbm_binary=Path(os.environ['ATLAS_TEST_PROVIDER']))
payload = candidate.render().encode()
journal.allow(config_path, payload)
config_path.write_bytes(payload)
os._exit(91)
"""
            environment = dict(os.environ)
            environment.update({
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
                "XDG_DATA_HOME": str(recovery_data),
                "ATLAS_TEST_REPOSITORY": str(repository),
                "ATLAS_TEST_CONFIG": str(config_path),
                "ATLAS_TEST_PROVIDER": str(root / "new-provider"),
            })
            completed = subprocess.run(
                [sys.executable, "-c", script], env=environment, check=False
            )
            self.assertEqual(completed.returncode, 91)
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(recovery_data)}):
                observed, observed_code = status_project(repository)
                self.assertEqual(observed_code, 2)
                self.assertEqual(observed["reason_code"], "lifecycle_recovery_required")
                stopped, stopped_code = stop_project(repository, timeout_seconds=0)
                self.assertFalse(journal_path(repository).exists())
            self.assertEqual(stopped_code, 0, stopped)
            # The fixture has no completed index, so stop correctly becomes an
            # idempotent not-enabled result after performing recovery first.
            self.assertEqual(stopped["status"], "not_enabled")
            self.assertEqual(config_path.read_bytes(), original)
            self.assertFalse((repository / "AGENTS.md").exists())
            self.assertFalse((repository / ".agents").exists())

    def test_owned_orphan_staging_is_cleaned_but_foreign_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository, _config, _config_path = configured_project(root)
            recovery_data = root / "xdg-data"
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(recovery_data)}):
                recovery_root = journal_path(repository).parent
                orphan = recovery_root / ("lifecycle-" + "a" * 32)
                orphan.mkdir(parents=True)
                (orphan / "config.bak").write_bytes(b"owned")
                result = recover_lifecycle_transaction(repository)
                self.assertEqual(result["removed"], 1)
                self.assertFalse(orphan.exists())
                foreign = recovery_root / ("lifecycle-" + "b" * 32)
                foreign.mkdir()
                (foreign / "user.txt").write_text("preserve", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "contents are unsafe"):
                    recover_lifecycle_transaction(repository)
                self.assertTrue((foreign / "user.txt").exists())
