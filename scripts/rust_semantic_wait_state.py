"""Read-only prepared Linux x64/glibc-2.39 wait-state measurement.

No library loading, signal changes, spawn, wait or execution entry. Caller must
first verify supplied libc/tool/source receipts and install independent timeout.
This measures native state, NOT sole ownership of future waits or qualification.
"""
import ctypes
import math
import os
import platform
import stat
import sys
import time


class SigAction(ctypes.Structure):
    _fields_ = [('handler', ctypes.c_void_p), ('mask', ctypes.c_uint64 * 16),
                ('flags', ctypes.c_int32), ('restorer', ctypes.c_void_p)]


def _linux_abi():
    return (sys.platform == 'linux' and platform.machine() == 'x86_64'
            and ctypes.sizeof(ctypes.c_void_p) == 8 and ctypes.sizeof(ctypes.c_ulong) == 8
            and ctypes.sizeof(ctypes.c_int) == 4 and ctypes.sizeof(SigAction) == 152
            and (SigAction.mask.offset, SigAction.flags.offset, SigAction.restorer.offset)
            == (8, 136, 144))


class NativeWaitState:
    def __init__(self, libc, os_api=None, clock=None):
        if not _linux_abi():
            raise ValueError('fixed Linux x64 glibc sigaction ABI required')
        self.libc = libc  # already measured by trusted caller; never CDLL here
        self.os = os if os_api is None else os_api
        self.clock = time.monotonic if clock is None else clock
        libc.gnu_get_libc_version.argtypes = []
        libc.gnu_get_libc_version.restype = ctypes.c_char_p
        libc.sigaction.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(SigAction)]
        libc.sigaction.restype = ctypes.c_int
        self.samples, self.errors = [], []
        self.attempted = False

    def measure(self):
        if self.attempted:
            raise RuntimeError('one prelaunch wait-state measurement only')
        self.attempted = True
        fd = None
        failed = False
        try:
            start = self.clock()
            if type(start) not in (int, float) or not math.isfinite(start) or not 0 <= start <= 2**40:
                raise ValueError('finite monotonic sample clock required')
            if self.libc.gnu_get_libc_version() != b'2.39':
                raise ValueError('unreviewed libc ABI version')
            pid = self.os.getpid()
            if type(pid) is not int or not 0 < pid <= 2**31-1:
                raise ValueError('exact own PID required')
            fd = self.os.open(f'/proc/{pid}/task', self.os.O_RDONLY | self.os.O_DIRECTORY
                              | self.os.O_CLOEXEC | self.os.O_NOFOLLOW)
            before = self.os.fstat(fd)
            identity = (before.st_dev, before.st_ino, before.st_uid, stat.S_IFMT(before.st_mode))
            if identity[2:] != (self.os.getuid(), stat.S_IFDIR) or self.os.get_inheritable(fd):
                raise ValueError('owned CLOEXEC task directory required')
            packet = {'observer': pid, 'task_identity': list(identity), 'samples': []}
            self.samples.append(packet)  # partial native evidence survives failure
            for phase in ('before', 'after'):
                names = []
                with self.os.scandir(fd) as entries:
                    for entry in entries:
                        names.append(entry.name)
                        if len(names) >= 2:
                            break  # bounded: a second entry already disqualifies
                action = SigAction()
                ctypes.set_errno(0)
                result = self.libc.sigaction(17, None, ctypes.byref(action))
                error = ctypes.get_errno()
                row = {'phase': phase, 'threads': names, 'result': result, 'errno': error,
                       'handler': action.handler, 'flags': action.flags}
                packet['samples'].append(row)
                if type(result) is not int or result != 0:
                    raise OSError(error, 'read-only SIGCHLD sigaction failed')
                if names != [str(pid)] or action.handler is not None or action.flags & 2:
                    raise ValueError('one own thread/default SIGCHLD/no-auto-reap required')
            after = self.os.fstat(fd)
            end = self.clock()
            packet['start'], packet['end'] = start, end
            if ((after.st_dev, after.st_ino, after.st_uid, stat.S_IFMT(after.st_mode)) != identity
                    or type(end) not in (int, float) or not math.isfinite(end)
                    or not start <= end <= start + .5):
                raise ValueError('task identity or sample deadline changed')
            return {'observer': pid, 'threads': 1, 'sigchld_default': True,
                    'sa_no_cldwait': False}  # deliberately no sole_waiter assertion
        except Exception as exc:
            failed = True
            self.errors.append({'operation': 'measure', 'type': type(exc).__name__,
                                'errno': getattr(exc, 'errno', None)})
            raise
        finally:
            if fd is not None:
                try:
                    self.os.close(fd)  # one close; uncertain result is never retried
                except Exception as exc:
                    self.errors.append({'operation': 'close', 'type': type(exc).__name__,
                                        'errno': getattr(exc, 'errno', None)})
                    if not failed:
                        raise  # preserve primary failure if measurement also failed

    def report(self):
        return {'qualified': False, 'outer_cleanup_complete': False,
                'samples': self.samples, 'errors': list(self.errors)}
