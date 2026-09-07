from pathlib import Path
import tempfile
import unittest

from codebase_atlas.routing_state import load_routing_state, publish_routing_state


class RoutingStateTests(unittest.TestCase):
    def test_round_trip_is_repository_bound(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = root / "repo"
            other = root / "other"
            repository.mkdir()
            other.mkdir()
            data = root / "data"
            publish_routing_state(data, repository, created_rule_file=True)
            self.assertTrue(load_routing_state(data, repository)["created_rule_file"])
            with self.assertRaisesRegex(RuntimeError, "identity"):
                load_routing_state(data, other)
