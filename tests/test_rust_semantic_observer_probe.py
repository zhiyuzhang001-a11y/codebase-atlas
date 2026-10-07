"""Trace predicate regressions only; these tests do not run tracing or tools."""
import unittest
from pathlib import Path
import tempfile

from scripts.rust_semantic_observer_probe import TOKEN, capture_raw, final_gates, trace_contains_control


class ObserverPredicateTests(unittest.TestCase):
    def test_complete_successful_control(self):
        line = f'412 execve("/usr/bin/true", ["/usr/bin/true", "{TOKEN}"], ["PATH=/usr/bin:/bin"]) = 0'
        self.assertTrue(trace_contains_control(line))

    def test_failed_truncated_or_other_control_is_not_success(self):
        prefix = f'412 execve("/usr/bin/true", ["/usr/bin/true", "{TOKEN}"], [])'
        for trace in (prefix + ' = -1 EPERM (Operation not permitted)',
                      prefix.replace(TOKEN, TOKEN[:10] + '...') + ' = 0',
                      prefix.replace('/usr/bin/true', '/usr/bin/false') + ' = 0',
                      '', 'strace: ptrace denied'):
            self.assertFalse(trace_contains_control(trace))

    def test_final_exit_deadline_and_aggregate_gates(self):
        final_gates(19.9, 1024 * 1024)
        with self.assertRaises(TimeoutError):
            final_gates(20, 0)
        with self.assertRaises(ValueError):
            final_gates(0.1, 1024 * 1024 + 1)

    def test_failed_oversized_output_is_captured_with_one_aggregate_budget(self):
        with tempfile.TemporaryDirectory(prefix='atlas-observer-test-') as temporary:
            root = Path(temporary)
            (root / 'stderr').write_bytes(b'failure detail')
            (root / 'stdout').write_bytes(b's' * (600 * 1024))
            (root / 'exec.trace').write_bytes(b't' * (600 * 1024))
            result = {}
            total = capture_raw(root, result)
            self.assertGreater(total, 1024 * 1024)
            self.assertEqual(result['stderr'], 'failure detail')
            self.assertEqual(sum(len(result[name]) for name in ('stderr', 'stdout', 'exec.trace')),
                             1024 * 1024)
            self.assertGreater(result['raw_omitted_bytes']['exec.trace'], 0)


if __name__ == '__main__':
    unittest.main()
