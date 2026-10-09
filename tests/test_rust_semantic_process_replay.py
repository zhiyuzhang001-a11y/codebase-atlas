"""Pure selected-event replay; no process, filesystem or tracer execution."""
import unittest

from scripts.rust_semantic_process_replay import replay, paired_events, absolute_cwd


def execrow(pid, result='0'):
    return f'{pid} execve("/trusted/tool", ["/trusted/tool", ""], ["LC_ALL=C"]) = {result}'


def exitrow(pid):
    return f'{pid} +++ exited with 0 +++'


class ProcessReplayTests(unittest.TestCase):
    def test_observed_vfork_spacing_with_interleaved_exec_result(self):
        records = [execrow(20), '20 vfork( <unfinished ...>',
                   '21 execve("/trusted/tool", ["/trusted/tool", ""], ["LC_ALL=C"] <unfinished ...>',
                   '20 <... vfork resumed>)              = 21',
                   '21 <... execve resumed>)             = 0', exitrow(21), exitrow(20)]
        result = replay(records, root_pid=20, initial_cwd='/owned')
        self.assertEqual(result['processes'][0]['child_pid'], 21)
        self.assertEqual(result['attempts'][1]['completion_index'], 4)
        for changed in ('20 vfork(unknown) = 21', '20 vfork(\t) = 21'):
            with self.assertRaises(ValueError):
                replay([execrow(20), changed, execrow(21), exitrow(21), exitrow(20)],
                       root_pid=20, initial_cwd='/owned')

    def test_creation_before_interleaved_child_exec_preserves_cwd(self):
        records = [execrow(20), '20 vfork(<unfinished ...>',
                   '21 chdir("/private/\\347\\233\\256\\345\\275\\225") = 0',
                   execrow(21), '20 <... vfork resumed>) = 21',
                   exitrow(21), execrow(20, '-1 ENOENT (No such file or directory)'), exitrow(20)]
        result = replay(records, root_pid=20, initial_cwd='/private/root')
        self.assertFalse(result['qualified'])
        self.assertEqual(result['terminal_pids'], [20, 21])
        self.assertEqual([a['cwd'] for a in result['attempts']],
                         ['/private/root', '/private/目录', '/private/root'])
        self.assertFalse(result['attempts'][-1]['launched'])
        self.assertEqual(result['attempts'][1]['start_index'], 3)

    def test_failed_chdir_and_creation_do_not_change_context(self):
        result = replay(['20 fork() = -1 EAGAIN (Resource temporarily unavailable)',
                         '20 chdir("/other") = -1 ENOENT (No such file or directory)',
                         execrow(20), exitrow(20)], root_pid=20, initial_cwd='/owned')
        self.assertEqual(result['processes'], [])
        self.assertEqual(result['attempts'][0]['cwd'], '/owned')

    def test_supported_clone_and_fd_exec_stays_unresolved(self):
        result = replay([execrow(20),
                         '20 clone(child_stack=NULL, flags=CLONE_CHILD_CLEARTID|CLONE_CHILD_SETTID|SIGCHLD, child_tidptr=0xab) = 21',
                         '21 execveat(3, "", ["/trusted/tool"], [], AT_EMPTY_PATH) = 0',
                         exitrow(21), exitrow(20)], root_pid=20, initial_cwd='/owned')
        self.assertEqual(result['attempts'][1]['fd'], 3)
        self.assertEqual(result['attempts'][1]['path'], '')

    def test_reject_unknown_or_shared_creation_formats(self):
        variants = ['20 clone(child_stack=NULL, flags=CLONE_FS|SIGCHLD) = 21',
                    '20 clone(child_stack=NULL, flags=CLONE_FILES|SIGCHLD) = 21',
                    '20 clone(child_stack=NULL, flags=CLONE_THREAD|SIGCHLD) = 21',
                    '20 clone(child_stack=NULL, flags=CLONE_NEWNS|SIGCHLD) = 21',
                    '20 clone(child_stack=NULL, flags=SIGCHLD|SIGCHLD) = 21',
                    '20 clone(child_stack=NULL, flags=SIGCHLD, unknown=CLONE_FS) = 21',
                    '20 clone3({flags=CLONE_VM|CLONE_VFORK, exit_signal=SIGCHLD}, 88) = 21',
                    '20 fork(unknown) = 21']
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                replay([execrow(20), raw, execrow(21), exitrow(21), exitrow(20)],
                       root_pid=20, initial_cwd='/owned')

    def test_missing_parent_exit_pid_reuse_and_pending_cwd_race_fail(self):
        variants = [[execrow(21), exitrow(21)], [execrow(20)],
                    [execrow(20), exitrow(20), execrow(20)],
                    [execrow(20), '20 fork() = 21', exitrow(21), '20 fork() = 21', exitrow(20)],
                    [execrow(20), '20 fork(<unfinished ...>', '20 chdir("/other") = 0',
                     '20 <... fork resumed>) = 21', exitrow(21), exitrow(20)],
                    [execrow(20), '20 fchdir(3) = 0', exitrow(20)],
                    [execrow(20), '20 unshare(CLONE_NEWNS) = -1 EPERM (Denied)', exitrow(20)]]
        for records in variants:
            with self.subTest(records=records), self.assertRaises(ValueError):
                replay(records, root_pid=20, initial_cwd='/owned')

    def test_pairing_missing_duplicate_wrong_and_unknown_results(self):
        for records in ([], ['20 fork(<unfinished ...>'], ['20 <... fork resumed>) = 21'],
                        ['20 fork(<unfinished ...>', '20 <... vfork resumed>) = 21'],
                        ['20 fork(<unfinished ...>', exitrow(20)], ['20 socket() = 3'],
                        ['20 fork() = ?'], ['x' * (1024 * 1024 + 1)], ['20 chdir("目录") = 0']):
            with self.assertRaises(ValueError):
                replay(records, root_pid=20, initial_cwd='/owned')
        with self.assertRaises(ValueError):
            paired_events(['20 +++ exited with 999 +++'])

    def test_cwd_input_and_root_bounds(self):
        for value in ('relative', '//owned', '/owned/../else', '/owned//child', '/owned\0'):
            with self.assertRaises(ValueError):
                absolute_cwd(value)
        for pid in (True, 0, -1, 2**31):
            with self.assertRaises(ValueError):
                replay([execrow(20), exitrow(20)], root_pid=pid, initial_cwd='/owned')


if __name__ == '__main__':
    unittest.main()
