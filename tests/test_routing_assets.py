from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.routing_assets import (
    RULE, SKILL, decode_routing_bundle, plan_routing, routing_bundle,
)
from codebase_atlas.routing_transaction import RoutingTransaction


class RoutingAssetTests(unittest.TestCase):
    def test_versioned_bundle_round_trip_and_validation(self):
        bundle = routing_bundle()
        decoded = decode_routing_bundle(bundle)
        self.assertEqual(decoded[:2], (RULE, SKILL))
        changed = dict(bundle, rule="not base64!")
        with self.assertRaisesRegex(RuntimeError, "encoding"):
            decode_routing_bundle(changed)
        changed = dict(bundle, known_rules=[])
        with self.assertRaisesRegex(RuntimeError, "content"):
            decode_routing_bundle(changed)

    def test_target_bundle_upgrades_only_known_old_assets(self):
        import base64
        import hashlib
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            RoutingTransaction(root).apply()
            new_rule = RULE.replace(b"Known single-file", b"Localized single-file")
            new_skill = SKILL.replace(b"Call project_status", b"Always call project_status")
            target = decode_routing_bundle({
                "schema_version": 1,
                "rule": base64.b64encode(new_rule).decode(),
                "skill": base64.b64encode(new_skill).decode(),
                "known_rules": [hashlib.sha256(RULE).hexdigest(), hashlib.sha256(new_rule).hexdigest()],
                "known_skills": [hashlib.sha256(SKILL).hexdigest(), hashlib.sha256(new_skill).hexdigest()],
            })
            plans = plan_routing(root, bundle=target)
            self.assertEqual([p.status for p in plans], ["owned-old", "owned-old"])
            self.assertEqual([p.after for p in plans], [new_rule, new_skill])
    def test_replacement_between_stat_and_open_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "AGENTS.md"
            path.write_bytes(b"original")
            replacement = root / "replacement"
            replacement.write_bytes(b"replacement")
            original_open = os.open

            def raced_open(target, flags):
                replacement.replace(path)
                return original_open(target, flags)

            with patch("codebase_atlas.routing_assets.os.open", side_effect=raced_open):
                with self.assertRaisesRegex(RuntimeError, "changed during inspection"):
                    plan_routing(root)
            self.assertEqual(path.read_bytes(), b"replacement")

    def test_symlink_asset_is_rejected_without_reading_target(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            outside = root / "private"
            outside.write_bytes(b"private content")
            try:
                (root / "AGENTS.md").symlink_to(outside)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaisesRegex(RuntimeError, "regular file"):
                plan_routing(root)
            self.assertEqual(outside.read_bytes(), b"private content")

    def test_planning_is_read_only(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plans = plan_routing(root)
            self.assertEqual([p.status for p in plans], ["absent", "absent"])
            self.assertEqual(list(root.iterdir()), [])

    def test_owned_rule_removal_preserves_foreign_bytes(self):
        for foreign in (b"", b"custom", b"custom\n", b"custom\n\n"):
            with self.subTest(foreign=foreign), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                path = root / "AGENTS.md"
                path.write_bytes(foreign)
                plan = plan_routing(root)[0]
                path.write_bytes(plan.after)
                self.assertEqual(plan_routing(root)[0].status, "matching")
                self.assertEqual(plan_routing(root, remove=True)[0].after, foreign)

    def test_markers_do_not_authorize_user_edit_replacement(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            changed = RULE.replace(b"cross-file", b"custom")
            (root / "AGENTS.md").write_bytes(changed)
            for remove in (False, True):
                plan = plan_routing(root, remove=remove)[0]
                self.assertEqual(plan.status, "conflict")
                self.assertEqual(plan.after, changed)

    def test_foreign_skill_and_siblings_are_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            folder = root / ".agents/skills/codebase-atlas"
            folder.mkdir(parents=True)
            path = folder / "SKILL.md"
            path.write_bytes(SKILL + b"user edit")
            sibling = folder / "notes.md"
            sibling.write_bytes(b"private")
            plan = plan_routing(root, remove=True)[1]
            self.assertEqual(plan.status, "conflict")
            self.assertEqual(plan.after, path.read_bytes())
            self.assertEqual(sibling.read_bytes(), b"private")
