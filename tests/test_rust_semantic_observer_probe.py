"""Trace predicate regressions only; these tests do not run tracing or tools."""
import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch, Mock

from scripts.rust_semantic_observer_probe import (
    TOKEN, FD_TOKEN, ARGUMENTS, capture_raw, final_gates, trace_contains_control,
    control_argv_literal, trace_contains_extended_controls,
    RSS_TOKEN, resource_marker, admit_resource_controls, resource_sample_gate,
    load_resource_sampler, control_object_samples, binary_identity,
)


class ObserverPredicateTests(unittest.TestCase):
    def test_resource_marker_complete_bounded_distinct_pids(self):
        self.assertIsNone(resource_marker(b''))
        self.assertIsNone(resource_marker((RSS_TOKEN + ' 21 22').encode()))
        self.assertEqual(resource_marker((RSS_TOKEN + ' 21 22 3\n').encode()), (21, 22, 3))
        for raw in (b'unknown 21 22\n', (RSS_TOKEN + ' 21 21\n').encode(),
                    (RSS_TOKEN + ' 21 2147483648\n').encode(), b'x' * 257,
                    (RSS_TOKEN + ' 21 22\nextra\n').encode(),
                    (RSS_TOKEN + ' 21 22 2\n').encode(),
                    (RSS_TOKEN + ' 21 22 65536\n').encode()):
            with self.assertRaises(ValueError):
                resource_marker(raw)

    @patch('scripts.rust_semantic_observer_probe.os.close')
    @patch('scripts.rust_semantic_observer_probe.os.O_ACCMODE', 3, create=True)
    @patch('scripts.rust_semantic_observer_probe.os.O_PATH', 0o10000000, create=True)
    @patch('scripts.rust_semantic_observer_probe.os.open', return_value=100)
    @patch('scripts.rust_semantic_observer_probe.os.O_NOFOLLOW', 0, create=True)
    @patch('scripts.rust_semantic_observer_probe.os.O_DIRECTORY', 0, create=True)
    @patch('scripts.rust_semantic_observer_probe.os.O_CLOEXEC', 0, create=True)
    def test_owned_live_cwd_fd_identity_fail_closed(self, opened, closed):
        rows = [dict(pid=pid, ppid=pid-1, starttime=pid+200, pgrp=20, session=20, state='S')
                for pid in (20, 21, 22)]
        cwds = {pid: dict(path='/owned/目录', device=1, inode=2) for pid in (21, 22)}
        tool = dict(device=3, inode=4, bytes=5)
        cwd_stat = SimpleNamespace(st_dev=1, st_ino=2)
        fd_stat = SimpleNamespace(st_dev=3, st_ino=4, st_size=5)
        def run(values, path='/owned/目录', stats=None):
            module = SimpleNamespace(proc_identity=lambda raw: raw, _read_at=Mock(side_effect=values))
            with patch('scripts.rust_semantic_observer_probe.os.readlink', return_value=path), \
                 patch('scripts.rust_semantic_observer_probe.os.stat', side_effect=stats or [cwd_stat, fd_stat]*4):
                return control_object_samples(module, rows, cwds, 3, tool)
        good = [rows[1], b'flags:\t02100000\n', rows[1], b'flags:\t02100000\n',
                rows[2], b'flags:\t02100000\n', rows[2], b'flags:\t02100000\n']
        result = run(good)
        self.assertEqual([row['pid'] for row in result], [21, 22])
        self.assertTrue(all(len(row['checks']) == 2 for row in result))
        for values in ([dict(rows[1], starttime=999)] + good[1:],
                       [rows[1], b'flags:\t02\n'] + good[2:],
                       [rows[1], b'flags:\t010000000\n'] + good[2:],
                       [rows[1], b'flags:\t0\nflags:\t0\n'] + good[2:],
                       good[:2] + [dict(rows[1], state='Z')] + good[3:]):
            with self.assertRaises(ValueError):
                run(values)
        with self.assertRaises(ValueError):
            run(good, path='/foreign')
        with self.assertRaises(ValueError):
            run(good, stats=[cwd_stat, SimpleNamespace(st_dev=3, st_ino=99, st_size=5)])
        with self.assertRaises(PermissionError):
            run([PermissionError('denied')])
        with self.assertRaises(FileNotFoundError):
            run([FileNotFoundError('exited')])

    def test_binary_receipt_binds_open_fd_and_rejects_changes(self):
        import stat
        with tempfile.TemporaryDirectory(prefix='atlas-identity-test-') as temporary:
            path = Path(temporary) / 'tool'
            path.write_bytes(b'abc')
            identity = SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0, st_size=3,
                                       st_dev=4, st_ino=5, st_mtime_ns=6, st_ctime_ns=7)
            with patch('scripts.rust_semantic_observer_probe.os.fstat', side_effect=[identity, identity]):
                receipt = binary_identity(path)
                self.assertEqual((receipt['device'], receipt['inode'], receipt['bytes']), (4, 5, 3))
            for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'):
                changed = SimpleNamespace(**vars(identity))
                setattr(changed, key, 999)
                with patch('scripts.rust_semantic_observer_probe.os.fstat', side_effect=[identity, changed]):
                    with self.assertRaises(ValueError):
                        binary_identity(path)
            for key, value in (('st_uid', 999), ('st_mode', stat.S_IFREG | 0o777),
                               ('st_mode', stat.S_IFDIR | 0o755)):
                changed = SimpleNamespace(**vars(identity))
                setattr(changed, key, value)
                with patch('scripts.rust_semantic_observer_probe.os.fstat', return_value=changed):
                    with self.assertRaises(ValueError):
                        binary_identity(path)

    def test_control_rss_positive_and_limit_gate(self):
        mib = 1024 * 1024
        rows = [{'pid': pid, 'rss_bytes': size * mib} for pid, size in ((20, 1), (21, 20), (22, 36))]
        resource_sample_gate({'samples': rows, 'rss_bytes': 57 * mib}, 21, 22)
        for packet in ({'samples': rows, 'rss_bytes': 58 * mib},
                       {'samples': rows[:2], 'rss_bytes': 21 * mib},
                       {'samples': rows + rows[:1], 'rss_bytes': 58 * mib},
                       {'samples': [{'pid': pid, 'rss_bytes': size * mib} for pid, size in
                                    ((20, 1), (21, 15), (22, 36))], 'rss_bytes': 52 * mib},
                       {'samples': [{'pid': pid, 'rss_bytes': 64 * mib} for pid in (20, 21, 22)],
                        'rss_bytes': 192 * mib}):
            with self.assertRaises(ValueError):
                resource_sample_gate(packet, 21, 22)

    @patch('scripts.rust_semantic_observer_probe.os.getpid', return_value=10)
    @patch('scripts.rust_semantic_observer_probe.os.close')
    @patch('scripts.rust_semantic_observer_probe.os.open', return_value=100)
    @patch('scripts.rust_semantic_observer_probe.os.O_NOFOLLOW', 0, create=True)
    @patch('scripts.rust_semantic_observer_probe.os.O_DIRECTORY', 0, create=True)
    @patch('scripts.rust_semantic_observer_probe.os.O_CLOEXEC', 0, create=True)
    def test_resource_admission_kernel_identity_ancestry(self, opened, closed, getpid):
        rows = [dict(pid=pid, ppid=ppid, starttime=200 + pid, session=20, pgrp=20, state='S')
                for pid, ppid in ((20, 10), (21, 20), (22, 21))]
        def module(values):
            return SimpleNamespace(proc_identity=lambda raw: raw, _read_at=Mock(side_effect=values))
        self.assertEqual(admit_resource_controls(module(rows), 20, 21, 22), rows)
        self.assertEqual(closed.call_count, 3)
        for key, value in (('ppid', 99), ('starttime', 0), ('pgrp', 99), ('session', 99), ('state', 'Z')):
            changed = [dict(row) for row in rows]
            changed[2][key] = value
            with self.assertRaises(ValueError):
                admit_resource_controls(module(changed), 20, 21, 22)
        with self.assertRaises(ValueError):
            admit_resource_controls(module(rows), 20, 21, 21)
        with self.assertRaises(PermissionError):
            admit_resource_controls(module([PermissionError('denied')]), 20, 21, 22)

    def test_sampler_load_binds_executed_bytes_without_pyc(self):
        module, receipt = load_resource_sampler()
        self.assertEqual(len(receipt['sha256']), 64)
        self.assertGreater(receipt['bytes'], 0)
        self.assertTrue(callable(module.sample_admitted))

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

    def test_extended_controls_pair_interleaved_results_without_guessing(self):
        simple, normal, fd = self.extended_trace().splitlines()
        split = [simple.replace(') = 0', ' <unfinished ...>'),
                 '411 vfork( <unfinished ...>',
                 normal.replace(') = 0', ' <unfinished ...>'),
                 '411 <... vfork resumed>) = 413',
                 '413 <... execve resumed>) = 0',
                 '412 <... execve resumed>) = 0', fd]
        self.assertTrue(trace_contains_extended_controls('\n'.join(split)))
        for changed in (split[:-2] + [fd],
                        split[:5] + ['412 <... execveat resumed>) = 0', fd],
                        split[:5] + ['412 <... execve resumed>) = -1 EPERM (Denied)', fd],
                        split + ['999 <... execve resumed>) = 0'],
                        split + [fd],
                        split + [simple.replace(') = 0', ') = -1 EPERM (Denied)')]):
            self.assertFalse(trace_contains_extended_controls('\n'.join(changed)))

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
