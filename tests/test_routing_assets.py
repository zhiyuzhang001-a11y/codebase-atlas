from pathlib import Path
import tempfile
import unittest

from codebase_atlas.routing_assets import RULE, SKILL, plan_routing


class RoutingAssetTests(unittest.TestCase):
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
