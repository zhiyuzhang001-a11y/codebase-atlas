from pathlib import Path
import shutil
import tempfile
import unittest
import json
from unittest.mock import patch

from scripts.rust_lifecycle_integration import install_execution_sentinels, execution_sentinel_state
from scripts import rust_lifecycle_qualification as qualification


class RustExecutionSentinelTests(unittest.TestCase):
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
