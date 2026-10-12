"""Mock-only native/source composition; no real state mutation or proc access."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rust_semantic_cleanup_policy import CleanupPolicy


class CleanupPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = 1
        self.os = SimpleNamespace(getpid=lambda: 9)
        self.subreaper = SimpleNamespace(pid=9,
                measure=Mock(side_effect=lambda _: dict(controller=9, subreaper=True)),
                report=Mock(return_value={'qualified': False}))
        self.group = SimpleNamespace(cancel_attempted=True,
                wait=SimpleNamespace(pid=10, terminal={'si_pid': 10}, reap_attempted=True, reaped=True),
                cancel_phase_receipt=Mock(return_value=dict(observer=10,
                    reap_attempted=False, reaped=False, identity_verified=True)))
        self.source = dict(source_sha256='a'*64, sole_waiter=True,
                           no_future_forks=True, no_other_adopter=True, no_escape=True)
        self.verify = Mock(side_effect=lambda: dict(self.source))
        p = patch('scripts.rust_semantic_cleanup_policy.NativeWaitState')
        self.native = p.start().return_value
        self.addCleanup(p.stop)
        self.native.measure.return_value = dict(observer=9, threads=1,
                                                sigchld_default=True, sa_no_cldwait=False)
        self.native.report.return_value = {'qualified': False, 'raw': [1]}

    def make(self):
        return CleanupPolicy(None, self.subreaper, self.group, self.verify,
                             'a'*64, 11, os_api=self.os, clock=lambda: self.now)

    def test_current_measurements_and_source_distinct_retained(self):
        policy = self.make()
        raw = policy()
        self.assertTrue(raw['sole_waiter'])
        self.assertEqual(self.subreaper.measure.call_count, 2)
        self.native.measure.assert_called_once()
        self.assertEqual(len(policy.records[0]['source_receipts']), 2)
        self.assertFalse(policy.report()['qualified'])
        report = policy.report()
        report['records'][0]['native_receipt']['raw'][0] = 8
        self.assertEqual(policy.records[0]['native_receipt']['raw'], [1])

    def test_missing_source_or_wrong_phase_prevents_native(self):
        for key in self.source:
            original = self.source[key]
            self.source[key] = 'b'*64 if key == 'source_sha256' else 1
            with self.assertRaises(ValueError):
                self.make()()
            self.source[key] = original
        for obj, key, bad in ((self.group, 'cancel_attempted', False),
                              (self.group.wait, 'reaped', 1),
                              (self.group.wait, 'terminal', None)):
            original = getattr(obj, key)
            setattr(obj, key, bad)
            with self.assertRaises(ValueError):
                self.make()()
            setattr(obj, key, original)
        self.subreaper.measure.assert_not_called()
        self.native.measure.assert_not_called()

    def test_native_state_not_boolean_assertion(self):
        for bad in ({}, dict(observer=9, threads=2, sigchld_default=True, sa_no_cldwait=False),
                    dict(observer=9, threads=1, sigchld_default=1, sa_no_cldwait=False)):
            self.native.measure.return_value = bad
            policy = self.make()
            with self.assertRaises(ValueError):
                policy()
            self.assertIn('native_receipt', policy.records[0])
            with self.assertRaises(RuntimeError):
                policy()

    def test_final_booleans_do_not_prove_cancellation_chronology(self):
        for receipt in (None, dict(observer=10, reap_attempted=True, reaped=True,
                                  identity_verified=True),
                        dict(observer=10, reap_attempted=False, reaped=False,
                             identity_verified=1)):
            self.group.cancel_phase_receipt.return_value = receipt
            with self.assertRaises(ValueError):
                self.make()()
        self.subreaper.measure.assert_not_called()

    def test_subreaper_post_or_source_post_changes_fail(self):
        self.subreaper.measure.side_effect = [dict(controller=9, subreaper=True),
                                              dict(controller=9, subreaper=False)]
        with self.assertRaises(ValueError):
            self.make()()
        self.subreaper.measure.side_effect = lambda _: dict(controller=9, subreaper=True)
        self.verify.side_effect = [dict(self.source), {**self.source, 'no_escape': False}]
        with self.assertRaises(ValueError):
            self.make()()

    def test_late_or_failed_native_keeps_partial_receipt(self):
        self.native.measure.side_effect = lambda: setattr(self, 'now', 1.6) or dict(
                observer=9, threads=1, sigchld_default=True, sa_no_cldwait=False)
        policy = self.make()
        with self.assertRaises(ValueError):
            policy()
        self.assertIn('native_receipt', policy.records[0])
        self.now = 1
        self.native.measure.side_effect = OSError(22, 'fake')
        policy = self.make()
        with self.assertRaises(OSError):
            policy()
        self.assertEqual(policy.records[0]['errno'], 22)

    def test_record_bound_and_backward_deadline(self):
        policy = self.make()
        policy.records = [{} for _ in range(4096)]
        with self.assertRaises(ValueError):
            policy()
        policy = self.make()
        self.now = 0
        with self.assertRaises(ValueError):
            policy()
        self.native.measure.assert_not_called()
