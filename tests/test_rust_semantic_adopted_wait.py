"""Fake-only adopted drain protocol: no native wait, signal or proc inspection."""
import errno
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_adopted_wait import AdoptedWait


class AdoptedWaitTests(unittest.TestCase):
    def setUp(self):
        platform = patch('scripts.rust_semantic_observer_wait.sys.platform', 'linux')
        platform.start()
        self.addCleanup(platform.stop)
        self.os = SimpleNamespace(waitid=Mock(return_value=None), getpid=lambda: 9,
                                  getuid=lambda: 1000, get_inheritable=lambda fd: False,
                                  P_PIDFD=3, WEXITED=4, WNOHANG=1, WNOWAIT=0x1000000)
        self.signal = SimpleNamespace(pidfd_send_signal=Mock())
        self.observer = SimpleNamespace(pid=10, terminal={'si_pid': 10}, reap_attempted=False)
        self.binding = dict(pid=11, pidfd=30, uid=1000, ppid=9, session=10, pgrp=10,
                            tracer=0, starttime=123, proc_dev=4, proc_ino=5,
                            pidfd_dev=6, pidfd_ino=7)
        self.raw = {**self.binding, 'controller': 9, 'threads': 1,
                    'sigchld_default': True, 'sa_no_cldwait': False,
                    'sole_waiter': True, 'journal_admitted': True}
        self.verifier = Mock(side_effect=lambda: dict(self.raw))
        self.now = 2
        self.result = SimpleNamespace(si_pid=11, si_uid=1000, si_signo=17,
                                      si_code=2, si_status=9)

    def make(self):
        return AdoptedWait(self.observer, self.binding, self.verifier, 12,
                           os_api=self.os, signal_api=self.signal, clock=lambda: self.now)

    def test_running_kill_once_then_verified_consuming_wait(self):
        child = self.make()
        self.assertFalse(child.tick())
        self.assertFalse(child.tick())
        self.signal.pidfd_send_signal.assert_called_once_with(30, 9, None, 0)
        self.os.waitid.return_value = self.result
        self.assertTrue(child.tick())
        self.assertEqual(self.os.waitid.call_args_list[-2].args, (3, 30, 0x1000005))
        self.assertEqual(self.os.waitid.call_args_list[-1].args, (3, 30, 5))
        calls = self.os.waitid.call_count
        self.assertTrue(child.tick())
        self.assertEqual(self.os.waitid.call_count, calls)
        self.assertTrue(child.report()['known_lifetime_drained'])
        self.assertFalse(child.report()['outer_cleanup_complete'])

    def test_already_terminal_never_signals(self):
        self.os.waitid.return_value = self.result
        child = self.make()
        self.assertTrue(child.tick())
        self.signal.pidfd_send_signal.assert_not_called()

    def test_bad_admission_identity_wait_policy_or_observer_no_native_calls(self):
        for key, bad in (('starttime', 124), ('ppid', 8), ('pgrp', 12),
                         ('tracer', 10), ('pidfd_ino', 8), ('threads', 2),
                         ('sole_waiter', False), ('journal_admitted', False),
                         ('sigchld_default', 1)):
            original = self.raw[key]
            self.raw[key] = bad
            child = self.make()
            with self.assertRaises(ValueError):
                child.tick()
            self.assertTrue(child.failed)
            self.raw[key] = original
        for key, bad in (('terminal', None), ('reap_attempted', True)):
            original = getattr(self.observer, key)
            setattr(self.observer, key, bad)
            with self.assertRaises(ValueError):
                self.make().tick()
            setattr(self.observer, key, original)
        self.os.waitid.assert_not_called()
        self.signal.pidfd_send_signal.assert_not_called()

    def test_send_errors_not_exit_proof_or_repeated_but_allow_terminal_drain(self):
        for code in (errno.ESRCH, errno.EPERM, errno.EINTR):
            self.signal.pidfd_send_signal.reset_mock()
            self.signal.pidfd_send_signal.side_effect = OSError(code, 'fake')
            self.os.waitid.return_value = None
            child = self.make()
            self.assertFalse(child.tick())
            self.assertEqual(child.records[0]['signal_errno'], code)
            self.assertFalse(child.tick())
            self.signal.pidfd_send_signal.assert_called_once()
            self.os.waitid.return_value = self.result
            self.assertTrue(child.tick())

    def test_ambiguous_consumption_not_retried(self):
        for bad in (None, ChildProcessError(errno.ECHILD, 'fake'),
                    SimpleNamespace(**{**vars(self.result), 'si_pid': 12})):
            self.os.waitid.reset_mock()
            self.os.waitid.side_effect = [self.result, bad]
            child = self.make()
            with self.assertRaises((ValueError, ChildProcessError)):
                child.tick()
            self.assertTrue(child.wait.reap_attempted)
            self.assertFalse(child.complete)
            with self.assertRaises(RuntimeError):
                child.tick()
            self.assertEqual(self.os.waitid.call_count, 2)
        self.signal.pidfd_send_signal.assert_not_called()

    def test_deadline_includes_last_consuming_wait_and_verifier(self):
        child = self.make()
        self.verifier.side_effect = lambda: (setattr(self, 'now', 12) or dict(self.raw))
        with self.assertRaises(ValueError):
            child.tick()
        self.os.waitid.assert_not_called()
        self.now = 2
        self.verifier.side_effect = lambda: dict(self.raw)
        child = self.make()
        def wait(*args):
            if args[-1] == 5:
                self.now = 12
            return self.result
        self.os.waitid.side_effect = wait
        with self.assertRaises(ValueError):
            child.tick()
        self.assertTrue(child.wait.reaped)
        self.assertTrue(child.records[-1]['consumed'])
        self.assertFalse(child.complete)
        self.assertTrue(child.failed)

    def test_recheck_before_consume_and_report_snapshot(self):
        self.os.waitid.return_value = self.result
        def verify():
            raw = dict(self.raw)
            if self.verifier.call_count == 2:
                raw['pidfd_ino'] = 99
            return raw
        self.verifier.side_effect = verify
        child = self.make()
        with self.assertRaises(ValueError):
            child.tick()
        self.assertEqual(self.os.waitid.call_count, 1)
        self.assertFalse(child.wait.reap_attempted)
        report = child.report()
        report['records'][0]['verifications'][0]['pid'] = 99
        self.assertEqual(child.records[0]['verifications'][0]['pid'], 11)
        self.assertEqual(len(child.records[0]['verifications']), 2)

    def test_invalid_binding_or_renewed_deadline_rejected(self):
        for binding in (None, [], {**self.binding, 'starttime': 0},
                        {**self.binding, 'pid': 10}, {**self.binding, 'ppid': 8}):
            with self.assertRaises(ValueError):
                AdoptedWait(self.observer, binding, self.verifier, 12,
                            os_api=self.os, signal_api=self.signal, clock=lambda: 2)
        for now in (1, 12, float('nan'), float('inf')):
            self.now = now
            with self.assertRaises(ValueError):
                self.make()
        self.os.waitid.assert_not_called()
        self.signal.pidfd_send_signal.assert_not_called()
