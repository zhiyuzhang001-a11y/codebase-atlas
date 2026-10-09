"""Injected Linux waitid/pidfd tests; no actual waits, processes or signals."""
import errno
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_observer_wait import ObserverWait


class ObserverWaitTests(unittest.TestCase):
    def setUp(self):
        platform = patch('scripts.rust_semantic_observer_wait.sys.platform', 'linux')
        platform.start()
        self.addCleanup(platform.stop)
        self.os = SimpleNamespace(waitid=Mock(return_value=None), getpid=lambda: 9,
                                  getuid=lambda: 1000,
                                  get_inheritable=Mock(return_value=False),
                                  P_PIDFD=3, WEXITED=4, WNOHANG=1, WNOWAIT=0x1000000)
        self.result = SimpleNamespace(si_pid=10, si_uid=1000, si_signo=17,
                                      si_code=1, si_status=0)

    def make(self):
        return ObserverWait(10, 30, self.os)

    def test_nonconsuming_terminal_then_explicit_fd_reap(self):
        observer = self.make()
        self.assertIsNone(observer.poll())
        self.os.waitid.assert_called_once_with(3, 30, 0x1000005)
        self.os.waitid.return_value = self.result
        result = observer.poll()
        result['si_pid'] = 99  # returned snapshot cannot mutate retained evidence
        self.assertEqual(observer.poll()['si_pid'], 10)
        self.assertFalse(observer.report()['observer_reaped'])
        observer.reap()
        self.os.waitid.assert_called_with(3, 30, 5)
        self.assertTrue(observer.report()['observer_reaped'])
        self.assertFalse(observer.report()['outer_cleanup_complete'])
        with self.assertRaises(RuntimeError):
            observer.poll()
        with self.assertRaises(RuntimeError):
            observer.reap()

    def test_never_consume_without_terminal_and_validate_input(self):
        observer = self.make()
        with self.assertRaises(RuntimeError):
            observer.reap()
        self.os.waitid.assert_not_called()
        for pid, fd in ((9, 30), (True, 30), (0, 30), (10, True), (10, 2), (10, 65536)):
            with self.assertRaises(ValueError):
                ObserverWait(pid, fd, self.os)
        with patch('scripts.rust_semantic_observer_wait.sys.platform', 'win32'):
            with self.assertRaises(ValueError):
                self.make()
        self.os.get_inheritable.return_value = True
        with self.assertRaises(ValueError):
            self.make()

    def test_unknown_pid_uid_stop_or_status_retains_raw_but_not_terminal(self):
        for key, bad in (('si_pid', 11), ('si_uid', 1001), ('si_signo', 5),
                         ('si_code', 4), ('si_status', 256), ('si_status', True)):
            result = SimpleNamespace(**vars(self.result))
            setattr(result, key, bad)
            self.os.waitid.return_value = result
            observer = self.make()
            with self.assertRaises(ValueError):
                observer.poll()
            self.assertIsNone(observer.terminal)
            self.assertEqual(observer.records[-1]['result'][key], bad)

    def test_kill_dump_signals_valid_but_not_control_success(self):
        for kind in (2, 3):
            self.result.si_code, self.result.si_status = kind, 9
            self.os.waitid.return_value = self.result
            observer = self.make()
            observer.poll()
            observer.reap()
            self.assertTrue(observer.reaped)
            self.assertFalse(observer.report()['qualified'])
        self.result.si_status = 0
        with self.assertRaises(ValueError):
            self.make().poll()

    def test_disappeared_or_changed_waitable_terminal_is_not_reap(self):
        for new in (None, SimpleNamespace(**{**vars(self.result), 'si_status': 1})):
            self.os.waitid.side_effect = [self.result, new]
            observer = self.make()
            observer.poll()
            with self.assertRaises(ValueError):
                observer.poll()
            self.assertFalse(observer.reaped)

    def test_ambiguous_consuming_call_is_never_retried(self):
        for new in (None, SimpleNamespace(**{**vars(self.result), 'si_status': 1}),
                    ChildProcessError(errno.ECHILD, 'mock')):
            self.os.waitid.side_effect = [self.result, new]
            observer = self.make()
            observer.poll()
            with self.assertRaises((ValueError, ChildProcessError)):
                observer.reap()
            self.assertTrue(observer.reap_attempted)
            self.assertFalse(observer.reaped)
            with self.assertRaises(RuntimeError):
                observer.reap()
            self.assertEqual(self.os.waitid.call_count, 2)
            self.os.waitid.reset_mock()
