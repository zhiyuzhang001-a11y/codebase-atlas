from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.refresh_planner import (
    RefreshPlanError,
    build_generation_manifest,
    manifest_path,
    plan_refresh,
    stage_generation_manifest_candidate,
    validate_generation_manifest,
)
from codebase_atlas.rust_scope import (
    RustScopeError,
    build_rust_source_scope,
    validate_rust_source_scope,
)


def git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args], check=True, capture_output=True
    )


class RustSourceScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repo"
        self.repository.mkdir()
        git(self.repository, "init", "-q")
        git(self.repository, "config", "user.email", "atlas@example.invalid")
        git(self.repository, "config", "user.name", "Atlas Test")
        (self.repository / "Cargo.toml").write_text(
            '[package]\nname = "scope-fixture"\nversion = "0.1.0"\n'
            'edition = "2021"\n',
            encoding="utf-8",
        )
        (self.repository / "Cargo.lock").write_text("version = 3\n", encoding="utf-8")
        (self.repository / "src/api").mkdir(parents=True)
        (self.repository / "src/generated").mkdir()
        (self.repository / "src/lib.rs").write_text(
            "pub mod api;\n"
            '#[path = "generated/value.rs"]\nmod generated_value;\n'
            'include!("included.rs");\n',
            encoding="utf-8",
        )
        (self.repository / "src/api.rs").write_text(
            "pub mod nested;\n", encoding="utf-8"
        )
        (self.repository / "src/api/nested.rs").write_text(
            "pub fn nested() {}\n", encoding="utf-8"
        )
        (self.repository / "src/generated/value.rs").write_text(
            "pub const VALUE: u8 = 1;\n", encoding="utf-8"
        )
        (self.repository / "src/included.rs").write_text(
            "pub fn included() {}\n", encoding="utf-8"
        )
        (self.repository / "tests").mkdir()
        (self.repository / "tests/integration.rs").write_text(
            "#[test]\nfn works() {}\n", encoding="utf-8"
        )
        (self.repository / "templates").mkdir()
        (self.repository / "templates/not_compilable.rs").write_text(
            "{{ generated_rust }}\n", encoding="utf-8"
        )
        git(self.repository, "add", ".")
        git(self.repository, "commit", "-qm", "initial")
        self.data = self.root / "data"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def generation(self) -> dict:
        return build_generation_manifest(
            self.repository,
            "rust-project",
            "rust",
            generation_id="rust-generation-1",
            provider_identity={"status": "not_applicable", "tier": "T0"},
            sidecar_identity={"status": "not_applicable", "tier": "T0"},
            created_at="generation:rust-generation-1",
        )

    def test_scope_is_cargo_module_bounded_and_records_exclusions(self) -> None:
        original_run = subprocess.run

        def git_only(argv, **kwargs):
            self.assertEqual(argv[0], "git")
            return original_run(argv, **kwargs)

        with patch(
            "codebase_atlas.providers.python_inventory.subprocess.run",
            side_effect=git_only,
        ):
            scope = build_rust_source_scope(self.repository)
        self.assertEqual(scope["status"], "complete_exact")
        self.assertEqual(
            scope["source_paths"],
            [
                "src/api.rs", "src/api/nested.rs", "src/generated/value.rs",
                "src/included.rs", "src/lib.rs", "tests/integration.rs",
            ],
        )
        self.assertIn(
            {"path": "templates/not_compilable.rs", "reason": "outside_cargo_module_scope"},
            scope["exclusions"],
        )
        self.assertEqual(scope["lockfile"]["status"], "present")
        self.assertTrue(all(value is False for value in scope["execution"].values()))

    def test_unresolved_module_is_partial_not_silently_complete(self) -> None:
        (self.repository / "src/lib.rs").write_text("mod missing;\n", encoding="utf-8")
        scope = build_rust_source_scope(self.repository)
        self.assertEqual(scope["status"], "exact_hits_partial_scope")
        self.assertIn(
            {"path": "src/lib.rs", "reason": "unresolved_module:missing"},
            scope["exclusions"],
        )

    def test_inline_modules_contribute_their_directory_to_child_modules(self) -> None:
        (self.repository / "src/outer/inner").mkdir(parents=True)
        (self.repository / "src/outer/child.rs").write_text(
            "pub fn child() {}\n", encoding="utf-8"
        )
        (self.repository / "src/outer/inner/leaf.rs").write_text(
            "pub fn leaf() {}\n", encoding="utf-8"
        )
        (self.repository / "src/lib.rs").write_text(
            'const BRACES: &str = "}}}}";\n'
            'const RAW: &str = r#"not code: " // }"#;\n'
            "fn lifetime<'a>(value: &'a str) -> &'a str { value }\n"
            "mod outer {\n"
            "    pub mod child;\n"
            "    fn unrelated() { if true { } }\n"
            "    mod inner { mod leaf; }\n"
            "}\n",
            encoding="utf-8",
        )
        git(self.repository, "add", "src")

        scope = build_rust_source_scope(self.repository)

        self.assertEqual(scope["status"], "complete_exact")
        self.assertEqual(
            scope["source_paths"],
            [
                "src/lib.rs",
                "src/outer/child.rs",
                "src/outer/inner/leaf.rs",
                "tests/integration.rs",
            ],
        )

    def test_path_attribute_inside_inline_module_uses_inline_directory(self) -> None:
        (self.repository / "src/outer/custom").mkdir(parents=True)
        (self.repository / "src/outer/custom/value.rs").write_text(
            "pub const VALUE: u8 = 1;\n", encoding="utf-8"
        )
        (self.repository / "src/lib.rs").write_text(
            'mod outer { #[path = "custom/value.rs"] mod value; }\n',
            encoding="utf-8",
        )
        git(self.repository, "add", "src")

        scope = build_rust_source_scope(self.repository)

        self.assertEqual(scope["status"], "complete_exact")
        self.assertIn("src/outer/custom/value.rs", scope["source_paths"])

    def test_nonstandard_cargo_target_root_resolves_modules_from_parent(self) -> None:
        (self.repository / "tests/support").mkdir()
        (self.repository / "tests/integration.rs").write_text(
            "mod support;\n", encoding="utf-8"
        )
        (self.repository / "tests/support/mod.rs").write_text(
            "mod helper;\n", encoding="utf-8"
        )
        (self.repository / "tests/support/helper.rs").write_text(
            "pub fn helper() {}\n", encoding="utf-8"
        )
        git(self.repository, "add", "tests")

        scope = build_rust_source_scope(self.repository)

        self.assertEqual(scope["status"], "complete_exact")
        self.assertIn("tests/support/mod.rs", scope["source_paths"])
        self.assertIn("tests/support/helper.rs", scope["source_paths"])

    def test_invoked_local_macro_resolves_modules_at_invocation_directory(self) -> None:
        (self.repository / "src/lib.rs").write_text(
            "#[macro_use]\nmod declarations;\ndeclare_modules!();\n",
            encoding="utf-8",
        )
        (self.repository / "src/declarations.rs").write_text(
            "macro_rules! declare_modules { () => { mod generated; }; }\n",
            encoding="utf-8",
        )
        (self.repository / "src/generated.rs").write_text(
            "pub fn generated() {}\n", encoding="utf-8"
        )
        git(self.repository, "add", "src")

        scope = build_rust_source_scope(self.repository)

        self.assertEqual(scope["status"], "complete_exact")
        self.assertIn("src/generated.rs", scope["source_paths"])

    def test_generation_binds_language_scope_and_transactional_candidate(self) -> None:
        generation = self.generation()
        self.assertEqual(generation["language"], "rust")
        self.assertEqual(generation["build_context"]["cargo_features"], "all")
        self.assertTrue(generation["build_context"]["cargo_no_deps"])
        self.assertEqual(
            generation["build_context"]["cargo_cfgs"], "all_package_features"
        )
        self.assertFalse(generation["build_context"]["build_scripts"])
        self.assertEqual(
            [entry["path"] for entry in generation["files"]],
            generation["source_scope"]["source_paths"],
        )
        with stage_generation_manifest_candidate(
            self.data, generation, self.repository, "rust-project"
        ) as staged:
            self.assertTrue(staged.temporary.is_file())
            self.assertFalse(manifest_path(self.data).exists())
            staged.publish(manifest_path(self.data))
            self.assertEqual(
                json.loads(manifest_path(self.data).read_text())["language"], "rust"
            )
            staged.rollback()
        self.assertFalse(manifest_path(self.data).exists())

    def test_refresh_reports_scope_changes_without_running_a_provider(self) -> None:
        generation = self.generation()
        self.data.mkdir()
        manifest_path(self.data).write_text(
            json.dumps(generation, indent=2) + "\n", encoding="utf-8"
        )
        (self.repository / "src/new.rs").write_text("pub fn new() {}\n", encoding="utf-8")
        (self.repository / "src/lib.rs").write_text(
            (self.repository / "src/lib.rs").read_text() + "mod new;\n",
            encoding="utf-8",
        )
        plan = plan_refresh(
            self.data, self.repository, "rust-project", "rust"
        )
        self.assertEqual(plan["dirty_paths"], ["src/lib.rs", "src/new.rs"])
        self.assertTrue(plan["provider_inputs_changed"])
        self.assertEqual(plan["source_scope"]["status"], "complete_exact")

    def test_scope_and_manifest_validation_fail_closed(self) -> None:
        scope = build_rust_source_scope(self.repository)
        invalid_scope = dict(scope)
        invalid_scope["execution"] = dict(scope["execution"], cargo=True)
        with self.assertRaisesRegex(RustScopeError, "execution boundary"):
            validate_rust_source_scope(invalid_scope)
        generation = self.generation()
        generation["source_scope"] = invalid_scope
        with self.assertRaisesRegex(RefreshPlanError, "execution boundary"):
            validate_generation_manifest(
                generation, self.repository, "rust-project"
            )
        generation = self.generation()
        generation["build_context"]["cargo_features"] = "default"
        with self.assertRaisesRegex(RefreshPlanError, "build context"):
            validate_generation_manifest(
                generation, self.repository, "rust-project"
            )

    def test_workspace_members_are_distinct_packages(self) -> None:
        member = self.repository / "crates/member"
        (member / "src").mkdir(parents=True)
        (member / "Cargo.toml").write_text(
            '[package]\nname = "member"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (member / "src/lib.rs").write_text("pub fn member() {}\n", encoding="utf-8")
        (self.repository / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/*"]\nresolver = "2"\n', encoding="utf-8"
        )
        scope = build_rust_source_scope(self.repository)
        self.assertEqual(
            scope["packages"],
            [{
                "name": "member", "root": "crates/member",
                "manifest": "crates/member/Cargo.toml",
            }],
        )
        self.assertEqual(
            [item["path"] for item in scope["manifests"]],
            ["Cargo.toml", "crates/member/Cargo.toml"],
        )
        self.assertEqual(scope["source_paths"], ["crates/member/src/lib.rs"])
        self.assertIn(
            {"path": "src/lib.rs", "reason": "outside_cargo_module_scope"},
            scope["exclusions"],
        )


if __name__ == "__main__":
    unittest.main()
