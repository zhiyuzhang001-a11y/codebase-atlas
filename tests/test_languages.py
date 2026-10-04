from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.cli import main as advanced_main
from codebase_atlas.config import AtlasConfig
from codebase_atlas.languages import (
    all_language_ids,
    default_language,
    get_language,
    public_language_choices,
)
from codebase_atlas.runtime import required_checks_ok, runtime_checks
from codebase_atlas.simple_cli import main as simple_main


class LanguageRegistryTests(unittest.TestCase):
    def test_registry_is_complete_immutable_and_rust_is_hidden(self) -> None:
        self.assertEqual(all_language_ids(), ("python", "typescript", "rust"))
        self.assertEqual(public_language_choices(), ("python", "typescript"))
        rust = get_language("rust")
        self.assertFalse(rust.public_enabled)
        self.assertEqual(rust.source_extensions, frozenset({".rs"}))
        self.assertEqual(rust.manifest_inputs, ("Cargo.toml", "Cargo.lock"))
        self.assertEqual(rust.capability("definition"), "required")
        self.assertEqual(rust.capability("callers"), "unsupported")
        with self.assertRaises(TypeError):
            rust.capabilities["definition"] = "enabled"  # type: ignore[index]

    def test_unknown_language_and_capability_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported project language"):
            get_language("unknown")
        with self.assertRaisesRegex(ValueError, "unknown capability"):
            get_language("rust").capability("telepathy")

    def test_default_detection_remains_typescript_then_python(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            self.assertEqual(default_language(repository), "python")
            (repository / "Cargo.toml").touch()
            self.assertEqual(default_language(repository), "python")
            (repository / "tsconfig.json").touch()
            self.assertEqual(default_language(repository), "typescript")

    def test_rust_runtime_is_blocked_without_executing_tools(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            calls = []

            def runner(*args, **kwargs):
                calls.append((args, kwargs))
                raise AssertionError("disabled Rust must not execute a runtime")

            checks = runtime_checks(Path(raw), language="rust", runner=runner)
            self.assertEqual(calls, [])
            feature = next(item for item in checks if item["name"] == "language_feature")
            self.assertFalse(feature["ok"])
            self.assertTrue(feature["required"])
            self.assertFalse(required_checks_ok(checks))

    def test_user_facing_clis_do_not_offer_rust(self) -> None:
        for entrypoint, arguments in (
            (advanced_main, ["setup", "--language", "rust"]),
            (simple_main, ["enable", "--language", "rust"]),
        ):
            with self.subTest(entrypoint=entrypoint.__module__):
                with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as raised:
                    entrypoint(arguments)
                self.assertEqual(raised.exception.code, 2)

    def test_internal_rust_config_cannot_start_index_or_query_providers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw) / "repo"
            repository.mkdir()
            config_path = repository / ".codebase-atlas.toml"
            AtlasConfig(
                repository, "rust", Path("/missing/node"),
                Path("/missing/provider"), Path("/missing/python"),
                Path(raw) / "data", "rust-project",
            ).write(config_path)
            for arguments in (
                ["index", "--config", str(config_path)],
                ["plan-refresh", "--config", str(config_path)],
                ["migrate-provider", "--config", str(config_path)],
                ["repair", "--config", str(config_path)],
                ["query", "definition", "run", "--config", str(config_path)],
            ):
                with self.subTest(command=arguments[0]), patch(
                    "codebase_atlas.cli._provider_lifecycle"
                ) as provider, redirect_stdout(StringIO()) as output:
                    code = advanced_main(arguments)
                self.assertEqual(code, 2)
                self.assertFalse(provider.called)
                self.assertEqual(
                    json.loads(output.getvalue())["code"],
                    "language_not_product_enabled",
                )


if __name__ == "__main__":
    unittest.main()
