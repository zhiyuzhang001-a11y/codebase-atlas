"""Mock-only census: no native wait, signal, process or FD access."""
import errno
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_terminal_census import TerminalCensus


class TerminalCensusTests(unittest.TestCase):
    def setUp(self):
        platform = patch('scripts.rust_semantic_terminal_census.sys.platform', 'linux')
        platform.start()
        self.addCleanup(platform.stop)
        self.os = SimpleNamespace(getpid=lambda: 9, getuid=lambda: 1000,
                                  waitid=Mock(return_value=None), P_ALL=0,
                                  WEXITED=4, WNOHANG=1, WNOWAIT=0x1000000)
        self.observer = SimpleNamespace(pid=10, terminal={'si_pid': 10},
                                        reap_attempted=True, reaped=True)
        self.policy = dict(controller=9, threads=1, sigchld_default=True,
                           sa_no_cldwait=False, sole_waiter=True, subreaper=True,
                           observer_consumed=True, group_cancel_before_reap=True,
                           group_signal_retired=True, no_future_forks=True,
                           no_other_adopter=True, no_escape=True)
        self.verifier = Mock(side_effect=lambda: dict(self.policy))
        self.now = 2

    def make(self):
        return TerminalCensus(self.observer, self.verifier, 12,
                              os_api=self.os, clock=lambda: self.now)

    def test_pending_not_empty_and_exact_nonconsuming_flags(self):
        census = self.make()
        self.assertEqual(census.tick()['outcome'], 'pending')
        self.os.waitid.assert_called_once_with(0, 0, 0x41000005)
        self.assertFalse(census.report()['outer_cleanup_complete'])

    def test_echild_candidate_only_with_both_policy_checks(self):
        self.os.waitid.side_effect = ChildProcessError(errno.ECHILD, 'fake')
        census = self.make()
        row = census.tick()
        self.assertEqual(row['outcome'], 'candidate_no_children')
        self.assertEqual(row['wait_errno'], errno.ECHILD)
        self.assertEqual(len(row['verifications']), 2)
        self.assertFalse(census.report()['qualified'])

    def test_terminal_discovery_never_admits_or_consumes(self):
        self.os.waitid.return_value = SimpleNamespace(si_pid=11, si_uid=1000,
                                                      si_signo=17, si_code=2, si_status=9)
        census = self.make()
        for _ in range(2):
            self.assertEqual(census.tick()['outcome'], 'unadmitted_terminal')
        self.assertEqual(self.os.waitid.call_count, 2)

    def test_each_bad_policy_refuses_wait(self):
        for key, value in self.policy.items():
            original = value
            self.policy[key] = 1 if type(value) is bool else True
            with self.assertRaises(ValueError):
                self.make().tick()
            self.policy[key] = original
        self.os.waitid.assert_not_called()

    def test_observer_unconsumed_or_nonbool_refuses_wait(self):
        for key, bad in (('terminal', None), ('reaped', False), ('reaped', 1),
                         ('reap_attempted', False)):
            old = getattr(self.observer, key)
            setattr(self.observer, key, bad)
            with self.assertRaises(ValueError):
                self.make().tick()
            setattr(self.observer, key, old)
        self.os.waitid.assert_not_called()

    def test_postwait_bad_policy_retains_errno_and_latches(self):
        self.os.waitid.side_effect = ChildProcessError(errno.ECHILD, 'fake')
        self.verifier.side_effect = [dict(self.policy), {**self.policy, 'no_escape': False}]
        census = self.make()
        with self.assertRaises(ValueError):
            census.tick()
        self.assertEqual(census.records[0]['wait_errno'], errno.ECHILD)
        with self.assertRaises(RuntimeError):
            census.tick()
        self.os.waitid.assert_called_once()

    def test_other_wait_errors_never_empty(self):
        for code in (errno.EINTR, errno.EINVAL, errno.ESRCH):
            self.os.waitid.side_effect = OSError(code, 'fake')
            census = self.make()
            with self.assertRaises(OSError):
                census.tick()
            self.assertEqual(census.records[0]['wait_errno'], code)
            self.assertNotIn('outcome', census.records[0])

    def test_bad_siginfo_preserved_not_admitted(self):
        good = dict(si_pid=11, si_uid=1000, si_signo=17, si_code=2, si_status=9)
        for key, bad in (('si_pid', 10), ('si_pid', True), ('si_uid', 999),
                         ('si_signo', 1), ('si_code', 4), ('si_status', 65)):
            self.os.waitid.return_value = SimpleNamespace(**{**good, key: bad})
            census = self.make()
            with self.assertRaises(ValueError):
                census.tick()
            self.assertEqual(census.records[0]['result'][key], bad)

    def test_postwait_deadline_retains_result_without_success(self):
        def late(*args):
            self.now = 12
            return None
        self.os.waitid.side_effect = late
        census = self.make()
        with self.assertRaises(ValueError):
            census.tick()
        self.assertIn('result', census.records[0])
        self.assertNotIn('outcome', census.records[0])

    def test_clock_and_record_bounds_and_immutable_receipts(self):
        census = self.make()
        receipt = census.tick()
        receipt['verifications'][0]['sole_waiter'] = False
        report = census.report()
        report['records'][0]['verifications'][0]['sole_waiter'] = False
        self.assertTrue(census.records[0]['verifications'][0]['sole_waiter'])
        self.now = 1
        with self.assertRaises(ValueError):
            census.tick()
        self.now = 2
        census = self.make()
        census.records = [{} for _ in range(4096)]
        with self.assertRaises(ValueError):
            census.tick()
        for bad in (float('nan'), float('inf'), True, 12):
            self.now = bad
            with self.assertRaises(ValueError):
                self.make()
