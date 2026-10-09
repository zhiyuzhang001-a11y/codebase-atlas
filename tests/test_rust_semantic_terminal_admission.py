"""Injected protocol tests only; actual proc identity checks tested separately."""
import errno
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_terminal_admission import TerminalAdmission


class TerminalAdmissionTests(unittest.TestCase):
    def setUp(self):
        for module in ('terminal_census', 'observer_wait'):
            p = patch('scripts.rust_semantic_' + module + '.sys.platform', 'linux')
            p.start()
            self.addCleanup(p.stop)
        self.native = SimpleNamespace(getpid=lambda: 9, getuid=lambda: 1000,
                waitid=Mock(return_value=SimpleNamespace(si_pid=11, si_uid=1000,
                              si_signo=17, si_code=2, si_status=9)),
                P_ALL=0, P_PIDFD=3, WEXITED=4, WNOHANG=1, WNOWAIT=0x1000000,
                O_RDONLY=0, O_DIRECTORY=0x10000, O_CLOEXEC=0x80000, O_NOFOLLOW=0x20000,
                open=Mock(return_value=20), pidfd_open=Mock(return_value=21),
                get_inheritable=Mock(return_value=False), close=Mock(),
                fstat=Mock(side_effect=lambda fd: SimpleNamespace(st_dev=4, st_ino=fd,
                     st_uid=1000, st_mode=stat.S_IFDIR if fd == 20 else stat.S_IFREG)))
        self.observer = SimpleNamespace(pid=10, terminal={'si_pid': 10},
                                        reap_attempted=True, reaped=True)
        self.policy = dict(controller=9, threads=1, sigchld_default=True,
                sa_no_cldwait=False, sole_waiter=True, subreaper=True,
                observer_consumed=True, group_cancel_before_reap=True,
                group_signal_retired=True, no_future_forks=True,
                no_other_adopter=True, no_escape=True)
        self.verifier = Mock(side_effect=lambda: dict(self.policy))
        self.journal = dict(pid=11, starttime=123, session=10, pgrp=10)
        self.now = 2
        p = patch('scripts.rust_semantic_terminal_admission.AdoptedIdentity')
        self.identity = p.start().return_value
        self.identity.report.return_value = {'qualified': False}
        self.addCleanup(p.stop)

    def make(self):
        return TerminalAdmission(self.observer, self.verifier, None, 12,
                                  os_api=self.native, clock=lambda: self.now)

    def test_matching_journal_identity_and_terminal_only(self):
        owner = self.make()
        binding = owner.admit(self.journal)
        self.assertEqual(binding['starttime'], 123)
        self.assertEqual(self.identity.verify.call_count, 2)
        self.assertEqual(self.native.waitid.call_args_list[0].args, (0, 0, 0x41000005))
        self.assertEqual(self.native.waitid.call_args_list[1].args, (3, 21, 0x1000005))
        self.assertFalse(owner.report()['outer_cleanup_complete'])
        owner.close()
        owner.close()
        self.assertEqual(self.native.close.call_count, 2)
        with self.assertRaises(RuntimeError):
            owner.admit(self.journal)

    def test_unknown_discovery_pending_or_echild_no_fd_open(self):
        for result in (None, SimpleNamespace(si_pid=12, si_uid=1000,
                                             si_signo=17, si_code=1, si_status=0)):
            self.native.waitid.return_value = result
            with self.assertRaises(ValueError):
                self.make().admit(self.journal)
        self.native.waitid.side_effect = ChildProcessError(errno.ECHILD, 'fake')
        with self.assertRaises(ValueError):
            self.make().admit(self.journal)
        self.native.open.assert_not_called()
        self.native.pidfd_open.assert_not_called()

    def test_malformed_journal_no_discovery(self):
        for bad in ({}, {**self.journal, 'pid': True}, {**self.journal, 'session': 12},
                    {**self.journal, 'starttime': 0}, {**self.journal, 'extra': 1}):
            with self.assertRaises(ValueError):
                self.make().admit(bad)
        self.native.waitid.assert_not_called()

    def test_pidfd_open_failure_retains_owned_directory(self):
        self.native.pidfd_open.side_effect = OSError(errno.ESRCH, 'fake')
        owner = self.make()
        with self.assertRaises(OSError):
            owner.admit(self.journal)
        self.assertEqual(owner.owned, {'proc_fd': 20})
        owner.close()
        self.native.close.assert_called_once_with(20)
        self.assertFalse(owner.admitted)

    def test_native_identity_failure_retains_both_handles(self):
        self.identity.verify.side_effect = ValueError('changed lifetime')
        owner = self.make()
        with self.assertRaises(ValueError):
            owner.admit(self.journal)
        self.assertEqual(owner.owned, {'proc_fd': 20, 'pidfd': 21})
        self.assertEqual(self.native.waitid.call_count, 1)
        owner.close()

    def test_rejected_created_fd_still_owned_and_closed_once(self):
        for fd in (0, 65536):
            self.native.open.return_value = fd
            self.native.close.reset_mock()
            owner = self.make()
            with self.assertRaises(ValueError):
                owner.admit(self.journal)
            self.assertEqual(owner.owned, {'proc_fd': fd})
            owner.close()
            owner.close()
            self.native.close.assert_called_once_with(fd)
        self.native.open.return_value = 20
        self.native.pidfd_open.return_value = 65536
        self.native.close.reset_mock()
        owner = self.make()
        with self.assertRaises(ValueError):
            owner.admit(self.journal)
        self.assertEqual(owner.owned, {'proc_fd': 20, 'pidfd': 65536})
        owner.close()
        owner.close()
        self.assertEqual([c.args[0] for c in self.native.close.call_args_list], [20, 65536])

    def test_changed_pidfd_terminal_never_admitted(self):
        good = self.native.waitid.return_value
        self.native.waitid.side_effect = [good, SimpleNamespace(si_pid=11, si_uid=1000,
                                            si_signo=17, si_code=2, si_status=15)]
        owner = self.make()
        with self.assertRaises(ValueError):
            owner.admit(self.journal)
        self.assertEqual(owner.records[0]['pidfd_terminal']['si_status'], 15)
        self.assertFalse(owner.admitted)
        owner.close()

    def test_close_failure_retired_once_and_both_attempted(self):
        owner = self.make()
        owner.admit(self.journal)
        self.native.close.side_effect = OSError(errno.EINTR, 'fake')
        with self.assertRaises(OSError):
            owner.close()
        owner.close()
        self.assertEqual(self.native.close.call_count, 2)
        self.assertEqual(owner.owned, {})
        self.assertEqual(owner.records[-1]['close_errno'], errno.EINTR)

    def test_packet_deadline_and_receipts(self):
        owner = self.make()
        self.identity.verify.side_effect = lambda: setattr(self, 'now', self.now + .3)
        with self.assertRaises(ValueError):
            owner.admit(self.journal)
        self.assertFalse(owner.admitted)
        report = owner.report()
        report['records'][0]['journal']['starttime'] = 999
        self.assertEqual(owner.records[0]['journal']['starttime'], 123)
        owner.close()
