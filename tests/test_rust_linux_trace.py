from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from scripts.rust_linux_trace import bootstrap_environment, trace_summary, observe, require_offline_metadata


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
                else:
                    text += '2 execve("/cargo", ["/cargo", "metadata", "--offline", "--no-deps", "--format-version", "1", "--manifest-path", "/project/Cargo.toml"], []) = 0\n'
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
            self.assertIn("trace=%process,%network,chdir,fchdir,io_uring_setup,io_uring_enter", calls[0])
            self.assertEqual(report["initial_cwd"], str(base))
            self.assertIn("child cwd reconstruction and admission", report["not_proven"])
            self.assertEqual(report["status"], "observed_no_non_unix_socket_attempts")
            self.assertEqual(report["offline_metadata_launches"], 1)
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
        self.assertTrue(summary["execution_results"][0]["launched"])

    def test_failed_and_interleaved_exec_attempts_are_not_successful_launches(self):
        summary = self.summary('10 execve("/rustup", ["rustup"], [] <unfinished ...>\n'
                               '11 execve("/cargo", ["cargo"], []) = 0\n'
                               '10 <... execve resumed>) = -1 ENOENT (No such file or directory)\n')
        self.assertEqual([entry["launched"] for entry in summary["execution_results"]],
                         [True, False])
        self.assertEqual(summary["execution_results"][1]["pid"], 10)

    def test_unresolved_or_unpaired_exec_results_fail_closed(self):
        for text in ('10 execve("/tool", ["tool"], [] <unfinished ...>\n',
                     '10 execve("/tool", ["tool"], []) = 0\n'
                     '11 <... execve resumed>) = 0\n',
                     '10 execve("/tool", ["tool"], [])\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.summary(text)

    def test_metadata_gate_checks_launched_argv_not_environment_or_failed_attempts(self):
        for text in (
                '1 execve("/cargo", ["cargo", "metadata", "--no-deps"], ["--offline"]) = 0\n',
                '1 execve("/cargo", ["cargo", "metadata", "--offline"], []) = 0\n',
                '1 execve("/cargo", ["cargo", "metadata", "--offline", "--no-deps"], []) = -1 ENOENT (No such file or directory)\n',
                '1 execve("/cargo", ["cargo", "--version"], []) = 0\n',
                '1 execveat(3, "cargo", ["cargo", "metadata", "--offline", "--no-deps"], [], 0) = 0\n',
                '1 execve("/cargo", ["cargo", "metadata", "\\\\x2d\\\\x2doffline", "--no-deps"], []) = 0\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                require_offline_metadata(self.summary(text))

    def test_metadata_gate_handles_interleaving_and_brackets_in_paths(self):
        summary = self.summary('1 execve("/bin/[tools]/cargo", ["/bin/[tools]/cargo", "metadata", "--offline", "--no-deps", "--format-version", "1", "--manifest-path", "/project/[x]/Cargo.toml"], [] <unfinished ...>\n'
                               '2 execve("/python", ["python"], []) = 0\n'
                               '1 <... execve resumed>) = 0\n')
        self.assertEqual(require_offline_metadata(summary), 1)

    def test_metadata_option_roles_fail_closed(self):
        valid = ["/cargo", "metadata", "--format-version", "1", "--no-deps",
                 "--all-features", "--manifest-path", "/project/Cargo.toml",
                 "--offline", "--filter-platform", "x86_64-unknown-linux-gnu"]
        def audit(argv):
            return require_offline_metadata(self.summary(
                '1 execve("/cargo", ' + json.dumps(argv) + ', []) = 0\n'))
        self.assertEqual(audit(valid), 1)
        invalid = [
            ["/cargo", "metadata", "--manifest-path", "--offline", "--no-deps"],
            ["/cargo", "metadata", "--", "--offline", "--no-deps"],
            ["cargo", *valid[1:]],
            [*valid, "--offline"], [*valid, "--locked"], [*valid, "@args"],
            [*valid, "--manifest-path", "/other/Cargo.toml"],
        ]
        for option, value in (("--format-version", "2"),
                              ("--manifest-path", "relative/Cargo.toml"),
                              ("--manifest-path", "/project/other.toml"),
                              ("--filter-platform", "/project/target.json")):
            changed = valid.copy()
            changed[changed.index(option) + 1] = value
            invalid.append(changed)
        for argv in invalid:
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                audit(argv)

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
