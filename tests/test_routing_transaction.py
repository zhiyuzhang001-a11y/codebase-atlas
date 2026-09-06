from pathlib import Path
import tempfile
import os
import unittest
from unittest.mock import patch

from codebase_atlas.routing_transaction import RoutingTransaction


class RoutingTransactionTests(unittest.TestCase):
    def test_failure_after_atomic_publish_restores_original_state(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            transaction = RoutingTransaction(root)
            replace = os.replace
            calls = 0

            def publish_then_fail(source, destination):
                nonlocal calls
                replace(source, destination)
                calls += 1
                if calls == 1:
                    raise OSError("failure after publication")

            with patch("codebase_atlas.routing_transaction.os.replace", side_effect=publish_then_fail):
                with self.assertRaisesRegex(OSError, "after publication"):
                    transaction.apply()
            self.assertEqual(list(root.iterdir()), [])

    def test_apply_and_rollback_restore_absence(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            transaction = RoutingTransaction(root)
            transaction.apply()
            self.assertTrue((root / "AGENTS.md").is_file())
            self.assertEqual(transaction.rollback(), [])
            self.assertEqual(list(root.iterdir()), [])

    def test_failure_on_second_file_restores_first(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original = root / "AGENTS.md"
            original.write_bytes(b"custom rules")
            original.chmod(0o600)
            transaction = RoutingTransaction(root)
            publish = transaction._publish

            def fail_skill(plan, *args):
                if plan.path.name == "SKILL.md":
                    raise OSError("injected write failure")
                return publish(plan, *args)

            with patch.object(transaction, "_publish", side_effect=fail_skill):
                with self.assertRaisesRegex(OSError, "injected"):
                    transaction.apply()
            self.assertEqual(original.read_bytes(), b"custom rules")
            self.assertEqual(original.stat().st_mode & 0o777, 0o600)

    def test_rollback_preserves_concurrent_user_edit(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            transaction = RoutingTransaction(root)
            transaction.apply()
            path = root / "AGENTS.md"
            path.write_bytes(b"new user rules")
            self.assertTrue(transaction.rollback())
            self.assertEqual(path.read_bytes(), b"new user rules")

    def test_preflight_conflict_does_not_write_first_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            transaction = RoutingTransaction(root)
            skill = root / ".agents/skills/codebase-atlas/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_bytes(b"user skill")
            with self.assertRaisesRegex(RuntimeError, "changed since planning"):
                transaction.apply()
            self.assertFalse((root / "AGENTS.md").exists())
            self.assertEqual(skill.read_bytes(), b"user skill")
