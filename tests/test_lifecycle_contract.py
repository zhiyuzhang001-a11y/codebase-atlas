import unittest

from codebase_atlas.provider_transport import (
    EOF_CLOSE_TIMEOUT_SECONDS,
    KILL_TIMEOUT_SECONDS,
    TERMINATE_TIMEOUT_SECONDS,
)
from codebase_atlas.simple_cli import (
    STATUS_DISCOVERY_TIMEOUT_SECONDS,
    STATUS_GIT_PROBE_TIMEOUT_SECONDS,
    STATUS_MAX_DEPTH,
    STATUS_MAX_DIRECTORIES,
)
from scripts.run_multi_mcp_stress import PROCESS_CLEANUP_GRACE_SECONDS


class LifecycleContractTests(unittest.TestCase):
    def test_status_discovery_budgets_are_frozen(self):
        self.assertEqual(STATUS_DISCOVERY_TIMEOUT_SECONDS, 2.0)
        self.assertEqual(STATUS_GIT_PROBE_TIMEOUT_SECONDS, 0.25)
        self.assertEqual(STATUS_MAX_DIRECTORIES, 4096)
        self.assertEqual(STATUS_MAX_DEPTH, 6)

    def test_owned_provider_cleanup_grace_is_frozen(self):
        self.assertEqual(EOF_CLOSE_TIMEOUT_SECONDS, 10.0)
        self.assertEqual(TERMINATE_TIMEOUT_SECONDS, 3.0)
        self.assertEqual(KILL_TIMEOUT_SECONDS, 3.0)
        self.assertEqual(PROCESS_CLEANUP_GRACE_SECONDS, 20.0)
