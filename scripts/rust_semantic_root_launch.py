"""Prepared fixed tracee launch, not a complete supervisor or execution entry.

Only a reviewed isolated observer may call launch(): outer strong limits and
exact Python/libc/stdlib/true/source/cwd receipts MUST already be installed.
No CLI or arbitrary command option. Tests inject all native APIs; no real fork.
"""
import ctypes
import hashlib
import os
import select
import signal
import sys
import time

from scripts.rust_semantic_owned_journal import OwnedJournalPipe


PYTHON = '/usr/bin/python3.12'  # frozen Ubuntu 24.04 system interpreter
ROOT_PERMIT = b'ATROOT01'


class RootLaunchGate:
    """One fork's dedicated pre-exec pipe, not harness authentication.

    The eventual trusted caller must make handoff() perform the reviewed native
    harness exchange. A Python callback/receipt alone is never that evidence.
    All APIs are mocked in tests; no execution entry exposes this draft.
    """
    def __init__(self, budget, *, os_api=None, fcntl_api=None, clock=None, poll_factory=None):
        if sys.platform != 'linux':
            raise ValueError('Linux fixed root gate only')
        self.os = os if os_api is None else os_api
        self.clock = time.monotonic if clock is None else clock
        self.poll_factory = select.poll if poll_factory is None else poll_factory
        self.budget = budget
        self.owner = OwnedJournalPipe(budget.active_deadline, os_api=self.os,
                                      fcntl_api=fcntl_api, clock=self.clock)
        self.creator = self.os.getpid()
        self.pair = None
        self.role = None
        self.role_attempted = False
        self.await_attempted = False
        self.attempted = False
        self.released = False
        self.write_count = None
        self.write_attempted = False
        self.errors = []

    def _failure(self, operation, exc):
        self.errors.append({'operation': operation, 'type': type(exc).__name__,
                            'errno': getattr(exc, 'errno', None)})

    def _retire(self):
        # Close uncertainty is separately recorded, never retried, and must not
        # overwrite an earlier ACK/write/clock/read exception and its errno.
        try:
            self.owner.close_all()
        except Exception as exc:
            self._failure('close', exc)

    def _finish(self, primary):
        self._retire()
        if primary is not None:
            return  # retain the primary exception, even if cleanup also failed
        if self.owner.errors or self.errors:
            raise ValueError('gate endpoint retirement failed')
        try:
            self.budget.check_active(self.clock())
        except Exception as exc:
            self._failure('post-retirement-clock', exc)
            raise

    def prepare(self):
        self.budget.check_active(self.clock())
        self.pair = self.owner.allocate()

    def _select_role(self, role):
        if self.pair is None or self.role_attempted:
            raise RuntimeError('prepared gate selects one fork role only')
        self.role_attempted = True
        current = self.os.getpid()
        # Cleanup belongs to this fork copy even if role admission fails.
        # This only retires its inherited, identity-guarded pipe descriptors.
        self.owner.controller = current
        self.budget.check_active(self.clock())
        if role not in ('parent', 'child'):
            raise ValueError('fixed gate role required')
        if ((role == 'parent' and current != self.creator)
                or (role == 'child' and (current == self.creator
                                        or self.os.getppid() != self.creator))):
            raise ValueError('exact creating parent or direct fork child required')
        self.role = role
        # A fork copy owns precisely the inherited pair, not arbitrary FDs.
        self.owner._close([self.pair['read_fd'] if role == 'parent'
                           else self.pair['write_fd']])
        if self.owner.errors:
            raise ValueError('unused gate endpoint retirement failed')

    def release_parent(self, root, pidfd, handoff):
        if self.attempted:
            raise RuntimeError('one handoff/release attempt only')
        self.attempted = True
        primary = None
        try:
            self._select_role('parent')
            self.budget.check_active(self.clock())
            # The exact peer/native binding is supplied by the future reviewed
            # harness entry, not manufactured here from PID or a bool flag.
            ack = handoff(root, pidfd)
            expected = {'root': root, 'pidfd': pidfd, 'recovery_held': True}
            if (type(ack) is not dict or ack != expected
                    or any(type(ack[k]) is not type(v) for k, v in expected.items())):
                raise ValueError('exact root recovery handoff acknowledgement required')
            self.budget.check_active(self.clock())
            fd = self.pair['write_fd']
            if self.owner._identity(fd, True) != self.owner.owned[fd]:
                raise ValueError('gate writer identity changed')
            self.write_attempted = True
            count = self.os.write(fd, ROOT_PERMIT)  # atomic <= PIPE_BUF, no resend
            self.write_count = count if type(count) is int else None
            if type(count) is int and 0 < count <= len(ROOT_PERMIT):
                self.budget.feed('trace', ROOT_PERMIT[:count])
            if type(count) is not int or count != len(ROOT_PERMIT):
                raise ValueError('complete one-shot root permit required')
            self.budget.check_active(self.clock())
            if self.owner._identity(fd, True) != self.owner.owned[fd]:
                raise ValueError('gate writer identity changed after write')
            self.released = True
        except Exception as exc:
            primary = exc
            self._failure('handoff-or-write', exc)
            raise
        finally:
            self._finish(primary)  # EOF, not terminal or successful cleanup

    def await_child(self):
        if self.await_attempted:
            raise RuntimeError('one child permit wait attempt only')
        self.await_attempted = True
        primary = None
        try:
            self._select_role('child')
            fd = self.pair['read_fd']
            raw = bytearray()
            poller = self.poll_factory()
            # Frozen Linux poll(2) values, even in non-Linux injected tests.
            # Construction still refuses non-Linux native execution.
            poller.register(fd, 0x001 | 0x010 | 0x008)
            for unused in range(2048):
                remaining = self.budget.check_active(self.clock())
                if self.os.getppid() != self.creator:
                    raise ValueError('creator lost while awaiting permit')
                if self.owner._identity(fd, False) != self.owner.owned[fd]:
                    raise ValueError('gate reader identity changed')
                try:
                    chunk = self.os.read(fd, 9 - len(raw))
                except (BlockingIOError, InterruptedError):
                    poller.poll(max(1, min(10, int(remaining * 1000))))
                    continue
                if type(chunk) is not bytes or len(chunk) > 9 - len(raw):
                    raise ValueError('bounded native permit bytes required')
                if chunk:
                    raw.extend(chunk)
                    self.budget.feed('trace', chunk)
                self.budget.check_active(self.clock())
                if self.owner._identity(fd, False) != self.owner.owned[fd]:
                    raise ValueError('gate reader identity changed after read')
                if not chunk:
                    if bytes(raw) != ROOT_PERMIT:
                        raise ValueError('missing, truncated or foreign permit')
                    self.released = True
                    return
                if len(raw) > len(ROOT_PERMIT):
                    raise ValueError('extra permit bytes refused')
            raise TimeoutError('bounded root permit polling exhausted')
        except Exception as exc:
            primary = exc
            self._failure('await-permit', exc)
            raise
        finally:
            self._finish(primary)

    def report(self):
        return {'qualified': False, 'harness_authenticated': False,
                'outer_cleanup_complete': False, 'role': self.role,
                'role_attempted': self.role_attempted,
                'await_attempted': self.await_attempted,
                'handoff_attempted': self.attempted, 'permit_released': self.released,
                'write_count': self.write_count,
                'write_attempted': self.write_attempted,
                'errors': [dict(e) for e in self.errors],
                'pipe': self.owner.report()}


