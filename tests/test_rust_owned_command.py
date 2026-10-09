import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from codebase_atlas.rust_owned_command import run_owned


class RustOwnedCommandTests(unittest.TestCase):
    def test_capture_and_failed_exit_use_normal_run_contract(self):
        result = run_owned([sys.executable, "-c", "import sys; print('identity'); sys.stderr.write('detail'); sys.exit(2)"],
                           env=dict(os.environ), timeout=5, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout.strip(), "identity")
        self.assertEqual(result.stderr, "detail")
        with self.assertRaises(subprocess.CalledProcessError):
            run_owned([sys.executable, "-c", "raise SystemExit(2)"],
                      env=dict(os.environ), timeout=5, capture_output=True, check=True)

    def test_output_and_missing_environment_fail_closed(self):
        with self.assertRaises(ValueError):
            run_owned([sys.executable], timeout=1, capture_output=True)
        with self.assertRaisesRegex(ValueError, "capture limit"):
            run_owned([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 2097152)"],
                      env=dict(os.environ), timeout=5, capture_output=True)

    def worker_case(self, timeout):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "worker-heartbeat"
            worker = "import pathlib,time; p=pathlib.Path(" + repr(str(marker)) + ")\n"
            worker += "while True: p.write_text(str(time.time_ns())); time.sleep(0.02)"
            parent = "import subprocess,sys,time,pathlib\n"
            parent += "subprocess.Popen([sys.executable,'-c'," + repr(worker) + "])\n"
            parent += "while not pathlib.Path(" + repr(str(marker)) + ").exists(): time.sleep(0.01)\n"
            if timeout:
                parent += "time.sleep(30)\n"
            started = time.monotonic()
            argv = [sys.executable, "-c", parent]
            if timeout:
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_owned(argv, cwd=temporary, env=dict(os.environ), timeout=1, capture_output=True)
            else:
                self.assertEqual(run_owned(argv, cwd=temporary, env=dict(os.environ),
                                           timeout=5, capture_output=True).returncode, 0)
            self.assertLess(time.monotonic() - started, 15)
            self.assertTrue(marker.exists(), "worker did not start; no cleanup evidence")
            before = marker.read_bytes()
            time.sleep(0.15)
            self.assertEqual(marker.read_bytes(), before, "owned worker survived cleanup")

    def test_normal_parent_exit_reaps_remaining_worker(self):
        self.worker_case(False)

    def test_timeout_reaps_parent_and_worker(self):
        self.worker_case(True)
