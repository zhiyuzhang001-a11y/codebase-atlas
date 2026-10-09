"""Whole C0-O5 supervision entry DRAFT. DO NOT EXECUTE before source/card review.

The fixed fork topology is harness -> controller -> observer -> root -> 2 true,
plus one harness-owned cancellation-only watchdog (six children in total).
Only this file owns role bootstrap, barrier acknowledgements, aggregate output,
normal retirement and controller-death recovery. No CLI, workflow registration,
metadata, build, user command or public qualification is supplied here.

The public run_control remains unconditionally closed. Private draft methods
contain native operations and MUST NOT be invoked. Pure tests exercise
the same retirement ledger without constructing any native adapter. The caller
must be an independently reviewed dedicated -I -S harness with frozen source,
Python/libc/stdlib/true/cwd receipts; this draft does NOT establish that provenance.
"""
import array
import copy
import ctypes
import errno
import hashlib
import json
import os
import platform
import signal
import socket
import stat
import time

from scripts import rust_semantic_linux_resources as resources
from scripts import rust_semantic_ptrace as ptrace
from scripts.rust_semantic_control_budget import ControlBudget
from scripts.rust_semantic_control_code import ROOT_SOURCE
from scripts.rust_semantic_root_launch import RootLaunchGate, RootLauncher, arm_parent_death
from scripts.rust_semantic_wait_state import NativeWaitState
from scripts.rust_semantic_subreaper import Subreaper


ROLES = ('watchdog', 'controller', 'observer', 'root', 'true-path', 'true-fd')
PARENT = dict(watchdog='harness', controller='harness', observer='controller', root='observer',
              **{'true-path': 'root', 'true-fd': 'root'})
TERMINAL_CODES = {1, 2, 3}


class TreeLedger:
    """One harness-owned lifecycle/retirement ledger, never an execution grant.

    Native binding and exact consuming waits must precede these transitions.
    An ordinary dictionary cannot authenticate its own origin. No signal or
    cleanup success follows from this protocol's booleans alone.
    """
    def __init__(self, harness, budget):
        self.harness, self.budget = harness, budget
        self.created, self.held, self.terminals, self.stopped = {}, {}, {}, set()
        self.session_confirmed = False
        self.group_state = 'held'
        self.cancel_attempted = self.cancel_sent = False
        self.failed = False
        self.controller_consumed = self.observer_consumed = False
        self.events = []

    def register(self, role, identity, handle):
        if (role not in ROLES or role in self.created or self.group_state != 'held'
                or type(identity) is not dict or type(handle) is not int or handle < 3):
            raise ValueError('one pre-retirement role registration required')
        parent = self.harness if PARENT[role] == 'harness' else self.created[PARENT[role]]['pid']
        if (set(identity) != {'pid', 'ppid', 'session', 'pgrp', 'starttime', 'state'}
                or any(type(identity[k]) is not int or identity[k] <= 0
                       for k in ('pid', 'ppid', 'session', 'pgrp', 'starttime'))
                or identity['ppid'] != parent
                or identity['pid'] == self.harness
                or identity['pid'] in {v['pid'] for v in self.created.values()}):
            raise ValueError('distinct lifetime and physical creation parent required')
        if role in ('root', 'true-path', 'true-fd'):
            observer = self.created['observer']['pid']
            if not self.session_confirmed or (identity['session'], identity['pgrp']) != (observer, observer):
                raise ValueError('confirmed observer session before tracee creation required')
        self.created[role] = copy.deepcopy(identity)
        self.held[role] = handle
        self.events.append(('held', role, identity['pid'], identity['starttime']))

    def confirm_session(self, identity):
        old = self.created['observer']
        if (self.session_confirmed or identity['pid'] != old['pid']
                or identity['starttime'] != old['starttime'] or identity['ppid'] != old['ppid']
                or (identity['session'], identity['pgrp']) != (old['pid'], old['pid'])
                or identity['state'] != 'T'):
            raise ValueError('same held observer stopped after setsid required')
        self.created['observer'] = copy.deepcopy(identity)
        self.session_confirmed = True
        self.events.append(('observer-session-confirmed',))

    def stop(self, role, starttime, rss):
        if (role not in ('root', 'true-path', 'true-fd') or role in self.stopped
                or self.created[role]['starttime'] != starttime
                or type(rss) is not int or rss <= 0 or self.group_state != 'held'):
            raise ValueError('first stopped lifetime with positive RSS before CONT required')
        self.stopped.add(role)
        self.events.append(('first-stop-held', role, rss))

    def terminal(self, role, pid, starttime, status, consumer):
        row = self.created[role]
        expected = 'observer' if role in ('root', 'true-path', 'true-fd') else PARENT[role]
        if consumer == 'harness':
            if role not in ('controller', 'watchdog') and not self.controller_consumed:
                raise ValueError('controller must be consumed before recovery admission')
            if role not in ('controller', 'observer', 'watchdog') and not self.observer_consumed:
                raise ValueError('observer must be consumed before tracee adoption')
        elif consumer != expected:
            raise ValueError('only physical normal waiter or recovery harness may consume')
        if (role in self.terminals or pid != row['pid'] or starttime != row['starttime']
                or type(status) is not int
                or not ((0 <= status <= 0xff00 and status & 0xff == 0)
                        or (0 < status <= 0xff and 0 < status & 0x7f <= 64))):
            raise ValueError('one exact consuming terminal required')
        if role in ('controller', 'observer', 'watchdog') and self.group_state == 'held':
            raise ValueError('group authority must retire before role consumption')
        self.terminals[role] = dict(pid=pid, starttime=starttime, status=status, consumer=consumer)
        if role == 'controller': self.controller_consumed = True
        if role == 'observer': self.observer_consumed = True
        self.events.append(('terminal-consumed', role, consumer))

    def begin_cancel(self):
        if self.cancel_attempted or self.group_state != 'held' or 'observer' in self.terminals:
            raise ValueError('one last held-leader group cancellation only')
        self.cancel_attempted = True  # uncertain syscall must never be retried

    def retire(self, *, sent, failed_recovery=False):
        if (not self.cancel_attempted or type(sent) is not bool or not sent
                or self.group_state != 'held'):
            raise ValueError('successful last cancellation required; no inferred exit')
        self.cancel_sent = True
        covered = set(self.created) == set(ROLES) and all(
            role in self.held or role in self.terminals for role in self.created)
        if not failed_recovery and not covered:
            raise ValueError('normal retirement requires all six creation lifetimes')
        self.group_state = 'abandoned-failed' if failed_recovery else 'retired-normal'
        self.failed |= failed_recovery
        self.events.append(('group-authority-retired', self.group_state))

    def abandon_unacquired_group(self):
        if (self.group_state != 'held' or self.session_confirmed or self.cancel_attempted
                or any(r in self.created for r in ('root', 'true-path', 'true-fd'))):
            raise ValueError('only pre-session bootstrap lacks group authority')
        self.failed = True
        self.group_state = 'not-acquired-failed'
        self.events.append(('no-group-authority-acquired',))

    def report(self):
        return dict(qualified=False, outer_cleanup_complete=False,
                    native_provenance_verified=False, group_state=self.group_state,
                    registered=copy.deepcopy(self.created), held_roles=sorted(self.held),
                    stopped=sorted(self.stopped), terminals=copy.deepcopy(self.terminals),
                    failed=self.failed, events=copy.deepcopy(self.events), budget=self.budget.report())


