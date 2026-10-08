"""Pure bounded process/cwd replay; not kernel admission or execution policy.

Input is an explicitly selected process-create/exec/chdir/terminal-exit sequence.
The controller must separately prove no relevant events were omitted, trace
coverage, inode/FD identity, live PID/starttime admission and resource coverage.
Unknown syscall formats, shared cwd/files, threads and namespaces fail closed.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from scripts.rust_semantic_exec_decode import decode_chdir_record, pair_records


NAMES = 'execve|execveat|fork|vfork|clone|clone3|chdir'
FAILED = r'-1 [A-Z0-9_]+ \([^\r\n]*\)'
ALLOWED_FLAGS = {'CLONE_VM', 'CLONE_VFORK', 'SIGCHLD',
                 'CLONE_CHILD_CLEARTID', 'CLONE_CHILD_SETTID'}


def absolute_cwd(value: str) -> str:
    if not isinstance(value, str) or not value or '\0' in value or len(value.encode()) > 65536:
        raise ValueError('bounded absolute cwd required')
    path = PurePosixPath(value)
    if not path.is_absolute() or value.startswith('//') or '..' in path.parts or str(path) != value:
        raise ValueError('canonical absolute cwd required')
    return value


def paired_events(records: list[str]) -> list[dict]:
    """Pair before replay, preserving creation before a child's early exec."""
    if type(records) is not list or not 0 < len(records) <= 8192:
        raise ValueError('bounded nonempty selected process events required')
    total, pending, events = 0, {}, []
    for index, raw in enumerate(records):
        if not isinstance(raw, str) or not raw.isascii() or len(raw) > 1024 * 1024:
            raise ValueError('unsupported process event encoding/size')
        total += len(raw)
        if total > 16 * 1024 * 1024:
            raise ValueError('selected process events exceed aggregate bound')
        terminal = re.fullmatch(r'([1-9][0-9]*) +\+\+\+ (?:exited with (?:[0-9]{1,3})|killed by SIG[A-Z0-9]+(?: \(core dumped\))?) \+\+\+', raw)
        start = re.match(r'([1-9][0-9]*) +(' + NAMES + r')\(', raw)
        resume = re.fullmatch(r'([1-9][0-9]*) +<\.\.\. (' + NAMES + r') resumed>(.*)', raw)
        if terminal:
            pid, name, begin, whole = int(terminal[1]), 'exit', index, raw
            exit_code = re.search(r'exited with ([0-9]+)', raw)
            if exit_code is not None and int(exit_code[1]) > 255:
                raise ValueError('terminal status outside exit-code bound')
            if pid in pending:
                raise ValueError('terminal exit with unresolved syscall')
        elif resume:
            pid, name = int(resume[1]), resume[2]
            if pid not in pending:
                raise ValueError('process completion without start')
            begin, old_name, prefix = pending.pop(pid)
            if old_name != name:
                raise ValueError('process completion syscall mismatch')
            whole = prefix[:-len('<unfinished ...>')] + resume[3]
        elif start:
            pid, name, begin, whole = int(start[1]), start[2], index, raw
            if pid in pending:
                raise ValueError('overlapping syscall on same PID')
            if raw.endswith('<unfinished ...>'):
                pending[pid] = (begin, name, raw)
                continue
        else:
            raise ValueError('unknown selected process event')
        if not 0 < pid <= 2**31 - 1:
            raise ValueError('PID outside bound')
        events.append(dict(pid=pid, name=name, start_index=begin, completion_index=index, raw=whole))
    if pending:
        raise ValueError('missing process syscall completion')
    return sorted(events, key=lambda event: event['start_index'])


def creation_child(event: dict) -> int | None:
    raw, name = event['raw'], event['name']
    result = re.search(r'\) += ([1-9][0-9]*|' + FAILED + r')$', raw)
    if result is None:
        raise ValueError('unknown process creation result')
    if name in {'fork', 'vfork'}:
        if not re.fullmatch(r'[1-9][0-9]* +' + name + r'\(\) += ([1-9][0-9]*|' + FAILED + r')', raw):
            raise ValueError('unknown fork arguments')
    elif name == 'clone':
        pointer = r'(?:NULL|0x[0-9a-f]+)'
        shape = re.fullmatch(r'[1-9][0-9]* +clone\(child_stack=' + pointer
                             + r', flags=([A-Z0-9_|]+)(?:, (?:child_tidptr|parent_tidptr|tls)='
                             + pointer + r')*\) += ([1-9][0-9]*|' + FAILED + r')', raw)
        if shape is None:
            raise ValueError('unknown clone argument shape')
        pieces = shape[1].split('|')
        if len(pieces) != len(set(pieces)) or not set(pieces) <= ALLOWED_FLAGS or 'SIGCHLD' not in pieces:
            raise ValueError('shared/thread/namespace/unknown clone flags unsupported')
    else:
        # clone3 exit_signal/struct layout is deliberately not guessed from
        # clone flags. A real controlled format must precede support.
        raise ValueError('clone3 structure unsupported')
    if result[1].startswith('-1 '):
        return None
    child = int(result[1])
    if not 0 < child <= 2**31 - 1:
        raise ValueError('child PID outside bound')
    return child


def replay(records: list[str], *, root_pid: int, initial_cwd: str) -> dict:
    """Require observed ancestry and terminal exits; attach cwd to all attempts.

    Strictly excludes shared filesystem/FD tables and PID reuse. Reconstructed
    cwd is a string only; execveat FDs stay unresolved, never become a pathname.
    Even a successful replay has qualified=false and cannot authorize metadata.
    """
    if type(root_pid) is not int or not 0 < root_pid <= 2**31 - 1:
        raise ValueError('exact root PID required')
    contexts = {root_pid: absolute_cwd(initial_cwd)}
    seen, processes, attempts = {root_pid}, [], []
    events = paired_events(records)
    for event in events:
        pid, name = event['pid'], event['name']
        if pid not in contexts:
            raise ValueError('unobserved parent, exited PID or PID reuse')
        if name == 'exit':
            del contexts[pid]
        elif name in {'fork', 'vfork', 'clone', 'clone3'}:
            child = creation_child(event)
            if child is None:
                continue
            if child in seen or len(seen) >= 512:
                raise ValueError('reused or excess process identity')
            # Creation can complete after the child runs. A same-parent cwd
            # mutation while creation is pending cannot have a guessed order.
            if any(other['pid'] == pid and other['name'] == 'chdir'
                   and event['start_index'] < other['start_index'] <= event['completion_index']
                   for other in events):
                raise ValueError('cwd mutation during pending creation')
            contexts[child] = contexts[pid]
            seen.add(child)
            processes.append({**event, 'child_pid': child, 'cwd': contexts[pid]})
        elif name == 'chdir':
            change = decode_chdir_record(event['raw'])
            if change['changed']:
                contexts[pid] = change['path']
        else:
            attempt = pair_records([event['raw']])[0]
            attempts.append({**attempt, 'cwd': contexts[pid],
                             'start_index': event['start_index'],
                             'completion_index': event['completion_index']})
    if contexts or not attempts:
        raise ValueError('missing terminal exits or exec attempts')
    return {'root_pid': root_pid, 'processes': processes, 'attempts': attempts,
            'terminal_pids': sorted(seen), 'qualified': False}
