from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

from codebase_atlas import provider_process


class ProviderProcessTests(unittest.TestCase):
    def test_windows_capture_uses_files_not_descendant_pipes(self) -> None:
        def run(command, **arguments):
            self.assertNotIn("capture_output", arguments)
            self.assertNotIn("text", arguments)
            arguments["stdout"].write(b'{"status":"ok"}')
            arguments["stderr"].write(b"warning")
            return SimpleNamespace(args=command, returncode=0)

        with (
            mock.patch.object(provider_process.os, "name", "nt"),
            mock.patch.object(provider_process.subprocess, "run", side_effect=run),
        ):
            completed = provider_process.run_provider_command(["provider"], env={})

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, '{"status":"ok"}')
        self.assertEqual(completed.stderr, "warning")


if __name__ == "__main__":
    unittest.main()
