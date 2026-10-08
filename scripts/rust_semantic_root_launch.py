"""Prepared fixed tracee launch, not a complete supervisor or execution entry.

Only a reviewed isolated observer may call launch(): outer strong limits and
exact Python/libc/stdlib/true/source/cwd receipts MUST already be installed.
No CLI or arbitrary command option. Tests inject all native APIs; no real fork.
"""
import hashlib
import os
import signal
import sys


PYTHON = '/usr/bin/python3.12'  # frozen Ubuntu 24.04 system interpreter


def prepare_root_argv(root_source, true_fd):
    """Caller supplies exact measured ROOT_SOURCE constant, never project code.

    Validation/hash is not provenance verification. Outer source receipt must
    bind these bytes to the reviewed clean commit before evaluating them.
    """
    if (type(root_source) is not bytes or not 0 < len(root_source) <= 16384
            or type(true_fd) is not int or not 3 <= true_fd <= 65535):
        raise ValueError('bounded measured fixed-root source and held true FD required')
    root_source.decode('utf-8', 'strict')
    compile(root_source, '<fixed-root-definition>', 'exec')
    source = ('import ctypes, os\n' + root_source.decode('utf-8') +
              f'\nfixed_root(ctypes.CDLL(None, use_errno=True), {true_fd}, dict(os.environ))\n')
    compile(source, '<fixed-root-entry>', 'exec')  # syntax only, never evaluate
    return [PYTHON, '-I', '-S', '-c', source], {
        'root_source_sha256': hashlib.sha256(root_source).hexdigest(),
        'entry_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'entry_bytes': len(source.encode()), 'true_fd': true_fd}


class RootLauncher:
    """Observer-owned fresh fork/exec; never Popen or implicit child reaping.

    Isolated single-thread caller owns ALL waits with SIGCHLD default. The root
    PID cannot be reused before a consuming wait; hold its pidfd for bootstrap
    cancellation. Failure after fork needs outer group cleanup, NOT bare-PID kill.
    """
    def __init__(self, os_api=None, signal_api=None):
        if sys.platform != 'linux':
            raise ValueError('Linux fixed root only')
        self.os = os if os_api is None else os_api
        self.signal = signal if signal_api is None else signal_api
        self.attempted = False
        self.root = self.pidfd = None
        self.receipt, self.errors = None, []

    def launch(self, root_source, true_fd, environment, verify_wait_contract=None):
        if self.attempted:
            raise RuntimeError('one root launch only')
        self.attempted = True
        try:
            argv, source_receipt = prepare_root_argv(root_source, true_fd)
            if (type(environment) is not dict or set(environment) != {'PATH', 'HOME', 'LC_ALL'}
                    or any(type(v) is not str or '\x00' in v for v in environment.values())
                    or environment['PATH'] != '/usr/bin:/bin' or environment['LC_ALL'] != 'C'
                    or not environment['HOME'].startswith('/')
                    or len(environment['HOME'].encode()) > 4096):
                raise ValueError('fixed clean control environment required')
            pid = self.os.getpid()
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
            self.receipt = {'observer': pid, 'argv': argv, 'env': dict(environment),
                            'cwd': self.os.getcwd(), 'source': source_receipt,
                            'wait_contract': dict(contract)}
            self.root = self.os.fork()
            if self.root == 0:
                try:
                    self.os.set_inheritable(true_fd, True)
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
            return self.root, self.pidfd
        except Exception as exc:
            self.errors.append({'operation': 'launch', 'type': type(exc).__name__,
                                'errno': getattr(exc, 'errno', None)})
            raise

    def report(self):
        return {'qualified': False, 'outer_cleanup_complete': False,
                'root': self.root, 'owned_bootstrap_pidfd': self.pidfd,
                'receipt': self.receipt, 'errors': list(self.errors)}
