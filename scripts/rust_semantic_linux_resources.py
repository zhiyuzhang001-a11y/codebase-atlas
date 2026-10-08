"""Bounded read-only Linux RSS samples for explicitly admitted process identities.

Not a process-tree discoverer, peak bound, execution gate or metadata permission.
The controller must freeze admission, observe creation/escape/exit, enforce time
and cleanup, and obtain positive controls before using samples for qualification.
"""
from __future__ import annotations

import os
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
