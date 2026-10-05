from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from scripts.rust_linux_trace import bootstrap_environment, trace_summary, observe


class LinuxTraceTests(unittest.TestCase):
    def test_native_observer_runs_positive_control_before_lifecycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                path = Path(argv[argv.index("-o") + 1])
                text = '1 execve("/python", ["python"], []) = 0\n1 +++ exited with 0 +++\n'
                if "positive-control" in path.name:
                    text += '2 execve("/python", ["python", "-c", "pass"], []) = 0\n2 socket(AF_INET, SOCK_STREAM, 0) = 3\n'
                path.write_text(text)
                self.assertTrue(kwargs["capture_output"])
                self.assertNotIn("GITHUB_TOKEN", kwargs["env"])
                return subprocess.CompletedProcess(argv, 0, "fixture", "")
            with patch("scripts.rust_linux_trace.sys.platform", "linux"), \
                    patch("scripts.rust_linux_trace.shutil.which", return_value="/usr/bin/strace"), \
                    patch("scripts.rust_linux_trace.run_owned", side_effect=run):
                report = observe(["/python", "lifecycle.py"], cwd=base, directory=base / "raw")
            self.assertEqual(len(calls), 2)
            self.assertIn("-f", calls[0])
            self.assertIn("--seccomp-bpf", calls[0])
            self.assertEqual(report["status"], "observed_no_non_unix_socket_attempts")
            self.assertTrue((base / "raw/lifecycle.trace").is_file())

    def summary(self, text):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace"
            path.write_text(text)
            return trace_summary(path)

    def test_network_positive_control_and_raw_argv_are_detected(self):
        summary = self.summary('10 execve("/tool", ["tool", "--version"], []) = 0\n'
                               '10 socket(AF_INET, SOCK_STREAM, IPPROTO_IP) = 3\n'
                               '10 +++ exited with 0 +++\n')
        self.assertEqual(len(summary["non_unix_socket_attempts"]), 1)
        self.assertEqual(summary["exit_records"], 1)
        self.assertIn('"--version"', summary["execution_argv_raw"][0])

    def test_unix_socket_is_not_internet_and_unfinished_result_keeps_full_argv(self):
        summary = self.summary('10 execve("/tool", ["tool"], [] <unfinished ...>\n'
                               '10 <... execve resumed>) = 0\n'
                               '10 socket(AF_UNIX, SOCK_STREAM, 0) = 3\n')
        self.assertFalse(summary["non_unix_socket_attempts"])

    def test_missing_truncated_detached_and_io_uring_evidence_fail(self):
        for text in ('', 'socket(AF_INET, SOCK_STREAM, 0) = 3\n',
                     'execve("tool", ["tool", "long"...], []) = 0\n',
                     'execve("tool", ["tool"], []) = 0\nProcess detached\n',
                     'execve("tool", ["tool"], []) = 0\nio_uring_setup(1, {}) = 3\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.summary(text)

    def test_bootstrap_environment_excludes_ci_tokens_and_overrides(self):
        with patch.dict("os.environ", {"HOME":"/private", "PATH":"/bin", "GITHUB_TOKEN":"secret",
                                       "ACTIONS_RUNTIME_TOKEN":"secret", "RUSTC_WRAPPER":"foreign"}, clear=True):
            environment = bootstrap_environment()
        self.assertEqual(environment, {"HOME":"/private", "PATH":"/bin"})
