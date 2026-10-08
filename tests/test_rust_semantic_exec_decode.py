"""Pure trace decoding tests; never spawn a tool or authorize a build."""
import unittest

from scripts.rust_semantic_exec_decode import decode_record, pair_records


class ExecDecodeTests(unittest.TestCase):
    def test_ascii_escaping_empty_and_unicode_bytes(self):
        result = decode_record(r'14 execve("/tool", ["tool", "", "a\"b\\", "\n\t\r", "\346\226\207"], ["HOME=/owned"]) = 0')
        self.assertEqual(result['argv'], ['tool', '', 'a"b\\', '\n\t\r', '文'])
        self.assertEqual(result['environment'], ['HOME=/owned'])

    def test_fd_execveat_does_not_claim_path_identity(self):
        result = decode_record('15 execveat(3, "", ["tool"], [], AT_EMPTY_PATH) = 0')
        self.assertEqual((result['fd'], result['path'], result['flags']), (3, '', 'AT_EMPTY_PATH'))

    def test_failed_attempt_and_paired_prefix_are_only_decoded(self):
        self.assertEqual(decode_record('14 execve("/tool", ["tool"], []) = -1 ENOENT (No such file or directory)')['path'], '/tool')
        self.assertEqual(decode_record('14 execve("/tool", ["tool"], [] <unfinished ...>')['path'], '/tool')

    def test_reject_unknown_missing_and_truncated_fields(self):
        valid = '14 execve("/tool", ["tool"], []) = 0'
        for raw in (valid.replace('["tool"]', '["tool"...]'), valid + ' trailing',
                    valid.replace('[]', '0x1234'), valid.replace('"/tool"', '0x1234'),
                    valid.replace('"tool"', r'"\q"'), valid.replace('"tool"', r'"\000"'),
                    valid.replace('"tool"', r'"\377"'), valid.replace(') = 0', ''),
                    '15 execveat(3, "", ["tool"], [], UNKNOWN_FLAG) = 0'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_record(raw)

    def test_argument_and_raw_bounds(self):
        for raw in ('14 execve("/tool", ["' + 'a' * 65537 + '"], []) = 0',
                    '14 execve("/tool", [' + ','.join(['"x"'] * 4097) + '], []) = 0',
                    '14 execve(' + 'a' * (1024 * 1024 + 1),
                    '14' + ' ' * (1024 * 1024) + 'execve("/tool", ["tool"], []) = 0'):
            with self.assertRaises(ValueError):
                decode_record(raw)

    def test_pair_interleaved_results_and_preserve_failed_attempt(self):
        records = [
            '14 execve("/a", ["a", ""], [] <unfinished ...>',
            '15 execveat(3, "", ["b"], [], AT_EMPTY_PATH <unfinished ...>',
            '16 execve("/missing", ["missing"], []) = -1 ENOENT (No such file or directory)',
            '15 <... execveat resumed>) = 0',
            '14 <... execve resumed>) = 0',
        ]
        result = pair_records(records)
        self.assertEqual([entry['pid'] for entry in result], [16, 15, 14])
        self.assertEqual([entry['launched'] for entry in result], [False, True, True])
        self.assertEqual([(entry['start_index'], entry['completion_index']) for entry in result],
                         [(2, 2), (1, 3), (0, 4)])
        self.assertEqual(result[1]['fd'], 3)
        self.assertEqual(result[2]['argv'], ['a', ''])

    def test_pair_missing_duplicate_mismatch_and_unknown_fail_closed(self):
        prefix = '14 execve("/a", ["a"], [] <unfinished ...>'
        complete = '14 execve("/a", ["a"], []) = 0'
        for records in ([prefix], ['14 <... execve resumed>) = 0'],
                        [prefix, prefix], [prefix, complete],
                        [prefix, '14 execveat(3, "", ["a"], [], AT_EMPTY_PATH) = 0'],
                        [prefix, '14 <... execveat resumed>) = 0'],
                        [prefix, '14 <... execve resumed>) = ?'],
                        [prefix, '14 <... execve resumed>, ["extra"]) = 0'],
                        [complete + ' trailing'], ['14 +++ exited with 0 +++'],
                        [prefix, '14 <... execve resumed>) = 0', '14 <... execve resumed>) = 0']):
            with self.subTest(records=records), self.assertRaises(ValueError):
                pair_records(records)

    def test_pair_bounds_and_single_line_results(self):
        complete = '14 execve("/a", ["a"], []) = 0'
        self.assertEqual(pair_records([complete])[0]['result'], '0')
        for records in ([], [complete] * 4097, [None], 'not a list',
                        [' ' * (1024 * 1024 + 1)],
                        [' ' * (1024 * 1024)] * 17):
            with self.assertRaises(ValueError):
                pair_records(records)


if __name__ == '__main__':
    unittest.main()
