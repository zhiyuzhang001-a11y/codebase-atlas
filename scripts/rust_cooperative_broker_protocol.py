"""I0 own-fixture protocol ONLY, not a product launcher or OS execution sandbox.

No subprocess, tool discovery, network or filesystem mutation occurs here. A
future native harness must enforce transport deadlines, peer/handle isolation,
file identity and disconnect/launch ordering. Protocol unit tests cannot prove
those properties or unchanged official Rust tool compatibility.
"""
from dataclasses import dataclass
import json
from pathlib import Path
from threading import Lock


MAX_FRAME = 1024
OPERATION = "own-fixture-observe-v1"
FIXTURE = "owned-probe-v1"
FIXED_STDIN = b"atlas-i0-owned-broker-input-v1\n"
FIXED_PROGRAM = (
    "import hashlib,json,os,sys; "
    "print(json.dumps({'argv':sys.argv,'cwd':os.getcwd(),"
    "'env':dict(os.environ),'parent_id':os.getppid(),'process_id':os.getpid(),"
    "'stdin_sha256':hashlib.sha256(sys.stdin.buffer.read()).hexdigest()},sort_keys=True))"
)


class ProtocolDenied(ValueError):
    """A denied request must never reach a native launch boundary."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolDenied("duplicate-key")
        result[key] = value
    return result


def _deny_constant(value):
    raise ProtocolDenied("non-json-constant")


@dataclass(frozen=True)
class Request:
    operation: str
    fixture: str
    sequence: int


def decode_request(frame: bytes) -> Request:
    # Exactly one bounded newline-terminated frame, not free-form stdin.
    if (type(frame) is not bytes or not frame or len(frame) > MAX_FRAME
            or not frame.endswith(b"\n") or frame.count(b"\n") != 1):
        raise ProtocolDenied("invalid-frame")
    try:
        record = json.loads(frame.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=_deny_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolDenied("invalid-json") from exc
    if type(record) is not dict or set(record) != {"operation", "fixture", "sequence"}:
        raise ProtocolDenied("unknown-or-missing-field")
    if (type(record["operation"]) is not str or record["operation"] != OPERATION
            or type(record["fixture"]) is not str or record["fixture"] != FIXTURE
            or type(record["sequence"]) is not int or record["sequence"] != 1):
        raise ProtocolDenied("invalid-operation-fixture-sequence")
    return Request(OPERATION, FIXTURE, 1)


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    cwd: str
    environment: tuple[tuple[str, str], ...]
    stdin: bytes


def own_fixture_command(python: str, scratch: str, *, system_root: str | None = None) -> Command:
    """Arguments are controller-owned, NEVER values decoded from requester IPC.

    This only constructs a fixture command; native harness still must verify
    executable/cwd identity and bind them at the actual creation boundary.
    """
    for path in (python, scratch):
        if type(path) is not str or "\0" in path or not Path(path).is_absolute():
            raise ProtocolDenied("invalid-controller-path")
    environment = ()
    if system_root is not None:
        if type(system_root) is not str or "\0" in system_root or not Path(system_root).is_absolute():
            raise ProtocolDenied("invalid-controller-os-root")
        environment = (("SystemRoot", system_root),)
    return Command((python, "-I", "-c", FIXED_PROGRAM, FIXTURE), scratch,
                   environment, FIXED_STDIN)


class Session:
    """Single-use protocol state, no execution authority or native handles.

    claim() is NOT atomic with an OS launch; that unresolved boundary must be
    tested by the native harness, not claimed from this state-machine test.
    """
    def __init__(self, command: Command):
        if (type(command) is not Command or type(command.argv) is not tuple
                or any(type(value) is not str for value in command.argv)
                or type(command.cwd) is not str or type(command.stdin) is not bytes
                or type(command.environment) is not tuple
                or any(type(pair) is not tuple or len(pair) != 2
                       or any(type(value) is not str for value in pair)
                       for pair in command.environment)):
            raise ProtocolDenied("mutable-controller-command")
        self._command = command
        self._state = "open"
        self._lock = Lock()

    def approve(self, frame: bytes) -> Command:
        with self._lock:
            if self._state != "open":
                self._state = "closed"
                raise ProtocolDenied("replayed-or-disconnected")
            try:
                decode_request(frame)
            except ProtocolDenied:
                self._state = "closed"
                raise
            self._state = "approved"
            return self._command

    def claim(self, command: Command) -> Command:
        with self._lock:
            if self._state != "approved" or command is not self._command:
                self._state = "closed"
                raise ProtocolDenied("unapproved-or-disconnected")
            self._state = "consumed"
            return self._command

    def disconnect(self) -> None:
        with self._lock:
            self._state = "closed"
