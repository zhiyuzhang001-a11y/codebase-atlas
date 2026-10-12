"""Unwired C0-O5 Linux harness receive draft; no launch or ACK authority.

Receives at most five fixed lifetime messages from a dedicated inherited local
endpoint. Kernel credentials are NOT a lifetime/source audit; received pidfds
remain unvalidated until the full native harness binds their exact proc identity.
Only injected socket/OS APIs are used in tests. No CLI or experiment entry.
"""
import copy
import os
import stat
import struct
import sys
import time


FRAME = struct.Struct('!8sIIQ')
MAGIC = b'ATHELD01'
ACK_MAGIC = b'ATACKD01'
CREDS = struct.Struct('=iII')
FD = struct.Struct('=i')
# Frozen Linux ABI; native construction is Linux-only. No Windows constant access.
SOL_SOCKET, SO_TYPE, SO_PASSCRED, SO_DOMAIN = 1, 3, 16, 39
AF_UNIX, SOCK_SEQPACKET, SCM_RIGHTS, SCM_CREDENTIALS = 1, 5, 1, 2
MSG_CMSG_CLOEXEC, MSG_DONTWAIT, MSG_TRUNC, MSG_CTRUNC = 0x40000000, 0x40, 0x20, 8
# Linux LP64 cmsghdr=16, alignment=8: SPACE(ucred12)+SPACE(4*int4)=32+32.
ANCILLARY_LIMIT = 64


