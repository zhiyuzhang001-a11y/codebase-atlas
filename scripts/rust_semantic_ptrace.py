"""C0-O5 prepared stop observer. No spawn, attach, CLI or CI execution entry.

Only for the reviewed fixed fork controls, NOT arbitrary process supervision,
syscall policy, peak RSS, metadata permission or product qualification.
An independently reviewed outer owner must create the TRACEME root, install
deadlines/output/cleanup, own its session and bind source/tool/control receipts.
"""
from __future__ import annotations

import ctypes
import math
import os
import platform
import sys
import time

from scripts.rust_semantic_linux_resources import _read_at, proc_identity, rollup_rss

TRACEME, CONT, SETOPTIONS, GETEVENTMSG, GETSIGINFO = 0, 7, 0x4200, 0x4201, 0x4202
FORK, EXEC, EXIT = 1, 4, 6
OPTIONS = 1 | 2 | 4 | 8 | 16 | 64 | 0x100000  # all creation, exec, exit, EXITKILL
WALL = 0x40000000
SIGSTOP, SIGTRAP, SIGCHLD = 19, 5, 17  # Linux ABI, not the pure-test host's ABI


class NativeStops:
    """Linux x64 ABI, lazy explicit construction only; never attach or detach."""

    def __init__(self, session: int):
        if (sys.platform != 'linux' or platform.machine() != 'x86_64'
                or ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(ctypes.c_long) != 8
                or type(session) is not int or session != os.getpid()
                or os.getsid(0) != session or os.getpgrp() != session):
            raise ValueError('isolated Linux x64 observer session required')
        self.session = session
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.ptrace.restype = ctypes.c_long
        self.libc.ptrace.argtypes = [ctypes.c_uint, ctypes.c_int,
                                    ctypes.c_void_p, ctypes.c_void_p]
        self.calls = []
        self.observations = []

    def ptrace(self, request: int, pid: int, data=0):
        if request not in {CONT, SETOPTIONS, GETEVENTMSG, GETSIGINFO}:
            raise ValueError('no attach/detach/register mutation permitted')
        if len(self.calls) >= 8192:
            raise ValueError('ptrace request log exhausted')
        ctypes.set_errno(0)
        result = self.libc.ptrace(request, pid, None, data)
        error = ctypes.get_errno()
        self.calls.append({'request': request, 'pid': pid,
                           'data': data if type(data) is int else 'event-message-buffer',
                           'result': result, 'errno': error})
        if result == -1:
            raise OSError(error, 'ptrace request failed')
        if result != 0:
            raise ValueError('unexpected ptrace ABI result')
        return result

    def configure(self, pid: int):
        self.ptrace(SETOPTIONS, pid, OPTIONS)

    def resume(self, pid: int, sig: int):
        self.ptrace(CONT, pid, sig)

    def message(self, pid: int) -> int:
        value = ctypes.c_ulong()
        self.ptrace(GETEVENTMSG, pid, ctypes.byref(value))
        return value.value

    def wait(self):
        return os.waitpid(-1, os.WNOHANG | WALL)

    def signal_delivery(self, pid: int, sig: int):
        # siginfo_t is 128 bytes on this explicitly restricted Linux x64 ABI.
        info = (ctypes.c_int * 32)()
        self.ptrace(GETSIGINFO, pid, ctypes.byref(info))
        row = {'pid': pid, 'signo': info[0], 'errno': info[1], 'code': info[2]}
        self.observations.append({'signal_info': row})
        if sig != SIGCHLD or info[0] != sig or info[1] != 0 or not 1 <= info[2] <= 6:
            raise ValueError('expected kernel SIGCHLD delivery required')

    def sample(self, pid: int, start: int | None, parent: int) -> dict:
        begin = time.monotonic()
        observation = {'pid': pid, 'expected_starttime': start,
                       'expected_parent': parent, 'start_seconds': begin}
        self.observations.append(observation)
        directory = os.open(f'/proc/{pid}', os.O_RDONLY | os.O_DIRECTORY |
                            os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            before = proc_identity(_read_at(directory, 'stat', 8192))
            observation['before'] = before
            rss = rollup_rss(_read_at(directory, 'smaps_rollup', 65536))
            observation['rss_bytes'] = rss
            after = proc_identity(_read_at(directory, 'stat', 8192))
            observation['after'] = after
        finally:
            os.close(directory)
        end = time.monotonic()
        observation['end_seconds'] = end
        for item in (before, after):
            if (item['pid'] != pid or item['starttime'] <= 0
                    or item['state'] not in {'t', 'T'}
                    or item['ppid'] not in {parent, self.session}
                    or item['pgrp'] != self.session or item['session'] != self.session):
                raise ValueError('stopped owned identity changed')
        if (before['starttime'] != after['starttime']
                or (start is not None and start != before['starttime'])
                or end - begin > 0.5):
            raise ValueError('starttime or sampling duration changed')
        return {'before': before, 'after': after, 'rss_bytes': rss,
                'start_seconds': begin, 'end_seconds': end}


class FixedForkStops:
    """Kernel event loop for one root + exactly two serial fixed fork children.

    Caller owns outer deadline and cleanup; any exception MUST trigger that
    cleanup. Events and admissions remain inspectable even on partial failure.
    This loop alone is intentionally not an executable or a successful gate.
    """

    def __init__(self, root: int, observer: int, ops):
        if (type(root) is not int or type(observer) is not int
                or not 0 < root <= 2**31-1 or not 0 < observer <= 2**31-1
                or root == observer):
            raise ValueError('exact distinct owned root and observer required')
        self.root, self.observer, self.ops = root, observer, ops
        self.parents = {root: observer}
        self.admitted = {}
        self.early = {}
        self.execs = set()
        self.exits = {}
        self.terminals = {}
        self.events = []
        self.samples = []
        self.child_count = 0

    def _sample(self, pid: int):
        packet = self.ops.sample(pid, self.admitted.get(pid), self.parents[pid])
        start = packet['before']['starttime']
        if (type(start) is not int or start <= 0
                or type(packet['rss_bytes']) is not int or packet['rss_bytes'] <= 0):
            raise ValueError('positive kernel identity and RSS required')
        self.admitted[pid] = start
        self.samples.append({'pid': pid, **packet})

    def _initial(self, pid: int):
        self.ops.configure(pid)
        self._sample(pid)
        self.ops.resume(pid, 0)

    def event(self, pid: int, status: int):
        if (type(pid) is not int or not 0 < pid <= 2**31-1
                or type(status) is not int or not 0 <= status <= 2**32-1
                or len(self.events) >= 4096):
            raise ValueError('bounded kernel wait event required')
        self.events.append({'pid': pid, 'status': status})
        if pid in self.terminals:
            raise ValueError('PID reuse or duplicate terminal')
        if pid not in self.parents:
            # Auto-attached child may notify before its parent's creation event.
            if (len(self.early) >= 2 or pid in self.early
                    or status != (SIGSTOP << 8 | 0x7f)
                    or self.root not in self.admitted):
                raise ValueError('unknown PID/initial child stop')
            self.early[pid] = status
            return
        # Explicit Linux wait-word decoding, even when pure tests run on macOS.
        stopped = status & 0xff == 0x7f
        exited = status & 0x7f == 0
        signaled = status & 0x7f not in {0, 0x7f}
        if exited or signaled:
            if (pid not in self.exits or not exited
                    or status != 0 or self.exits[pid] != status):
                raise ValueError('terminal missing early exit or normal control status')
            if pid == self.root and (self.child_count != 2 or self.early
                                     or set(self.terminals) != set(self.parents)-{pid}):
                raise ValueError('root ended without complete children')
            self.terminals[pid] = status
            return
        if not stopped:
            raise ValueError('unsupported wait status')
        sig, kind = status >> 8 & 0xff, status >> 16
        if pid not in self.admitted:
            if status != (SIGSTOP << 8 | 0x7f):
                raise ValueError('missing expected bootstrap/child SIGSTOP')
            self._initial(pid)
            return
        if kind:
            if sig != SIGTRAP or kind not in {FORK, EXEC, EXIT} or pid in self.exits:
                raise ValueError('invalid event stop')
            message = self.ops.message(pid)
            if kind == FORK:
                if (pid != self.root or self.child_count >= 2
                        or type(message) is not int or not 0 < message <= 2**31-1
                        or message in self.parents or message == self.observer
                        or any(child not in self.terminals for child in self.parents
                               if child != self.root)):
                    raise ValueError('not the fixed serial fork shape')
                self.parents[message] = pid
                self.child_count += 1
                if message in self.early:
                    del self.early[message]
                    self._initial(message)
            elif kind == EXEC:
                if pid == self.root or pid in self.execs or message != pid:
                    raise ValueError('unexpected exec identity/count')
                self.execs.add(pid)
            elif kind == EXIT:
                if message != 0 or (pid != self.root and pid not in self.execs):
                    raise ValueError('unexpected early exit status')
                self.exits[pid] = message
            else:
                raise ValueError('unsupported creation/event; no generic clone policy')
            self._sample(pid)
            self.ops.resume(pid, 0)
        elif sig == SIGCHLD and pid == self.root:
            # Pure status is not enough to prove signal-delivery vs group stop.
            self.ops.signal_delivery(pid, SIGCHLD)
            self._sample(pid)
            self.ops.resume(pid, SIGCHLD)
        else:
            raise ValueError('unknown or additional signal/stop')

    def run(self, deadline: float):
        now = time.monotonic()
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not now < deadline <= now + 20):
            raise ValueError('finite caller-owned remaining deadline <=20s required')
        while len(self.terminals) != 3:
            if time.monotonic() >= deadline:
                raise TimeoutError('absolute stop observer deadline')
            pid, status = self.ops.wait()
            if pid:
                self.event(pid, status)
            else:
                time.sleep(0.001)
        return {'qualified': False, 'scope': 'fixed fork stop samples only',
                'parents': self.parents, 'admitted': self.admitted,
                'events': self.events, 'samples': self.samples,
                'terminals': self.terminals}
