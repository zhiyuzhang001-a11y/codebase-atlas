"""Trace predicate regressions only; these tests do not run tracing or tools."""
import unittest
from pathlib import Path
import tempfile

from scripts.rust_semantic_observer_probe import (
    TOKEN, FD_TOKEN, ARGUMENTS, capture_raw, final_gates, trace_contains_control,
    control_argv_literal, trace_contains_extended_controls,
)


class ObserverPredicateTests(unittest.TestCase):
    def extended_trace(self):
        simple = f'412 execve("/usr/bin/true", ["/usr/bin/true", "{TOKEN}"], []) = 0'
        normal = '413 execve("/usr/bin/true", ' + control_argv_literal(['/usr/bin/true', TOKEN] + ARGUMENTS) + ', []) = 0'
        fd = '414 execveat(3, "", ' + control_argv_literal(['/usr/bin/true', FD_TOKEN] + ARGUMENTS) + ', [], AT_EMPTY_PATH) = 0'
        return '\n'.join((simple, normal, fd))

    def test_extended_controls_require_both_complete_successes(self):
        trace = self.extended_trace()
        self.assertTrue(trace_contains_extended_controls(trace))
        for changed in (trace.replace('AT_EMPTY_PATH', '0'),
                        trace.replace('execveat(3, ""', 'execveat(3, "/usr/bin/true"'),
                        trace.replace(FD_TOKEN, 'unknown'),
                        trace.replace('x' * 4096, 'x' * 80 + '...'),
                        trace.replace(') = 0', ') = -1 ENOSYS (Function not implemented)'),
                        '\n'.join(trace.splitlines()[:2])):
            self.assertFalse(trace_contains_extended_controls(changed))

    def test_control_escaping_and_not_a_general_parser(self):
        self.assertEqual(control_argv_literal(['', 'a"b\\', '\n\t\r']),
                         '["", "a\\"b\\\\", "\\n\\t\\r"]')
        with self.assertRaises(ValueError):
            control_argv_literal(['非 ASCII'])

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
