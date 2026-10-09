"""Bounded C0-O5 identity transport preparation, NOT admission authority.

Only the reviewed observer may emit native first-stop identities before CONT.
The complete owner must authenticate the dedicated pipe/source/peer first.
Decoding bytes or EOF does not establish that trust, process exit or cleanup.
No native operations, source execution, FD allocation or launcher here.
"""
import copy
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
