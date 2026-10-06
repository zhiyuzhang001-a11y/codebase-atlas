from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codebase_atlas.rust_runtime import RustRuntimeError, VerifiedRustTool, rust_runtime_environment


class RustRuntimePreflightTests(unittest.TestCase):
    def test_official_component_lock_has_all_five_platforms_and_exact_checksums(self):
        path = Path(__file__).resolve().parents[1] / "src/codebase_atlas/rust_release_lock.json"
        lock = json.loads(path.read_text())
        self.assertEqual(lock["toolchain"], "1.98.0")
        self.assertEqual(set(lock["targets"]), {"linux-x86_64", "linux-arm64", "macos-arm64", "windows-x86_64", "windows-arm64"})
        self.assertRegex(lock["manifest_sha256"], r"^[0-9a-f]{64}$")
        for target in lock["targets"].values():
            for component in target["components"].values():
                self.assertTrue(component["available"])
                self.assertRegex(component["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(component["url"].startswith("https://static.rust-lang.org/dist/" + lock["release_date"] + "/"))
                self.assertIn("1.98.0", component["url"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.tools = []
        for name in ("cargo", "rustc", "rust-analyzer"):
            path = self.root / name
            path.write_bytes(b"sentinel: do not execute")
            self.tools.append(VerifiedRustTool(path, hashlib.sha256(path.read_bytes()).hexdigest()))

    def check(self, environment=None, *, toolchain_root=None):
        return rust_runtime_environment(self.repo, cargo=self.tools[0], rustc=self.tools[1],
                                        analyzer=self.tools[2], cargo_home=self.root / "cargo-home",
                                        rustup_home=self.root / "rustup-home", environment=environment or {},
                                        toolchain_root=toolchain_root)

    def prepare_sysroot(self):
        root = self.root / "verified-toolchain"
        (root / "bin").mkdir(parents=True)
        tools = []
        for tool in self.tools:
            path = root / "bin" / tool.path.name
            path.write_bytes(tool.path.read_bytes())
            tools.append(VerifiedRustTool(path, tool.sha256))
        self.tools = tools
        source = root / "lib/rustlib/src/rust"
        (source / "library/core").mkdir(parents=True)
        return root, source

    def test_verified_sysroot_and_nested_source_config_are_checked_without_execution(self):
        root, source = self.prepare_sysroot()
        self.check(toolchain_root=root)
        for directory in (source / "library/core", source, root / "lib/rustlib", root):
            with self.subTest(directory=directory):
                config = directory / ".cargo/config.toml"
                config.parent.mkdir(parents=True, exist_ok=True)
                config.write_text('[build]\nrustc-wrapper="sysroot-execution-trap"\n')
                before = config.read_bytes()
                with self.assertRaisesRegex(RustRuntimeError, "Cargo configuration"):
                    self.check(toolchain_root=root)
                self.assertEqual(config.read_bytes(), before)
                config.unlink()
        config = source / "library/core/rust-analyzer.toml"
        config.write_text('[procMacro]\nenable=true\n')
        with self.assertRaisesRegex(RustRuntimeError, "analyzer configuration"):
            self.check(toolchain_root=root)
        config.unlink()
        config = source / "library/rust-toolchain"
        config.write_text("nightly\n")
        with self.assertRaisesRegex(RustRuntimeError, "toolchain selection"):
            self.check(toolchain_root=root)
        config.unlink()
        self.check(toolchain_root=root)

    def test_sysroot_context_cannot_be_inferred_from_foreign_tools_or_missing_source(self):
        root, source = self.prepare_sysroot()
        with self.assertRaisesRegex(RustRuntimeError, "sysroot configuration context"):
            self.check(toolchain_root=self.root)
        source.rename(source.parent / "removed-rust-src")
        with self.assertRaisesRegex(RustRuntimeError, "sysroot configuration context"):
            self.check(toolchain_root=root)

    def test_rustup_override_of_verified_sysroot_requires_review(self):
        root, source = self.prepare_sysroot()
        home = self.root / "rustup-home"
        home.mkdir()
        config = home / "settings.toml"
        config.write_text('[overrides]\n' + json.dumps(str(source / "library")) + '="nightly"\n')
        before = config.read_bytes()
        with self.assertRaisesRegex(RustRuntimeError, "repository override"):
            self.check(toolchain_root=root)
        self.assertEqual(config.read_bytes(), before)

    def test_returns_minimum_environment_without_executing_sentinels(self):
        result = self.check({"PATH": "/untrusted", "SECRET": "must_not_leak"})
        self.assertNotIn("SECRET", result)
        self.assertEqual(result["PATH"], str(self.root))
        self.assertEqual(result["RUSTC"], str(self.tools[1].path))
        self.assertEqual(result["CARGO_NET_OFFLINE"], "true")

    def test_cargo_home_proxies_cannot_override_verified_absolute_tools(self):
        directory = self.root / "cargo-home/bin"
        directory.mkdir(parents=True)
        for name in ("cargo", "rustc", "rustup", "rustfmt", "cargo.exe", "rustup.cmd"):
            path = directory / name
            path.write_bytes(b"foreign executable: never run")
            with self.subTest(name=name), self.assertRaisesRegex(RustRuntimeError, "unverified tool proxy"):
                self.check()
            self.assertEqual(path.read_bytes(), b"foreign executable: never run")
            path.unlink()

    def test_unrecorded_rustup_next_to_verified_binaries_is_rejected(self):
        for name in ("rustup", "rustup.exe", "rustup.cmd", "rustup.bat"):
            path = self.root / name
            path.write_bytes(b"download trap: never run")
            with self.subTest(name=name), self.assertRaisesRegex(RustRuntimeError, "unverified tool proxy"):
                self.check()
            path.unlink()

    def test_rejects_wrapper_preload_registry_and_toolchain_overrides(self):
        for name in ("RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "RUSTUP_TOOLCHAIN",
                     "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER", "LD_PRELOAD",
                     "DYLD_INSERT_LIBRARIES", "RUSTFLAGS", "CARGO_REGISTRIES_CRATES_IO_INDEX"):
            with self.subTest(name=name), self.assertRaises(RustRuntimeError):
                self.check({name: "sentinel"})

    def test_diagnostic_log_filter_is_not_forwarded_or_treated_as_execution_override(self):
        result = self.check({"RUST_LOG": "info,atlas=debug"})
        self.assertNotIn("RUST_LOG", result)
        with self.assertRaises(RustRuntimeError):
            self.check({"RUST_LOG": "info", "RUSTC_WRAPPER": "sentinel"})

    def test_rejects_project_ancestor_and_user_cargo_config(self):
        for directory in (self.repo / ".cargo", self.root / ".cargo", self.root / "cargo-home"):
            with self.subTest(directory=directory):
                directory.mkdir(parents=True, exist_ok=True)
                config = directory / "config.toml"
                config.write_text('[build]\nrustc-wrapper="sentinel"\n')
                with self.assertRaises(RustRuntimeError):
                    self.check()
                config.unlink()

    def test_toolchain_file_must_match_without_installing_anything(self):
        path = self.repo / "rust-toolchain.toml"
        path.write_text('[toolchain]\nchannel="nightly"\n')
        with self.assertRaises(RustRuntimeError):
            self.check()
        path.write_text('[toolchain]\nchannel="1.98.0"\n')
        self.check()

    def test_nested_workspace_configuration_is_checked_before_any_probe(self):
        member = self.repo / "ignored-or-untracked" / "member"
        member.mkdir(parents=True)
        vectors = {
            ".cargo/config": '[build]\nrustc-wrapper="sentinel"\n',
            ".cargo/config.toml": '[env]\nRUSTC_WRAPPER="sentinel"\n',
            "rust-toolchain": "nightly\n",
            "rust-toolchain.toml": '[toolchain]\nchannel="nightly"\n',
            "rust-analyzer.toml": '[cargo.buildScripts]\nenable=true\n',
        }
        for name, content in vectors.items():
            with self.subTest(name=name):
                config = member / name
                config.parent.mkdir(parents=True, exist_ok=True)
                config.write_text(content)
                before = config.read_bytes()
                with self.assertRaises(RustRuntimeError):
                    self.check()
                self.assertEqual(config.read_bytes(), before)
                for tool in self.tools:
                    self.assertEqual(tool.path.read_bytes(), b"sentinel: do not execute")
                config.unlink()
        (member / ".cargo/config.toml").write_text('[net]\noffline=true\n')
        (member / "rust-toolchain.toml").write_text('[toolchain]\nchannel="1.98.0"\n')
        self.check()

    def test_project_discovery_is_bounded_and_does_not_ignore_configuration(self):
        (self.repo / "first").mkdir()
        (self.repo / "second").mkdir()
        with patch("codebase_atlas.rust_runtime.MAX_PROJECT_ENTRIES", 1):
            with self.assertRaisesRegex(RustRuntimeError, "discovery limit"):
                self.check()
        self.assertEqual({p.name for p in self.repo.iterdir()}, {"first", "second"})

    def test_directory_aliases_and_ancestor_config_aliases_require_review(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "config.toml").write_text("")
        alias = self.repo / "member"
        alias.symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(RustRuntimeError, "directory alias"):
            self.check()
        alias.unlink()
        (self.root / ".cargo").symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(RustRuntimeError, "configuration is unsafe"):
            self.check()
        self.assertEqual((foreign / "config.toml").read_bytes(), b"")

    def test_server_config_cannot_reenable_project_execution(self):
        path = self.repo / "rust-analyzer.toml"
        path.write_text('[cargo.buildScripts]\nenable=true\n')
        with self.assertRaisesRegex(RustRuntimeError, "analyzer configuration"):
            self.check()
        path.unlink()
        home = self.root / "user-config"
        (home / "rust-analyzer").mkdir(parents=True)
        (home / "rust-analyzer/config.toml").write_text('[procMacro]\nenable=true\n')
        with self.assertRaisesRegex(RustRuntimeError, "analyzer configuration"):
            self.check({"XDG_CONFIG_HOME": str(home)})

    def test_bad_checksum_and_symlink_fail_before_probing(self):
        self.tools[0].path.write_bytes(b"tampered")
        with self.assertRaisesRegex(RustRuntimeError, "checksum mismatch"):
            self.check()
        target = self.root / "other"
        target.write_bytes(b"sentinel: do not execute")
        self.tools[0].path.unlink()
        self.tools[0].path.symlink_to(target)
        with self.assertRaises(RustRuntimeError):
            self.check()

    def test_repository_rustup_override_is_not_silently_ignored(self):
        home = self.root / "rustup-home"
        home.mkdir()
        # TOML quoted Windows paths need escaping too.
        root = str(self.repo).replace("\\", "\\\\")
        (home / "settings.toml").write_text('[overrides]\n"' + root + '"="stable"\n')
        with self.assertRaises(RustRuntimeError):
            self.check()

    def test_nested_member_rustup_override_requires_review(self):
        member = self.repo / "member"
        member.mkdir()
        home = self.root / "rustup-home"
        home.mkdir()
        config = home / "settings.toml"
        config.write_text('[overrides]\n' + json.dumps(str(member)) + '="stable"\n')
        before = config.read_bytes()
        with self.assertRaisesRegex(RustRuntimeError, "repository override"):
            self.check()
        self.assertEqual(config.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
