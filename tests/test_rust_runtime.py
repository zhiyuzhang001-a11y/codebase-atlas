from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

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

    def check(self, environment=None):
        return rust_runtime_environment(self.repo, cargo=self.tools[0], rustc=self.tools[1],
                                        analyzer=self.tools[2], cargo_home=self.root / "cargo-home",
                                        rustup_home=self.root / "rustup-home", environment=environment or {})

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


if __name__ == "__main__":
    unittest.main()
