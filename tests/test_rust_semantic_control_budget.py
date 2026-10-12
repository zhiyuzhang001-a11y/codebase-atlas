"""Pure accounting tests, no native control/pipe/signal execution."""
import hashlib
import unittest

from scripts.rust_semantic_control_budget import ControlBudget


class ControlBudgetTests(unittest.TestCase):
    def test_shared_active_and_nonrenewable_cleanup(self):
        budget = ControlBudget(100)
        self.assertEqual(budget.check_active(119), 1)
        with self.assertRaises(TimeoutError):
            budget.check_active(120)
        self.assertEqual(budget.begin_cleanup(121), 131)
        self.assertEqual(budget.begin_cleanup(129), 131)
        self.assertEqual(budget.check_cleanup(130), 1)
        with self.assertRaises(TimeoutError):
            budget.check_cleanup(131)
        with self.assertRaises(RuntimeError):
            budget.check_active(132)

    def test_invalid_time_and_backward_clock(self):
        for value in (True, None, -1, float('nan'), float('inf'), 2**41):
            with self.assertRaises(ValueError):
                ControlBudget(value)
        budget = ControlBudget(10)
        budget.check_active(11)
        with self.assertRaises(ValueError):
            budget.begin_cleanup(10)
        with self.assertRaises(RuntimeError):
            budget.check_cleanup(12)

    def test_one_output_limit_including_failure_drain(self):
        budget = ControlBudget(0)
        chunk = b'x' * 65536
        for _ in range(8):
            budget.feed('stdout', chunk)
            budget.feed('trace', chunk)
        self.assertEqual(budget.total_retained, 1024*1024)
        with self.assertRaises(OverflowError):
            budget.feed('stderr', b'ab')
        with self.assertRaises(RuntimeError):
            budget.check_active(.5)
        self.assertEqual(budget.begin_cleanup(1), 11)
        with self.assertRaises(OverflowError):
            budget.feed('stderr', b'cd')
        report = budget.report()
        self.assertEqual(report['seen'], 1024*1024+4)
        self.assertEqual(report['streams']['stderr']['omitted'], 4)
        self.assertEqual(report['streams']['stderr']['sha256'], hashlib.sha256(b'abcd').hexdigest())
        self.assertEqual(sum(len(item['retained']) for item in budget.records.values()), budget.LIMIT)
        with self.assertRaises(RuntimeError):
            budget.check_active(2)
        self.assertFalse(report['qualified'])
        self.assertFalse(report['outer_cleanup_complete'])

    def test_chunk_bounds_eof_and_report_snapshot(self):
        budget = ControlBudget(0)
        for chunk in (b'', b'x'*65537, bytearray(b'x'), 'x', None):
            with self.assertRaises(ValueError):
                budget.feed('stdout', chunk)
        with self.assertRaises(ValueError):
            budget.feed('unknown', b'x')
        budget.feed('stdout', b'hello')
        snapshot = budget.report()
        for name in budget.STREAMS:
            budget.eof(name)
        with self.assertRaises(ValueError):
            budget.eof('stdout')
        with self.assertRaises(ValueError):
            budget.feed('stdout', b'x')
        self.assertFalse(snapshot['all_eof'])
        self.assertTrue(budget.report()['all_eof'])
        self.assertFalse(budget.report()['outer_cleanup_complete'])