class HandoffPair:
    """Own a dedicated socketpair before any role fork; no launch/ACK entry.

    Role inheritance and sender lifetime/source authentication remain the
    reviewed harness caller's duties. Tests inject every socket operation.
    """
    def __init__(self, budget, *, socket_api=None, os_api=None, fcntl_api=None,
                 clock=None):
        if sys.platform != 'linux' or struct.calcsize('P') != 8:
            raise ValueError('frozen Linux LP64 local endpoint owner required')
        if socket_api is None:
            import socket
            socket_api = socket
        if fcntl_api is None:
            import fcntl
            fcntl_api = fcntl
        self.socket, self.os, self.fcntl = socket_api, os if os_api is None else os_api, fcntl_api
        self.clock = time.monotonic if clock is None else clock
        self.budget, self.owner = budget, self.os.getpid()
        self.started, self.ready = False, False
        self.creator, self.role, self.role_attempted = self.owner, None, False
        self.owned, self.pair, self.errors = {}, None, []

    def _check(self):
        self.budget.check_active(self.clock())
        if self.os.getpid() != self.owner:
            raise ValueError('creating role alone manages endpoint ownership')

    def _identity(self, endpoint):
        fd = endpoint.fileno()
        info = self.os.fstat(fd)
        if (type(fd) is not int or not 3 <= fd <= 65535
                or not stat.S_ISSOCK(info.st_mode) or info.st_ino <= 0
                or info.st_uid != self.os.getuid() or self.os.get_inheritable(fd)
                or not self.fcntl.fcntl(fd, self.fcntl.F_GETFL) & self.os.O_NONBLOCK
                or endpoint.getsockopt(SOL_SOCKET, SO_DOMAIN) != AF_UNIX
                or endpoint.getsockopt(SOL_SOCKET, SO_TYPE) != SOCK_SEQPACKET
                or endpoint.getsockopt(SOL_SOCKET, SO_PASSCRED) != 1):
            raise ValueError('owned nonblocking CLOEXEC PASSCRED local endpoint required')
        return dict(device=info.st_dev, inode=info.st_ino, uid=info.st_uid)

    def allocate(self):
        if self.started:
            raise RuntimeError('one socketpair allocation attempt only')
        self.started = True
        try:
            self._check()
            begin = self.clock()
            # Frozen Linux SOCK_CLOEXEC=02000000, SOCK_NONBLOCK=04000.
            pair = self.socket.socketpair(AF_UNIX, SOCK_SEQPACKET | 0x80000 | 0x800, 0)
            self.pair = pair
            if type(pair) is not tuple or len(pair) != 2:
                raise ValueError('native dedicated socketpair required')
            for endpoint in pair:
                fd = endpoint.fileno()
                if type(fd) is int and fd >= 0:
                    self.owned.setdefault(fd, [endpoint, None])
            descriptors = [endpoint.fileno() for endpoint in pair]
            if (any(type(fd) is not int or not 3 <= fd <= 65535 for fd in descriptors)
                    or len(set(descriptors)) != 2):
                raise ValueError('distinct newly owned endpoint FDs required')
            for endpoint in pair:
                endpoint.setsockopt(SOL_SOCKET, SO_PASSCRED, 1)
                self.owned[endpoint.fileno()][1] = self._identity(endpoint)
            self._check()
            end = self.clock()
            self.budget.check_active(end)
            if not begin <= end <= begin + .5:
                raise ValueError('endpoint allocation exceeded .5s')
            self.ready = True
            borrowed = self.borrow()
            end = self.clock()
            self.budget.check_active(end)
            if not begin <= end <= begin + .5:
                raise ValueError('endpoint final validation exceeded .5s')
            return borrowed
        except Exception as exc:
            self.ready = False
            self.errors.append(dict(operation='allocate', type=type(exc).__name__,
                                    errno=getattr(exc, 'errno', None)))
            try:
                self.close_all()
            except Exception as close_exc:
                self.errors.append(dict(operation='close-guard', type=type(close_exc).__name__,
                                        errno=getattr(close_exc, 'errno', None)))
            raise

    def borrow(self):
        self._check()
        if not self.ready or len(self.owned) != 2:
            raise RuntimeError('complete owned endpoint pair required')
        for endpoint, expected in self.owned.values():
            if self._identity(endpoint) != expected:
                raise ValueError('endpoint identity changed')
        self._check()
        return self.pair

    def receiver(self, peer):
        endpoint = self._role_endpoint('receiver')
        return HandoffReceiver(endpoint, dict(self.owned[endpoint.fileno()][1]), peer,
            self.budget, os_api=self.os, fcntl_api=self.fcntl, clock=self.clock)

    def sender(self, peer):
        endpoint = self._role_endpoint('sender')
        return HandoffSender(endpoint, dict(self.owned[endpoint.fileno()][1]), peer,
            self.budget, os_api=self.os, fcntl_api=self.fcntl, clock=self.clock)

    def select_role(self, role, expected_parent=None):
        """Retire the unused fork-inherited endpoint, not authenticate a role.

        Source/peer proof is still mandatory outside this ownership selection.
        No permission to fork or initialize an unreviewed actor is granted here.
        """
        if not self.ready or self.role_attempted:
            raise RuntimeError('one complete pair role selection only')
        self.role_attempted = True  # failed time/parent checks never restart selection
        current = self.os.getpid()
        # A fork copy owns its copied wrappers for identity-guarded retirement,
        # even when role admission fails; this gives no peer/source authority.
        self.owner = current
        try:
            self.budget.check_active(self.clock())
            if role == 'receiver':
                if current != self.creator or expected_parent is not None:
                    raise ValueError('creator alone retains receiver')
                keep, retire = self.pair
            elif role == 'sender':
                if (current == self.creator or type(expected_parent) is not int
                        or not 1 < expected_parent <= 2**31-1
                        or self.os.getppid() != expected_parent):
                    raise ValueError('frozen physical parent and distinct child required')
                retire, keep = self.pair
            else:
                raise ValueError('fixed role required')
            self.role = role
            self._close_fds([retire.fileno()])
            if self.errors:
                raise ValueError('unused inherited endpoint retirement incomplete')
            self.ready = True
            return self._role_endpoint(role)
        except Exception as exc:
            self.ready = False
            self.errors.append(dict(operation='role', type=type(exc).__name__,
                                    errno=getattr(exc, 'errno', None)))
            raise

    def _role_endpoint(self, role):
        self._check()
        if not self.ready or self.role != role or len(self.owned) != 1:
            raise ValueError('one selected held endpoint required')
        endpoint, expected = next(iter(self.owned.values()))
        if self._identity(endpoint) != expected:
            raise ValueError('selected endpoint changed')
        self._check()
        return endpoint

    def close_all(self):
        self.ready = False
        self._close_fds(list(self.owned))

    def _close_fds(self, descriptors):
        if self.os.getpid() != self.owner:
            raise ValueError('creating role alone retires endpoints')
        for fd in descriptors:
            if fd not in self.owned:
                continue
            endpoint, identity = self.owned.pop(fd)
            try:
                changed = endpoint.fileno() != fd
                if identity is not None:
                    if not changed:
                        try:
                            info = self.os.fstat(fd)
                        except Exception:
                            detached = endpoint.detach()
                            self.errors.append(dict(operation='unverified-detach', fd=fd, detached=detached))
                            raise
                        changed = (not stat.S_ISSOCK(info.st_mode)
                            or dict(device=info.st_dev, inode=info.st_ino, uid=info.st_uid) != identity)
                if changed:
                    # Refusing close alone is insufficient: the retained Python
                    # wrapper would later close a replaced FD in its destructor.
                    detached = endpoint.detach()  # retire wrapper; never close foreign FD
                    self.errors.append(dict(operation='foreign-detach', fd=fd, detached=detached))
                    raise ValueError('endpoint changed; wrapper detached, foreign close refused')
                endpoint.close()  # retire Python wrapper as well; never os.close()+destructor
            except Exception as exc:
                self.errors.append(dict(operation='close', fd=fd, type=type(exc).__name__,
                                        errno=getattr(exc, 'errno', None)))

    def report(self):
        return copy.deepcopy(dict(qualified=False, harness_authenticated=False,
            started=self.started, ready=self.ready, role=self.role,
            role_attempted=self.role_attempted,
            owned_fds=sorted(self.owned), errors=self.errors))


