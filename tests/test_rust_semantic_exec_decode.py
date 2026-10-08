"""Pure trace decoding tests; never spawn a tool or authorize a build."""
import unittest

from scripts.rust_semantic_exec_decode import decode_record


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


if __name__ == '__main__':
    unittest.main()
