from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from codebase_atlas.runtime import required_checks_ok, runtime_checks


class RuntimeCheckTests(unittest.TestCase):
    def test_validates_versions_and_import_capability(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            node = root / "node"
            cbm = root / "codebase-memory-mcp"
            serena = root / "python"

            def runner(command, **_kwargs):
                if command[0] == str(node):
                    output = "v20.18.0"
                elif command[0] == str(cbm):
                    output = "codebase-memory-mcp 0.4.0"
                else:
                    output = "1.7.1"
                return SimpleNamespace(returncode=0, stdout=output, stderr="")

            checks = runtime_checks(
                repo,
                language="python",
                node=node,
                cbm_binary=cbm,
                serena_python=serena,
                runner=runner,
            )
            self.assertTrue(required_checks_ok(checks))
            by_name = {item["name"]: item for item in checks}
            self.assertEqual(by_name["node"]["version"], "20.18.0")
            self.assertEqual(by_name["serena_python"]["version"], "1.7.1")

    def test_missing_runtime_has_actionable_remediation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            checks = runtime_checks(
                Path(raw),
                language="python",
                node=Path(raw) / "missing-node",
                cbm_binary=Path(raw) / "missing-cbm",
                serena_python=Path(raw) / "missing-python",
            )
            self.assertFalse(required_checks_ok(checks))
            failed = [item for item in checks if item["required"] and not item["ok"]]
            self.assertTrue(all(item["remediation"] for item in failed))

    def test_typescript_language_server_is_optional(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            (repo / "tsconfig.json").touch()

            def runner(command, **_kwargs):
                output = "v18.20.0" if command[-1] == "--version" else "1.7.1"
                return SimpleNamespace(returncode=0, stdout=output, stderr="")

            checks = runtime_checks(
                repo,
                language="typescript",
                node=root / "node",
                cbm_binary=root / "cbm",
                serena_python=root / "python",
                runner=runner,
            )
            language_server = next(
                item for item in checks if item["name"] == "typescript_language_server"
            )
            self.assertFalse(language_server["required"])
            self.assertTrue(required_checks_ok(checks))


if __name__ == "__main__":
    unittest.main()
