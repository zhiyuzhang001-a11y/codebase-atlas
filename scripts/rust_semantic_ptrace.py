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
import signal
import sys
import time

TRACEME, CONT, SETOPTIONS, GETEVENTMSG, GETSIGINFO = 0, 7, 0x4200, 0x4201, 0x4202
FORK, EXEC, EXIT = 1, 4, 6
OPTIONS = 1 | 2 | 4 | 8 | 16 | 64 | 0x100000  # all creation, exec, exit, EXITKILL
WALL = 0x40000000
SIGSTOP, SIGTRAP, SIGCHLD = 19, 5, 17  # Linux ABI, not the pure-test host's ABI


class NativeStops:
    """Linux x64 ABI, lazy explicit construction only; never attach or detach."""

    def __init__(self, session: int, resources=None):
        if (sys.platform != 'linux' or platform.machine() != 'x86_64'
                or ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(ctypes.c_long) != 8
                or type(session) is not int or session != os.getpid()
                or os.getsid(0) != session or os.getpgrp() != session):
            raise ValueError('isolated Linux x64 observer session required')
        if resources is None:
            raise ValueError('measured resource module injection required')
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            raise ValueError('native pidfd APIs required; no bare-PID fallback')
        self.resources = resources
        self.session = session
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.ptrace.restype = ctypes.c_long
        self.libc.ptrace.argtypes = [ctypes.c_uint, ctypes.c_int,
                                    ctypes.c_void_p, ctypes.c_void_p]
        self.calls = []
        self.observations = []
        self.wait_stops, self.wait_terminals = set(), {}
        self.handles = {}

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
        self.bind_stopped(pid)

    def resume(self, pid: int, sig: int):
        self.ptrace(CONT, pid, sig)
        self.wait_stops.discard(pid)

    def message(self, pid: int) -> int:
        value = ctypes.c_ulong()
        self.ptrace(GETEVENTMSG, pid, ctypes.byref(value))
        return value.value

    def wait(self):
        pid, status = os.waitpid(-1, os.WNOHANG | WALL)
        if pid:
            self.observations.append({'owned_wait': {'pid': pid, 'status': status}})
            if status & 0xff == 0x7f:
                self.wait_stops.add(pid)
            elif ((status & 0xff == 0 and status <= 0xff00)
                  or (status <= 0xff and 0 < status & 0x7f <= 64)):
                self.wait_stops.discard(pid)
                self.wait_terminals[pid] = status
        return pid, status

    def bind_stopped(self, pid: int):
        """Bind only a consumed ptrace stop, with same-FD identity checks.

        Caller owns all waits in the isolated single-thread observer. No PID
        reuse/rebinding, inherited handle or arbitrary attach is accepted.
        This is a prepared native adapter, not permission to execute it.
        """
        if (type(pid) is not int or not 0 < pid <= 2**31-1
                or pid == self.session or pid not in self.wait_stops
                or pid in self.wait_terminals
                or (pid not in self.handles and len(self.handles) >= 8)):
            raise ValueError('consumed owned live stop required for handle binding')
        begin = time.monotonic()
        row = {'handle_bind': {'pid': pid, 'start_seconds': begin}}
        self.observations.append(row)
        evidence = row['handle_bind']
        directory = os.open(f'/proc/{pid}', os.O_RDONLY | os.O_DIRECTORY |
                            os.O_CLOEXEC | os.O_NOFOLLOW)
        new_fd = None
        try:
            before = self.resources.proc_identity(self.resources._read_at(directory, 'stat', 8192))
            evidence['before'] = before
            status = self.resources._read_at(directory, 'status', 16384)
            evidence['status_text'] = status.decode('ascii', 'strict')
            fields = {}
            for line in status.splitlines():
                key, sep, value = line.partition(b':')
                if key in {b'TracerPid', b'Tgid', b'Pid', b'Uid'}:
                    if not sep or key in fields:
                        raise ValueError('ambiguous proc trace ownership')
                    fields[key] = value.split()
            expected = {b'TracerPid': [str(self.session).encode()],
                        b'Tgid': [str(pid).encode()], b'Pid': [str(pid).encode()],
                        b'Uid': [str(os.getuid()).encode()] * 4}
            if fields != expected:
                raise ValueError('same-account tracer-owned process leader required')
            if (before['pid'] != pid or before['starttime'] <= 0
                    or before['state'] not in {'t', 'T'}
                    or before['pgrp'] != self.session or before['session'] != self.session):
                raise ValueError('owned stopped identity required')
            if pid in self.handles:
                handle = self.handles[pid]
                if handle['starttime'] != before['starttime']:
                    raise ValueError('PID lifetime changed; never rebind')
                fd = handle['fd']
            else:
                fd = new_fd = os.pidfd_open(pid, 0)
                if os.get_inheritable(fd):
                    raise ValueError('pidfd must be close-on-exec')
            info_dir = os.open('/proc/self/fdinfo', os.O_RDONLY | os.O_DIRECTORY |
                               os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                info = self.resources._read_at(info_dir, str(fd), 4096)
            finally:
                os.close(info_dir)
            evidence['fdinfo_text'] = info.decode('ascii', 'strict')
            pid_rows = [line.partition(b':')[2].split() for line in info.splitlines()
                        if line.partition(b':')[0] == b'Pid']
            if pid_rows != [[str(pid).encode()]]:
                raise ValueError('pidfd does not identify stopped lifetime')
            after = self.resources.proc_identity(self.resources._read_at(directory, 'stat', 8192))
            evidence['after'] = after
            evidence['end_seconds'] = time.monotonic()
            if before != after or evidence['end_seconds'] - begin > .5:
                raise ValueError('handle binding identity/time changed')
            if new_fd is not None:
                self.handles[pid] = {'fd': fd, 'starttime': before['starttime']}
                new_fd = None
            evidence['fd'] = fd
        finally:
            if new_fd is not None:
                os.close(new_fd)
            os.close(directory)

    def kill_bound(self, pid: int):
        if type(pid) is not int or pid in self.wait_terminals:
            raise ValueError('live bound lifetime required')
        handle = self.handles[pid]  # missing authority is fail-closed
        row = {'bound_kill': {'pid': pid, 'fd': handle['fd'],
                              'starttime': handle['starttime'], 'signal': 9}}
        self.observations.append(row)
        try:
            signal.pidfd_send_signal(handle['fd'], 9, None, 0)
        except OSError as exc:
            row['bound_kill']['errno'] = exc.errno
            raise
        row['bound_kill']['result'] = 0

    def close_handles(self):
        """Release only observer-owned pidfds; never implies exit or reap."""
        errors = []
        for pid in list(self.handles):
            # Relinquish registry ownership before close, even if close fails:
            # retrying an ambiguous/reused FD can close an unrelated object.
            handle = self.handles.pop(pid)
            row = {'handle_close': {'pid': pid, 'fd': handle['fd']}}
            self.observations.append(row)
            try:
                os.close(handle['fd'])
            except OSError as exc:
                row['handle_close']['errno'] = exc.errno
                errors.append(exc)
            else:
                row['handle_close']['result'] = 0
        if errors:
            raise OSError(errors[0].errno, 'one or more pidfd closes failed; never retry raw FDs')

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
            before = self.resources.proc_identity(self.resources._read_at(directory, 'stat', 8192))
            observation['before'] = before
            rss = self.resources.rollup_rss(self.resources._read_at(directory, 'smaps_rollup', 65536))
            observation['rss_bytes'] = rss
            after = self.resources.proc_identity(self.resources._read_at(directory, 'stat', 8192))
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

    def __init__(self, root: int, observer: int, ops, *, journal_emit=None):
        if (type(root) is not int or type(observer) is not int
                or not 0 < root <= 2**31-1 or not 0 < observer <= 2**31-1
                or root == observer):
            raise ValueError('exact distinct owned root and observer required')
        self.root, self.observer, self.ops = root, observer, ops
        if journal_emit is not None and not callable(journal_emit):
            raise ValueError('reviewed first-stop journal emitter required')
        self.journal_emit = journal_emit
        self.parents = {root: observer}
        self.admitted = {}
        self.early = {}
        self.execs = set()
        self.exits = {}
        self.terminals = {}
        self.events = []
        self.samples = []
        self.pending_stops = set()
        self.child_count = 0

    def _sample(self, pid: int):
        first = pid not in self.admitted
        packet = self.ops.sample(pid, self.admitted.get(pid), self.parents[pid])
        start = packet['before']['starttime']
        if (type(start) is not int or start <= 0
                or type(packet['rss_bytes']) is not int or packet['rss_bytes'] <= 0):
            raise ValueError('positive kernel identity and RSS required')
        self.admitted[pid] = start
        self.samples.append({'pid': pid, **packet})
        if first and self.journal_emit is not None:
            if self.journal_emit(pid, self.parents[pid], packet) is not True:
                raise ValueError('first-stop journal send not confirmed')

    def _initial(self, pid: int):
        self.ops.configure(pid)
        self._sample(pid)
        self.ops.resume(pid, 0)
        self.pending_stops.discard(pid)

    def event(self, pid: int, status: int):
        if (type(pid) is not int or not 0 < pid <= 2**31-1
                or type(status) is not int or not 0 <= status <= 2**32-1
                or len(self.events) >= 4096):
            raise ValueError('bounded kernel wait event required')
        self.events.append({'pid': pid, 'status': status})
        if status & 0xff == 0x7f:
            self.pending_stops.add(pid)
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
            self.pending_stops.discard(pid)
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
            self.pending_stops.discard(pid)
        elif sig == SIGCHLD and pid == self.root:
            # Pure status is not enough to prove signal-delivery vs group stop.
            self.ops.signal_delivery(pid, SIGCHLD)
            self._sample(pid)
            self.ops.resume(pid, SIGCHLD)
            self.pending_stops.discard(pid)
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


class FixedForkCleanup:
    """Prepared bounded abort protocol, not a native kill implementation.

    The outer owner must supply bind_stopped(pid) and kill_bound(pid), backed
    by verified lifetime handles (at bootstrap for root), NEVER bare-PID kill.
    wait() is exclusively the tracer's owned-child wait stream. This protocol
    drains abnormal terminal events without misreporting them as control success.
    No process creation or executable entry is provided here.
    """

    def __init__(self, loop: FixedForkStops, ops):
        self.root, self.observer, self.ops = loop.root, loop.observer, ops
        self.known = set(loop.parents) | set(loop.early)
        self.terminals = dict(loop.terminals)
        self.parents = dict(loop.parents)
        self.events, self.errors = [], []
        # Include an event consumed immediately before a failed admission.
        self.stopped = set(loop.pending_stops) - set(self.terminals)
        self.known.update(self.stopped)
        if len(self.known) > 8:
            raise ValueError('cleanup lifetime PID bound exceeded')

    def _abort_stopped(self, pid):
        try:
            # Bind even a newly discovered child before any resume. CONT's
            # signal argument can be ignored at non-delivery stops, so it is
            # NEVER used as kill authority. Failure leaves the task stopped.
            self.ops.bind_stopped(pid)
            self.ops.kill_bound(pid)
        except OSError as exc:
            self.errors.append({'pid': pid, 'operation': 'bind/kill-stop',
                                'errno': exc.errno})
            return
        except KeyError:
            self.errors.append({'pid': pid, 'operation': 'missing-bound-handle'})
            return
        try:
            self.ops.resume(pid, 0)  # advance pending SIGKILL/exit-stop only
        except OSError as exc:
            self.errors.append({'pid': pid, 'operation': 'advance-killed-stop',
                                'errno': exc.errno})

    def run(self, deadline):
        now = time.monotonic()
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not now < deadline <= now + 10):
            raise ValueError('shared finite remaining cleanup deadline <=10s required')
        # Bound handles for every already-running lifetime, not PID numbers.
        for pid in sorted(self.known - set(self.terminals) - self.stopped):
            try:
                self.ops.kill_bound(pid)
            except OSError as exc:
                self.errors.append({'pid': pid, 'operation': 'bound-kill',
                                    'errno': exc.errno})
            except KeyError:
                self.errors.append({'pid': pid, 'operation': 'missing-bound-handle'})
        # Failed/held stops are still pending; successfully resumed tasks are
        # covered by bound handles, and are never spuriously continued here.
        for pid in sorted(self.stopped):
            self._abort_stopped(pid)
        no_children = False
        while time.monotonic() < deadline and len(self.events) < 4096:
            try:
                pid, status = self.ops.wait()
            except ChildProcessError:
                no_children = True
                break
            if not pid:
                time.sleep(.001)
                continue
            if (type(pid) is not int or not 0 < pid <= 2**31-1 or pid == self.observer
                    or type(status) is not int or not 0 <= status <= 2**32-1):
                raise ValueError('invalid owned cleanup wait record')
            self.events.append({'pid': pid, 'status': status})
            if pid in self.terminals:
                raise ValueError('duplicate cleanup terminal/PID reuse')
            self.known.add(pid)
            if len(self.known) > 8:
                raise ValueError('cleanup lifetime PID bound exceeded')
            if status & 0xff == 0x7f:
                kind = status >> 16
                if kind in {1, 2, 3}:  # fork/vfork/clone, cleanup only
                    child = self.ops.message(pid)
                    if (type(child) is not int or not 0 < child <= 2**31-1
                            or child == self.observer
                            or child in self.parents or child in self.terminals
                            or (child not in self.known and len(self.known) >= 8)):
                        raise ValueError('invalid cleanup creation identity')
                    self.known.add(child)
                    self.parents[child] = pid
                self._abort_stopped(pid)
            elif ((status & 0xff == 0 and status <= 0xff00)
                  or (status <= 0xff and 0 < status & 0x7f <= 64)):
                self.terminals[pid] = status
            else:
                raise ValueError('unsupported cleanup wait word')
        return {'qualified': False, 'scope': 'owned cleanup wait stream only',
                'known': sorted(self.known), 'terminals': self.terminals,
                'events': self.events, 'errors': self.errors,
                'no_waitable_children': no_children,
                'all_terminal': set(self.terminals) == self.known,
                'root_reaped': self.root in self.terminals,
                # Group absence and outer observer reap MUST be checked outside.
                'outer_cleanup_complete': False}
