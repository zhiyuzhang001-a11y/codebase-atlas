from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

from scripts.run_multi_mcp_stress import parse_windows_process_table


class MultiMcpStressUnitTests(unittest.TestCase):
    def test_parses_windows_process_inventory_for_cleanup_checks(self) -> None:
        table = parse_windows_process_table(
            '[{"ProcessId":12,"ParentProcessId":4,"CommandLine":"atlas mcp"},'
            '{"ProcessId":13,"ParentProcessId":12,"CommandLine":null}]'
        )
        self.assertEqual(table, {12: (4, "atlas mcp"), 13: (12, "")})


class MultiMcpStressIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("ATLAS_CONCURRENCY_PROVIDER_BINARY"),
        "set ATLAS_CONCURRENCY_PROVIDER_BINARY for the real multi-MCP gate",
    )
    def test_real_independent_mcp_processes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(root / "src") + os.pathsep + environment.get("PYTHONPATH", "")
        languages = os.environ.get("ATLAS_CONCURRENCY_LANGUAGES", "python").split(",")
        ledger_directory = os.environ.get("ATLAS_CONCURRENCY_LEDGER_DIR")
        for language in languages:
            with self.subTest(language=language):
                command = [
                    sys.executable,
                    str(root / "scripts/run_multi_mcp_stress.py"),
                    "--provider-binary", os.environ["ATLAS_CONCURRENCY_PROVIDER_BINARY"],
                    "--node", os.environ["ATLAS_CONCURRENCY_NODE"],
                    "--serena-python", os.environ["ATLAS_CONCURRENCY_SERENA_PYTHON"],
                    "--rounds", os.environ.get("ATLAS_CONCURRENCY_ROUNDS", "1"),
                    "--language", language,
                    "--clients", os.environ.get("ATLAS_CONCURRENCY_CLIENTS", "4"),
                    "--writers", os.environ.get("ATLAS_CONCURRENCY_WRITERS", "3"),
                    "--files-per-writer",
                    os.environ.get("ATLAS_CONCURRENCY_FILES_PER_WRITER", "1"),
                ]
                if ledger_directory:
                    ledger_path = Path(ledger_directory) / f"{language}-ledger.json"
                    command.extend(["--ledger-out", str(ledger_path)])
                completed = subprocess.run(
                    command, cwd=root, env=environment, capture_output=True, text=True,
                    timeout=1800, check=False,
                )
                self.assertEqual(
                    completed.returncode, 0, completed.stderr or completed.stdout
                )
                self.assertIn('"status": "passed"', completed.stdout)
                self.assertIn(f'"language": "{language}"', completed.stdout)


if __name__ == "__main__":
    unittest.main()
