from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from scripts import rust_cooperative_broker_protocol as protocol


def frame(**changes):
    record = {"operation": protocol.OPERATION, "fixture": protocol.FIXTURE, "sequence": 1}
    record.update(changes)
    return json.dumps(record).encode() + b"\n"


class CooperativeBrokerProtocolTests(unittest.TestCase):
    def command(self):
        return protocol.own_fixture_command(sys.executable, str(Path.cwd()))

    def test_only_fixed_operation_fixture_and_sequence_are_admitted(self):
        self.assertEqual(protocol.decode_request(frame()), protocol.Request(
            protocol.OPERATION, protocol.FIXTURE, 1))
        for changes in ({"sequence": True}, {"sequence": 1.0}, {"sequence": 2},
                        {"operation": "cargo"}, {"fixture": "../project"},
                        {"fixture": [protocol.FIXTURE]}, {"operation": None}):
            with self.subTest(changes=changes), self.assertRaises(protocol.ProtocolDenied):
                protocol.decode_request(frame(**changes))

    def test_no_requester_command_parameters_are_accepted(self):
        for key in ("argv", "executable", "cwd", "env", "stdin", "shell", "path", "handle"):
            with self.subTest(key=key), self.assertRaises(protocol.ProtocolDenied):
                protocol.decode_request(frame(**{key: "untrusted"}))

    def test_duplicate_missing_malformed_truncated_and_oversized_frames_deny(self):
        duplicate = frame().replace(b'"sequence": 1', b'"sequence": 1,"sequence": 1')
        for payload in (duplicate, b'{}\n', b'[]\n', b'null\n', b'NaN\n',
                        b'{"sequence": Infinity}\n', b'\xff\n', b'{\n',
                        frame()[:-1], frame() + frame(), b' ' * 1025 + b'\n',
                        bytearray(frame()), b''):
            with self.subTest(payload=repr(payload)[:60]), self.assertRaises(protocol.ProtocolDenied):
                protocol.decode_request(payload)

    def test_approved_command_cannot_be_replaced_by_mutating_request(self):
        request = bytearray(frame())
        command = self.command()
        session = protocol.Session(command)
        approved = session.approve(bytes(request))
        request[:] = frame(argv=["shell"], stdin="project-code")
        self.assertIs(session.claim(approved), command)
        self.assertEqual(command.argv, (sys.executable, "-I", "-c", protocol.FIXED_PROGRAM,
                                       protocol.FIXTURE))
        self.assertEqual(command.stdin, protocol.FIXED_STDIN)
        with self.assertRaises(FrozenInstanceError):
            command.stdin = b"project-code"

    def test_parent_environment_never_enters_command(self):
        expected = (("LC_ALL", "C"),)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "foreign", "PATH": "foreign",
                                     "RUSTC_WRAPPER": "foreign"}):
            self.assertEqual(self.command().environment, expected)
        root = str(Path.cwd())
        self.assertEqual(protocol.own_fixture_command(sys.executable, root, system_root=root).environment,
                         expected + (("SystemRoot", root),))

    def test_replay_invalidates_prior_approval(self):
        session = protocol.Session(self.command())
        approved = session.approve(frame())
        with self.assertRaises(protocol.ProtocolDenied):
            session.approve(frame())
        with self.assertRaises(protocol.ProtocolDenied):
            session.claim(approved)

    def test_disconnect_before_or_after_approval_denies_claim(self):
        session = protocol.Session(self.command())
        session.disconnect()
        with self.assertRaises(protocol.ProtocolDenied):
            session.approve(frame())
        session = protocol.Session(self.command())
        approved = session.approve(frame())
        session.disconnect()
        with self.assertRaises(protocol.ProtocolDenied):
            session.claim(approved)

    def test_claim_is_identity_bound_and_one_use(self):
        command = self.command()
        session = protocol.Session(command)
        session.approve(frame())
        with self.assertRaises(protocol.ProtocolDenied):
            session.claim(replace(command))
        session = protocol.Session(command)
        session.approve(frame())
        self.assertIs(session.claim(command), command)
        with self.assertRaises(protocol.ProtocolDenied):
            session.claim(command)

    def test_disconnect_after_claim_does_not_revoke_returned_command(self):
        # Explicit missing native gate: this object is NOT permission to launch
        # after disconnect. Future native harness must resolve launch ordering.
        command = self.command()
        session = protocol.Session(command)
        session.approve(frame())
        claimed = session.claim(command)
        session.disconnect()
        self.assertIs(claimed, command)
        with self.assertRaises(protocol.ProtocolDenied):
            session.claim(claimed)

    def test_invalid_frame_closes_session(self):
        session = protocol.Session(self.command())
        with self.assertRaises(protocol.ProtocolDenied):
            session.approve(b'{\n')
        with self.assertRaises(protocol.ProtocolDenied):
            session.approve(frame())

    def test_concurrent_claim_can_only_consume_once(self):
        command = self.command()
        session = protocol.Session(command)
        session.approve(frame())

        def claim():
            try:
                session.claim(command)
                return True
            except protocol.ProtocolDenied:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: claim(), range(2))), [False, True])

    def test_controller_paths_and_mutable_command_are_rejected(self):
        for value in ("relative", "bad\0path", None):
            with self.assertRaises(protocol.ProtocolDenied):
                protocol.own_fixture_command(value, str(Path.cwd()))
            with self.assertRaises(protocol.ProtocolDenied):
                protocol.own_fixture_command(sys.executable, value)
        with self.assertRaises(protocol.ProtocolDenied):
            protocol.Session(replace(self.command(), environment=[("PATH", "foreign")]))
