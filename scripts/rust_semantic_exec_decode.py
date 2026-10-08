"""Strict bounded exec argument decoding; no execution or authorization policy.

Consumes an exec start record, not arbitrary log prose. An unfinished argument
prefix is accepted only as data; its caller must separately pair the completion.
Unsupported formatting is missing evidence. FD identity/cwd/process coverage and
the exact command allow-list remain the observer/controller's responsibility.
"""
from __future__ import annotations

import re


class Cursor:
    def __init__(self, text: str):
        if len(text) > 1024 * 1024 or not text.isascii():
            raise ValueError("oversized or unsupported raw trace encoding")
        self.text = text
        self.at = 0

    def spaces(self):
        while self.at < len(self.text) and self.text[self.at] == " ":
            self.at += 1

    def take(self, literal: str):
        self.spaces()
        if not self.text.startswith(literal, self.at):
            raise ValueError("incomplete or unsupported trace syntax")
        self.at += len(literal)

    def string(self) -> str:
        self.take('"')
        output = bytearray()
        escapes = {'a': 7, 'b': 8, 'f': 12, 'n': 10, 'r': 13, 't': 9,
                   'v': 11, '\\': 92, '"': 34}
        while self.at < len(self.text):
            char = self.text[self.at]
            self.at += 1
            if char == '"':
                if b'\0' in output:
                    raise ValueError("NUL is not an exec argument")
                return output.decode('utf-8', errors='strict')
            if char == '\\':
                if self.at == len(self.text):
                    raise ValueError("incomplete escape")
                char = self.text[self.at]
                self.at += 1
                if char in escapes:
                    value = escapes[char]
                elif char in '01234567':
                    digits = char
                    while len(digits) < 3 and self.at < len(self.text) and self.text[self.at] in '01234567':
                        digits += self.text[self.at]
                        self.at += 1
                    value = int(digits, 8)
                    if value > 255:
                        raise ValueError("octal escape outside byte range")
                elif char == 'x':
                    digits = self.text[self.at:self.at + 2]
                    if not re.fullmatch(r'[0-9a-fA-F]{2}', digits):
                        raise ValueError("unsupported hexadecimal escape")
                    self.at += 2
                    # C hex escapes are variable-length: do not guess where a
                    # third hex digit belongs. This format requires two bytes.
                    if self.at < len(self.text) and self.text[self.at] in '0123456789abcdefABCDEF':
                        raise ValueError("ambiguous hexadecimal escape")
                    value = int(digits, 16)
                else:
                    raise ValueError("unknown escape")
            else:
                if not 32 <= ord(char) <= 126:
                    raise ValueError("unescaped control byte")
                value = ord(char)
            output.append(value)
            if len(output) > 65536:
                raise ValueError("argument exceeds bound")
        raise ValueError("unterminated argument")

    def vector(self) -> list[str]:
        self.take('[')
        result = []
        self.spaces()
        if self.text.startswith(']', self.at):
            self.at += 1
            return result
        while True:
            if len(result) >= 4096:
                raise ValueError("argument vector exceeds bound")
            result.append(self.string())
            self.spaces()
            if self.text.startswith(']', self.at):
                self.at += 1
                return result
            self.take(',')


def decode_record(raw: str) -> dict:
    """Decode execve/execveat, without resolving FDs or permitting commands."""
    if len(raw) > 1024 * 1024 or not raw.isascii():
        raise ValueError("oversized or unsupported complete raw trace encoding")
    start = re.match(r'^([1-9][0-9]*) +(execve|execveat)\(', raw)
    if start is None:
        raise ValueError("exact PID and syscall start required")
    cursor = Cursor(raw[start.end():])
    fd = None
    flags = None
    if start[2] == 'execveat':
        cursor.spaces()
        match = re.match(r'(?:AT_FDCWD|[0-9]+),', cursor.text[cursor.at:])
        if match is None:
            raise ValueError("unresolved or unsupported execveat fd")
        fd_text = match[0][:-1]
        fd = fd_text if fd_text == 'AT_FDCWD' else int(fd_text)
        cursor.at += len(match[0])
    path = cursor.string()
    cursor.take(',')
    argv = cursor.vector()
    if not argv:
        raise ValueError("empty argv unsupported")
    cursor.take(',')
    environment = cursor.vector()
    if start[2] == 'execveat':
        cursor.take(',')
        cursor.spaces()
        match = re.match(r'(?:0|AT_EMPTY_PATH|AT_SYMLINK_NOFOLLOW)(?:\|(?:AT_EMPTY_PATH|AT_SYMLINK_NOFOLLOW))*',
                         cursor.text[cursor.at:])
        if match is None:
            raise ValueError("unknown execveat flags")
        flags = match[0]
        cursor.at += len(flags)
    cursor.spaces()
    suffix = cursor.text[cursor.at:]
    if suffix != '<unfinished ...>' and not re.fullmatch(r'\) += (?:0|-1 [A-Z0-9_]+ \([^\r\n]*\))', suffix):
        raise ValueError("incomplete, trailing or unsupported result format")
    return {'pid': int(start[1]), 'syscall': start[2], 'fd': fd, 'path': path,
            'argv': argv, 'environment': environment, 'flags': flags}


def pair_records(records: list[str]) -> list[dict]:
    """Pair a bounded exec-only subsequence, not certify a whole trace.

    Every input row must be an exec start/completion. The caller must prove
    trace coverage, PID lifetimes, cwd/FD provenance and that no exec rows were
    omitted. Results preserve attempts (including failures); only result 0 is
    a successful launch. This pure function neither executes nor permits it.
    """
    if not isinstance(records, list) or not 0 < len(records) <= 4096:
        raise ValueError("nonempty bounded exec record list required")
    total = 0
    for raw in records:
        if not isinstance(raw, str) or len(raw) > 1024 * 1024 or not raw.isascii():
            raise ValueError("unsupported complete exec record")
        total += len(raw)
        if total > 16 * 1024 * 1024:
            raise ValueError("aggregate exec records exceed bound")
    pending = {}
    completed = []
    for index, raw in enumerate(records):
        resumed = re.fullmatch(
            r'([1-9][0-9]*) +<\.\.\. (execve|execveat) resumed>(.*)', raw)
        if resumed:
            pid = int(resumed[1])
            if pid not in pending:
                raise ValueError("exec completion without its start")
            start_index, prefix, decoded = pending.pop(pid)
            if resumed[2] != decoded['syscall']:
                raise ValueError("exec completion syscall mismatch")
            whole = prefix[:-len('<unfinished ...>')] + resumed[3]
            # Validate the complete reconstructed record again: a resume may
            # not smuggle extra arguments, flags, or an unknown result suffix.
            final = decode_record(whole)
            if final != decoded:
                raise ValueError("exec completion changed argument identity")
        else:
            decoded = decode_record(raw)
            pid = decoded['pid']
            if pid in pending:
                raise ValueError("previous exec result missing for PID")
            if raw.endswith('<unfinished ...>'):
                pending[pid] = (index, raw, decoded)
                continue
            start_index, whole = index, raw
        result = re.search(r'\) += (0|-1 [A-Z0-9_]+ \([^\r\n]*\))$', whole)
        if result is None:
            raise ValueError("exec completion result missing")
        completed.append({**decoded, 'start_index': start_index,
                          'completion_index': index, 'result': result[1],
                          'launched': result[1] == '0'})
    if pending:
        raise ValueError("unresolved exec attempts at end of input")
    return completed