class NativeHarness:
    """Unreviewed native whole-entry adapter. Construction itself opens nothing."""
    def __init__(self, *, original_started=None):
        now = time.monotonic()
        # Preparation belongs to this SAME active clock. An earlier timestamp
        # only consumes allowance; it cannot confer execution permission.
        self.budget = ControlBudget(now if original_started is None else original_started)
        self.budget.check_active(now)  # reject expired/future origin before OS identity work
        self.pid, self.uid = os.getpid(), os.getuid()
        self.gid = os.getgid()
        self.harness_pid = self.pid
        self.audit_endpoint = None
        self.ledger = TreeLedger(self.pid, self.budget)
        self.libc = None
        self.channels, self.fds, self.raw = [], {}, []
        self.errors = []
        self.sequence = {0: 0, 1: 0}
        self.controller = self.observer = None
        self.cleanup_deadline = None
        self.eof = set()
        self.consume_attempts, self.wait_raw, self.receive_raw = {}, [], []
        self.output_reads, self.output_writes = {}, {}
        self.output_failed = False
        self.cleanup_omitted = 0
        self.cleanup_omitted_hash = hashlib.sha256()
        self.watch_channel, self.watchdog = None, None
        self.watch_held, self.watch_cancelled = {}, set()
        self.watch_handoffs = {}  # verified locally BEFORE uncertain send/ACK
        self.watch_retire_attempted = False
        self.watch_stream_invalid = False
        self.watch_first_cleanup = None
        self.watch_raw, self.watch_stopping = [], False
        self.watch_cancel_attempted = False
        self.watch_health_checking = False
        self.watch_pumping = False
        self.watch_pending = []
        self.watch_evidence_complete = False
        self.watch_cancel_end = None
        self.watch_publish_failures = []
        self.watch_deadline_records = []
        self.watch_fault_seen = False
        self.watch_cleanup_confirmed = False
        self.direct_births, self.pending_cancelled = {}, set()

    def check(self):
        if os.getpid() == self.harness_pid:
            if not self.output_failed:
                try: self.drain_outputs()
                except Exception as exc:
                    self.output_failed = True
                    if self.budget.failure is None: self.budget.failure = 'output-observation-failed'
                    self.errors.append(dict(phase='output-failed', type=type(exc).__name__,
                                            errno=getattr(exc, 'errno', None)))
                    if self.cleanup_deadline is None: raise
            # Output observation failure NEVER suspends safe cleanup clocks,
            # already-held cancellation or single-attempt terminal accounting.
            # Failed streams are not read/fed again; EOF remains unproved.
        fn = self.budget.check_cleanup if self.cleanup_deadline is not None else self.budget.check_active
        fn(time.monotonic())
        # A ready ACK is not a continuing supervision lease. Before every
        # active operation/permit, inspect the held direct child's terminal.
        # The guard only prevents recursive check() inside terminal(); cleanup
        # keeps its own clock and must not be blocked by this failure latch.
        if (os.getpid() == self.harness_pid and self.cleanup_deadline is None
                and 'watchdog' in getattr(getattr(self, 'ledger', None), 'held', {})
                and not self.watch_stopping and not self.watch_health_checking):
            self.watch_health_checking = True
            try:
                row = self.terminal(self.watchdog, self.ledger.held['watchdog'])
                if row is not None:
                    self.watch_stopping = True
                    self.budget.failure = 'watchdog-terminal-before-retirement'
                    raise RuntimeError('independent watchdog died; no further release')
                self.pump_watchdog()
            finally: self.watch_health_checking = False

    def prepare_outputs(self):
        for name in ('stdout', 'stderr'):
            read, write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
            self.hold(read); self.hold(write)
            self.output_reads[name], self.output_writes[name] = read, write

    def drain_outputs(self):
        # Independent harness process drains even when controller/tracer stall.
        # Each tick performs one <=64KiB nonblocking read per owned stream.
        for name, fd in self.output_reads.items():
            if self.budget.records[name]['eof']: continue
            expected = self.fds[fd]; info = os.fstat(fd)
            if (info.st_dev, info.st_ino, info.st_uid, stat.S_IFMT(info.st_mode)) != expected:
                raise ValueError('harness output pipe identity changed')
            try: raw = os.read(fd, 65536)
            except BlockingIOError: continue
            if not raw: self.budget.eof(name)
            else: self.budget.feed(name, raw)

    def inherit_outputs(self):
        # Called only in the controller fork copy, before any observer fork.
        # Dup'ed 1/2 remain inherited by observer/root/true; original endpoints
        # and every inherited read endpoint are retired in this copy.
        for name, target in (('stdout', 1), ('stderr', 2)):
            os.dup2(self.output_writes[name], target, inheritable=True)
        for fd in [*self.output_reads.values(), *self.output_writes.values()]:
            self.retire_fd(fd)
        self.output_reads, self.output_writes = {}, {}

    def account(self, packet):
        raw = json.dumps(packet, sort_keys=True, separators=(',', ':')).encode('ascii')
        if not 0 < len(raw) <= 65536: raise ValueError('bounded wire/raw packet required')
        if self.cleanup_deadline is not None and self.budget.failure is not None:
            # Preserve explicit omission count/hash after the original failure;
            # do not repeatedly feed a failed ledger or allocate new raw copies.
            self.cleanup_omitted += len(raw)
            self.cleanup_omitted_hash.update(raw)
            return raw
        self.raw.append(raw[:max(0, self.budget.LIMIT-self.budget.total_retained)])
        self.budget.feed('trace', raw)
        if os.getpid() != self.harness_pid:
            if self.audit_endpoint is None:
                raise ValueError('child native raw evidence lacks harness output route')
            wrapped = json.dumps(dict(op='raw-native', raw=raw.hex()), separators=(',', ':')).encode('ascii')
            if len(wrapped) > 65536:
                raise ValueError('native evidence exceeds fixed wire packet')
            if self.audit_endpoint.sendmsg([wrapped], [], socket.MSG_DONTWAIT | socket.MSG_NOSIGNAL) != len(wrapped):
                raise ValueError('uncertain native evidence delivery; never resend')
        return raw

    def wait_policy(self):
        self.check()
        packet = NativeWaitState(self.libc).measure()
        self.account(dict(wait_state=packet))
        # Sole-waiter is a SOURCE policy of this fixed draft, NOT a native fact.
        # It cannot be accepted as execution authority before whole-file review.
        packet['sole_waiter'] = True
        self.check()
        return packet

    def hold(self, fd):
        if type(fd) is not int or fd < 3 or fd in self.fds:
            raise ValueError('new unique owned descriptor required')
        self.fds[fd] = None  # ownership precedes ALL validation, including fstat
        info = os.fstat(fd)
        self.fds[fd] = (info.st_dev, info.st_ino, info.st_uid, stat.S_IFMT(info.st_mode))
        if os.get_inheritable(fd): raise ValueError('owned CLOEXEC FD required')
        return fd

    def retire_fd(self, fd):
        expected = self.fds.pop(fd)  # uncertain close never retried
        if expected is None:
            raise ValueError('unverifiable owned FD retirement remains incomplete')
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_uid, stat.S_IFMT(info.st_mode)) != expected:
            raise ValueError('refuse closing replaced owned FD')
        os.close(fd)

    def identity(self, pid, fd, *, parent, session=None, stopped=False, terminal=False):
        """Native same-proc-directory before/after identity plus pidfd check."""
        self.check(); begin = time.monotonic()
        directory = self.hold(os.open(f'/proc/{pid}', os.O_RDONLY | os.O_DIRECTORY |
                                     os.O_CLOEXEC | os.O_NOFOLLOW))
        try:
            a = resources._read_at(directory, 'stat', 8192)
            status = resources._read_at(directory, 'status', 16384)
            b = resources._read_at(directory, 'stat', 8192)
            info_dir = self.hold(os.open('/proc/self/fdinfo', os.O_RDONLY | os.O_DIRECTORY |
                                        os.O_CLOEXEC | os.O_NOFOLLOW))
            try: info = resources._read_at(info_dir, str(fd), 4096)
            finally: self.retire_fd(info_dir)
            self.account(dict(identity_pid=pid, stat_before=a.hex(), status=status.hex(),
                              stat_after=b.hex(), fdinfo=info.hex()))
            before, after = resources.proc_identity(a), resources.proc_identity(b)
            fields = {}
            for line in status.splitlines():
                key, sep, val = line.partition(b':')
                if key in {b'Pid', b'Tgid', b'Uid', b'TracerPid'}:
                    if not sep or key in fields: raise ValueError('duplicate proc identity')
                    fields[key] = val.split()
            tracer = self.observer if stopped else 0
            if fields != {b'Pid': [str(pid).encode()], b'Tgid': [str(pid).encode()],
                           b'Uid': [str(self.uid).encode()]*4,
                           b'TracerPid': [str(tracer).encode()]}:
                raise ValueError('native same-account/tracer process leader required')
            pid_rows = [line.partition(b':')[2].split() for line in info.splitlines()
                        if line.partition(b':')[0] == b'Pid']
            if pid_rows != [[str(pid).encode()]] or before != after or before['ppid'] != parent:
                raise ValueError('same pidfd/lifetime and physical parent required')
            if (before['pid'] != pid or before['starttime'] <= 0
                    or (session is not None and (before['session'], before['pgrp']) != (session, session))
                    or (stopped and before['state'] not in {'t', 'T'})
                    or (terminal and before['state'] != 'Z')):
                raise ValueError('phase-specific native identity required')
            self.check()
            if time.monotonic()-begin > .5: raise TimeoutError('native identity packet .5s')
            return before
        finally: self.retire_fd(directory)

    def pair(self):
        pair = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET |
                                 socket.SOCK_CLOEXEC | socket.SOCK_NONBLOCK)
        self.channels.extend(pair)
        for endpoint in pair:
            self.hold(endpoint.fileno())
            endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        return pair

    def send(self, channel, packet, fd=None):
        self.check()
        raw = self.account(packet)
        ancillary = [] if fd is None else [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))]
        if channel.sendmsg([raw], ancillary, socket.MSG_DONTWAIT | socket.MSG_NOSIGNAL) != len(raw):
            raise ValueError('short/uncertain fixed message; never resend')
        self.check()

    def receive(self, channel, peer=None):
        self.check()
        if (channel is getattr(self, 'watch_channel', None)
                and not self.watch_pumping and self.watch_pending):
            return self.watch_pending.pop(0)  # native raw already retained/accounted
        if channel.fileno() in self.eof: return None
        try:
            raw, anc, flags, address = channel.recvmsg(65536, 128, socket.MSG_CMSG_CLOEXEC | socket.MSG_DONTWAIT)
        except BlockingIOError: return None
        owned, credentials, ancillary_errors = [], [], []
        # Register delivered rights BEFORE validating message or credentials.
        for level, kind, data in anc:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                values = array.array('i'); values.frombytes(data[:len(data)//4*4])
                if len(data) % 4: ancillary_errors.append('misaligned-rights')
                for fd in values:
                    owned.append(fd)
                    try: self.hold(fd)
                    except Exception as exc: ancillary_errors.append(type(exc).__name__)
        # All delivered rights now retained even when an earlier cmsg is invalid.
        remaining = max(0, self.budget.LIMIT-self.budget.total_retained)
        self.receive_raw.append(dict(raw=raw[:remaining], flags=flags,
            ancillary=[(level, kind, data.hex()) for level, kind, data in anc],
            delivered_fds=list(owned), ancillary_errors=list(ancillary_errors)))
        for level, kind, data in anc:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                continue
            elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
                import struct
                if len(data) != 12: ancillary_errors.append('credential-length')
                else: credentials.append(struct.unpack('=iii', data))
            else: ancillary_errors.append('unknown-ancillary')
        self.raw.append(raw[:max(0, self.budget.LIMIT-self.budget.total_retained)])
        self.budget.feed('trace', raw) if raw else None
        self.check()
        if not raw and not anc and not flags and not address:
            self.eof.add(channel.fileno())
            return None  # transport EOF does NOT imply terminal or cleanup
        if (ancillary_errors or not raw or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or address
                or len(credentials) != 1 or credentials[0][1:] != (self.uid, self.gid)
                or (peer is not None and credentials[0][0] != peer) or len(owned) > 1):
            raise ValueError('exact authenticated local transport required')
        packet = json.loads(raw)
        if type(packet) is not dict: raise ValueError('fixed packet object required')
        if (os.getpid() == self.harness_pid and channel is self.watch_channel
                and packet.get('op') in ('watch-fault', 'watch-cancel', 'watch-cancel-end', 'watch-cleanup-applied')):
            if owned: raise ValueError('watchdog failure evidence has no signal authority')
            if packet['op'] == 'watch-cleanup-applied':
                if set(packet) != {'op', 'deadline'} or self.cleanup_deadline is None:
                    raise ValueError('cleanup acknowledgement requires an existing FIRST bound')
                self.adopt_cleanup(packet['deadline'])
                self.watch_cleanup_confirmed = self.cleanup_deadline == packet['deadline']
                return None
            if packet['op'] == 'watch-fault':
                if set(packet) != {'op', 'deadline', 'error'} or type(packet['error']) is not str:
                    raise ValueError('exact watchdog fault required')
                if self.watch_fault_seen: raise ValueError('one watchdog fault notice only')
                self.watch_fault_seen = True
                self.budget.failure = 'watchdog-fault'
                self.watch_stopping = True
                self.adopt_cleanup(packet['deadline'])  # even invalid/expired FIRST cannot unlock fault
                raise RuntimeError('independent watchdog fault; no further release')
            self.accept_watch_cancel(packet)
            return None
        if packet.get('op') == 'raw-native':
            if owned or set(packet) != {'op', 'raw'} or type(packet['raw']) is not str:
                raise ValueError('raw evidence may not carry authority or rights')
            if len(packet['raw']) > 65536 or len(packet['raw']) % 2:
                raise ValueError('bounded exact native evidence encoding required')
            bytes.fromhex(packet['raw'])  # retained wire already counted globally
            return None
        return packet, owned, credentials[0][0]

    def ack(self, channel, sequence):
        self.send(channel, dict(op='ack', sequence=sequence))

    def await_ack(self, channel, peer, sequence):
        for _ in range(4096):
            item = self.receive(channel, peer)
            if item is None: time.sleep(.001); continue
            packet, fds, _ = item
            if packet != dict(op='ack', sequence=sequence) or fds:
                raise ValueError('exact one-shot harness ACK required')
            return
        raise TimeoutError('bounded ACK ticks exhausted')

    def dual_hold(self, role, fd, identity, stopped=False):
        """No startup/CONT grant until the second process holds exact lifetime."""
        if self.watch_channel is None or self.watch_stopping:
            raise ValueError('independent ready watchdog required before release')
        if role in self.watch_handoffs:
            raise ValueError('one exact handoff attempt per fixed role')
        self.watch_handoffs[role] = dict(identity=copy.deepcopy(identity))
        self.send(self.watch_channel, dict(op='watch-hold', role=role,
            identity=identity, stopped=stopped), fd)
        self.await_ack(self.watch_channel, self.watchdog, ROLES.index(role))
        self.watch_held[role] = dict(identity=copy.deepcopy(identity))

    def enter_cleanup(self):
        deadline = self.adopt_cleanup(self.budget.begin_cleanup(time.monotonic()))
        # The independent process autonomously stops at the original active
        # limit; a late parent notice cannot buy a fresh grace beyond that.
        if self.watch_channel is not None and not self.watch_stopping:
            try: self.send(self.watch_channel, dict(op='watch-cleanup', deadline=deadline))
            except Exception as exc:
                # Wire failure cannot suspend already-held cancellation. A
                # missing actual acknowledgement keeps deadline proof incomplete.
                self.watch_cleanup_confirmed = False
                self.errors.append(dict(phase='watch-cleanup-notice', error=type(exc).__name__))
        return deadline

    def adopt_cleanup(self, deadline):
        """Merge absolute FIRST notices by earliest bound; never renew grace."""
        deadline = ControlBudget._number(deadline)
        now = time.monotonic()
        local = self.budget.begin_cleanup(now)
        chosen = min(local, deadline, self.budget.active_deadline+10)
        if chosen != self.cleanup_deadline:
            self.watch_cleanup_confirmed = False
        self.budget.cleanup_deadline = self.cleanup_deadline = chosen
        self.watch_deadline_records.append(dict(local=local, received=deadline,
                                                adopted=chosen, observed_at=now))
        # A late notice is evidence of an exhausted bound, not a new grace.
        if chosen <= now: raise TimeoutError('shared FIRST cleanup already exhausted')
        return chosen

    def pump_watchdog(self):
        """One nonblocking watcher read per active check; never steal an ACK."""
        if self.watch_channel is None or self.watch_pumping or self.watch_stopping:
            return
        self.watch_pumping = True
        try:
            item = self.receive(self.watch_channel, self.watchdog)
            if item is not None:
                if len(self.watch_pending) >= 8:
                    raise ValueError('fixed watchdog pending packet bound')
                self.watch_pending.append(item)
        finally: self.watch_pumping = False

    def publish_watch(self, endpoint, packet):
        """Single bounded failure-evidence send, independent of failed ledger.

        Kernel credentials are checked by the receiver. EAGAIN/short/exception
        is missing evidence, never a reason to retry or delay cancellation.
        """
        raw = json.dumps(packet, sort_keys=True, separators=(',', ':')).encode('ascii')
        if not 0 < len(raw) <= 4096: raise ValueError('bounded watchdog failure record')
        try:
            count = endpoint.sendmsg([raw], [], socket.MSG_DONTWAIT | socket.MSG_NOSIGNAL)
            if count != len(raw): raise ValueError('uncertain watchdog evidence delivery')
        except Exception as exc:
            self.watch_publish_failures.append(dict(op=packet['op'], error=type(exc).__name__))
            self.watch_evidence_complete = False

    def accept_watch_cancel(self, packet):
        """Authenticated failure stream only; never creates signal authority.

        A final packet proves only the bounded records received so far. It does
        NOT prove EOF, terminal, shared FIRST, or successful tree cleanup.
        Pending handoffs only reconcile logs; never grant release or admission.
        """
        if self.watch_cancel_end is not None:
            raise ValueError('no failure stream packets after final boundary')
        inventory = {**self.watch_handoffs, **self.watch_held}
        if packet.get('op') == 'watch-cancel-end':
            expected = [dict(role=record['role'], pid=record['pid'], starttime=record['starttime'])
                        for record in self.watch_raw]
            digest = hashlib.sha256(json.dumps(self.watch_raw, sort_keys=True,
                separators=(',', ':')).encode('ascii')).hexdigest()
            if (set(packet) != {'op', 'count', 'lifetimes', 'sha256', 'publish_failures'}
                    or type(packet['count']) is not int
                    or packet['count'] != len(expected) or len(self.watch_raw) != len(expected)
                    or type(packet['lifetimes']) is not list
                    or any(type(item) is not dict or set(item) != {'role', 'pid', 'starttime'}
                        or type(item['role']) is not str or type(item['pid']) is not int
                        or type(item['starttime']) is not int for item in packet['lifetimes'])
                    or packet['lifetimes'] != expected or packet['sha256'] != digest
                    or type(packet['publish_failures']) is not int
                    or packet['publish_failures'] != 0
                    or not set(self.watch_held) <= self.watch_cancelled <= set(inventory)):
                raise ValueError('exact complete held-lifetime cancellation boundary required')
            self.watch_cancel_end = copy.deepcopy(packet)
            return
        if set(packet) != {'op', 'sequence', 'record'} or packet.get('op') != 'watch-cancel':
            raise ValueError('exact single cancellation result required')
        record = packet['record']
        if type(record) is not dict: raise ValueError('fixed cancellation record required')
        role = record.get('role')
        required = {'role', 'pid', 'starttime', 'signal', 'sent'}
        if (type(packet['sequence']) is not int or packet['sequence'] != len(self.watch_raw)
                or role not in inventory or role in self.watch_cancelled
                or type(record.get('pid')) is not int or type(record.get('starttime')) is not int
                or record.get('pid') != inventory[role]['identity']['pid']
                or record.get('starttime') != inventory[role]['identity']['starttime']
                or type(record.get('signal')) is not int or record['signal'] != 9
                or type(record.get('sent')) is not bool
                or (record['sent'] and set(record) != required)
                or (not record['sent'] and (set(record) != required | {'error', 'errno'}
                    or type(record['error']) is not str
                    or (record['errno'] is not None and type(record['errno']) is not int)))):
            raise ValueError('one exact sequenced result per dual-held cancellation lifetime')
        self.watch_cancelled.add(role)
        self.watch_raw.append(copy.deepcopy(record))

    def watchdog_role(self, pair, harness):
        """Only pidfd cancellation. NEVER waits the control tree or subreaps."""
        arm_parent_death(self.libc, os, harness)
        self.pid = os.getpid()
        self.audit_endpoint = pair[1]
        self._close_channel(pair[0])
        # The watchdog owns neither output pipeline nor control endpoints.
        for fd in [*self.output_reads.values(), *self.output_writes.values()]:
            self.retire_fd(fd)
        self.output_reads, self.output_writes = {}, {}
        self.await_ack(pair[1], harness, 0)  # harness holds this exact watchdog
        self.wait_policy()  # measured single thread/default/no-auto-reap; no SET subreaper
        self.send(pair[1], dict(op='watch-ready', active_deadline=self.budget.active_deadline))
        first = None
        failed = False
        fault_notified = False
        cancel_end_published = False
        retire_requested = False
        while True:
            now = time.monotonic()
            if first is None and now >= self.budget.active_deadline:
                first = self.budget.active_deadline+10
                failed = True
            if failed and not fault_notified:
                fault_notified = True  # uncertain send cannot retry
                self.publish_watch(pair[1], dict(op='watch-fault', deadline=first,
                                                error='watchdog-active-or-protocol-failure'))
            if first is not None:
                # Do not use active check after its expiry. This process still
                # owns one original absolute cleanup bound, never a renewed one.
                if self.cleanup_deadline is None:
                    self.budget.begin_cleanup(now)
                self.budget.cleanup_deadline = self.cleanup_deadline = first
                if failed or now >= first:
                    for role, held in self.watch_held.items():
                        if role in self.watch_cancelled: continue
                        self.watch_cancelled.add(role)  # uncertain send never retry
                        record = dict(role=role, pid=held['identity']['pid'],
                                      starttime=held['identity']['starttime'], signal=9)
                        try:
                            signal.pidfd_send_signal(held['fd'], 9, None, 0)
                            record['sent'] = True
                        except Exception as exc:
                            record.update(sent=False, error=type(exc).__name__, errno=getattr(exc, 'errno', None))
                        self.watch_raw.append(record)
                        self.publish_watch(pair[1], dict(op='watch-cancel',
                            sequence=len(self.watch_raw)-1, record=record))
                    if not cancel_end_published:
                        cancel_end_published = True  # no retry after uncertain delivery
                        digest = hashlib.sha256(json.dumps(self.watch_raw, sort_keys=True,
                            separators=(',', ':')).encode('ascii')).hexdigest()
                        self.publish_watch(pair[1], dict(op='watch-cancel-end',
                            count=len(self.watch_raw), sha256=digest,
                            lifetimes=[dict(role=role, pid=held['identity']['pid'],
                                starttime=held['identity']['starttime'])
                                for role, held in self.watch_held.items()],
                            publish_failures=len(self.watch_publish_failures)))
                    if now >= first:
                        os._exit(124)  # not cleanup success; harness must consume this role
                    if retire_requested:
                        for held in self.watch_held.values(): self.retire_fd(held['fd'])
                        self._close_channel(pair[1]); os._exit(124)
            try:
                item = self.receive(pair[1], harness)
                if item is None: time.sleep(.001); continue
                packet, fds, _ = item
                op = packet.get('op')
                if op == 'watch-hold':
                    role = packet.get('role')
                    if (failed or first is not None or role not in ROLES[1:] or role in self.watch_held
                            or len(fds) != 1):
                        raise ValueError('one active role handoff to independent watchdog')
                    expected = packet['identity']
                    if role == 'controller': parent = harness
                    else: parent = self.watch_held[PARENT[role]]['identity']['pid']
                    if role == 'observer': self.observer = expected['pid']
                    identity = self.identity(expected['pid'], fds[0], parent=parent,
                        session=self.observer if role in ('root', 'true-path', 'true-fd') else None,
                        stopped=packet['stopped'])
                    if identity['starttime'] != expected['starttime']:
                        raise ValueError('watchdog native lifetime differs from harness')
                    self.watch_held[role] = dict(fd=fds[0], identity=identity)
                    self.ack(pair[1], ROLES.index(role))
                elif op == 'watch-cleanup':
                    deadline = packet.get('deadline')
                    if (fds or type(deadline) not in (int, float)
                            or not now < ControlBudget._number(deadline)
                            <= min(now+10, self.budget.active_deadline+10)):
                        raise ValueError('FIRST cleanup deadline cannot renew or move')
                    first = min(first, deadline) if first is not None else deadline
                    self.publish_watch(pair[1], dict(op='watch-cleanup-applied', deadline=first))
                elif op == 'watch-stop':
                    if fds or first is None or failed:
                        raise ValueError('watchdog stop only after normal retirement')
                    self.send(pair[1], dict(op='watch-done', cancel_records=self.watch_raw))
                    for held in self.watch_held.values(): self.retire_fd(held['fd'])
                    self._close_channel(pair[1]); os._exit(0)
                elif op == 'watch-failure-retire':
                    if (fds or set(packet) != {'op', 'deadline'} or retire_requested
                            or not now < ControlBudget._number(packet['deadline'])
                            <= min(now+10, self.budget.active_deadline+10)):
                        raise ValueError('one failure retirement under original FIRST only')
                    first = min(first, packet['deadline']) if first is not None else packet['deadline']
                    retire_requested, failed = True, True
                    self.publish_watch(pair[1], dict(op='watch-cleanup-applied', deadline=first))
                else: raise ValueError('unknown watchdog instruction')
            except Exception:
                failed = True
                first = min(first if first is not None else now+10, self.budget.active_deadline+10)
                # Cancellation continues despite wire/accounting/parser failure.
                # No failure is rewritten as a normal watch-done or terminal.

    def start_watchdog(self):
        """Ready before controller creation; watchdog is a registered child."""
        pair = self.pair()
        self.watchdog = os.fork()
        if self.watchdog == 0:
            try: self.watchdog_role(pair, self.harness_pid)
            finally: os._exit(125)
        self.direct_births['watchdog'] = self.watchdog  # before pidfd/identity may fail
        fd = self.hold(os.pidfd_open(self.watchdog, 0))
        identity = self.identity(self.watchdog, fd, parent=self.harness_pid)
        self.ledger.register('watchdog', identity, fd)
        self._close_channel(pair[1])
        self.watch_channel = pair[0]
        self.ack(pair[0], 0)
        for _ in range(4096):
            item = self.receive(pair[0], self.watchdog)
            if item is None: time.sleep(.001); continue
            packet, fds, _ = item
            if fds or packet != dict(op='watch-ready', active_deadline=self.budget.active_deadline):
                raise ValueError('measured watchdog ready before controller fork')
            return
        raise TimeoutError('watchdog ready missing')

    def stop_watchdog(self):
        self.send(self.watch_channel, dict(op='watch-stop'))
        self.watch_stopping = True
        for _ in range(4096):
            item = self.receive(self.watch_channel, self.watchdog)
            if item is None: time.sleep(.001); continue
            packet, fds, _ = item
            if fds or set(packet) != {'op', 'cancel_records'} or packet['op'] != 'watch-done':
                raise ValueError('exact watchdog final transport required')
            if packet['cancel_records'] != [] or self.watch_raw:
                raise ValueError('normal watchdog must not have cancelled a control lifetime')
            self.watch_raw = packet['cancel_records']
            self.watch_evidence_complete = True
            break
        else: raise TimeoutError('watchdog final transport missing')
        fd = self.ledger.held['watchdog']
        row = self.wait_terminal(self.watchdog, fd, True)
        if row['si_code'] != 1 or row['si_status'] != 0:
            raise ValueError('watchdog normal terminal missing')
        lifetime = self.ledger.created['watchdog']
        self.ledger.terminal('watchdog', self.watchdog, lifetime['starttime'], 0, 'harness')

    def recover_watchdog(self):
        """Failure-only exact direct-child wait, before the final P_ALL census.

        The watchdog never reaps the control tree. Killing it is not proof of
        control-tree cleanup, and an ambiguous consuming attempt is not retried.
        """
        if 'watchdog' not in self.ledger.created or 'watchdog' in self.ledger.terminals:
            return
        row = self.ledger.created['watchdog']
        if row['pid'] in self.consume_attempts:
            raise RuntimeError('watchdog consuming attempt uncertain; no retry')
        # Do not relinquish the sole remaining supervisor on inferred signal
        # success: every registered control lifetime needs an exact terminal.
        controls = set(self.ledger.created)-{'watchdog'}
        if controls - set(self.ledger.terminals):
            raise ValueError('known control lifetimes must be terminal before watcher retirement')
        if self.watch_channel is not None and not self.watch_retire_attempted:
            self.check()
            self.watch_retire_attempted = True  # uncertain delivery NEVER retried
            self.watch_stopping = True
            try:
                self.send(self.watch_channel, dict(op='watch-failure-retire',
                                                   deadline=self.cleanup_deadline))
            except Exception as exc:
                self.watch_stream_invalid = True
                self.errors.append(dict(phase='watch-failure-retire', error=type(exc).__name__))
            else:
                self.finish_failed_watchdog(row)
                return
        if not self.watch_cancel_attempted:
            self.check()
            self.watch_cancel_attempted = True
            self.watch_stopping = True
            try: signal.pidfd_send_signal(self.ledger.held['watchdog'], 9, None, 0)
            except Exception as exc:
                self.errors.append(dict(phase='watchdog-force-cancel',
                    error=type(exc).__name__, errno=getattr(exc, 'errno', None)))
                # Uncertain signal is never retried, but is not a terminal.
                # Keep observing the owned direct child under original FIRST.
        event = self.wait_terminal(row['pid'], self.ledger.held['watchdog'], True)
        self.ledger.terminal('watchdog', row['pid'], row['starttime'], self.wait_word(event), 'harness')
        # A terminal is NOT a complete evidence-stream boundary. Drain queued
        # failure packets boundedly; missing/invalid/late remains incomplete.
        for _ in range(32):
            try:
                item = self.receive(self.watch_channel, self.watchdog)
                if item is not None: raise ValueError('unexpected watchdog recovery packet')
                if self.watch_channel.fileno() in self.eof: break
            except Exception as exc:
                self.errors.append(dict(phase='watchdog-evidence-drain', error=type(exc).__name__))
                break

    def finish_failed_watchdog(self, row):
        """Hold terminal, drain EOF, then single consume; all under FIRST.

        Transport proof is separate from all-tree cleanup/qualification. A
        missing packet never authorizes a second signal or consuming wait.
        """
        held = None
        for _ in range(4096):
            self.check()
            try:
                item = self.receive(self.watch_channel, self.watchdog)
                if item is not None: raise ValueError('unexpected failure-retirement packet')
            except RuntimeError as exc:
                # Exact first authenticated fault is a one-way failure notice,
                # not a reason to drop subsequent cancellation evidence.
                if str(exc) != 'independent watchdog fault; no further release':
                    self.watch_stream_invalid = True
                self.errors.append(dict(phase='watch-failed-stream', error=str(exc)))
            except Exception as exc:
                self.watch_stream_invalid = True
                self.errors.append(dict(phase='watch-failed-stream', error=type(exc).__name__))
            if held is None:
                held = self.terminal(row['pid'], self.ledger.held['watchdog'])
            if held is not None and self.watch_channel.fileno() in self.eof:
                actual = self.terminal(row['pid'], self.ledger.held['watchdog'], True)
                if actual != held: raise ValueError('uncertain watcher consumption; never retry')
                self.ledger.terminal('watchdog', row['pid'], row['starttime'],
                                     self.wait_word(actual), 'harness')
                self.check()
                self.watch_evidence_complete = (not self.watch_stream_invalid
                    and self.watch_cancel_end is not None and self.watch_cleanup_confirmed
                    and held['si_code'] == 1 and held['si_status'] == 124)
                return
            time.sleep(.001)
        raise TimeoutError('failed watcher boundary/EOF/terminal missing under FIRST')

    def recover_pending_direct(self, role):
        """DRAFT source-derived fork rollback only; never normal admission.

        Before creation permission and with the frozen harness sole-waiter/no
        auto-reap policy, its direct unreaped fork child cannot recycle its PID.
        This path does not grant authority over any received/adopted PID. Exact
        source/card independent review is still REQUIRED before native use.
        """
        if (role not in ('controller', 'watchdog') or role in self.ledger.created
                or role not in self.direct_births):
            raise ValueError('own unregistered fixed direct fork child only')
        pid = self.direct_births[role]
        if type(pid) is not int or pid <= 0 or pid == self.harness_pid:
            raise ValueError('exact positive own fork result required')
        if pid in self.consume_attempts:
            raise RuntimeError('pending child consume already attempted; never retry')
        if role not in self.pending_cancelled:
            self.check()  # original FIRST cleanup clock before signal
            self.pending_cancelled.add(role)  # uncertain signal is never retried
            result = dict(role=role, pid=pid, signal=9, sent=False)
            try:
                os.kill(pid, signal.SIGKILL)
                result['sent'] = True
            except Exception as exc:
                result.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
                self.errors.append(dict(phase='pending-direct-cancel', **result))
            self.account(dict(pending_direct_cancel=result))
            # An error is not a terminal: still inspect this exact child once
            # under the original bound, and never retry the signal.
        held = None
        for _ in range(4096):
            self.check()
            event = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if event is None: time.sleep(.001); continue
            held = {k: getattr(event, k) for k in ('si_pid', 'si_uid', 'si_signo', 'si_code', 'si_status')}
            self.wait_raw.append(dict(pending_role=role, wait=copy.deepcopy(held), consumed=False))
            if (held['si_pid'] != pid or held['si_uid'] != self.uid or held['si_signo'] != 17
                    or held['si_code'] not in TERMINAL_CODES):
                raise ValueError('exact direct-child held terminal required')
            break
        if held is None: raise TimeoutError('pending direct child terminal missing')
        self.consume_attempts[pid] = dict(pending_role=role, attempted=True, result=None)
        try: actual = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG)
        except Exception as exc:
            self.consume_attempts[pid]['error'] = type(exc).__name__
            raise
        row = None if actual is None else {k: getattr(actual, k) for k in held}
        self.consume_attempts[pid]['result'] = copy.deepcopy(row)
        self.wait_raw.append(dict(pending_role=role, wait=copy.deepcopy(row), consumed=True))
        self.account(dict(pending_direct_child=role, held=held, consumed=row,
                          normal_admission=False, qualified=False))
        self.check()
        if row != held: raise ValueError('uncertain pending consumption; never retry')

    def terminal(self, pid, fd, consume=False):
        self.check()
        if pid in self.consume_attempts:
            raise ValueError('consuming attempt permanently retires lifetime wait authority')
        if consume:
            self.consume_attempts[pid] = dict(fd=fd, attempted=True, result=None)
        flags = os.WEXITED | os.WNOHANG | (0 if consume else os.WNOWAIT)
        try: event = os.waitid(os.P_PIDFD, fd, flags)
        except Exception as exc:
            if consume: self.consume_attempts[pid]['error'] = type(exc).__name__
            raise
        if event is None:
            self.wait_raw.append(dict(wait=None, consumed=consume))
            self.check()
            if consume: raise ValueError('consuming wait returned no terminal; never retry')
            return None
        row = {key: getattr(event, key) for key in ('si_pid', 'si_uid', 'si_signo', 'si_code', 'si_status')}
        self.wait_raw.append(dict(wait=copy.deepcopy(row), consumed=consume))
        if consume: self.consume_attempts[pid]['result'] = copy.deepcopy(row)
        self.account(dict(wait=row, consumed=consume))
        self.check()
        if (row['si_pid'] != pid or row['si_uid'] != self.uid or row['si_signo'] != 17
                or row['si_code'] not in TERMINAL_CODES
                or not 0 <= row['si_status'] <= (255 if row['si_code'] == 1 else 64)):
            raise ValueError('exact held terminal siginfo required')
        return row

    def wait_terminal(self, pid, fd, consume=False):
        for _ in range(4096):
            row = self.terminal(pid, fd)
            if row is None: time.sleep(.001); continue
            if consume:
                actual = self.terminal(pid, fd, True)
                if actual != row: raise ValueError('uncertain consume; never retry')
            return row
        raise TimeoutError('bounded terminal ticks exhausted')

    def _close_channel(self, endpoint):
        fd = endpoint.detach()  # disarm wrapper before identity guarded close
        self.retire_fd(fd)

    def controller_role(self, ctrl, obs_pair, harness):
        """Only direct observer waiter; copied harness endpoints are retired."""
        arm_parent_death(self.libc, os, harness)
        self.pid = os.getpid()
        self.audit_endpoint = ctrl[1]
        # Controller must not keep either inherited watchdog endpoint alive.
        if self.watch_channel is not None: self._close_channel(self.watch_channel)
        self.inherit_outputs()
        self._close_channel(ctrl[0]); self._close_channel(obs_pair[0])
        self.await_ack(ctrl[1], harness, 0)  # harness holds controller BEFORE any child creation
        Subreaper(self.libc, self.pid).arm(self.wait_policy, self.budget.active_deadline)
        parent = self.pid
        self.observer = os.fork()
        if self.observer == 0:
            try: self.observer_role(ctrl[1], obs_pair[1], parent, harness)
            finally: os._exit(125)
        handle = self.hold(os.pidfd_open(self.observer, 0))
        self.send(ctrl[1], dict(op='observer-created', pid=self.observer), handle)
        self._close_channel(obs_pair[1])  # controller never forwards root messages
        self.await_ack(ctrl[1], harness, 1)
        for _ in range(4096):
            self.check()
            pid, status = os.waitpid(self.observer, os.WUNTRACED | os.WNOHANG)
            if pid == 0: time.sleep(.001); continue
            if pid != self.observer or status != 19 << 8 | 0x7f:
                raise ValueError('observer must stop after setsid before root')
            break
        else: raise TimeoutError('observer session stop missing')
        self.send(ctrl[1], dict(op='observer-session-stopped', pid=self.observer))
        self.await_ack(ctrl[1], harness, 2)
        # Exact owned direct child is held stopped and unreaped; source reviewed
        # SIGCONT is bootstrap release, not arbitrary PID signal authority.
        os.kill(self.observer, signal.SIGCONT)
        row = self.wait_terminal(self.observer, handle)
        self.send(ctrl[1], dict(op='observer-terminal-held', terminal=row))
        self.await_ack(ctrl[1], harness, 3)  # AFTER harness last group retirement
        actual = self.terminal(self.observer, handle, True)
        if row != actual: raise ValueError('observer consume mismatch')
        self.send(ctrl[1], dict(op='observer-consumed', terminal=actual))
        self.retire_fd(handle)
        self._close_channel(ctrl[1])
        os._exit(0)

    def observer_role(self, ctrl_endpoint, endpoint, parent, harness):
        arm_parent_death(self.libc, os, parent)
        self._close_channel(ctrl_endpoint)
        self.pid = self.observer = os.getpid()
        self.audit_endpoint = endpoint
        self.await_ack(endpoint, harness, 1)  # pre-session handle held by harness
        os.setsid()
        os.kill(self.pid, signal.SIGSTOP)
        self.check(); self.wait_policy()
        launcher = RootLauncher(libc=self.libc)
        gate = RootLaunchGate(self.budget)
        sequence = 1
        def handoff(root, pidfd):
            nonlocal sequence
            sequence += 1
            self.send(endpoint, dict(op='root-pretrace', pid=root, sequence=sequence), pidfd)
            self.await_ack(endpoint, harness, sequence)
            return dict(root=root, pidfd=pidfd, recovery_held=True)
        root, bootstrap = launcher.launch(ROOT_SOURCE, self.true_fd,
            dict(PATH='/usr/bin:/bin', HOME=self.cwd, LC_ALL='C'), self.wait_policy,
            gate=gate, handoff=handoff)
        ops = ptrace.NativeStops(self.pid, resources)
        # Forward every retained native request/observation before giving the
        # caller its result; failures preserve a partial wire transcript. The
        # harness, not this fork's local budget copy, owns the aggregate limit.
        for name in ('wait', 'ptrace', 'bind_stopped', 'sample', 'kill_bound', 'close_handles'):
            original = getattr(ops, name)
            def recorded(*args, _name=name, _call=original, **kwargs):
                calls, observations = len(ops.calls), len(ops.observations)
                try: return _call(*args, **kwargs)
                finally:
                    for row in ops.calls[calls:]: self.account(dict(native_operation=_name, call=row))
                    for row in ops.observations[observations:]:
                        self.account(dict(native_operation=_name, observation=row))
            setattr(ops, name, recorded)
        loop = ptrace.FixedForkStops(root, self.pid, ops)
        def first_stop(pid, parent_pid, packet):
            nonlocal sequence
            sequence += 1
            self.send(endpoint, dict(op='first-stop', pid=pid, parent=parent_pid,
                sequence=sequence, sample=packet), ops.handles[pid]['fd'])
            self.await_ack(endpoint, harness, sequence)
            return True
        loop.journal_emit = first_stop
        result = loop.run(self.budget.active_deadline)
        self.send(endpoint, dict(op='tracees-consumed', admitted=result['admitted'],
                                 terminals=result['terminals'], sequence=sequence+1))
        ops.close_handles(); os.close(bootstrap)
        self._close_channel(endpoint)
        os._exit(0)

    def last_group_cancel(self, failure):
        self.ledger.begin_cancel()
        role = self.ledger.created['observer']
        # Held leader cannot be reused: normal controller is waiting for ACK3
        # without consuming, or controller is dead and cannot consume anything.
        self.identity(role['pid'], self.ledger.held['observer'],
                      # Kernel reparenting occurs at controller termination,
                      # not at consuming wait. Recovery already holds its
                      # WNOWAIT terminal; the harness subreaper is the parent.
                      parent=self.controller if not failure else self.pid,
                      session=role['pid'])
        os.killpg(role['pid'], signal.SIGKILL)
        self.ledger.retire(sent=True, failed_recovery=failure)

    def normal(self, ctrl, obs):
        self.await_bootstrap(ctrl, obs)
        consumed = False
        for _ in range(4096):
            self.check()
            item = self.receive(obs, self.observer)
            if item is not None:
                packet, fds, _ = item
                op = packet.get('op')
                if op in ('root-pretrace', 'first-stop'):
                    if len(fds) != 1: raise ValueError('exact recovery pidfd required')
                    role = 'root' if op == 'root-pretrace' or packet['pid'] == self.ledger.created['root']['pid'] else (
                        'true-path' if 'true-path' not in self.ledger.created else 'true-fd')
                    parent = self.observer if role == 'root' else self.ledger.created['root']['pid']
                    identity = self.identity(packet['pid'], fds[0], parent=parent,
                                              session=self.observer, stopped=op == 'first-stop')
                    if role not in self.ledger.created: self.ledger.register(role, identity, fds[0])
                    else:
                        if identity['starttime'] != self.ledger.created[role]['starttime']:
                            raise ValueError('first stop lifetime mismatch')
                        self.retire_fd(fds[0])  # retain original root recovery FD
                    if op == 'first-stop':
                        sample = packet['sample']
                        if sample['before'] != identity or sample['after'] != identity:
                            raise ValueError('first-stop sample differs from held native identity')
                        self.ledger.stop(role, identity['starttime'], sample['rss_bytes'])
                    if role not in self.watch_held:
                        self.dual_hold(role, self.ledger.held[role], identity, stopped=op == 'first-stop')
                        self.watch_held[role] = dict(identity=copy.deepcopy(identity))
                    self.sequence[1] += 1
                    if packet['sequence'] != self.sequence[1]+1: raise ValueError('observer message order')
                    if self.failure_mode == 'controller-death' and role == 'root' and op == 'first-stop':
                        signal.pidfd_send_signal(self.ledger.held['controller'], 9, None, 0)
                        raise RuntimeError('fixed controller-death injection')
                    self.ack(obs, packet['sequence'])
                elif op == 'tracees-consumed':
                    if fds or consumed: raise ValueError('one final observer summary required')
                    if set(self.ledger.stopped) != {'root', 'true-path', 'true-fd'}:
                        raise ValueError('all three first-stop handoffs required')
                    for role in ('root', 'true-path', 'true-fd'):
                        row = self.ledger.created[role]; pid = row['pid']
                        if packet['admitted'].get(str(pid)) != row['starttime']:
                            raise ValueError('summary lifetime mismatch')
                        self.ledger.terminal(role, pid, row['starttime'], packet['terminals'][str(pid)], 'observer')
                    consumed = True
                else: raise ValueError('unknown observer event')
            item = self.receive(ctrl, self.controller)
            if item is not None:
                packet, fds, _ = item
                if fds: raise ValueError('no post-bootstrap controller FD accepted')
                if packet.get('op') == 'observer-terminal-held':
                    if not consumed: raise ValueError('missing normal tracee summary')
                    self.enter_cleanup()
                    self.last_group_cancel(False)
                    self.ack(ctrl, 3)
                elif packet.get('op') == 'observer-consumed':
                    row = self.ledger.created['observer']; terminal = packet['terminal']
                    if terminal['si_pid'] != row['pid'] or terminal['si_code'] != 1 or terminal['si_status'] != 0:
                        raise ValueError('normal observer must consume zero exit')
                    self.ledger.terminal('observer', row['pid'], row['starttime'], 0, 'controller')
                    return
                else: raise ValueError('unknown controller event')
            dead = self.terminal(self.controller, self.ledger.held['controller'])
            if dead is not None: raise RuntimeError('controller died before complete normal retirement')
            time.sleep(.001)
        raise TimeoutError('bounded whole-tree active ticks exhausted')

    def await_bootstrap(self, ctrl, obs):
        for _ in range(4096):
            item = self.receive(ctrl, self.controller)
            if item is None: time.sleep(.001); continue
            packet, fds, _ = item
            if set(packet) != {'op', 'pid'} or packet['op'] != 'observer-created' or len(fds) != 1:
                raise ValueError('one fixed observer creation with held FD required')
            self.observer = packet['pid']
            identity = self.identity(self.observer, fds[0], parent=self.controller)
            self.ledger.register('observer', identity, fds[0])
            self.dual_hold('observer', fds[0], identity)
            self.watch_held['observer'] = dict(identity=copy.deepcopy(identity))
            self.ack(obs, 1); self.ack(ctrl, 1)
            break
        else: raise TimeoutError('observer pre-session handoff missing')
        for _ in range(4096):
            item = self.receive(ctrl, self.controller)
            if item is None: time.sleep(.001); continue
            packet, fds, _ = item
            if packet != dict(op='observer-session-stopped', pid=self.observer) or fds:
                raise ValueError('fixed observer session stop receipt required')
            identity = self.identity(self.observer, self.ledger.held['observer'], parent=self.controller,
                                      session=self.observer)
            self.ledger.confirm_session(identity); self.ack(ctrl, 2)
            return
        raise TimeoutError('observer post-session confirmation missing')

    def recovery(self):
        """Failure-only adoption; unknown terminal never receives CONT/signal."""
        self.ledger.failed = True
        self.enter_cleanup()
        controller = self.ledger.created['controller']
        attempt = self.consume_attempts.get(controller['pid'])
        if attempt is not None:
            # An uncertain consuming syscall may have removed the parent. Never
            # wait/reap it again or infer adoption from an absent return value.
            # Still exercise only already-held pidfd cancellation and drain raw
            # output; no retired PGID is resurrected to make recovery look good.
            self.cancel_remaining_handles()
            self.drain_failed_output()
            raise RuntimeError('controller consume already attempted; recovery remains incomplete')
        signal.pidfd_send_signal(self.ledger.held['controller'], 9, None, 0)
        retained = self.wait_terminal(controller['pid'], self.ledger.held['controller'])
        if self.ledger.group_state == 'held':
            if self.ledger.session_confirmed:
                self.last_group_cancel(True)
            else:
                # No valid observer PGID has ever been acquired. Never invent
                # one from the inherited pre-session numeric group identity.
                self.ledger.abandon_unacquired_group()
        actual = self.terminal(controller['pid'], self.ledger.held['controller'], True)
        if retained != actual: raise ValueError('uncertain controller consumption')
        self.ledger.terminal('controller', controller['pid'], controller['starttime'], self.wait_word(actual), 'harness')
        if 'observer' in self.ledger.created and not self.ledger.observer_consumed:
            row = self.ledger.created['observer']; fd = self.ledger.held['observer']
            event = self.wait_terminal(row['pid'], fd, True)
            self.ledger.terminal('observer', row['pid'], row['starttime'], self.wait_word(event), 'harness')
        # Do not consume/stop the independent supervisor while known tracees
        # are still live. It remains a cancellation capability until these exact
        # held lifetimes have terminal receipts (unknown adoption stays bounded).
        for role in ('root', 'true-path', 'true-fd'):
            if role in self.ledger.created and role not in self.ledger.terminals:
                row = self.ledger.created[role]
                event = self.wait_terminal(row['pid'], self.ledger.held[role], True)
                self.ledger.terminal(role, row['pid'], row['starttime'], self.wait_word(event), 'harness')
        self.recover_watchdog()
        for _ in range(4096):
            self.check(); self.wait_policy()
            try: event = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT | 0x40000000)
            except ChildProcessError:
                self.account(dict(census='ECHILD', phase='failed-recovery'))
                return  # candidate only; cannot qualify original control
            if event is None: time.sleep(.001); continue
            if event.si_code not in TERMINAL_CODES or event.si_uid != self.uid or event.si_signo != 17:
                raise ValueError('unknown nonterminal never admitted')
            pid = event.si_pid
            fd = self.hold(os.pidfd_open(pid, 0))
            session = self.observer if self.ledger.session_confirmed else None
            identity = self.identity(pid, fd, parent=self.pid, session=session, terminal=True)
            role = next((r for r, v in self.ledger.created.items() if v['pid'] == pid), None)
            if role is None:
                # Strict failure-only terminal receipt, NOT normal registration.
                if len(self.ledger.created)+len(self.unknown) >= 6:
                    raise ValueError('fixed topology recovery lifetime bound exceeded')
                self.unknown.append(dict(identity=identity, fd=fd, terminal_only=True))
            elif identity['starttime'] != self.ledger.created[role]['starttime']:
                raise ValueError('adopted lifetime changed')
            row = self.wait_terminal(pid, fd, True)
            if row['si_code'] != event.si_code or row['si_status'] != event.si_status:
                raise ValueError('P_ALL/P_PIDFD held terminal mismatch')
            if role is not None:
                self.ledger.terminal(role, pid, identity['starttime'], self.wait_word(row), 'harness')
            self.account(dict(recovery_terminal=identity, consumed=row, registered_role=role))
            self.retire_fd(fd)
        raise TimeoutError('bounded recovery census ticks exhausted')

    def cancel_remaining_handles(self):
        for role, fd in self.ledger.held.items():
            if role in self.ledger.terminals: continue
            try:
                self.check()
                signal.pidfd_send_signal(fd, 9, None, 0)
                self.account(dict(failure_cancel_held=role, fd=fd, signal=9, sent=True))
            except Exception as exc:
                self.errors.append(dict(phase='held-cancel', role=role,
                    type=type(exc).__name__, errno=getattr(exc, 'errno', None)))
                # A failed signal is not terminal evidence. Continue other held
                # cancellation capabilities without retrying this one.

    def drain_failed_output(self):
        for _ in range(4096):
            try:
                self.check()
                if all(self.budget.records[n]['eof'] for n in ('stdout', 'stderr')): return
                time.sleep(.001)
            except Exception as exc:
                self.errors.append(dict(phase='failed-output-drain', type=type(exc).__name__))
                return

    def finish_normal(self, ctrl, obs):
        """All EOF + native census + read-only group absence + final clock."""
        if (not self.watch_cleanup_confirmed or self.ledger.group_state != 'retired-normal'
                or set(self.ledger.terminals) != set(ROLES)):
            raise ValueError('full normal terminal coverage before final census')
        empty = False
        for _ in range(4096):
            self.check()
            for endpoint, peer in ((ctrl, self.controller), (obs, self.observer)):
                item = self.receive(endpoint, peer)
                if item is not None:
                    raise ValueError('unexpected authority packet after normal terminals')
            if not empty:
                self.wait_policy()
                try:
                    event = os.waitid(os.P_ALL, 0,
                                     os.WEXITED | os.WNOHANG | os.WNOWAIT | 0x40000000)
                except ChildProcessError:
                    self.account(dict(census='ECHILD', phase='normal-final'))
                    empty = True
                else:
                    if event is not None: raise ValueError('unknown terminal in normal census')
            if (empty and all(e.fileno() in self.eof for e in (ctrl, obs))
                    and all(self.budget.records[n]['eof'] for n in ('stdout', 'stderr'))):
                try: os.killpg(self.observer, 0)  # read-only, NEVER a post-retirement signal
                except ProcessLookupError: self.account(dict(group_absent=self.observer))
                else: raise ValueError('group absence not proved')
                self.budget.eof('trace')
                self.check()
                return
            time.sleep(.001)
        raise TimeoutError('final output/census closure missing within FIRST cleanup')

    @staticmethod
    def wait_word(row):
        return row['si_status'] << 8 if row['si_code'] == 1 else row['si_status'] | (0x80 if row['si_code'] == 3 else 0)

    def run_control(self, true_fd, cwd, *, failure_mode='normal'):
        # No callback, boolean receipt or environment variable can unlock this.
        # Remove only after exact whole-entry source/card independent review and
        # actual native provenance/limits/cleanup wiring, never before it.
        raise RuntimeError('whole-entry native execution gate remains closed')

    def _run_unreviewed_draft(self, true_fd, cwd, *, failure_mode='normal'):
        """Explicit unqualified native draft, NOT an authorized execution API.

        Source/tool/stdlib/procfs/kernel receipts and dedicated launcher remain
        an execution blocker: no existing workflow or CLI may call this draft.
        The inputs are already-owned fixed true FD and empty private cwd, not
        project paths. Full exact source/card review is mandatory before use.
        """
        if (platform.system() != 'Linux' or platform.machine() != 'x86_64'
                or failure_mode not in {'normal', 'controller-death'}):
            raise ValueError('fixed Linux x64 control mode only')
        self.true_fd, self.cwd, self.failure_mode = true_fd, cwd, failure_mode
        self.unknown = []
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.wait_policy()
        Subreaper(self.libc, self.pid).arm(self.wait_policy, self.budget.active_deadline)
        self.prepare_outputs()
        try:
            self.start_watchdog()
            ctrl, obs = self.pair(), self.pair()
            self.controller = os.fork()
            if self.controller == 0:
                try: self.controller_role(ctrl, obs, self.pid)
                finally: os._exit(125)
            self.direct_births['controller'] = self.controller
            controller_pidfd = self.hold(os.pidfd_open(self.controller, 0))
            identity = self.identity(self.controller, controller_pidfd, parent=self.pid)
            self.ledger.register('controller', identity, controller_pidfd)
            self.dual_hold('controller', controller_pidfd, identity)
            self.watch_held['controller'] = dict(identity=copy.deepcopy(identity))
            self._close_channel(ctrl[1]); self._close_channel(obs[1])
            for output_fd in self.output_writes.values(): self.retire_fd(output_fd)
            self.output_writes = {}
            self.ack(ctrl[0], 0)
            self.normal(ctrl[0], obs[0])
            row = self.wait_terminal(self.controller, controller_pidfd, True)
            self.ledger.terminal('controller', self.controller, identity['starttime'], self.wait_word(row), 'harness')
            self.stop_watchdog()
            self.finish_normal(ctrl[0], obs[0])
        except Exception as exc:
            self.errors.append(dict(phase='active', type=type(exc).__name__, errno=getattr(exc, 'errno', None)))
            # No ordinary child can be created before controller ACK0; pending
            # bootstrap rollback is a fixed own-fork source capability, not a
            # weakening of native admission or unknown-adoption policy.
            if any(r not in self.ledger.created for r in self.direct_births):
                try:
                    self.enter_cleanup()
                except Exception as cleanup:
                    self.errors.append(dict(phase='pending-direct-recovery',
                        type=type(cleanup).__name__, errno=getattr(cleanup, 'errno', None)))
                for role in ('controller', 'watchdog'):
                    if role in self.direct_births and role not in self.ledger.created:
                        try: self.recover_pending_direct(role)
                        except Exception as cleanup:
                            self.errors.append(dict(phase='pending-direct-recovery', role=role,
                                type=type(cleanup).__name__, errno=getattr(cleanup, 'errno', None)))
            if 'controller' in self.ledger.created:
                try: self.recovery()
                except Exception as cleanup:
                    self.errors.append(dict(phase='recovery', type=type(cleanup).__name__, errno=getattr(cleanup, 'errno', None)))
            elif 'watchdog' in self.ledger.created:
                try:
                    self.enter_cleanup()
                    self.ledger.abandon_unacquired_group()
                    self.recover_watchdog()
                except Exception as cleanup:
                    self.errors.append(dict(phase='watchdog-bootstrap-recovery',
                        type=type(cleanup).__name__, errno=getattr(cleanup, 'errno', None)))
        finally:
            for endpoint in self.channels:
                if endpoint.fileno() >= 0:
                    try: self._close_channel(endpoint)
                    except Exception as exc: self.errors.append(dict(phase='channel-close', type=type(exc).__name__))
            for fd in list(self.fds):
                try: self.retire_fd(fd)
                except Exception as exc: self.errors.append(dict(phase='fd-close', type=type(exc).__name__))
        return dict(ledger=self.ledger.report(), errors=list(self.errors),
                    unknown_terminal_recovery=copy.deepcopy(self.unknown),
                    consuming_attempts=copy.deepcopy(self.consume_attempts),
                    output_failed=self.output_failed, cleanup_omitted=self.cleanup_omitted,
                    watchdog_records=copy.deepcopy(self.watch_raw),
                    watchdog_cancel_end=copy.deepcopy(self.watch_cancel_end),
                    watchdog_evidence_complete=self.watch_evidence_complete,
                    watchdog_deadlines=copy.deepcopy(self.watch_deadline_records),
                    watchdog_cleanup_confirmed=self.watch_cleanup_confirmed,
                    direct_fork_births=copy.deepcopy(self.direct_births),
                    cleanup_omitted_sha256=self.cleanup_omitted_hash.hexdigest(),
                    qualified=False, outer_cleanup_complete=False,
                    execution_card_approved=False)