class HandoffReceiver:
    def __init__(self, endpoint, expected, peer, budget, *, os_api=None,
                 fcntl_api=None, clock=None):
        if sys.platform != 'linux':
            raise ValueError('Linux dedicated local handoff only')
        if struct.calcsize('P') != 8 or FD.size != 4 or CREDS.size != 12:
            raise ValueError('frozen Linux LP64 control-message ABI required')
        if fcntl_api is None:
            import fcntl
            fcntl_api = fcntl
        if (type(expected) is not dict or set(expected) != {'device', 'inode', 'uid'}
                or any(type(v) is not int or v < 0 for v in expected.values())
                or expected['inode'] == 0 or type(peer) is not dict
                or set(peer) != {'pid', 'uid', 'gid'}
                or any(type(v) is not int or v < 0 for v in peer.values())
                or not 1 < peer['pid'] <= 2**31-1):
            raise ValueError('frozen endpoint and expected role credentials required')
        self.os = os if os_api is None else os_api
        self.fcntl = fcntl_api
        self.clock = time.monotonic if clock is None else clock
        self.endpoint, self.expected, self.peer = endpoint, dict(expected), dict(peer)
        self.budget, self.owner = budget, self.os.getpid()
        self.held, self.messages, self.errors = {}, [], []
        self.proc_held, self.root_binding = {}, None
        self.failed, self.polls = False, 0
        self._verify()

    def _verify(self):
        self.budget.check_active(self.clock())
        if self.os.getpid() != self.owner:
            raise ValueError('creating harness alone receives and retires handles')
        fd = self.endpoint.fileno()
        info = self.os.fstat(fd)
        observed = {'device': info.st_dev, 'inode': info.st_ino, 'uid': info.st_uid}
        flags = self.fcntl.fcntl(fd, self.fcntl.F_GETFL)
        if (type(fd) is not int or not 3 <= fd <= 65535
                or not stat.S_ISSOCK(info.st_mode) or observed != self.expected
                or info.st_uid != self.os.getuid() or self.os.get_inheritable(fd)
                or not flags & self.os.O_NONBLOCK
                or self.endpoint.getsockopt(SOL_SOCKET, SO_DOMAIN) != AF_UNIX
                or self.endpoint.getsockopt(SOL_SOCKET, SO_TYPE) != SOCK_SEQPACKET
                or self.endpoint.getsockopt(SOL_SOCKET, SO_PASSCRED) != 1):
            raise ValueError('held CLOEXEC nonblocking PASSCRED local seqpacket required')
        self.budget.check_active(self.clock())

    def receive_once(self):
        if self.failed or len(self.messages) >= 5 or self.polls >= 2048:
            raise RuntimeError('failed or exhausted fixed handoff cannot resume')
        self.polls += 1
        previous = set(self.held)
        try:
            self._verify()
            try:
                result = self.endpoint.recvmsg(FRAME.size + 1,
                    ANCILLARY_LIMIT,
                    MSG_CMSG_CLOEXEC | MSG_DONTWAIT)
            except (BlockingIOError, InterruptedError):
                self._verify()
                return None  # shared deadline only; never manufacture ACK
            raw, ancillary, flags, address = result
            # Own ALL kernel-delivered descriptors BEFORE payload/peer/truncation
            # checks, including extra descriptors in an invalid packet.
            received, credentials = [], []
            for level, kind, data in ancillary:
                if level == SOL_SOCKET and kind == SCM_RIGHTS and type(data) is bytes:
                    for offset in range(0, len(data) - len(data) % FD.size, FD.size):
                        fd = FD.unpack_from(data, offset)[0]
                        if fd >= 0:
                            self.held.setdefault(fd, None)
                            received.append(fd)
                if level == SOL_SOCKET and kind == SCM_CREDENTIALS:
                    credentials.append(data)
            evidence = {'raw': raw, 'ancillary': copy.deepcopy(ancillary),
                        'flags': flags, 'address': address, 'fds': list(received)}
            self.messages.append(evidence)  # preserve invalid attempt evidence
            if type(raw) is bytes and raw:
                self.budget.feed('trace', raw)
            for level, kind, data in ancillary:
                if type(data) is bytes and data:
                    self.budget.feed('trace', data)
            if (type(raw) is not bytes or len(raw) != FRAME.size
                    or type(flags) is not int or flags & (MSG_TRUNC | MSG_CTRUNC)
                    or address not in (None, '', b'') or len(ancillary) != 2
                    or len(credentials) != 1 or type(credentials[0]) is not bytes
                    or len(credentials[0]) != CREDS.size or len(received) != 1
                    or any(level != SOL_SOCKET or kind not in (SCM_RIGHTS, SCM_CREDENTIALS)
                           for level, kind, data in ancillary)
                    or any(kind == SCM_RIGHTS and len(data) != FD.size
                           for level, kind, data in ancillary)):
                raise ValueError('exact untruncated single-pidfd credential packet required')
            pid, uid, gid = CREDS.unpack(credentials[0])
            if {'pid': pid, 'uid': uid, 'gid': gid} != self.peer:
                raise ValueError('unexpected kernel sender credentials')
            magic, sequence, root, start = FRAME.unpack(raw)
            if (magic != MAGIC or sequence != len(self.messages)
                    or not 1 < root <= 2**31-1 or start == 0
                    or any(item.get('pid') == root for item in self.messages[:-1])):
                raise ValueError('fixed unique ordered lifetime packet required')
            handle = received[0]
            if (handle in previous or not 3 <= handle <= 65535
                    or self.os.get_inheritable(handle)):
                raise ValueError('received CLOEXEC descriptor within bounds required')
            info = self.os.fstat(handle)
            identity = {'device': info.st_dev, 'inode': info.st_ino, 'uid': info.st_uid,
                        'type': stat.S_IFMT(info.st_mode)}
            self.held[handle] = identity
            evidence.update(pid=root, starttime=start, sequence=sequence,
                            received_fd=handle, kernel_credentials=dict(self.peer))
            self._verify()
            # No ACK, signal, pidfd use or proc binding here. Receiver-held FD
            # alone is not proof that this descriptor is the claimed lifetime.
            return copy.deepcopy(evidence)
        except Exception as exc:
            self.failed = True
            self.errors.append({'operation': 'receive', 'type': type(exc).__name__,
                                'errno': getattr(exc, 'errno', None)})
            # Previously held lifetimes may still be alive: preserve their
            # recovery handles. Retire only this failed receive's new handles.
            try:
                self.close_received(set(self.held) - previous)
            except Exception as close_exc:
                self.errors.append({'operation': 'close', 'type': type(close_exc).__name__,
                                    'errno': getattr(close_exc, 'errno', None)})
            raise

    def close_received(self, descriptors=None):
        if self.os.getpid() != self.owner:
            raise ValueError('receiving harness alone retires descriptors')
        selected = list(self.held) if descriptors is None else list(descriptors)
        for fd in selected:
            if fd not in self.held:
                continue
            identity = self.held.pop(fd)  # no retry, including uncertain close
            try:
                if identity is not None:
                    info = self.os.fstat(fd)
                    observed = {'device': info.st_dev, 'inode': info.st_ino,
                                'uid': info.st_uid, 'type': stat.S_IFMT(info.st_mode)}
                    if observed != identity:
                        raise ValueError('replaced received descriptor; refuse foreign close')
                self.os.close(fd)
            except Exception as exc:
                self.errors.append({'operation': 'close', 'fd': fd,
                                    'type': type(exc).__name__,
                                    'errno': getattr(exc, 'errno', None)})

    def bind_root(self, resources):
        """Read-only first-root binding while the sender still holds its gate.

        Not an ACK: actual sender lifetime/source, procfs provenance, global
        containment and the remaining created lifetimes still need verification.
        No normal adopted-lifetime cleanup contract is altered here.
        """
        if self.failed or self.root_binding is not None or len(self.messages) != 1:
            raise RuntimeError('one unfailed first-root binding only')
        message = self.messages[0]
        packet = message['root_binding_attempt'] = {}
        directory = None
        try:
            self._verify()
            begin = self.clock()
            pid, handle = message['pid'], message['received_fd']
            identity = self.held[handle]
            if (pid in {self.owner, self.peer['pid']} or identity is None
                    or self.peer['uid'] != self.os.getuid()):
                raise ValueError('distinct received root required')
            flags = self.os.O_RDONLY | self.os.O_CLOEXEC | self.os.O_NOFOLLOW
            directory = self.os.open(f'/proc/{pid}', flags | self.os.O_DIRECTORY)
            self.proc_held[directory] = None  # ownership before validation
            info = self.os.fstat(directory)
            measured = {'device': info.st_dev, 'inode': info.st_ino, 'uid': info.st_uid,
                        'type': stat.S_IFMT(info.st_mode)}
            self.proc_held[directory] = measured
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != self.peer['uid']
                    or self.os.get_inheritable(directory)):
                raise ValueError('owned CLOEXEC root proc directory required')

            def held():
                for fd, frozen in ((directory, measured), (handle, identity)):
                    current = self.os.fstat(fd)
                    observed = {'device': current.st_dev, 'inode': current.st_ino,
                                'uid': current.st_uid, 'type': stat.S_IFMT(current.st_mode)}
                    if observed != frozen or self.os.get_inheritable(fd):
                        raise ValueError('root held descriptor identity changed')

            def sample(name, label, relative=True):
                self._verify()
                fd = (self.os.open(name, flags, dir_fd=directory) if relative
                      else self.os.open(name, flags))
                primary = None
                try:
                    info = self.os.fstat(fd)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.peer['uid']
                            or self.os.get_inheritable(fd)):
                        raise ValueError('owned CLOEXEC proc sample required')
                    raw = self.os.read(fd, 8193)
                    packet[label] = raw  # retain before accounting/refusal
                    if type(raw) is not bytes or not 0 < len(raw) <= 8192:
                        raise ValueError('bounded nonempty proc sample required')
                    self.budget.feed('trace', raw)
                    self._verify()
                    return raw
                except Exception as exc:
                    primary = exc
                    raise
                finally:
                    try:
                        self.os.close(fd)  # one attempt, no uncertain retry
                    except Exception as exc:
                        packet[label + '_close_error'] = {'type': type(exc).__name__,
                            'errno': getattr(exc, 'errno', None)}
                        if primary is None:
                            raise

            held()
            before = resources.proc_identity(sample('stat', 'stat_before'))
            status = sample('status', 'status')
            fields = {}
            for line in status.splitlines():
                key, sep, value = line.partition(b':')
                if key in {b'Pid', b'Tgid', b'Uid', b'TracerPid'}:
                    if not sep or key in fields:
                        raise ValueError('ambiguous root status')
                    fields[key] = value.split()
            expected = {b'Pid': [str(pid).encode()], b'Tgid': [str(pid).encode()],
                b'Uid': [str(self.peer['uid']).encode()] * 4, b'TracerPid': [b'0']}
            raw = sample(f'/proc/self/fdinfo/{handle}', 'fdinfo', False)
            matches = [line.partition(b':')[2].split() for line in raw.splitlines()
                       if line.partition(b':')[0] == b'Pid']
            after = resources.proc_identity(sample('stat', 'stat_after'))
            wanted = {'pid': pid, 'ppid': self.peer['pid'], 'pgrp': self.peer['pid'],
                      'session': self.peer['pid'], 'starttime': message['starttime']}
            for observed in (before, after):
                if (any(type(observed[k]) is not int or observed[k] != v
                        for k, v in wanted.items()) or observed['state'] not in {'R', 'S'}):
                    raise ValueError('same live pre-trace root/parent/session required')
            held()
            self._verify()
            end = self.clock()
            self.budget.check_active(end)
            if fields != expected or matches != [expected[b'Pid']] or not begin <= end <= begin + .5:
                raise ValueError('root native samples incomplete or late')
            self.root_binding = dict(wanted, pidfd=handle, proc_fd=directory,
                                     proc_identity=measured, pidfd_identity=identity)
            packet['samples_matched'] = True
            return copy.deepcopy(self.root_binding)
        except Exception as exc:
            self.failed = True
            packet.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            # Preserve received recovery pidfd even on failed proc binding.
            try:
                self.close_proc()
            except Exception as close_exc:
                self.errors.append({'operation': 'proc-close-guard',
                    'type': type(close_exc).__name__, 'errno': getattr(close_exc, 'errno', None)})
            raise

    def close_proc(self):
        if self.os.getpid() != self.owner:
            raise ValueError('creating harness alone retires proc directories')
        for fd in list(self.proc_held):
            identity = self.proc_held.pop(fd)
            try:
                if identity is not None:
                    info = self.os.fstat(fd)
                    if {'device': info.st_dev, 'inode': info.st_ino, 'uid': info.st_uid,
                            'type': stat.S_IFMT(info.st_mode)} != identity:
                        raise ValueError('replaced proc directory; refuse foreign close')
                self.os.close(fd)
            except Exception as exc:
                self.errors.append({'operation': 'proc-close', 'fd': fd,
                    'type': type(exc).__name__, 'errno': getattr(exc, 'errno', None)})

    def report(self):
        return copy.deepcopy({'qualified': False, 'harness_authenticated': False,
            'lifetimes_verified': False, 'outer_cleanup_complete': False,
            'held_fds': sorted(self.held), 'messages': self.messages,
            'held_proc_fds': sorted(self.proc_held), 'root_binding': self.root_binding,
            'polls': self.polls, 'failed': self.failed, 'errors': self.errors})


