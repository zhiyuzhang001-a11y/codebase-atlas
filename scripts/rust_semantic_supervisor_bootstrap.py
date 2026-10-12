"""Single dedicated C0-O5 bootstrap DRAFT; native entry remains closed.

Never import checkout scripts here. BEFORE this Python process starts, a
reviewed external launcher must authenticate its Python/loader/libc/stdlib/
native-extension installation and this bootstrap against independent assets,
pass only fixed descriptors 0..6 and a clean environment, and install timeout.
In-process byte checks below do not manufacture that external trust root.
Tests inject every filesystem/native operation; do not run this draft.
"""
import hashlib
import json
import os
import stat
import sys
import time

# Filled only by exact independently approved execution-card assets, never by
# CLI arguments, environment variables, caller booleans or self-made receipts.
APPROVED_MANIFEST_SHA256 = None
FD_SET = frozenset(range(7))  # stdio, manifest, artifact, true, private cwd
MANIFEST_FD, ARTIFACT_FD, TRUE_FD, CWD_FD = 3, 4, 5, 6


class DedicatedBootstrap:
    def __init__(self, *, os_api=None, fcntl_api=None, sys_api=None, clock=None):
        self.os = os if os_api is None else os_api
        if fcntl_api is None:
            import fcntl  # dedicated Linux native adapter only
            fcntl_api = fcntl
        self.fcntl = fcntl_api
        self.sys = sys if sys_api is None else sys_api
        self.clock = time.monotonic if clock is None else clock
        self.started = False
        self.rows = []
        self.retired = set()
        self.retirement_attempted = False

    def check(self):
        now = self.clock()
        if type(now) not in (int, float) or not self.last <= now < self.deadline:
            raise TimeoutError('original bootstrap deadline exhausted or clock changed')
        self.last = now

    def identity(self, fd):
        info = self.os.fstat(fd)
        flags = self.fcntl.fcntl(fd, self.fcntl.F_GETFL)
        cloexec = self.fcntl.fcntl(fd, self.fcntl.F_GETFD)
        return dict(device=info.st_dev, inode=info.st_ino, uid=info.st_uid,
                    mode=info.st_mode, size=info.st_size, links=info.st_nlink,
                    mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns,
                    flags=flags, fd_flags=cloexec)

    def inventory(self):
        """Keep proc enumeration FD alive, then prove exactly it disappeared.

        No arbitrary FD closes. Sole-thread/source ownership and authenticated
        procfs are external prerequisites, not inferred from this snapshot.
        """
        self.check()
        live = {}
        packet = dict(operation='fd-inventory', live=live, after={}, enumeration_fd=None)
        self.rows.append(packet)  # partial inventory survives every failed check
        with self.os.scandir('/proc/self/fd') as entries:
            for item in entries:
                if len(live) >= 8 or not item.name.isascii() or not item.name.isdecimal():
                    raise ValueError('bounded exact inherited descriptor inventory required')
                fd = int(item.name)
                if fd in live or not 0 <= fd <= 65535:
                    raise ValueError('unique bounded descriptor numbers required')
                live[fd] = self.identity(fd)
        disappeared = []
        after = {}
        packet['after'] = after
        for fd, before in live.items():
            try:
                row = self.identity(fd)
            except OSError as exc:
                if exc.errno != 9: raise
                disappeared.append(fd)
            else:
                if before != row: raise ValueError('descriptor identity changed during inventory')
                after[fd] = row
        if (set(after) != FD_SET or len(disappeared) != 1
                or not stat.S_ISDIR(live[disappeared[0]]['mode'])):
            raise ValueError('only fixed inherited FDs and scanner-owned directory permitted')
        self.check()
        packet['enumeration_fd'] = disappeared[0]
        return after

    def read_held(self, fd, expected, limit, *, executable=False):
        self.check()
        before = self.identity(fd)
        self.rows.append(dict(operation='read-held', fd=fd, before=before))
        row = self.rows[-1]
        if (type(expected) is not dict or set(expected) != {'size', 'sha256'}
                or type(expected['size']) is not int or not 0 < expected['size'] <= limit
                or type(expected['sha256']) is not str or len(expected['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in expected['sha256'])
                or not stat.S_ISREG(before['mode']) or before['mode'] & 0o222
                or (executable and not before['mode'] & 0o111)
                or before['links'] != 1 or before['size'] != expected['size']
                or before['flags'] & self.os.O_ACCMODE != self.os.O_RDONLY
                or not before['flags'] & self.os.O_NONBLOCK
                or not before['fd_flags'] & self.fcntl.FD_CLOEXEC
                or self.os.lseek(fd, 0, self.os.SEEK_CUR) != 0):
            raise ValueError('frozen read-only regular same-FD byte receipt required')
        raw = self.os.read(fd, limit + 1)
        if type(raw) is not bytes: raise ValueError('native raw bytes required')
        row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        if (len(raw) != expected['size'] or row['sha256'] != expected['sha256']
                or self.identity(fd) != before
                or self.os.lseek(fd, 0, self.os.SEEK_CUR) != len(raw)):
            raise ValueError('changed, truncated or wrong held source/tool bytes')
        self.check()
        return raw

    def _prepare_unreviewed(self, manifest_receipt):
        """Native preparation draft, NOT permission; all tests mock its OS APIs.

        Runtime installation provenance/kernel/procfs are not yet implemented.
        Missing trust evidence remains incomplete even if every byte matches.
        """
        if self.started: raise RuntimeError('one bootstrap preparation attempt only')
        self.started = True
        self.last = self.clock()
        if type(self.last) not in (int, float) or not 0 <= self.last <= 2**40:
            raise ValueError('finite original monotonic clock required')
        self.deadline = self.last + 20
        self.original_started = self.last
        if (self.sys.platform != 'linux' or not self.sys.flags.isolated
                or not self.sys.flags.no_site or not self.sys.flags.ignore_environment
                or 'scripts' in self.sys.modules
                or set(self.os.environ) != {'PATH', 'HOME', 'LC_ALL'}
                or self.os.environ['PATH'] != '/usr/bin:/bin'
                or self.os.environ['LC_ALL'] != 'C'
                or self.os.getuid() != self.os.geteuid()
                or self.os.getgid() != self.os.getegid()):
            raise ValueError('dedicated isolated clean-environment nonprivileged bootstrap required')
        self.inventory()
        raw = self.read_held(MANIFEST_FD, manifest_receipt, 16384)
        manifest = json.loads(raw.decode('ascii'))
        if (type(manifest) is not dict or set(manifest) != {'artifact', 'true', 'mode'}
                or manifest['mode'] not in ('normal', 'controller-death')):
            raise ValueError('exact fixed control-card packet required')
        artifact = self.read_held(ARTIFACT_FD, manifest['artifact'], 512 * 1024)
        self.read_held(TRUE_FD, manifest['true'], 128 * 1024, executable=True)
        cwd = self.identity(CWD_FD)
        if (not stat.S_ISDIR(cwd['mode']) or cwd['uid'] != self.os.getuid()
                or stat.S_IMODE(cwd['mode']) != 0o700
                or not cwd['fd_flags'] & self.fcntl.FD_CLOEXEC):
            raise ValueError('owned private held cwd required')
        with self.os.scandir(CWD_FD) as entries:
            if next(iter(entries), None) is not None:
                raise ValueError('private cwd must be empty before source loading')
        self.os.fchdir(CWD_FD)  # actual directory change, not merely HOME assignment
        actual = self.os.stat('.')
        if ((actual.st_dev, actual.st_ino) != (cwd['device'], cwd['inode'])
                or self.os.getcwd() != self.os.environ['HOME']):
            raise ValueError('actual cwd not bound to held private directory')
        self.rows.append(dict(operation='actual-cwd', identity=cwd))
        self.inputs = self.inventory()
        self.check()
        # No import/eval/compile/fork/CDLL occurs during preparation.
        return artifact, manifest['mode']

    def _load_review_pending(self, artifact):
        # The exact approved artifact and independently verified runtime must
        # precede ANY source evaluation. This missing gate cannot be replaced
        # with a supplied bool/callback or matching self-generated hash.
        raise RuntimeError('runtime provenance and artifact evaluation gate remains closed')

    def _retire_inputs_unreviewed(self):
        """Retire only this fixed bootstrap's manifest/artifact/cwd FD copies.

        The true FD survives for the fixed root; stdio survives for output.
        No supplied FD list, blind close-range or uncertain close retry.
        """
        if self.retirement_attempted:
            raise RuntimeError('one fixed input retirement attempt only')
        self.retirement_attempted = True
        failures = []
        for fd in (MANIFEST_FD, ARTIFACT_FD, CWD_FD):
            row = dict(operation='retire-input', fd=fd, close_attempted=False)
            self.rows.append(row)
            try:
                if self.identity(fd) != self.inputs[fd]:
                    raise ValueError('input FD identity changed; never close replacement')
                self.retired.add(fd)  # irreversible before even a successful close
                row['close_attempted'] = True
                self.os.close(fd)
            except Exception as exc:
                row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
                failures.append(fd)  # still retire the OTHER exact owned copies
        if failures:
            raise ValueError('input retirement incomplete; no control creation allowed')

    def _run_unreviewed_draft(self, manifest_receipt):
        """Closed-gate composition draft; tests substitute ONLY fake OS/module.

        No native caller is authorized. The unconditional loader gate prevents
        reaching a native harness even through this private method today.
        """
        artifact, mode = self._prepare_unreviewed(manifest_receipt)
        self.check()
        self._load_review_pending(artifact)  # raises before any evaluation/construction
        self.check()
        module = self.sys.modules['scripts.rust_semantic_supervisor_entry']
        harness = module.NativeHarness(original_started=self.original_started)
        if (harness.budget.started != self.original_started
                or harness.budget.active_deadline != self.deadline):
            raise ValueError('bootstrap and harness must share the original clock')
        self._retire_inputs_unreviewed()
        self.check()
        harness.account(dict(bootstrap=self.rows))  # SAME aggregate trace budget
        return harness._run_unreviewed_draft(TRUE_FD, self.os.environ['HOME'], failure_mode=mode)

    def run(self):
        # Whole runtime/provenance/card review is still missing. Neither a
        # supplied digest nor a mocked successful preparation can bypass this.
        raise RuntimeError('dedicated bootstrap native execution gate remains closed')


if __name__ == '__main__':
    raise RuntimeError('dedicated bootstrap native execution gate remains closed')
