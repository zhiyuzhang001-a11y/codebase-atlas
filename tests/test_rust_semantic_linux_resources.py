"""Pure and mocked proc sampling tests; never spawn or inspect real processes."""
import unittest
from unittest.mock import patch

from scripts.rust_semantic_linux_resources import _read_at, proc_identity, rollup_rss, sample_admitted, audit_coverage


def stat(pid=14, session=14, starttime=123, state='S'):
    fields = [state] + ['0'] * 21
    fields[1:4] = ['1', str(session), str(session)]
    fields[19] = str(starttime)
    return f'{pid} (name ) with spaces) {" ".join(fields)}\n'.encode()


ROLLUP = b'0000-ffff ---p [rollup]\nRss:  20 kB\nPss:  3 kB\nShared_Hugetlb:  4 kB\nPrivate_Hugetlb:  8 kB\n'


class ResourcesTests(unittest.TestCase):
    def coverage_packet(self, pids=(14, 15), begin=0.1, end=0.11):
        return dict(start_seconds=begin, end_seconds=end, rss_bytes=20*len(pids),
                    samples=[dict(pid=pid, starttime=123, rss_bytes=20) for pid in pids])

    def test_coverage_requires_each_traced_lifetime_including_short_child(self):
        packet = self.coverage_packet()
        result = audit_coverage([14, 15], {14: 123, 15: 123}, [packet], 100)
        self.assertFalse(result['qualified'])
        self.assertEqual(result['sample_counts'], {14: 1, 15: 1})
        self.assertEqual(result['observed_aggregate_max_bytes'], 40)
        for admitted, packets in (({14: 123}, [packet]),
                                   ({14: 123, 15: 123}, [self.coverage_packet((14,))]),
                                   ({14: 123, 15: 124}, [packet])):
            with self.assertRaises(ValueError):
                audit_coverage([14, 15], admitted, packets, 100)

    def test_coverage_rejects_unknown_duplicate_zero_and_sum_limit(self):
        for field, value in (('pid', 99), ('pid', True), ('starttime', 124),
                             ('rss_bytes', 0), ('rss_bytes', -1), ('rss_bytes', True)):
            packet = self.coverage_packet()
            packet['samples'][1][field] = value
            with self.assertRaises(ValueError):
                audit_coverage([14, 15], {14: 123, 15: 123}, [packet], 100)
        packet = self.coverage_packet((14, 14))
        with self.assertRaises(ValueError):
            audit_coverage([14, 15], {14: 123, 15: 123}, [packet], 100)
        for limit, total in ((30, 40), (100, 41)):
            packet = self.coverage_packet()
            packet['rss_bytes'] = total
            with self.assertRaises(ValueError):
                audit_coverage([14, 15], {14: 123, 15: 123}, [packet], limit)

    def test_coverage_rejects_invalid_intervals_and_bounds(self):
        for begin, end in ((float('nan'), 0.1), (0.1, float('inf')), (-1, 0),
                           (0.2, 0.1), (0.1, 0.7), (True, 0.1)):
            with self.assertRaises(ValueError):
                audit_coverage([14, 15], {14: 123, 15: 123},
                               [self.coverage_packet(begin=begin, end=end)], 100)
        for begin in (0.1, 0.7):
            with self.assertRaises(ValueError):
                audit_coverage([14, 15], {14: 123, 15: 123},
                               [self.coverage_packet(), self.coverage_packet(begin=begin, end=begin+0.01)], 100)
        for pids, admitted, packets, limit in (([14, 14], {14: 123}, [self.coverage_packet()], 100),
                                              ([True], {True: 123}, [self.coverage_packet()], 100),
                                              ([14], {14: 123}, [], 100),
                                              ([14], {14: 123}, [self.coverage_packet((14,))]*16385, 100),
                                              ([14], {14: 123}, [self.coverage_packet((14,))], True)):
            with self.assertRaises(ValueError):
                audit_coverage(pids, admitted, packets, limit)

    @patch.multiple('scripts.rust_semantic_linux_resources.os', O_CLOEXEC=0,
                    O_NOFOLLOW=0, create=True)
    @patch('scripts.rust_semantic_linux_resources.os.close')
    @patch('scripts.rust_semantic_linux_resources.os.open', return_value=4)
    @patch('scripts.rust_semantic_linux_resources.os.read')
    def test_one_bounded_read_and_close_on_failure(self, read, opened, closed):
        read.return_value = b'abc'
        self.assertEqual(_read_at(3, 'stat', 4), b'abc')
        read.assert_called_once_with(4, 5)
        opened.assert_called_once_with('stat', 0, dir_fd=3)
        for failure in (b'', b'x' * 5, OSError('read failed')):
            read.side_effect = failure if isinstance(failure, OSError) else None
            read.return_value = failure
            with self.assertRaises((ValueError, OSError)):
                _read_at(3, 'stat', 4)
        self.assertEqual(closed.call_count, 4)

    def test_stat_field_offsets_and_parentheses(self):
        result = proc_identity(stat())
        self.assertEqual(result, dict(pid=14, ppid=1, pgrp=14, session=14,
                                      starttime=123, state='S'))

    def test_rollup_not_pss_and_hugetlb_included(self):
        self.assertEqual(rollup_rss(ROLLUP), 32 * 1024)
        for raw in (b'', ROLLUP + b'Rss: 20 kB\n',
                    ROLLUP.replace(b'Rss:  20 kB', b'Rss: 20 MB'),
                    ROLLUP.replace(b'Shared_Hugetlb:  4 kB\n', b''),
                    b'Rss: 0 kB\nShared_Hugetlb: 0 kB\nPrivate_Hugetlb: 0 kB\n',
                    b'x' * 65537):
            with self.assertRaises(ValueError):
                rollup_rss(raw)

    def test_invalid_stat_fails(self):
        for raw in (b'', b'14 (name) S 1', stat(state='?'),
                    stat(starttime=-1), stat(starttime=2**64), b'x' * 8193):
            with self.assertRaises(ValueError):
                proc_identity(raw)

    @patch('scripts.rust_semantic_linux_resources.sys.platform', 'linux')
    @patch.multiple('scripts.rust_semantic_linux_resources.os', O_DIRECTORY=0,
                    O_CLOEXEC=0, O_NOFOLLOW=0, create=True)
    @patch('scripts.rust_semantic_linux_resources.os.close')
    @patch('scripts.rust_semantic_linux_resources.os.open', return_value=3)
    @patch('scripts.rust_semantic_linux_resources._read_at')
    def test_tree_sum_and_identity_changes(self, read, opened, closed):
        read.side_effect = [stat(), ROLLUP, stat(), stat(pid=15), ROLLUP, stat(pid=15)]
        result = sample_admitted({14: 123, 15: 123}, 14)
        self.assertEqual(result['rss_bytes'], 64 * 1024)
        self.assertFalse(result['qualified'])
        self.assertEqual(closed.call_count, 2)
        for bad in (stat(starttime=124), stat(session=15), stat(state='Z')):
            read.side_effect = [stat(), ROLLUP, bad]
            with self.assertRaises(ValueError):
                sample_admitted({14: 123}, 14)
        read.side_effect = PermissionError('denied')
        with self.assertRaises(PermissionError):
            sample_admitted({14: 123}, 14)

    @patch('scripts.rust_semantic_linux_resources.sys.platform', 'linux')
    def test_invalid_admission_and_no_platform_skip(self):
        for identities in ({}, {True: 123}, {14: 0}, {14: -1}, {2**31: 1}):
            with self.assertRaises(ValueError):
                sample_admitted(identities, 14)
        with patch('scripts.rust_semantic_linux_resources.sys.platform', 'darwin'):
            with self.assertRaises(ValueError):
                sample_admitted({14: 123}, 14)
