from dataclasses import replace
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from codebase_atlas.refresh_planner import manifest_path
from codebase_atlas.rust_project import load_rust_service
from codebase_atlas.rust_refresh import RustRefreshCoordinator
from codebase_atlas.rust_runtime import RustRuntimeError
from codebase_atlas.cli import main
from codebase_atlas.languages import get_language
from tests import test_rust_refresh as fixture_module


class RustProjectTests(unittest.TestCase):
    def setUp(self):
        fixture = fixture_module.RustRefreshTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.fixture = fixture
        self.config = replace(fixture.config, node=None, cbm_binary=None, serena_python=None,
                              rust_runtime_receipt=fixture.root / "receipt.json")
        RustRefreshCoordinator(self.config, fixture.scanner, runner=fixture.runner).refresh()
        self.runtime = Mock()
        self.runtime.analyzer.path = fixture.scanner.resolve()
        patcher = patch("codebase_atlas.rust_project.runtime_from_receipt", return_value=self.runtime)
        self.loader = patcher.start()
        self.addCleanup(patcher.stop)

    def test_service_binds_tiers_without_legacy_dependencies_or_execution(self):
        with patch("codebase_atlas.providers.rust_analyzer.subprocess.Popen") as spawn:
            service = load_rust_service(self.config)
            self.assertIsNone(service.structural_provider)
            self.assertIsNone(service.semantic_provider)
            self.assertIsNone(service.test_provider)
            self.assertIs(service.rust_provider.runtime, self.runtime)
            self.assertEqual(service.rust_syntax_index.document["generation_id"],
                             service.rust_provider.generation["generation_id"])
            self.runtime.environment.assert_not_called()
            spawn.assert_not_called()
            self.loader.assert_called_once_with(self.config.rust_runtime_receipt,
                                                repository=self.config.repository)
            service.close()

    def test_receipt_is_mandatory(self):
        with self.assertRaisesRegex(RustRuntimeError, "receipt"):
            load_rust_service(self.fixture.config)
        self.loader.assert_not_called()

    def test_other_generation_artifact_cannot_be_joined(self):
        path = manifest_path(self.config.data_dir)
        document = json.loads(path.read_text())
        document["provider_identity"]["artifact"]["sha256"] = "0" * 64
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(RustRuntimeError, "artifact identity"):
            load_rust_service(self.config)

    def test_missing_generation_cannot_start_analyzer(self):
        manifest_path(self.config.data_dir).unlink()
        with patch("codebase_atlas.providers.rust_analyzer.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RustRuntimeError, "published Rust generation"):
                load_rust_service(self.config)
            spawn.assert_not_called()

    def test_normal_cli_schema2_reaches_closed_product_gate_without_legacy_tools(self):
        path = self.fixture.root / "atlas.toml"
        self.config.write(path)
        output = StringIO()
        with redirect_stdout(output), patch("codebase_atlas.cli._legacy_service") as legacy:
            result = main(["query", "definition", "run", "--config", str(path)])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output.getvalue())["code"], "language_not_product_enabled")
        legacy.assert_not_called()
        self.loader.assert_not_called()

    def test_candidate_cli_selects_rust_factory_not_legacy_factory(self):
        path = self.fixture.root / "atlas.toml"
        self.config.write(path)
        candidate = replace(get_language("rust"), public_enabled=True)
        with patch("codebase_atlas.cli.get_language", return_value=candidate), patch(
            "codebase_atlas.cli.operational_lifecycle_status", return_value={"ok": True}
        ), patch("codebase_atlas.cli._legacy_service") as legacy, patch(
            "codebase_atlas.cli._run_service_command", return_value=0
        ) as dispatch:
            result = main(["query", "definition", "run", "--config", str(path)])
        self.assertEqual(result, 0)
        service = dispatch.call_args.args[1]
        self.assertIs(service.rust_provider.runtime, self.runtime)
        legacy.assert_not_called()
        service.close()


if __name__ == "__main__":
    unittest.main()
