from pathlib import Path
import shutil
import tempfile
import unittest
import json
import hashlib
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

# Import before patching rust_project._load_index; otherwise the coordinator's
# module-level import can retain a mock and contaminate later full-suite tests.
from codebase_atlas import rust_mcp_refresh

from scripts.rust_lifecycle_integration import install_execution_sentinels, execution_sentinel_state, hostile_hook_check, git_audit_launch, active_config_change_check
from scripts import rust_lifecycle_qualification as qualification


class RustExecutionSentinelTests(unittest.TestCase):
    def test_active_config_change_requires_rejection_cleanup_and_preservation(self):
        for failure in (None, "accepted", "running", "wrapper"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary).resolve()
                repository = work / "project"
                repository.mkdir()
                (repository / ".codebase-atlas.toml").write_text("original")
                cargo_home = work / "cargo-home"
                cargo_home.mkdir()
                (work / "wrapper-positive-control").write_text("executed")
                process = MagicMock(pid=123)
                process.poll.return_value = None
                provider = SimpleNamespace(_process=process, running=True,
                                           runtime=SimpleNamespace(cargo_home=cargo_home))
                def query():
                    self.assertIn("rustc-wrapper", (cargo_home / "config.toml").read_text())
                    provider.running = failure == "running"
                    process.poll.return_value = None if provider.running else 0
                    if failure == "wrapper":
                        (work / "forbidden-wrapper").write_text("executed")
                    return {"status": "complete_exact" if failure == "accepted" else "unavailable",
                            "nodes": []}
                with patch("codebase_atlas.config.AtlasConfig.load", return_value=SimpleNamespace(
                        data_dir=work, repository=repository, project="fixture")), patch(
                        "codebase_atlas.rust_project._load_index", return_value=({"generation_id": "frozen"}, None)):
                    if failure:
                        with self.assertRaisesRegex(RuntimeError, "rejection failed"):
                            active_config_change_check(repository, work, SimpleNamespace(rust_provider=provider), query)
                    else:
                        result = active_config_change_check(repository, work, SimpleNamespace(rust_provider=provider), query)
                        self.assertTrue(result["analyzer_stopped"] and result["generation_preserved"])
                self.assertFalse((cargo_home / "config.toml").exists())
                self.assertEqual((repository / ".codebase-atlas.toml").read_text(), "original")

    def test_active_config_change_does_not_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary).resolve()
            repository = work / "project"
            repository.mkdir()
            (repository / ".codebase-atlas.toml").write_text("original")
            (work / "wrapper-positive-control").write_text("executed")
            cargo_home = work / "cargo-home"
            cargo_home.mkdir()
            path = cargo_home / "config.toml"
            path.write_text("foreign")
            process = MagicMock(pid=123)
            process.poll.return_value = None
            provider = SimpleNamespace(_process=process, running=True,
                                      runtime=SimpleNamespace(cargo_home=cargo_home))
            query = MagicMock()
            with patch("codebase_atlas.config.AtlasConfig.load", return_value=SimpleNamespace(
                    data_dir=work, repository=repository, project="fixture")), patch(
                    "codebase_atlas.rust_project._load_index", return_value=({"generation_id": "frozen"}, None)):
                with self.assertRaises(FileExistsError):
                    active_config_change_check(repository, work, SimpleNamespace(rust_provider=provider), query)
            query.assert_not_called()
            self.assertEqual(path.read_text(), "foreign")

    def test_windows_audit_none_executable_only_allows_exact_git_token(self):
        git = r"C:\Program Files\Git\cmd\git.exe"
        self.assertTrue(git_audit_launch(None, 'git -C "C:\\fixture" rev-parse HEAD', git))
        self.assertTrue(git_audit_launch(None, subprocess.list2cmdline([git, "rev-parse", "HEAD"]), git))
        self.assertTrue(git_audit_launch("git", ["git", "rev-parse", "HEAD"], git))
        for command in ('git-foreign rev-parse HEAD', 'git.exe-foreign rev-parse HEAD',
                        'cmd /c git rev-parse HEAD', 'python -c foreign',
                        r'C:\foreign\git.exe rev-parse HEAD'):
            self.assertFalse(git_audit_launch(None, command, git))
        self.assertFalse(git_audit_launch(None, "python foreign", None))

    def test_hostile_hooks_record_rejection_and_detect_forbidden_attempt(self):
        for forbidden in (False, True):
            with self.subTest(forbidden=forbidden), tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary).resolve()
                repository = work / "project"
                repository.mkdir()
                config_path = repository / ".codebase-atlas.toml"
                config_path.write_text("fixture")
                service = MagicMock()
                service.query.return_value = SimpleNamespace(status="unavailable", nodes=(), completeness={})
                coordinator = MagicMock()
                coordinator.refresh.return_value = {"status": "failed", "previous_generation_preserved": True}
                def enable(*args, **kwargs):
                    if forbidden:
                        subprocess.run([sys.executable, "-c", "raise SystemExit('must not execute')"], check=True)
                    return {"status": "blocked"}, 2
                with patch("codebase_atlas.config.AtlasConfig.load", return_value=SimpleNamespace(data_dir=work, repository=repository, project="fixture")), \
                        patch("codebase_atlas.rust_project.load_rust_service", return_value=service), \
                        patch("codebase_atlas.rust_project._load_index", return_value=({"generation_id":"frozen"}, None)), \
                        patch("codebase_atlas.rust_mcp_refresh.RustMcpRefreshCoordinator", return_value=coordinator), \
                        patch("codebase_atlas.config.diagnose", return_value=[{"name":"rust_toolchain","ok":False}]), \
                        patch("scripts.rust_lifecycle_integration.enable_project", side_effect=enable):
                    if forbidden:
                        with self.assertRaisesRegex(RuntimeError, "forbidden Python"):
                            hostile_hook_check(repository, work)
                    else:
                        result, code = hostile_hook_check(repository, work)
                        self.assertEqual(code, 0)
                        self.assertEqual([h["hook"] for h in result["hooks"]],
                                         ["enable", "doctor", "cold_query", "refresh"] * 2)
                        self.assertEqual([h["case"] for h in result["hooks"]],
                                         ["wrapper"] * 4 + ["rustup-download-entry"] * 4)
                service.close.assert_called_once()
                document = json.loads((work / "hostile-hooks.json").read_text())
                self.assertEqual(bool(document["forbidden_events"]), forbidden)
                self.assertTrue(document["config_unchanged"])
                self.assertFalse(document["wrapper_executed"])
                self.assertTrue(document["wrapper_positive_control"]["executed"])
                self.assertEqual(document["wrapper_positive_control"]["exit_code"], 0)
                self.assertTrue((work / "wrapper-positive-control").is_file())
                self.assertTrue(document["rustup_positive_control"]["executed"])
                self.assertFalse(document["rustup_download_entry_executed"])
                self.assertTrue((work / "rustup-positive-control").is_file())

    def test_existing_wrapper_is_not_overwritten_for_positive_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary).resolve()
            repository = work / "project"
            repository.mkdir()
            (repository / ".codebase-atlas.toml").write_text("fixture")
            import os
            wrapper = work / ("wrapper.cmd" if os.name == "nt" else "wrapper")
            wrapper.write_text("foreign fixture")
            with patch("codebase_atlas.config.AtlasConfig.load"), patch(
                "codebase_atlas.rust_project.load_rust_service"
            ), patch("codebase_atlas.rust_mcp_refresh.RustMcpRefreshCoordinator"), patch(
                "scripts.rust_lifecycle_integration.run_owned"
            ) as run:
                with self.assertRaises(FileExistsError):
                    hostile_hook_check(repository, work)
                run.assert_not_called()
            self.assertEqual(wrapper.read_text(), "foreign fixture")

    def test_missing_positive_marker_blocks_hooks_before_service_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary).resolve()
            repository = work / "project"
            repository.mkdir()
            (repository / ".codebase-atlas.toml").write_text("fixture")
            with patch("codebase_atlas.config.AtlasConfig.load"), patch(
                "codebase_atlas.rust_project.load_rust_service"
            ) as load, patch("scripts.rust_lifecycle_integration.run_owned",
                            return_value=SimpleNamespace(returncode=0)):
                with self.assertRaisesRegex(RuntimeError, "positive control failed"):
                    hostile_hook_check(repository, work)
                load.assert_not_called()

    def test_fixture_has_real_build_script_and_proc_macro_without_executing_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary).resolve()
            project = work / "project"
            source = Path(__file__).resolve().parents[1] / "tests/fixtures/rust-product/crate"
            shutil.copytree(source, project)
            original = (source / "src/lib.rs").read_bytes()
            install_execution_sentinels(project, work)
            self.assertIn("std::fs::write", (project / "build.rs").read_text())
            self.assertIn("proc-macro=true", (project / "sentinel-macro/Cargo.toml").read_text())
            self.assertIn("#[proc_macro_derive(Sentinel)]", (project / "sentinel-macro/src/lib.rs").read_text())
            self.assertIn("#[derive(atlas_sentinel_macro::Sentinel)]", (project / "src/lib.rs").read_text())
            self.assertFalse(any(execution_sentinel_state(work).values()))
            self.assertEqual((source / "src/lib.rs").read_bytes(), original)
            (work / "forbidden-proc-macro").write_text("detector positive control")
            self.assertTrue(execution_sentinel_state(work)["forbidden-proc-macro"])


