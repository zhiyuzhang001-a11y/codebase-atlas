"""Bounded C0-O5 identity transport preparation, NOT admission authority.

Only the reviewed observer may emit native first-stop identities before CONT.
The complete owner must authenticate the dedicated pipe/source/peer first.
Decoding bytes or EOF does not establish that trust, process exit or cleanup.
No native operations, source execution, FD allocation or launcher here.
"""
import copy
import math
import struct


FRAME = struct.Struct('!8sIIIQQQ')
MAGIC = b'ATJRN001'


class ControlJournal:
    def __init__(self, controller, observer):
        if (any(type(v) is not int or not 1 < v <= 2**31-1
                for v in (controller, observer)) or controller == observer):
            raise ValueError('exact distinct dedicated controller and observer required')
        self.controller, self.observer = controller, observer
        self.pending = bytearray()
        self.rows, self.raw = [], bytearray()
        self.seen = 0
        self.failed = self.ended = False

    def feed(self, raw):
        """Retain bounded raw bytes before decoding; fail latch, never overwrite."""
        if self.failed or self.ended:
            raise RuntimeError('failed or ended journal cannot resume')
        try:
            if type(raw) is not bytes:
                raise ValueError('exact raw journal bytes required')
            self.seen += len(raw)
            remaining = FRAME.size * 3 - len(self.raw)
            self.raw.extend(raw[:remaining])
            if not raw or len(raw) > remaining:
                raise ValueError('fixed three-lifetime journal overflow')
            self.pending.extend(raw)
            while len(self.pending) >= FRAME.size:
                frame = bytes(self.pending[:FRAME.size])
                del self.pending[:FRAME.size]
                magic, seq, pid, parent, start, session, group = FRAME.unpack(frame)
                expected_parent = self.observer if not self.rows else self.rows[0]['pid']
                if (magic != MAGIC or seq != len(self.rows) + 1
                        or not 1 < pid <= 2**31-1
                        or pid in {self.controller, self.observer}
                        or any(row['pid'] == pid for row in self.rows)
                        or parent != expected_parent or start == 0
                        or session != self.observer or group != self.observer):
                    raise ValueError('fixed first-stop journal identity/ordering mismatch')
                self.rows.append(dict(sequence=seq, pid=pid, parent=parent,
                                      starttime=start, session=session, pgrp=group))
        except Exception:
            self.failed = True
            raise

    def eof(self):
        if self.failed or self.ended:
            raise RuntimeError('unique nonfailed journal EOF required')
        self.ended = True
        if self.pending:
            self.failed = True
            raise ValueError('truncated identity frame at EOF')
        # Zero/partial rows are explicitly incomplete, not unknown-PID admission.

    def lookup(self, pid):
        """Copied transport identity only; caller still owes source/native proof."""
        if self.failed or type(pid) is not int:
            raise ValueError('nonfailed exact PID lookup required')
        row = next((row for row in self.rows if row['pid'] == pid), None)
        if row is None:
            raise ValueError('unknown journal lifetime')
        return {key: row[key] for key in ('pid', 'starttime', 'session', 'pgrp')}

    def report(self):
        return dict(qualified=False, outer_cleanup_complete=False,
                    source_authenticated=False, failed=self.failed, eof=self.ended,
                    transport_complete=self.ended and not self.failed and len(self.rows) == 3,
                    raw_hex=bytes(self.raw).hex(), pending_bytes=len(self.pending),
                    seen_bytes=self.seen, omitted_bytes=self.seen - len(self.raw),
                    identities=copy.deepcopy(self.rows))


class FirstStopEmitter:
    """Prepared first-stop writer; borrowed verified nonblocking pipe only.

    write_frame must be a reviewed, dedicated pipe adapter, not project callback.
    This class does NOT verify peer/FD ownership or freeze source/bootstrap. The
    outer controller still enforces the deadline independently of this callback.
    Tests inject byte collectors only; no native writer is constructed here.
    """
    def __init__(self, controller, observer, write_frame, clock, active_deadline):
        self.journal = ControlJournal(controller, observer)
        if not callable(write_frame) or not callable(clock):
            raise ValueError('owned writer and shared clock required')
        self.writer, self.clock = write_frame, clock
        self.deadline, self.last = active_deadline, None
        self.failed = False
        self.records = []
        now = self._now()
        if self.deadline - now > 20:
            raise ValueError('remaining shared active deadline <=20s required')

    def _now(self):
        now = self.clock()
        if (any(type(v) not in (int, float) or not math.isfinite(v)
                or not 0 <= v <= 2**40 for v in (now, self.deadline))
                or (self.last is not None and now < self.last)
                or now >= self.deadline):
            raise ValueError('shared active deadline exhausted or invalid clock')
        self.last = now
        return now

    def __call__(self, pid, parent, packet):
        if self.failed or len(self.records) >= 3:
            raise RuntimeError('failed or full emitter cannot resume')
        row = {'pid': pid, 'parent': parent, 'write_attempted': False}
        self.records.append(row)
        try:
            begin = row['started'] = self._now()
            row['packet'] = copy.deepcopy(packet)
            if type(packet) is not dict:
                raise ValueError('exact first-stop native packet required')
            times = (packet.get('start_seconds'), packet.get('end_seconds'))
            if (any(type(v) not in (int, float) or not math.isfinite(v)
                    or not 0 <= v <= 2**40 for v in times)
                    or not times[0] <= times[1] <= begin
                    or begin - times[0] > .5):
                raise ValueError('fresh bounded first-stop sample required')
            for key in ('before', 'after'):
                identity = packet.get(key)
                if (type(identity) is not dict
                        or any(type(identity.get(k)) is not int for k in
                               ('pid', 'ppid', 'session', 'pgrp', 'starttime'))
                        or identity['pid'] != pid or identity['ppid'] != parent
                        or identity['session'] != self.journal.observer
                        or identity['pgrp'] != self.journal.observer
                        or not 0 < identity['starttime'] <= 2**64-1
                        or identity.get('state') not in {'t', 'T'}):
                    raise ValueError('exact stopped creation identity required')
            if (packet['before']['starttime'] != packet['after']['starttime']
                    or type(packet.get('rss_bytes')) is not int or packet['rss_bytes'] <= 0
                    or type(pid) is not int or type(parent) is not int):
                raise ValueError('first-stop lifetime or RSS changed')
            raw = FRAME.pack(MAGIC, len(self.journal.rows) + 1, pid, parent,
                             packet['before']['starttime'], self.journal.observer,
                             self.journal.observer)
            row['raw_hex'] = raw.hex()
            # Local sequencing is committed before one write. A short/error/
            # late write can have delivered bytes, so never retry that lifetime.
            self.journal.feed(raw)
            self._now()
            row['write_attempted'] = True
            count = row['written'] = self.writer(raw)
            end = row['finished'] = self._now()
            if type(count) is not int or count != FRAME.size or end - times[0] > .5:
                raise ValueError('one full bounded frame write required')
            row['sent'] = True
            return True
        except Exception as exc:
            self.failed = True
            row.update(error=type(exc).__name__, errno=getattr(exc, 'errno', None))
            raise

    def report(self):
        return dict(qualified=False, source_authenticated=False,
                    outer_cleanup_complete=False, failed=self.failed,
                    records=copy.deepcopy(self.records), journal=self.journal.report())
