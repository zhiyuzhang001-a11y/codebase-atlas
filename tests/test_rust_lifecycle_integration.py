from pathlib import Path
import shutil
import tempfile
import unittest
import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

# Import before patching rust_project._load_index; otherwise the coordinator's
# module-level import can retain a mock and contaminate later full-suite tests.
from codebase_atlas import rust_mcp_refresh

from scripts.rust_lifecycle_integration import install_execution_sentinels, execution_sentinel_state, hostile_hook_check, git_audit_launch
from scripts import rust_lifecycle_qualification as qualification


class RustExecutionSentinelTests(unittest.TestCase):
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
                                         ["enable", "doctor", "cold_query", "refresh"])
                service.close.assert_called_once()
                document = json.loads((work / "hostile-hooks.json").read_text())
                self.assertEqual(bool(document["forbidden_events"]), forbidden)
                self.assertTrue(document["config_unchanged"])
                self.assertFalse(document["wrapper_executed"])

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
            def lifecycle(argv):
                work = Path(argv[argv.index("--work-dir") + 1])
                work.mkdir()
                (work / "results.json").write_text('[{"operation":"enable","exit_code":1}]')
                self.assertIn("--execution-sentinels", argv)
                paths = json.loads(Path(argv[argv.index("--archive-map") + 1]).read_text())
                self.assertEqual(paths, {"cargo": str(base / "cached.tar.xz")})
                raise RuntimeError("lifecycle failed")
            with patch.object(qualification, "install_toolchain", return_value={"root": str(base / "verified")}) as install, \
                    patch.object(qualification, "acquire_components", return_value={"cargo": base / "cached.tar.xz"}) as acquire, \
                    patch.object(qualification, "lifecycle_main", side_effect=lifecycle):
                with self.assertRaisesRegex(RuntimeError, "lifecycle failed"):
                    qualification.qualify(report, base, scanner, allow_network=True)
            self.assertTrue(install.call_args.kwargs["network_authorized"])
            self.assertFalse(acquire.call_args.kwargs["network_authorized"])
            self.assertEqual(report["operations"][0]["exit_code"], 1)
            self.assertEqual(report["status"], "failed")