def arm_parent_death(libc, os_api, expected_parent):
    """Fail closed before child progress; signal delivery is NOT terminal proof.

    Creator PID must be frozen before fork. Trusted native caller must also
    verify unchanged credentials and non-privileged executable receipts.
    """
    if type(expected_parent) is not int or not 1 < expected_parent <= 2**31-1:
        raise ValueError('pre-fork creator identity required')
    libc.prctl.restype = ctypes.c_int
    libc.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
    if libc.prctl(1, 9, 0, 0, 0) != 0:
        raise ValueError('SET_PDEATHSIG failed')
    observed = ctypes.c_int(0)
    address = ctypes.cast(ctypes.pointer(observed), ctypes.c_void_p).value
    if libc.prctl(2, address, 0, 0, 0) != 0 or observed.value != 9:
        raise ValueError('GET_PDEATHSIG failed or mismatched')
    if os_api.getppid() != expected_parent:
        raise ValueError('creator died before death-signal installation')


def prepare_root_argv(root_source, true_fd, expected_parent):
    """Caller supplies exact measured ROOT_SOURCE constant, never project code.

    Validation/hash is not provenance verification. Outer source receipt must
    bind these bytes to the reviewed clean commit before evaluating them.
    """
    if (type(root_source) is not bytes or not 0 < len(root_source) <= 16384
            or type(true_fd) is not int or not 3 <= true_fd <= 65535
            or type(expected_parent) is not int or not 1 < expected_parent <= 2**31-1):
        raise ValueError('bounded measured fixed-root source and held true FD required')
    root_source.decode('utf-8', 'strict')
    compile(root_source, '<fixed-root-definition>', 'exec')
    source = ('import ctypes, os\n' + root_source.decode('utf-8') +
              f'\nfixed_root(ctypes.CDLL(None, use_errno=True), {true_fd}, dict(os.environ), {expected_parent})\n')
    compile(source, '<fixed-root-entry>', 'exec')  # syntax only, never evaluate
    return [PYTHON, '-I', '-S', '-c', source], {
        'root_source_sha256': hashlib.sha256(root_source).hexdigest(),
        'entry_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'entry_bytes': len(source.encode()), 'true_fd': true_fd,
        'expected_parent': expected_parent}