class RustLifecycleQualificationTests(unittest.TestCase):
    def test_native_archive_selection_and_clean_identity_gate(self):
        for target, suffix in (("macos-arm64", ".tar.gz"), ("windows-arm64", ".zip")):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                archive = base / ("codebase-atlas-rust-syntax-" + qualification.scanner_lock()["version"]
                                  + "-" + target + suffix)
                archive.write_bytes(b"fixture")
                output = base / "report.json"
                def qualify(report, work, scanner, *, allow_network):
                    self.assertEqual(scanner, archive)
                    self.assertFalse(allow_network)
                    report["status"] = "passed"
                with patch.object(qualification, "validate_identity") as identity, \
                        patch.object(qualification, "qualify", side_effect=qualify):
                    self.assertEqual(qualification.main([
                        "--source-sha", "a" * 40, "--target", target,
                        "--directory", str(base), "--output", str(output)]), 0)
                self.assertTrue(identity.call_args.kwargs["require_clean"])
                document = json.loads(output.read_text())
                self.assertFalse(document["public_rust_enabled"])
                self.assertIn("OS-level network observation", document["not_proven"])

    def test_identity_failure_is_saved_and_does_not_acquire_or_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.json"
            argv = ["--source-sha", "a" * 40, "--target", "macos-arm64",
                    "--directory", temporary, "--output", str(output)]
            with patch.object(qualification, "validate_identity", side_effect=ValueError("wrong source")), \
                    patch.object(qualification, "qualify") as qualify:
                self.assertEqual(qualification.main(argv), 1)
                qualify.assert_not_called()
            document = json.loads(output.read_text())
            self.assertEqual(document["status"], "failed")
            self.assertEqual(document["error"]["message"], "wrong source")
            with self.assertRaises(FileExistsError):
                qualification.main(argv)
            self.assertEqual(json.loads(output.read_text()), document)

    def test_lifecycle_reuses_offline_archives_and_preserves_partial_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            scanner = base / "scanner.tar.gz"
            scanner.write_bytes(b"fixture")
            scanner.with_name(scanner.name + ".sha256").write_text("a" * 64 + "  " + scanner.name + "\n")
            report = {"target": "macos-arm64", "source_sha": "a" * 40, "status": "failed"}
            receipt = base / "receipt.json"
            document = {"root": str(base / "verified"), "target": "macos-arm64",
                        "tools": {"cargo": {"path": "bin/cargo", "sha256": "b" * 64}}}
            raw = json.dumps(document).encode()
            receipt.write_bytes(raw)
            def lifecycle(argv):
                work = Path(argv[argv.index("--work-dir") + 1])
                work.mkdir()
                (work / "results.json").write_text('[{"operation":"enable","exit_code":1}]')
                self.assertIn("--execution-sentinels", argv)
                paths = json.loads(Path(argv[argv.index("--archive-map") + 1]).read_text())
                self.assertEqual(paths, {"cargo": str(base / "cached.tar.xz")})
                raise RuntimeError("lifecycle failed")
            with patch.object(qualification, "install_toolchain", return_value={"root": str(base / "verified"), "receipt": str(receipt)}) as install, \
                    patch.object(qualification, "load_toolchain_receipt", return_value=document) as load, \
                    patch.object(qualification, "acquire_components", return_value={"cargo": base / "cached.tar.xz"}) as acquire, \
                    patch.object(qualification, "lifecycle_main", side_effect=lifecycle):
                with self.assertRaisesRegex(RuntimeError, "lifecycle failed"):
                    qualification.qualify(report, base, scanner, allow_network=True)
            self.assertTrue(install.call_args.kwargs["network_authorized"])
            self.assertFalse(acquire.call_args.kwargs["network_authorized"])
            self.assertEqual(report["operations"][0]["exit_code"], 1)
            self.assertEqual(report["status"], "failed")
            load.assert_called_once()
            self.assertEqual(report["verified_receipt"], document)
            self.assertEqual(report["verified_receipt_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(report["verified_receipt_raw_utf8"].encode(), raw)
            self.assertEqual(report["toolchain_source_lock"], qualification.release_lock())
            self.assertEqual(report["verified_executable_map"][str(base / "verified/bin/cargo")],
                             {"role": "cargo", "sha256": "b" * 64})

    def test_changed_receipt_fails_before_archive_acquisition_or_lifecycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            receipt = base / "receipt.json"
            receipt.write_text('{"unexpected":"replacement"}')
            report = {"target": "macos-arm64", "status": "failed"}
            with patch.object(qualification, "install_toolchain", return_value={"root": str(base), "receipt": str(receipt)}), \
                    patch.object(qualification, "load_toolchain_receipt", return_value={}), \
                    patch.object(qualification, "acquire_components") as acquire, \
                    patch.object(qualification, "lifecycle_main") as lifecycle:
                with self.assertRaisesRegex(ValueError, "receipt changed"):
                    qualification.qualify(report, base, base / "scanner", allow_network=False)
                acquire.assert_not_called()
                lifecycle.assert_not_called()
            self.assertNotIn("verified_receipt", report)
