"""Integrated owner/budget/outer protocol tests; ALL native/transport calls fake."""
import errno
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scripts.rust_semantic_control_budget import ControlBudget
from scripts.rust_semantic_control_journal import ControlJournal, FRAME, MAGIC as JMAGIC
from scripts.rust_semantic_control_owner import ControlOwner, encode_terminals, observer_terminal_summary
from scripts.rust_semantic_outer_control import OuterControl


class ControlOwnerTests(unittest.TestCase):
    def harness(self, consumed=(11, 12, 13), requested=False):
        self.now = 1.
        self.budget = ControlBudget(0)
        self.observer = SimpleNamespace(pid=10, terminal={'si_pid': 10},
                                        reap_attempted=False, reaped=False,
                                        report=Mock(return_value={}))
        self.observer.poll = Mock(return_value={'si_pid': 10})
        def reap():
            self.observer.reap_attempted = self.observer.reaped = True
            return {'si_pid': 10}
        self.observer.reap = Mock(side_effect=reap)
        decoded = ControlJournal(9, 10)
        for seq, pid in enumerate((11, 12, 13), 1):
            decoded.feed(FRAME.pack(JMAGIC, seq, pid, 10 if seq == 1 else 11,
                                    pid + 100, 10, 10))
        decoded.eof()
        self.journal = SimpleNamespace(role='reader', active_deadline=20., ended=True,
            failed=False, journal=decoded, begin_cleanup=Mock(), tick=Mock(),
            report=Mock(side_effect=decoded.report))
        self.ipc = SimpleNamespace(role='outer', active_deadline=20.,
            poll_cleanup_request=Mock(return_value=requested),
            deliver_cleanup_deadline=Mock(), report=Mock(return_value={}))
        self.group = SimpleNamespace(wait=self.observer, cancel_owned_group=Mock(),
            group_absent_after_reap=Mock(return_value=True), report=Mock(return_value={}))
        self.raw = encode_terminals(10, 'a'*40,
            [dict(pid=pid, starttime=pid+100, status=0) for pid in consumed])
        self.reader = Mock(side_effect=lambda: self.raw)
        self.census = SimpleNamespace(tick=Mock(return_value={'outcome': 'candidate_no_children'}))
        self.census_factory = Mock(return_value=self.census)
        self.binding = dict(pid=13, starttime=113, session=10, pgrp=10)
        self.admission = SimpleNamespace(admit=Mock(side_effect=lambda identity: dict(self.binding)),
            close=Mock(), report=Mock(side_effect=lambda: {'binding': dict(self.binding)}))
        self.admission_factory = Mock(return_value=self.admission)
        self.waiter = SimpleNamespace(tick=Mock(return_value=True), report=Mock(side_effect=lambda: {
            'known_lifetime_drained': True, 'binding': dict(self.binding),
            'wait': {'observer_reaped': True, 'reap_attempted': True, 'terminal': {'si_pid': 13}}}))
        self.wait_factory = Mock(return_value=self.waiter)
        self.owner = ControlOwner(self.observer, 'a'*40, self.budget, self.journal,
            self.ipc, self.group, self.reader, self.census_factory,
            self.admission_factory, self.wait_factory, lambda: self.now)
        def tick(clock, cleanup):
            if cleanup:
                for name in self.budget.STREAMS:
                    if not self.budget.records[name]['eof']:
                        self.budget.eof(name)
        self.pipes = SimpleNamespace(tick=Mock(side_effect=tick), report=Mock(return_value={}))
        self.outer = OuterControl(self.budget, self.pipes, self.observer, self.owner,
                                   lambda: self.now, self.sleep)
        return self.outer

    def sleep(self, delay): self.now += delay

    def test_whole_normal_owner_reconciles_consumed_terminals_before_final_census(self):
        result = self.harness().run()
        self.assertTrue(result['cleanup_protocol_complete'])
        self.assertFalse(result['qualified'])
        self.journal.begin_cleanup.assert_called_once_with(11.)
        self.observer.reap.assert_called_once()
        self.census.tick.assert_called_once()
        self.admission_factory.assert_not_called()
        self.assertEqual(set(result['owner']['observer_consumed_terminals']), {11, 12, 13})
        result['owner']['observer_consumed_terminals'][11]['status'] = 9
        self.assertEqual(self.owner.report()['observer_consumed_terminals'][11]['status'], 0)

    def test_requested_cleanup_shares_deadline_then_reconciles(self):
        self.harness(requested=True).run()
        self.ipc.deliver_cleanup_deadline.assert_called_once_with(11.)
        self.journal.begin_cleanup.assert_called_once_with(11.)
        self.group.cancel_owned_group.assert_called_once()

    def test_echild_without_missing_consuming_terminal_is_not_complete(self):
        result = self.harness(consumed=(11, 12)).run()
        self.assertFalse(result['cleanup_protocol_complete'])
        self.assertTrue(self.owner.failed)
        self.admission_factory.assert_not_called()
        self.group.group_absent_after_reap.assert_not_called()

    def test_known_adopted_terminal_is_admitted_then_exactly_drained_and_reconciled(self):
        self.harness(consumed=(11, 12))
        self.census.tick.side_effect = [dict(outcome='unadmitted_terminal', result={'si_pid': 13}),
                                        dict(outcome='candidate_no_children')]
        result = self.outer.run()
        self.assertTrue(result['cleanup_protocol_complete'])
        self.admission.admit.assert_called_once_with(dict(pid=13, starttime=113, session=10, pgrp=10))
        self.waiter.tick.assert_called_once()
        self.assertEqual(set(result['owner']['adopted_terminals']), {13})
        self.owner.close_admissions(); self.owner.close_admissions()
        self.admission.close.assert_called_once()

    def test_unknown_or_already_consumed_census_never_opens_admission(self):
        for pid in (99, 11, True):
            self.harness(consumed=(11, 12))
            self.census.tick.return_value = dict(outcome='unadmitted_terminal', result={'si_pid': pid})
            result = self.outer.run()
            self.assertFalse(result['cleanup_protocol_complete'])
            self.admission_factory.assert_not_called()

    def test_malformed_foreign_partial_summary_latches_no_census(self):
        for kind in ('sha', 'observer', 'pid', 'start', 'truncated', 'overflow'):
            self.harness()
            if kind == 'sha': self.raw = encode_terminals(10, 'b'*40, [])
            if kind == 'observer': self.raw = encode_terminals(99, 'a'*40, [])
            if kind == 'pid': self.raw = encode_terminals(10, 'a'*40, [dict(pid=99, starttime=199, status=0)])
            if kind == 'start': self.raw = encode_terminals(10, 'a'*40, [dict(pid=11, starttime=999, status=0)])
            if kind == 'truncated': self.raw = self.raw[:-1]
            if kind == 'overflow': self.raw = b'x'*85
            result = self.outer.run()
            self.assertFalse(result['cleanup_protocol_complete'])
            self.census_factory.assert_not_called()
            self.reader.assert_called_once()
            self.assertTrue(self.owner.failed)

    def test_pending_summary_eof_not_terminal_and_no_budget_renewal(self):
        self.harness(); self.raw = None
        result = self.outer.run()
        self.assertFalse(result['cleanup_protocol_complete'])
        self.assertEqual(self.budget.cleanup_deadline, 11.)
        self.census_factory.assert_not_called()
        self.assertGreaterEqual(self.now, 11.)

    def test_admission_failure_retains_owned_fds_without_retry(self):
        self.harness(consumed=(11, 12))
        self.census.tick.return_value = dict(outcome='unadmitted_terminal', result={'si_pid': 13})
        self.admission.admit.side_effect = OSError(errno.EINTR, 'uncertain')
        result = self.outer.run()
        self.assertFalse(result['cleanup_protocol_complete'])
        self.admission.admit.assert_called_once()
        self.assertIn(13, self.owner.admissions)
        self.wait_factory.assert_not_called()
        self.owner.close_admissions()
        self.admission.close.assert_called_once()

    def test_failed_owner_transition_does_not_skip_observer_consumption(self):
        self.harness()
        self.journal.begin_cleanup.side_effect = OSError(errno.EBADF, 'bad pipe')
        result = self.outer.run()
        self.assertFalse(result['cleanup_protocol_complete'])
        self.observer.reap.assert_called_once()
        self.group.cancel_owned_group.assert_called_once()
        self.census_factory.assert_not_called()

    def test_uncertain_close_retired_once_with_error_evidence(self):
        self.harness(consumed=(11, 12))
        self.census.tick.return_value = dict(outcome='unadmitted_terminal', result={'si_pid': 13})
        self.admission.admit.side_effect = OSError(errno.EINTR, 'admit')
        self.outer.run()
        self.admission.close.side_effect = OSError(errno.EINTR, 'close')
        with self.assertRaises(OSError): self.owner.close_admissions()
        self.owner.close_admissions()
        self.admission.close.assert_called_once()
        report = self.owner.report()
        self.assertEqual(report['close_errors'][0]['errno'], errno.EINTR)
        self.assertIn(13, report['retired_admissions'])

    def test_terminal_encoder_rejects_stop_duplicate_and_boolean_words(self):
        for status in (True, 0x137f, 0x10000, 0x80):
            with self.assertRaises(ValueError):
                encode_terminals(10, 'a'*40, [dict(pid=11, starttime=111, status=status)])
        row = dict(pid=11, starttime=111, status=0)
        with self.assertRaises(ValueError): encode_terminals(10, 'a'*40, [row, row])

    def test_observer_summary_uses_consuming_terminals_not_early_exit_and_keeps_failure(self):
        result = {'stops': {'admitted': {11: 111, 12: 112},
                            'terminals': {11: 0}, 'early': {12: 0}},
                  'cleanup': {'terminals': {11: 0, 12: 9}}}
        self.assertEqual(observer_terminal_summary(10, 'a'*40, result),
            encode_terminals(10, 'a'*40, [dict(pid=11, starttime=111, status=0),
                                         dict(pid=12, starttime=112, status=9)]))
        result['cleanup'] = None
        self.assertEqual(observer_terminal_summary(10, 'a'*40, result),
            encode_terminals(10, 'a'*40, [dict(pid=11, starttime=111, status=0)]))
        for terminals in ({11: 9}, {99: 0}, {12: True}):
            result['cleanup'] = {'terminals': terminals}
            with self.assertRaises(ValueError): observer_terminal_summary(10, 'a'*40, result)

    def test_wrong_adopted_receipt_cannot_turn_true_into_consuming_terminal(self):
        for kind in ('binding', 'pid', 'reaped'):
            self.harness(consumed=(11, 12))
            self.census.tick.return_value = dict(outcome='unadmitted_terminal', result={'si_pid': 13})
            report = self.waiter.report()
            if kind == 'binding': report['binding']['starttime'] = 999
            if kind == 'pid': report['wait']['terminal']['si_pid'] = 99
            if kind == 'reaped': report['wait']['observer_reaped'] = False
            self.waiter.report.side_effect = None; self.waiter.report.return_value = report
            result = self.outer.run()
            self.assertFalse(result['cleanup_protocol_complete'])
            self.waiter.tick.assert_called_once()
            self.group.group_absent_after_reap.assert_not_called()


if __name__ == '__main__': unittest.main()
