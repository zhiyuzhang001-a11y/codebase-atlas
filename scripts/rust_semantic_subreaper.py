"""Prepared process-local subreaper setup for a dedicated owned controller.

No libc loading, spawn, waits, signals, CLI, or actual adoption proof. Borrowed
glibc 2.39 must already be source/file-verified and loaded with use_errno=True.
arm() must
only run under the separately reviewed fixed controller before any child spawn.
Never invoke on Codex, a shared worker, or another existing application process.
"""
import ctypes
import math
import os
import platform
import sys
import time


def _linux_abi():
    if (sys.platform != 'linux' or platform.machine() != 'x86_64'
            or ctypes.sizeof(ctypes.c_void_p) != 8
            or ctypes.sizeof(ctypes.c_ulong) != 8
            or ctypes.sizeof(ctypes.c_long) != 8 or ctypes.sizeof(ctypes.c_int) != 4):
        raise ValueError('frozen Linux x64 ABI required')


class Subreaper:
    def __init__(self, libc, controller_pid, *, os_api=None, clock=None):
        _linux_abi()
        self.os = os if os_api is None else os_api
        if type(controller_pid) is not int or not 1 < controller_pid == self.os.getpid():
            raise ValueError('exact dedicated owned controller PID required')
        self.pid, self.libc = controller_pid, libc
        self.clock = time.monotonic if clock is None else clock
        self.attempted = self.set_attempted = self.prepared = False
        self.records, self.contracts, self.errors = [], [], []
        self.last = None
        # Explicit machine-width arguments for libc's variadic prctl wrapper.
        libc.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
        libc.prctl.restype = ctypes.c_int

    def _now(self, deadline):
        now = self.clock()
        if (type(now) not in {int, float} or not math.isfinite(now)
                or not 0 <= now <= 2**40 or (self.last is not None and now < self.last)
                or not now < deadline or self.os.getpid() != self.pid):
            raise ValueError('unchanged controller and remaining monotonic deadline required')
        self.last = now
        return now

    def _contract(self, verifier, deadline):
        self._now(deadline)
        raw = verifier()
        self.contracts.append(dict(raw) if type(raw) is dict else {'invalid_type': type(raw).__name__})
        expected = {'observer': self.pid, 'threads': 1, 'sigchld_default': True,
                    'sa_no_cldwait': False, 'sole_waiter': True}
        if (type(raw) is not dict or set(raw) != set(expected)
                or any(type(raw[key]) is not type(value) or raw[key] != value
                       for key, value in expected.items())):
            raise ValueError('measured wait state plus reviewed exclusive waiter policy required')
        self._now(deadline)

    def _call(self, operation, argument, deadline, output=None):
        now = self._now(deadline)
        row = {'operation': operation, 'arg2': argument, 'unused_args': [0, 0, 0],
               'started': now, 'deadline': deadline}
        self.records.append(row)
        ctypes.set_errno(0)
        result = self.libc.prctl(operation, argument, 0, 0, 0)
        row.update(result=result, errno=ctypes.get_errno())
        if output is not None:
            row['output'] = output.value
        if type(result) is not int or result != 0:
            raise OSError(row['errno'], 'subreaper prctl failed')
        row['finished'] = self._now(deadline)
        return row

    def arm(self, verifier, active_deadline):
        if self.attempted:
            raise RuntimeError('one subreaper setup attempt only')
        self.attempted = True
        try:
            if not callable(verifier):
                raise ValueError('native wait-state and fixed source policy verifier required')
            if (type(active_deadline) not in {int, float} or not math.isfinite(active_deadline)
                    or not 0 < active_deadline <= 2**40):
                raise ValueError('frozen remaining active deadline required')
            started = self._now(active_deadline)
            deadline = min(active_deadline, started + 1.5)
            self._contract(verifier, deadline)
            before = ctypes.c_int(-1)
            self._call(37, ctypes.addressof(before), deadline, before)
            if before.value != 0:
                raise ValueError('dedicated controller must start with subreaper unset')
            self.set_attempted = True  # uncertain SET never retried or automatically undone
            self._call(36, 1, deadline)
            after = ctypes.c_int(-1)
            self._call(37, ctypes.addressof(after), deadline, after)
            if after.value != 1:
                raise ValueError('subreaper setup not verified')
            self._contract(verifier, deadline)
            self._now(deadline)
            self.prepared = True
            return True
        except Exception as exc:
            self.errors.append({'error': type(exc).__name__, 'errno': getattr(exc, 'errno', None)})
            raise

    def report(self):
        return {'qualified': False, 'outer_cleanup_complete': False,
                'controller_pid': self.pid, 'attempted': self.attempted,
                'set_attempted': self.set_attempted, 'prepared': self.prepared,
                'records': [dict(row) for row in self.records],
                'contracts': [dict(row) for row in self.contracts],
                'errors': [dict(row) for row in self.errors]}