class RootLauncher:
    """Observer-owned fresh fork/exec; never Popen or implicit child reaping.

    Isolated single-thread caller owns ALL waits with SIGCHLD default. The root
    PID cannot be reused before a consuming wait; hold its pidfd for bootstrap
    cancellation. Failure after fork needs outer group cleanup, NOT bare-PID kill.
    """
    def __init__(self, os_api=None, signal_api=None, libc=None):
        if sys.platform != 'linux':
            raise ValueError('Linux fixed root only')
        self.os = os if os_api is None else os_api
        self.signal = signal if signal_api is None else signal_api
        self.libc = ctypes.CDLL(None, use_errno=True) if libc is None else libc
        self.attempted = False
        self.root = self.pidfd = None
        self.gate = None
        self.receipt, self.errors = None, []

    def launch(self, root_source, true_fd, environment, verify_wait_contract=None,
               *, gate=None, handoff=None):
        if self.attempted:
            raise RuntimeError('one root launch only')
        self.attempted = True
        try:
            pid = self.os.getpid()  # creator identity frozen BEFORE fork
            argv, source_receipt = prepare_root_argv(root_source, true_fd, pid)
            if (type(environment) is not dict or set(environment) != {'PATH', 'HOME', 'LC_ALL'}
                    or any(type(v) is not str or '\x00' in v for v in environment.values())
                    or environment['PATH'] != '/usr/bin:/bin' or environment['LC_ALL'] != 'C'
                    or not environment['HOME'].startswith('/')
                    or len(environment['HOME'].encode()) > 4096):
                raise ValueError('fixed clean control environment required')
            if self.os.getsid(0) != pid or self.os.getpgrp() != pid:
                raise ValueError('isolated observer session/group required')
            if not callable(getattr(self.os, 'pidfd_open', None)):
                raise ValueError('pidfd API required before fork')
            self.signal.signal(self.signal.SIGCHLD, self.signal.SIG_DFL)
            # signal(SIG_DFL) alone does not prove SA_NOCLDWAIT is clear. This
            # mandatory trusted verifier is NOT implemented by this component;
            # missing proof fails before fork. The reviewed caller must measure
            # native disposition/flags and thread ownership, not assert them.
            if not callable(verify_wait_contract):
                raise ValueError('measured sole-waiter contract verifier required')
            contract = verify_wait_contract()
            expected = {'observer': pid, 'threads': 1, 'sigchld_default': True,
                        'sa_no_cldwait': False, 'sole_waiter': True}
            if (type(contract) is not dict or contract != expected
                    or any(type(contract[k]) is not type(v) for k, v in expected.items())):
                raise ValueError('measured default SIGCHLD/no-auto-reap/sole-waiter required')
            if not isinstance(gate, RootLaunchGate) or not callable(handoff):
                raise ValueError('dedicated root gate and trusted harness handoff required')
            if gate.creator != pid or gate.os is not self.os:
                raise ValueError('gate must belong to this exact observer')
            self.gate = gate
            gate.prepare()
            self.receipt = {'observer': pid, 'argv': argv, 'env': dict(environment),
                            'cwd': self.os.getcwd(), 'source': source_receipt,
                            'wait_contract': dict(contract)}
            self.root = self.os.fork()
            if self.root == 0:
                try:
                    arm_parent_death(self.libc, self.os, pid)
                    gate.await_child()
                    self.os.set_inheritable(true_fd, True)
                    gate.budget.check_active(gate.clock())
                    self.os.execve(PYTHON, argv, dict(environment))
                finally:
                    self.os._exit(125)  # failed exec; never continue observer logic
            if type(self.root) is not int or not 0 < self.root <= 2**31-1 or self.root == pid:
                raise ValueError('fresh owned fork child required')
            self.receipt['root'] = self.root
            self.pidfd = self.os.pidfd_open(self.root, 0)
            if (type(self.pidfd) is not int or not 3 <= self.pidfd <= 65535
                    or self.os.get_inheritable(self.pidfd)):
                raise ValueError('fresh root CLOEXEC pidfd required')
            self.receipt['bootstrap_pidfd'] = self.pidfd
            gate.release_parent(self.root, self.pidfd, handoff)
            return self.root, self.pidfd
        except Exception as exc:
            self.errors.append({'operation': 'launch', 'type': type(exc).__name__,
                                'errno': getattr(exc, 'errno', None)})
            if self.gate is not None and self.gate.creator == self.os.getpid():
                try:
                    self.gate.owner.close_all()
                except Exception as close_exc:
                    self.errors.append({'operation': 'gate-close',
                                        'type': type(close_exc).__name__,
                                        'errno': getattr(close_exc, 'errno', None)})
            raise

    def report(self):
        return {'qualified': False, 'outer_cleanup_complete': False,
                'root': self.root, 'owned_bootstrap_pidfd': self.pidfd,
                'gate': None if self.gate is None else self.gate.report(),
                'receipt': self.receipt, 'errors': list(self.errors)}
