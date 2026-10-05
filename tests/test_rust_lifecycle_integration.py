from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.rust_lifecycle_integration import install_execution_sentinels, execution_sentinel_state


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
