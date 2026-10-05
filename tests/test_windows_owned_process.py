import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from codebase_atlas.windows_owned_process import WindowsOwnedProcess


@unittest.skipUnless(os.name == "nt", "native Windows Job requires Windows")
class NativeWindowsOwnedProcessTests(unittest.TestCase):
    def test_stdio_child_is_owned_and_reaped(self):
        process = WindowsOwnedProcess([sys.executable, "-c", "import sys; print(sys.stdin.readline().strip())"],
                                      cwd=Path.cwd(), env=dict(os.environ))
        try:
            process.stdin.write(b"atlas-owned\n")
            self.assertEqual(process.stdout.readline().strip(), b"atlas-owned")
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            process.close_owned_job(5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_job_retains_worker_ownership_after_parent_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            heartbeat = Path(temporary) / "heartbeat"
            worker = "import pathlib,time; p=pathlib.Path(" + repr(str(heartbeat)) + "); "
            worker += "\nwhile True: p.write_text(str(time.time_ns())); time.sleep(0.02)"
            parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(worker) + "])"
            process = WindowsOwnedProcess([sys.executable, "-c", parent], cwd=temporary, env=dict(os.environ))
            try:
                self.assertEqual(process.wait(timeout=5), 0)
                deadline = time.monotonic() + 5
                while not heartbeat.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(heartbeat.exists(), "worker never started")
                process.close_owned_job(5)
                before = heartbeat.read_bytes()
                time.sleep(0.1)
                self.assertEqual(heartbeat.read_bytes(), before)
            finally:
                process.close_owned_job(5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
