from pathlib import Path, PureWindowsPath, PurePosixPath
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import subprocess

from scripts.rust_linux_trace import bootstrap_environment, trace_summary, observe, require_offline_metadata, require_verified_tool_paths, cwd_execution_records


class LinuxTraceTests(unittest.TestCase):
    def tools(self):
        return {"/" + role: {"role": role, "sha256": "a" * 64}
                for role in ("cargo", "rustc", "rust-analyzer")}

    def test_native_observer_runs_positive_control_before_lifecycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            # Native command execution is mocked as Linux; its cwd must also
            # be Linux, independently of the host storing the fixture logs.
            linux_cwd = MagicMock(spec=Path)
            linux_cwd.resolve.return_value = PurePosixPath("/fixture")
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                path = Path(argv[argv.index("-o") + 1])
                text = '1 execve("/python", ["python"], []) = 0\n1 fork() = 2\n'
                if "positive-control" in path.name:
                    text += '2 execve("/python", ["python", "-c", "pass"], []) = 0\n2 socket(AF_INET, SOCK_STREAM, 0) = 3\n'
                else:
                    text += '2 execve("/cargo", ["/cargo", "metadata", "--offline", "--no-deps", "--format-version", "1", "--manifest-path", "/project/Cargo.toml"], []) = 0\n'
                    text += '1 fork() = 3\n3 execve("/rustc", ["/rustc", "--version"], []) = 0\n'
                    text += '1 fork() = 4\n4 execve("/rust-analyzer", ["/rust-analyzer"], []) = 0\n'
                text += '1 +++ exited with 0 +++\n'
                path.write_text(text)
                self.assertTrue(kwargs["capture_output"])
                self.assertIs(kwargs["cwd"], linux_cwd)
                self.assertNotIn("GITHUB_TOKEN", kwargs["env"])
                return subprocess.CompletedProcess(argv, 0, "fixture", "")
            with patch("scripts.rust_linux_trace.sys.platform", "linux"), \
                    patch("scripts.rust_linux_trace.shutil.which", return_value="/usr/bin/strace"), \
                    patch("scripts.rust_linux_trace.run_owned", side_effect=run):
                report = observe(["/python", "lifecycle.py"], cwd=linux_cwd, directory=base / "raw", verified_tools=self.tools())
            self.assertEqual(len(calls), 2)
            self.assertIn("-f", calls[0])
            self.assertNotIn("--seccomp-bpf", calls[0])
            self.assertIn("trace=%process,%network,chdir,fchdir,unshare,chroot,setns,pivot_root,io_uring_setup,io_uring_enter", calls[0])
            self.assertEqual(report["initial_cwd"], "/fixture")
            self.assertIn("cwd filesystem identity/context admission", report["not_proven"])
            self.assertEqual({x["cwd"] for x in report["lifecycle"]["execution_cwd_records"]}, {"/fixture"})
            self.assertEqual(report["status"], "observed_no_non_unix_socket_attempts")
            self.assertEqual(report["offline_metadata_launches"], 1)
            self.assertEqual(len(report["verified_rust_tool_launches"]), 3)
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

    def test_pointer_only_exec_at_thread_exit_cannot_be_inferred_as_success_or_failure(self):
        text = ('10 execve("/tool", ["tool"], []) = 0\n'
                '11 execve(0xab312ffd61b0, 0xab312ffd4da0, 0xab312ffc0fa0 <unfinished ...>\n'
                '11 +++ exited with 0 +++\n')
        with self.assertRaisesRegex(ValueError, "unresolved exec"):
            self.summary(text)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            cwd_execution_records(text, "/initial")

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

    def test_linux_trace_path_rules_do_not_depend_on_audit_host(self):
        summary = self.summary('1 execve("/tools/cargo", ["/tools/cargo", "metadata", "--offline", "--no-deps", "--format-version", "1", "--manifest-path", "/project/Cargo.toml"], []) = 0\n')
        self.assertFalse(PureWindowsPath("/tools/cargo").is_absolute())
        with patch("scripts.rust_linux_trace.Path", PureWindowsPath):
            self.assertEqual(require_offline_metadata(summary), 1)

    def test_verified_rust_paths_bind_every_role_and_reject_foreign_launches(self):
        text = ''.join(f'{pid} execve({json.dumps(path)}, [{json.dumps(path)}], []) = 0\n'
                       for pid, path in enumerate(self.tools(), 1))
        matched = require_verified_tool_paths(self.summary(text), self.tools())
        self.assertEqual({x["role"] for x in matched}, {"cargo", "rustc", "rust-analyzer"})
        negatives = [text.replace('"/cargo"', '"/foreign/cargo"'),
                     text.replace('["/cargo"]', '["cargo"]'),
                     text.replace('"/rustc"', '"/foreign/../rustc"'),
                     text + '4 execve("/rustup", ["/rustup"], []) = 0\n',
                     text.replace('3 execve("/rust-analyzer", ["/rust-analyzer"], []) = 0\n', ''),
                     text + '4 execveat(3, "cargo", ["cargo"], [], 0) = 0\n']
        for value in negatives:
            with self.subTest(value=value), self.assertRaises(ValueError):
                require_verified_tool_paths(self.summary(value), self.tools())
        failed = text + '4 execve("/rustup", ["/rustup"], []) = -1 ENOENT (No such file or directory)\n'
        self.assertEqual(len(require_verified_tool_paths(self.summary(failed), self.tools())), 3)
        for tools in ({}, {**self.tools(), "/rustup": {"role": "rustup", "sha256": "a" * 64}},
                      {**self.tools(), "/cargo": {"role": "cargo", "sha256": "bad"}}):
            with self.subTest(tools=tools), self.assertRaises(ValueError):
                require_verified_tool_paths(self.summary(text), tools)

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

    def test_cwd_inheritance_at_unfinished_vfork_not_parent_return(self):
        text = ('1 execve("/python", ["python"], []) = 0\n'
                '1 vfork( <unfinished ...>\n'
                '2 chdir("/project" <unfinished ...>\n'
                '2 <... chdir resumed>) = 0\n'
                '2 execve("/cargo", ["/cargo"], [] <unfinished ...>\n'
                '1 <... vfork resumed>) = 2\n'
                '2 <... execve resumed>) = 0\n'
                '1 execve("/other", ["/other"], []) = 0\n')
        records = cwd_execution_records(text, "/initial")
        self.assertEqual([(r["pid"],r["cwd"]) for r in records],
                         [(1,"/initial"),(2,"/project"),(1,"/initial")])

    def test_cwd_clone_fs_is_unshared_by_exec_and_failed_chdir_does_not_change_it(self):
        text = ('1 execve("/python", ["python"], []) = 0\n'
                '1 clone(flags=CLONE_FS|CLONE_VM) = 2\n'
                '2 execve("/tool", ["/tool"], []) = 0\n'
                '2 chdir("/private") = 0\n'
                '2 chdir("/missing") = -1 ENOENT (No such file or directory)\n'
                '2 execve("/tool", ["/tool"], []) = 0\n'
                '1 execve("/parent", ["/parent"], []) = 0\n')
        records = cwd_execution_records(text, "/initial")
        self.assertEqual([r["cwd"] for r in records],
                         ["/initial","/initial","/private","/initial"])

    def test_cwd_missing_parent_unsupported_fd_namespace_and_order_fail_closed(self):
        prefix = '1 execve("/python", ["python"], []) = 0\n'
        negatives = [
            '2 execve("/tool", ["/tool"], []) = 0\n',
            '1 fchdir(3) = 0\n', '1 chdir("relative") = 0\n',
            '1 chdir("/project/../other") = 0\n',
            '1 unshare(CLONE_FS) = 0\n', '1 chroot("/new") = 0\n',
            '1 setns(3, CLONE_NEWNS) = 0\n', '1 pivot_root("/new", "/old") = 0\n',
            '1 clone(flags=CLONE_NEWNS|SIGCHLD) = 2\n',
            '1 clone(flags=CLONE_FS|CLONE_VM) = 2\n2 chdir("/shared") = 0\n',
            '1 clone() = 2\n', '1 fork() = 1\n',
            '1 vfork( <unfinished ...>\n',
            '1 <... chdir resumed>) = 0\n',
            '1 chdir("/new"... ) = 0\n',
            '1 +++ exited with 0 +++\n1 execve("/tool", ["/tool"], []) = 0\n',
        ]
        for text in negatives:
            with self.subTest(text=text), self.assertRaises(ValueError):
                cwd_execution_records(prefix + text, "/initial")
        for initial in (r"C:\fixture", "relative", "/initial/../other"):
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                cwd_execution_records(prefix, initial)
