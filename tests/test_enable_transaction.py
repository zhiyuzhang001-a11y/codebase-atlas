from pathlib import Path
import tempfile
import unittest

from codebase_atlas.config import AtlasConfig
from codebase_atlas.enable_transaction import EnableTransaction
from tests.test_simple_cli import configured_project


class EnableTransactionTests(unittest.TestCase):
    def test_external_config_write_during_action_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as raw:
            _, config, path = configured_project(Path(raw))
            transaction = EnableTransaction(config, path)
            with self.assertRaisesRegex(RuntimeError, "unrecognized configuration write"):
                transaction.run(lambda: path.write_bytes(b"user content during indexing"), indexes=True)
            self.assertTrue(transaction.rollback())
            self.assertEqual(path.read_bytes(), b"user content during indexing")

    def test_index_and_config_roll_back_after_published_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            _, config, path = configured_project(Path(raw))
            config.cache_dir.mkdir(parents=True)
            database = config.cache_dir / f"{config.project}.db"
            database.write_bytes(b"previous database generation")
            path.write_bytes(b"# original comment\n" + path.read_bytes())
            before = path.read_bytes()
            path.chmod(0o600)
            transaction = EnableTransaction(config, path)

            def publish_then_fail():
                candidate = config.cache_dir / "candidate.db"
                candidate.write_bytes(b"new database generation")
                candidate.replace(database)
                path.write_text(config.render())
                raise RuntimeError("acceptance failed")

            with self.assertRaisesRegex(RuntimeError, "acceptance failed"):
                transaction.run(publish_then_fail, indexes=True)
            self.assertEqual(transaction.rollback(), [])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(database.read_bytes(), b"previous database generation")

    def test_external_edit_after_publication_is_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            _, config, path = configured_project(Path(raw))
            transaction = EnableTransaction(config, path)
            transaction.run(lambda: path.write_text(config.render()))
            path.write_bytes(b"user changed this")
            self.assertTrue(transaction.rollback())
            self.assertEqual(path.read_bytes(), b"user changed this")

    def test_update_candidate_can_be_explicitly_authorized(self):
        with tempfile.TemporaryDirectory() as raw:
            _, config, path = configured_project(Path(raw))
            transaction = EnableTransaction(config, path)
            candidate = config.with_project("project-b")
            transaction.allow_config(candidate)
            transaction.run(lambda: path.write_text(candidate.render()))
            self.assertEqual(transaction.rollback(), [])
            self.assertEqual(AtlasConfig.load(path).project, config.project)

    def test_initial_failure_removes_new_index(self):
        with tempfile.TemporaryDirectory() as raw:
            _, config, path = configured_project(Path(raw))
            transaction = EnableTransaction(config, path)

            def publish():
                config.cache_dir.mkdir(parents=True)
                transaction.database.write_bytes(b"new index")

            transaction.run(publish, indexes=True)
            self.assertEqual(transaction.rollback(), [])
            self.assertFalse(transaction.database.exists())
