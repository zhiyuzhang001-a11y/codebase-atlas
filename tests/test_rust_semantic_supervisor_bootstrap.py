"""Full dedicated bootstrap preparation with all native operations injected."""
import hashlib
import json
import stat
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.rust_semantic_supervisor_bootstrap import DedicatedBootstrap


class Entries:
    def __init__(self, rows, on_close=lambda: None):
        self.rows, self.on_close = rows, on_close
    def __enter__(self): return iter(self.rows)
    def __exit__(self, *unused): self.on_close()


class BootstrapTests(unittest.TestCase):
    def bootstrap(self):
        data = {4: b'# exact closed artifact\n', 5: b'frozen-true'}
        receipt = lambda raw: dict(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        manifest = dict(artifact=receipt(data[4]), true=receipt(data[5]), mode='normal')
        data[3] = json.dumps(manifest).encode('ascii')
        offsets = {fd: 0 for fd in data}
        enumeration = [False]
        def info(fd):
            if fd == 7 and not enumeration[0]: raise OSError(9, 'scanner closed')
            mode = stat.S_IFDIR | 0o700 if fd in (6, 7) else stat.S_IFREG | (0o500 if fd == 5 else 0o400)
            return SimpleNamespace(st_dev=1, st_ino=fd + 10, st_uid=1000,
                st_mode=mode, st_size=len(data.get(fd, b'')), st_nlink=1,
                st_mtime_ns=2, st_ctime_ns=3)
        def scan(path):
            if path == 6: return Entries([])
            self.assertEqual(path, '/proc/self/fd')
            enumeration[0] = True
            return Entries([SimpleNamespace(name=str(fd)) for fd in range(8)],
                           lambda: enumeration.__setitem__(0, False))
        def read(fd, limit):
            raw = data[fd]; offsets[fd] += len(raw); return raw
        native = SimpleNamespace(environ={'PATH': '/usr/bin:/bin', 'HOME': '/private', 'LC_ALL': 'C'},
            O_RDONLY=0, O_ACCMODE=3, O_NONBLOCK=2048, SEEK_CUR=1,
            getuid=lambda: 1000, geteuid=lambda: 1000, getgid=lambda: 1000, getegid=lambda: 1000,
            scandir=Mock(side_effect=scan), fstat=Mock(side_effect=info), read=Mock(side_effect=read),
            lseek=Mock(side_effect=lambda fd, *args: offsets[fd]), fchdir=Mock(),
            getcwd=Mock(return_value='/private'),
            close=Mock(),
            stat=Mock(return_value=info(6)))
        fcntl = SimpleNamespace(F_GETFL=3, F_GETFD=1, FD_CLOEXEC=1,
                               fcntl=Mock(side_effect=lambda fd, op: 2048 if op == 3 else 1))
        flags = SimpleNamespace(isolated=1, no_site=1, ignore_environment=1)
        system = SimpleNamespace(platform='linux', flags=flags, modules={})
        obj = DedicatedBootstrap(os_api=native, fcntl_api=fcntl, sys_api=system, clock=lambda: 1.)
        return obj, native, system, receipt(data[3]), data

    def test_complete_mock_preparation_never_loads_or_executes_source(self):
        obj, native, unused, receipt, data = self.bootstrap()
        raw, mode = obj._prepare_unreviewed(receipt)
        self.assertEqual((raw, mode), (data[4], 'normal'))
        native.fchdir.assert_called_once_with(6)
        self.assertEqual(native.read.call_count, 3)
        self.assertEqual([r['operation'] for r in obj.rows],
                         ['fd-inventory', 'read-held', 'read-held', 'read-held', 'actual-cwd', 'fd-inventory'])
        with self.assertRaises(RuntimeError): obj.run()
        with self.assertRaises(RuntimeError): obj._prepare_unreviewed(receipt)

    def test_environment_and_foreign_import_fail_before_fd_or_directory_mutation(self):
        for kind in ('env', 'site', 'foreign'):
            obj, native, system, receipt, unused = self.bootstrap()
            if kind == 'env': native.environ['LD_PRELOAD'] = 'foreign'
            if kind == 'site': system.flags.no_site = 0
            if kind == 'foreign': system.modules['scripts'] = object()
            with self.assertRaises(ValueError): obj._prepare_unreviewed(receipt)
            native.scandir.assert_not_called(); native.fchdir.assert_not_called()

    def test_wrong_hash_and_nonempty_cwd_never_change_directory(self):
        obj, native, unused, receipt, data = self.bootstrap()
        receipt['sha256'] = '0' * 64
        with self.assertRaises(ValueError): obj._prepare_unreviewed(receipt)
        native.fchdir.assert_not_called()
        obj, native, unused, receipt, data = self.bootstrap()
        original = native.scandir.side_effect
        native.scandir.side_effect = lambda path: Entries([object()]) if path == 6 else original(path)
        with self.assertRaises(ValueError): obj._prepare_unreviewed(receipt)
        native.fchdir.assert_not_called()

    def test_unknown_inherited_fd_never_closes_or_admits_it(self):
        obj, native, unused, receipt, data = self.bootstrap()
        original = native.scandir.side_effect
        def scan(path):
            entries = original(path)
            entries.rows.append(SimpleNamespace(name='8'))
            return entries
        native.scandir.side_effect = scan
        with self.assertRaises(ValueError): obj._prepare_unreviewed(receipt)
        native.read.assert_not_called(); native.fchdir.assert_not_called()

    def test_actual_cwd_mismatch_and_expired_deadline_fail(self):
        obj, native, unused, receipt, data = self.bootstrap()
        native.stat.return_value.st_ino = 999
        with self.assertRaises(ValueError): obj._prepare_unreviewed(receipt)
        obj, native, unused, receipt, data = self.bootstrap()
        ticks = iter([1., 21.])
        obj.clock = lambda: next(ticks)
        with self.assertRaises(TimeoutError): obj._prepare_unreviewed(receipt)
        native.scandir.assert_not_called()

    def test_loader_gate_stops_private_chain_before_retirement_or_control(self):
        obj, native, system, receipt, data = self.bootstrap()
        with self.assertRaisesRegex(RuntimeError, 'artifact evaluation gate'):
            obj._run_unreviewed_draft(receipt)
        native.close.assert_not_called()
        self.assertNotIn('scripts.rust_semantic_supervisor_entry', system.modules)

    def test_injected_composition_shares_clock_and_retires_fixed_inputs_first(self):
        obj, native, system, receipt, data = self.bootstrap()
        harness = SimpleNamespace(budget=SimpleNamespace(started=1., active_deadline=21.),
                                  account=Mock(), _run_unreviewed_draft=Mock(return_value={'qualified': False}))
        module = SimpleNamespace(NativeHarness=Mock(return_value=harness))
        def fake_load(raw):
            self.assertEqual(raw, data[4])
            system.modules['scripts.rust_semantic_supervisor_entry'] = module
        obj._load_review_pending = Mock(side_effect=fake_load)
        self.assertFalse(obj._run_unreviewed_draft(receipt)['qualified'])
        module.NativeHarness.assert_called_once_with(original_started=1.)
        self.assertEqual([c.args[0] for c in native.close.call_args_list], [3, 4, 6])
        harness._run_unreviewed_draft.assert_called_once_with(5, '/private', failure_mode='normal')
        harness.account.assert_called_once()

    def test_close_failure_keeps_other_retirements_but_never_starts_control(self):
        obj, native, system, receipt, data = self.bootstrap()
        obj._prepare_unreviewed(receipt)
        native.close.side_effect = [OSError(5, 'uncertain'), None, None]
        with self.assertRaises(ValueError): obj._retire_inputs_unreviewed()
        self.assertEqual([c.args[0] for c in native.close.call_args_list], [3, 4, 6])
        self.assertEqual(obj.retired, {3, 4, 6})
        with self.assertRaises(RuntimeError): obj._retire_inputs_unreviewed()
        self.assertEqual(native.close.call_count, 3)

    def test_replacement_input_is_not_closed(self):
        obj, native, system, receipt, data = self.bootstrap()
        obj._prepare_unreviewed(receipt)
        actual = obj.identity
        obj.identity = lambda fd: dict(actual(fd), inode=999) if fd == 4 else actual(fd)
        with self.assertRaises(ValueError): obj._retire_inputs_unreviewed()
        self.assertEqual([c.args[0] for c in native.close.call_args_list], [3, 6])

    def test_injected_clock_reset_never_retires_inputs_or_runs_control(self):
        obj, native, system, receipt, data = self.bootstrap()
        harness = SimpleNamespace(budget=SimpleNamespace(started=10., active_deadline=30.),
                                  account=Mock(), _run_unreviewed_draft=Mock())
        module = SimpleNamespace(NativeHarness=Mock(return_value=harness))
        obj._load_review_pending = Mock(side_effect=lambda raw: system.modules.update(
            {'scripts.rust_semantic_supervisor_entry': module}))
        with self.assertRaises(ValueError): obj._run_unreviewed_draft(receipt)
        native.close.assert_not_called(); harness._run_unreviewed_draft.assert_not_called()

    def test_injected_controller_death_uses_identical_entry_and_clock(self):
        obj, native, system, receipt, data = self.bootstrap()
        packet = json.loads(data[3]); packet['mode'] = 'controller-death'
        data[3] = json.dumps(packet).encode('ascii')
        receipt = dict(size=len(data[3]), sha256=hashlib.sha256(data[3]).hexdigest())
        harness = SimpleNamespace(budget=SimpleNamespace(started=1., active_deadline=21.),
                                  account=Mock(), _run_unreviewed_draft=Mock(return_value={'qualified': False}))
        module = SimpleNamespace(NativeHarness=Mock(return_value=harness))
        obj._load_review_pending = Mock(side_effect=lambda raw: system.modules.update(
            {'scripts.rust_semantic_supervisor_entry': module}))
        obj._run_unreviewed_draft(receipt)
        module.NativeHarness.assert_called_once_with(original_started=1.)
        harness._run_unreviewed_draft.assert_called_once_with(5, '/private', failure_mode='controller-death')


class RuntimeInventoryContractTests(unittest.TestCase):
    def test_inventory_is_fixed_read_only_and_never_a_control_entry(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    '.github/workflows/rust-runtime-safety.yml').read_text()
        inventory = workflow.split('  semantic-runtime-inventory:\n', 1)[1].split(
            '  semantic-observer-probe:\n', 1)[0]
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", inventory)
        self.assertIn("github.event.pull_request.head.ref == 'codex/rust-public-enablement'", inventory)
        self.assertIn('runs-on: ubuntu-24.04', inventory)
        self.assertIn('timeout-minutes: 2', inventory)
        self.assertIn('          ulimit -f 64\n', inventory)
        self.assertIn('qualified=false', inventory)
        self.assertIn('${{ github.event.pull_request.head.sha }}', inventory)
        self.assertIn('"$ImageOS" "$ImageVersion"', inventory)
        command = next(line.strip() for line in inventory.splitlines()
                       if line.strip().startswith('/usr/bin/env -i'))
        self.assertEqual(command, "/usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C "
            "/usr/bin/timeout --signal=KILL 20s /usr/bin/dpkg-query --no-pager --show "
            "--showformat='${binary:Package}\\t${Version}\\t${Architecture}\\t${db:Status-Status}\\n' "
            "python3.12-minimal libpython3.12-minimal libpython3.12-stdlib libc6 libffi8 "
            "libssl3t64 zlib1g libbz2-1.0 liblzma5 libexpat1 coreutils ubuntu-keyring "
            "dpkg gpgv bash tar >> semantic-runtime-packages.txt")
        actions = [line.strip() for line in inventory.splitlines() if 'uses:' in line]
        self.assertEqual(actions, ['uses: actions/upload-artifact@v7'])
        self.assertIn('if: always()', inventory)
        self.assertIn('if-no-files-found: error', inventory)


if __name__ == '__main__': unittest.main()
