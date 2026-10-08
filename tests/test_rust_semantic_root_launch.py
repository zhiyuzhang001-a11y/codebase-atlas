"""Preparation-only tests: all process/signal APIs mocked; never real fork."""
import ast
import errno
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_control_code import ROOT_SOURCE
from scripts.rust_semantic_root_launch import PYTHON, RootLauncher, prepare_root_argv


class RootLaunchTests(unittest.TestCase):
    def setUp(self):
        platform = patch('scripts.rust_semantic_root_launch.sys.platform', 'linux')
        platform.start()
        self.addCleanup(platform.stop)
        self.os = SimpleNamespace(getpid=Mock(return_value=10), getsid=Mock(return_value=10),
            getpgrp=Mock(return_value=10), getcwd=Mock(return_value='/owned/control'),
            fork=Mock(return_value=11), pidfd_open=Mock(return_value=30),
            get_inheritable=Mock(return_value=False), set_inheritable=Mock(),
            execve=Mock(), _exit=Mock(side_effect=SystemExit))
        self.signal = SimpleNamespace(SIGCHLD=17, SIG_DFL=0, signal=Mock())
        self.contract = {'observer': 10, 'threads': 1, 'sigchld_default': True,
                         'sa_no_cldwait': False, 'sole_waiter': True}
        self.verify = Mock(side_effect=lambda: dict(self.contract))
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': '/owned/home', 'LC_ALL': 'C'}
        self.launcher = RootLauncher(self.os, self.signal)
        self.source = ROOT_SOURCE.encode()

    def launch(self):
        return self.launcher.launch(self.source, 40, self.env, self.verify)

    def test_fixed_isolated_entry_syntax_and_receipt_without_evaluation(self):
        argv, receipt = prepare_root_argv(self.source, 40)
        self.assertEqual(argv[:4], [PYTHON, '-I', '-S', '-c'])
        parsed = ast.parse(argv[4])
        self.assertEqual(parsed.body[-1].value.func.id, 'fixed_root')
        self.assertEqual(receipt['true_fd'], 40)
        self.assertEqual(receipt['entry_bytes'], len(argv[4].encode()))
        self.assertEqual(len(receipt['root_source_sha256']), 64)
        for source, fd in ((b'', 40), (b'x'*16385, 40), (b'\xff', 40),
                           (self.source, True), (self.source, 2)):
            with self.subTest(fd=fd), self.assertRaises((ValueError, UnicodeError)):
                prepare_root_argv(source, fd)

    def test_owned_fresh_child_pidfd_and_one_attempt(self):
        self.assertEqual(self.launch(), (11, 30))
        self.os.pidfd_open.assert_called_once_with(11, 0)
        self.signal.signal.assert_called_once_with(17, 0)
        self.verify.assert_called_once_with()
        self.os.execve.assert_not_called()
        self.assertEqual(self.launcher.report()['receipt']['wait_contract'], self.contract)
        self.assertFalse(self.launcher.report()['qualified'])
        with self.assertRaises(RuntimeError):
            self.launch()
        self.os.fork.assert_called_once_with()

    def test_missing_or_invalid_wait_proof_fails_before_fork(self):
        with self.assertRaises(ValueError):
            self.launcher.launch(self.source, 40, self.env)
        self.os.fork.assert_not_called()
        for key, value in (('threads', 2), ('threads', True), ('observer', 12),
                           ('sigchld_default', False), ('sa_no_cldwait', True),
                           ('sole_waiter', False), ('sole_waiter', 1)):
            with self.subTest(key=key, value=value):
                self.setUp()
                self.contract[key] = value
                with self.assertRaises(ValueError):
                    self.launch()
                self.os.fork.assert_not_called()

    def test_bad_environment_session_or_capability_fails_before_fork(self):
        for failure in ('env', 'session', 'api'):
            with self.subTest(failure=failure):
                self.setUp()
                if failure == 'env':
                    self.env['RUSTFLAGS'] = 'foreign'
                elif failure == 'session':
                    self.os.getsid.return_value = 9
                else:
                    self.os.pidfd_open = None
                with self.assertRaises(ValueError):
                    self.launch()
                self.os.fork.assert_not_called()

    def test_child_exec_failure_cannot_continue_observer_logic(self):
        self.os.fork.return_value = 0
        self.os.execve.side_effect = OSError(errno.ENOENT, 'mock')
        with self.assertRaises(SystemExit):
            self.launch()
        self.os.set_inheritable.assert_called_once_with(40, True)
        argv = self.os.execve.call_args.args
        self.assertEqual(argv[0], PYTHON)
        self.assertEqual(argv[1][:4], [PYTHON, '-I', '-S', '-c'])
        self.assertEqual(argv[2], self.env)
        self.os._exit.assert_called_once_with(125)
        self.os.pidfd_open.assert_not_called()

    def test_postfork_failures_preserve_owned_identity_for_outer_cleanup(self):
        for failure in ('open', 'flags'):
            with self.subTest(failure=failure):
                self.setUp()
                if failure == 'open':
                    self.os.pidfd_open.side_effect = OSError(errno.ESRCH, 'mock')
                else:
                    self.os.get_inheritable.return_value = True
                with self.assertRaises((OSError, ValueError)):
                    self.launch()
                report = self.launcher.report()
                self.assertEqual(report['root'], 11)
                self.assertEqual(report['owned_bootstrap_pidfd'], None if failure == 'open' else 30)
                self.assertFalse(report['outer_cleanup_complete'])
                self.assertEqual(len(report['errors']), 1)
                self.os.fork.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
