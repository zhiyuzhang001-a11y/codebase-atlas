"""Bounded read-only Linux RSS samples for explicitly admitted process identities.

Not a process-tree discoverer, peak bound, execution gate or metadata permission.
The controller must freeze admission, observe creation/escape/exit, enforce time
and cleanup, and obtain positive controls before using samples for qualification.
"""
from __future__ import annotations

import os
import math
import re
import sys


def proc_identity(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 8192:
        raise ValueError("bounded proc stat bytes required")
    prefix, separator, tail = raw.rpartition(b") ")
    first = re.match(rb"^([1-9][0-9]*) \(", prefix)
    fields = tail.split()
    if not separator or first is None or len(fields) < 22:
        raise ValueError("incomplete proc stat")
    if fields[0] not in {b'R', b'S', b'D', b'Z', b'T', b't', b'X', b'x', b'I', b'P'}:
        raise ValueError("unsupported proc state")
    values = []
    for index in (1, 2, 3, 19):  # original fields 4, 5, 6 and 22
        if not re.fullmatch(rb'[0-9]{1,20}', fields[index]):
            raise ValueError("invalid proc identity field")
        value = int(fields[index])
        if value > 2**64 - 1:
            raise ValueError("proc identity field exceeds bound")
        values.append(value)
    return {'pid': int(first[1]), 'state': fields[0].decode('ascii'),
            'ppid': values[0], 'pgrp': values[1], 'session': values[2],
            'starttime': values[3]}


def rollup_rss(raw: bytes) -> int:
    """RSS plus explicit hugetlb accounting; never substitute PSS/VmRSS."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 65536:
        raise ValueError("bounded smaps_rollup bytes required")
    values = {}
    for line in raw.splitlines():
        key = line.split(b':', 1)[0]
        if key not in {b'Rss', b'Shared_Hugetlb', b'Private_Hugetlb'}:
            continue
        match = re.fullmatch(rb'([^:]+): +([0-9]{1,18}) kB', line)
        if match is None or key in values:
            raise ValueError("ambiguous RSS field")
        values[key] = int(match[2]) * 1024
    if set(values) != {b'Rss', b'Shared_Hugetlb', b'Private_Hugetlb'}:
        raise ValueError("missing RSS/hugetlb accounting")
    total = sum(values.values())
    if total <= 0:
        raise ValueError("zero RSS is missing evidence")
    return total


def _read_at(directory: int, name: str, bound: int) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                         dir_fd=directory)
    try:
        # One read, avoiding multi-read smaps walks with changing maps.
        raw = os.read(descriptor, bound + 1)
        if not raw or len(raw) > bound:
            raise ValueError("empty or oversized proc sample")
        return raw
    finally:
        os.close(descriptor)


def sample_admitted(identities: dict[int, int], session: int) -> dict:
    """Sample known PID/starttime pairs; lost/changed membership fails closed.

    Session isolation and admission must have been independently established by
    the caller. This does not find short-lived children or certify no escapes.
    Missing/denied/vanished proc entries propagate, never become zero samples.
    """
    if sys.platform != 'linux':
        raise ValueError("Linux required; no skipped success")
    if (type(identities) is not dict or not 0 < len(identities) <= 512
            or type(session) is not int or not 0 < session <= 2**31 - 1):
        raise ValueError("bounded admitted identities and session required")
    for pid, starttime in identities.items():
        if (type(pid) is not int or not 0 < pid <= 2**31 - 1
                or type(starttime) is not int or not 0 < starttime <= 2**64 - 1):
            raise ValueError("exact PID/starttime required")
    samples = []
    for pid, starttime in sorted(identities.items()):
        directory = os.open(f'/proc/{pid}', os.O_RDONLY | os.O_DIRECTORY |
                            os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            before = proc_identity(_read_at(directory, 'stat', 8192))
            rss = rollup_rss(_read_at(directory, 'smaps_rollup', 65536))
            after = proc_identity(_read_at(directory, 'stat', 8192))
            for identity in (before, after):
                if (identity['pid'] != pid or identity['starttime'] != starttime
                        or identity['session'] != session or identity['pgrp'] != session
                        or identity['state'] in {'Z', 'X', 'x'}):
                    raise ValueError("admitted process identity/membership changed")
            samples.append({'pid': pid, 'starttime': starttime, 'rss_bytes': rss})
        finally:
            os.close(directory)
    return {'samples': samples, 'rss_bytes': sum(row['rss_bytes'] for row in samples),
            'qualified': False}


def audit_coverage(required_pids: list[int], admitted: dict[int, int],
                   packets: list[dict], rss_limit: int) -> dict:
    """Pure reconciliation, not discovery, a lifetime/peak proof or permission.

    Caller supplies validated trace lifetimes plus tracer PID, kernel-admitted
    PID/starttime pairs and already collected samples. Every required PID needs
    admission and positive RSS, including short-lived children. The caller must
    still prove complete trace selection, live ancestry, sampling through exit,
    escapes, cancellation and cleanup. Nothing here reads proc or runs tools.
    """
    if (type(required_pids) is not list or not 0 < len(required_pids) <= 512
            or any(type(pid) is not int or not 0 < pid <= 2**31-1 for pid in required_pids)
            or len(set(required_pids)) != len(required_pids)):
        raise ValueError('bounded distinct trace/tracer PIDs required')
    required = set(required_pids)
    if type(admitted) is not dict or set(admitted) != required:
        raise ValueError('missing or extra kernel admission for traced lifetime')
    if any(type(pid) is not int or type(start) is not int or not 0 < start <= 2**64-1
           for pid, start in admitted.items()):
        raise ValueError('exact kernel starttime required')
    if (type(packets) is not list or not 0 < len(packets) <= 16384
            or type(rss_limit) is not int or not 0 < rss_limit <= 2**63-1):
        raise ValueError('bounded packets and explicit RSS limit required')
    counts = dict.fromkeys(required, 0)
    maxima = dict.fromkeys(required, 0)
    last_end, observed_max, row_count = None, 0, 0
    for packet in packets:
        if type(packet) is not dict:
            raise ValueError('sample packet required')
        begin, end = packet.get('start_seconds'), packet.get('end_seconds')
        if any(type(value) not in (int, float) or (type(value) is float and not math.isfinite(value))
               for value in (begin, end)):
            raise ValueError('finite sample interval required')
        if begin < 0 or end < begin or end-begin > 0.5 or (last_end is not None and not 0 <= begin-last_end <= 0.5):
            raise ValueError('sample interval overlap, duration or gap invalid')
        rows = packet.get('samples')
        if type(rows) is not list or not 0 < len(rows) <= 512:
            raise ValueError('bounded nonempty sample rows required')
        row_count += len(rows)
        if row_count > 65536:
            raise ValueError('aggregate sample rows exceed bound')
        seen, total = set(), 0
        for row in rows:
            if type(row) is not dict:
                raise ValueError('sample identity row required')
            pid, start, rss = row.get('pid'), row.get('starttime'), row.get('rss_bytes')
            if (type(pid) is not int or pid not in required or pid in seen
                    or type(start) is not int or start != admitted[pid]
                    or type(rss) is not int or not 0 < rss <= rss_limit):
                raise ValueError('unknown, duplicate, changed or missing RSS identity')
            seen.add(pid)
            total += rss
            counts[pid] += 1
            maxima[pid] = max(maxima[pid], rss)
        if type(packet.get('rss_bytes')) is not int or packet['rss_bytes'] != total or total > rss_limit:
            raise ValueError('sample sum mismatch or RSS limit exceeded')
        observed_max = max(observed_max, total)
        last_end = end
    missing = sorted(pid for pid, count in counts.items() if count == 0)
    if missing:
        raise ValueError('traced lifetimes without positive RSS samples: ' + repr(missing))
    return {'sample_counts': counts, 'observed_pid_maxima': maxima,
            'observed_aggregate_max_bytes': observed_max, 'qualified': False}
