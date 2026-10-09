"""C0-O5 prepared shared accounting; NOT a strong native supervisor.

No spawn, signals, pipe reads or CLI. The reviewed outer owner must enforce
these deadlines even while the tracer is blocked, drain bounded pipe chunks,
and separately prove lifetime terminals, group absence and observer reap.
"""
import hashlib
import math


class ControlBudget:
    """One active clock and one non-renewable cleanup clock, injected time only."""

    LIMIT = 1024 * 1024
    STREAMS = frozenset({'stdout', 'stderr', 'trace'})

    def __init__(self, started):
        self.started = self._number(started)
        self.last = self.started
        self.active_deadline = self.started + 20.0
        self.cleanup_started = self.cleanup_deadline = None
        self.failure = None
        self.total_seen = self.total_retained = 0
        self.records = {name: {'seen': 0, 'retained': bytearray(),
                               'hash': hashlib.sha256(), 'eof': False}
                        for name in self.STREAMS}

    @staticmethod
    def _number(value):
        if (type(value) not in (int, float) or not math.isfinite(value)
                or value < 0 or value > 2**40):
            raise ValueError('finite bounded monotonic timestamp required')
        return float(value)

    def _time(self, now):
        now = self._number(now)
        if now < self.last:
            raise ValueError('monotonic timestamp moved backwards')
        self.last = now
        return now

    def check_active(self, now):
        now = self._time(now)
        if self.cleanup_started is not None:
            raise RuntimeError('active phase cannot resume after cleanup')
        if self.failure is not None:
            raise RuntimeError('failed control cannot resume')
        if now >= self.active_deadline:
            self.failure = 'active-deadline'
            raise TimeoutError('shared 20s active deadline exhausted')
        return self.active_deadline - now

    def begin_cleanup(self, now):
        now = self._time(now)
        if self.cleanup_started is None:
            self.cleanup_started = now
            self.cleanup_deadline = now + 10.0
        return self.cleanup_deadline

    def check_cleanup(self, now):
        now = self._time(now)
        if self.cleanup_started is None:
            raise RuntimeError('cleanup clock not started')
        if now >= self.cleanup_deadline:
            raise TimeoutError('shared 10s cleanup deadline exhausted')
        return self.cleanup_deadline - now

    def feed(self, name, chunk):
        """Account raw bytes, including cleanup output; retain bounded prefix.

        Outer readers must use <=64KiB reads and call even during failure drain.
        Overflow records the offending chunk's count/hash then raises; no bytes
        silently omitted and no stream gets an independent 1MiB allowance.
        """
        if name not in self.STREAMS or type(chunk) is not bytes or not 0 < len(chunk) <= 65536:
            raise ValueError('fixed stream and nonempty <=64KiB raw chunk required')
        item = self.records[name]
        if item['eof']:
            raise ValueError('output after recorded EOF')
        item['seen'] += len(chunk)
        item['hash'].update(chunk)
        self.total_seen += len(chunk)
        count = min(len(chunk), self.LIMIT - self.total_retained)
        item['retained'].extend(chunk[:count])
        self.total_retained += count
        if self.total_seen > self.LIMIT:
            if self.failure is None:
                self.failure = 'aggregate-output-limit'
            raise OverflowError('aggregate raw output/trace exceeds 1MiB')

    def eof(self, name):
        if name not in self.STREAMS or self.records[name]['eof']:
            raise ValueError('unique fixed stream EOF required')
        self.records[name]['eof'] = True

    def report(self):
        # EOF proves no more pipe bytes only; it proves no terminal or ownership.
        return {'qualified': False, 'outer_cleanup_complete': False,
                'active_deadline': self.active_deadline,
                'cleanup_started': self.cleanup_started,
                'cleanup_deadline': self.cleanup_deadline, 'failure': self.failure,
                'seen': self.total_seen, 'retained': self.total_retained,
                'all_eof': all(item['eof'] for item in self.records.values()),
                'streams': {name: {'seen': item['seen'],
                                   'retained': len(item['retained']),
                                   'omitted': item['seen'] - len(item['retained']),
                                   'sha256': item['hash'].hexdigest(), 'eof': item['eof']}
                            for name, item in sorted(self.records.items())}}