class HandoffSender:
    """Single-attempt transport draft, deliberately NOT RootLaunchGate ACK.

    Even a matching credential ACK returns harness_authenticated=False: the
    complete harness must first bind trusted sender/receiver lifetime/source,
    global containment, all ownership and failure recovery. No ACK is emitted
    by this module, and no returned dictionary grants permission to execute.
    """
    def __init__(self, endpoint, expected, peer, budget, *, os_api=None,
                 fcntl_api=None, clock=None):
        self.verifier = HandoffReceiver(endpoint, expected, peer, budget,
            os_api=os_api, fcntl_api=fcntl_api, clock=clock)
        self.endpoint, self.budget = endpoint, budget
        self.os, self.clock = self.verifier.os, self.verifier.clock
        self.pending, self.attempted, self.failed = None, False, False
        self.root_identity = None
        self.records, self.errors, self.unexpected = [], [], {}
        self.polls = 0

    def send_once(self, pid, pidfd, starttime):
        if self.attempted or self.failed:
            raise RuntimeError('one fixed root transfer attempt only')
        self.attempted = True
        packet = {'pid': pid, 'pidfd': pidfd, 'starttime': starttime}
        self.records.append(packet)
        try:
            self.verifier._verify()
            if (type(pid) is not int or not 1 < pid <= 2**31-1
                    or type(pidfd) is not int or not 3 <= pidfd <= 65535
                    or pidfd == self.endpoint.fileno()
                    or type(starttime) is not int or not 0 < starttime <= 2**64-1
                    or self.os.get_inheritable(pidfd)):
                raise ValueError('held distinct CLOEXEC root descriptor required')
            info = self.os.fstat(pidfd)
            identity = (info.st_dev, info.st_ino, info.st_uid, stat.S_IFMT(info.st_mode))
            self.root_identity = identity
            raw, ancillary = FRAME.pack(MAGIC, 1, pid, starttime), FD.pack(pidfd)
            packet.update(raw=raw, rights=ancillary)
            # Nonblocking + MSG_NOSIGNAL: short/uncertain/EAGAIN sends never retry.
            count = self.endpoint.sendmsg([raw], [(SOL_SOCKET, SCM_RIGHTS, ancillary)],
                                          MSG_DONTWAIT | 0x4000)
            packet['written'] = count
            if type(count) is int and 0 < count <= len(raw):
                self.budget.feed('trace', raw[:count])
                self.budget.feed('trace', ancillary)
            if type(count) is not int or count != len(raw):
                raise ValueError('one complete seqpacket transfer required')
            self.verifier._verify()
            after = self.os.fstat(pidfd)
            if (self.os.get_inheritable(pidfd) or identity !=
                    (after.st_dev, after.st_ino, after.st_uid, stat.S_IFMT(after.st_mode))):
                raise ValueError('borrowed root descriptor changed during transfer')
            self.pending = (pid, pidfd, starttime)
            return dict(qualified=False, pending=True, harness_authenticated=False)
        except Exception as exc:
            self.failed = True
            packet.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise

    def receive_ack_once(self):
        if self.failed or self.pending is None or self.polls >= 2048:
            raise RuntimeError('pending unfailed bounded root transfer required')
        self.polls += 1
        packet = {}
        try:
            self.verifier._verify()
            try:
                raw, ancillary, flags, address = self.endpoint.recvmsg(
                    FRAME.size + 1, ANCILLARY_LIMIT, MSG_CMSG_CLOEXEC | MSG_DONTWAIT)
            except (BlockingIOError, InterruptedError):
                self.verifier._verify()
                return None
            # Own unexpected kernel-delivered handles before refusing the ACK.
            for level, kind, data in ancillary:
                if level == SOL_SOCKET and kind == SCM_RIGHTS and type(data) is bytes:
                    for offset in range(0, len(data) - len(data) % FD.size, FD.size):
                        fd = FD.unpack_from(data, offset)[0]
                        if fd >= 0: self.unexpected.setdefault(fd, None)
            packet.update(raw=raw, ancillary=copy.deepcopy(ancillary), flags=flags, address=address)
            self.records.append(packet)
            if type(raw) is bytes and raw: self.budget.feed('trace', raw)
            for level, kind, data in ancillary:
                if type(data) is bytes and data: self.budget.feed('trace', data)
            if (type(raw) is not bytes or len(raw) != FRAME.size
                    or type(flags) is not int or flags & (MSG_TRUNC | MSG_CTRUNC)
                    or address not in (None, '', b'') or len(ancillary) != 1
                    or ancillary[0][:2] != (SOL_SOCKET, SCM_CREDENTIALS)
                    or type(ancillary[0][2]) is not bytes or len(ancillary[0][2]) != CREDS.size):
                raise ValueError('exact credential-only untruncated ACK required')
            pid, uid, gid = CREDS.unpack(ancillary[0][2])
            if dict(pid=pid, uid=uid, gid=gid) != self.verifier.peer:
                raise ValueError('unexpected kernel ACK credentials')
            root, pidfd, start = self.pending
            if FRAME.unpack(raw) != (ACK_MAGIC, 1, root, start):
                raise ValueError('ACK does not match pending root lifetime')
            info = self.os.fstat(pidfd)
            if (self.os.get_inheritable(pidfd) or self.root_identity !=
                    (info.st_dev, info.st_ino, info.st_uid, stat.S_IFMT(info.st_mode))):
                raise ValueError('borrowed root descriptor changed before ACK')
            self.verifier._verify()
            self.pending = None
            packet['transport_ack_matched'] = True
            return dict(qualified=False, transport_ack_matched=True,
                        harness_authenticated=False, root=root, pidfd=pidfd, starttime=start)
        except Exception as exc:
            self.failed = True
            packet.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            self.errors.append(dict(operation='ack', type=type(exc).__name__,
                                    errno=getattr(exc, 'errno', None)))
            for fd in list(self.unexpected):
                self.unexpected.pop(fd)
                try: self.os.close(fd)
                except Exception as close_exc:
                    self.errors.append(dict(operation='close-unexpected', fd=fd,
                        type=type(close_exc).__name__, errno=getattr(close_exc, 'errno', None)))
            raise

    def report(self):
        return copy.deepcopy(dict(qualified=False, harness_authenticated=False,
            failed=self.failed, attempted=self.attempted, pending=self.pending,
            records=self.records, polls=self.polls, errors=self.errors))
