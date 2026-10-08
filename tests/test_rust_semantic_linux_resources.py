"""Pure and mocked proc sampling tests; never spawn or inspect real processes."""
import unittest
from unittest.mock import patch

from scripts.rust_semantic_linux_resources import _read_at, proc_identity, rollup_rss, sample_admitted


def stat(pid=14, session=14, starttime=123, state='S'):
    fields = [state] + ['0'] * 21
    fields[1:4] = ['1', str(session), str(session)]
    fields[19] = str(starttime)
    return f'{pid} (name ) with spaces) {" ".join(fields)}\n'.encode()


ROLLUP = b'0000-ffff ---p [rollup]\nRss:  20 kB\nPss:  3 kB\nShared_Hugetlb:  4 kB\nPrivate_Hugetlb:  8 kB\n'


class ResourcesTests(unittest.TestCase):
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
