from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import sys
import signal
import subprocess
import tempfile
import threading
from time import monotonic
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

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

    def claimed_session(self, command=None):
        command = command or self.command()
        session = protocol.Session(command)
        session.claim(session.approve(frame()))
        return session, command

    def test_claim_to_launch_barrier_disconnect_prevents_create(self):
        session, command = self.claimed_session()
        at_barrier, release = threading.Event(), threading.Event()
        create, cleanup = Mock(), Mock()
        result = []

        def worker():
            at_barrier.set()
            if not release.wait(2):
                result.append("barrier-timeout")
                return
            try:
                session.launch(command, create, cleanup)
            except protocol.ProtocolDenied:
                result.append("denied")

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(at_barrier.wait(2))
            session.disconnect()
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["denied"])
        create.assert_not_called()
        cleanup.assert_not_called()

    def test_live_replay_reclaims_resource_and_cleanup_failure_retains_ownership(self):
        session, command = self.claimed_session()
        resource = object()
        cleanup = Mock(side_effect=[RuntimeError("cleanup failed"), None])
        self.assertIs(session.launch(command, lambda _: resource, cleanup), resource)
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            session.disconnect()
        session.disconnect()
        session.disconnect()
        self.assertEqual(cleanup.call_count, 2)
        with self.assertRaises(protocol.ProtocolDenied):
            session.launch(command, Mock(), cleanup)
        session, command = self.claimed_session()
        cleanup = Mock()
        session.launch(command, lambda _: resource, cleanup)
        with self.assertRaises(protocol.ProtocolDenied):
            session.approve(frame())
        cleanup.assert_called_once_with(resource)

    def test_disconnect_ack_waits_for_starting_creation_and_cleanup(self):
        session, command = self.claimed_session()
        creating, release, cancelling, acknowledged = [threading.Event() for _ in range(4)]
        resource, outcomes = object(), []
        cleanup = Mock()

        def create(_):
            creating.set()
            if not release.wait(2):
                raise TimeoutError("creation test barrier")
            return resource

        def launch():
            try:
                session.launch(command, create, cleanup)
                outcomes.append("created")
            except BaseException as exc:
                outcomes.append(type(exc).__name__)

        def disconnect():
            cancelling.set()
            session.disconnect()
            acknowledged.set()

        starter, canceller = threading.Thread(target=launch), threading.Thread(target=disconnect)
        starter.start()
        try:
            self.assertTrue(creating.wait(2))
            canceller.start()
            self.assertTrue(cancelling.wait(2))
            self.assertFalse(acknowledged.is_set())
        finally:
            release.set()
            starter.join(2)
            if canceller.ident is not None:
                canceller.join(2)
        self.assertFalse(starter.is_alive())
        self.assertFalse(canceller.is_alive())
        self.assertEqual(outcomes, ["created"])
        self.assertTrue(acknowledged.is_set())
        cleanup.assert_called_once_with(resource)

    def test_creation_errors_and_invalid_callbacks_fail_closed(self):
        for create, cleanup, error in (
                (Mock(side_effect=RuntimeError("own spawn failure")), Mock(), RuntimeError),
                (Mock(return_value=None), Mock(), protocol.ProtocolDenied),
                (None, Mock(), protocol.ProtocolDenied),
                (Mock(), None, protocol.ProtocolDenied)):
            with self.subTest(create=create):
                session, command = self.claimed_session()
                with self.assertRaises(error):
                    session.launch(command, create, cleanup)
                retry = Mock()
                with self.assertRaises(protocol.ProtocolDenied):
                    session.launch(command, retry, Mock())
                retry.assert_not_called()
                session.disconnect()
        session, command = self.claimed_session()
        cleanup, retry, resource = Mock(), Mock(), object()
        session.launch(command, lambda _: resource, cleanup)
        with self.assertRaises(protocol.ProtocolDenied):
            session.launch(command, retry, cleanup)
        retry.assert_not_called()
        cleanup.assert_called_once_with(resource)

    def test_native_started_child_disconnect_reaps_owned_process(self):
        # Native owned child ONLY, not restricted requester/IPC or full I0 proof.
        from scripts.rust_windows_child_policy_feasibility import windows_directory
        with tempfile.TemporaryDirectory(prefix="atlas-gate-own-") as scratch:
            command = protocol.own_fixture_command(str(Path(sys.executable).resolve()),
                str(Path(scratch).resolve()),
                system_root=windows_directory() if os.name == "nt" else None)
            session, command = self.claimed_session(command)

            def create(fixed):
                if os.name == "nt":
                    from codebase_atlas.windows_owned_process import WindowsOwnedProcess
                    return WindowsOwnedProcess(fixed.argv, cwd=fixed.cwd, env=dict(fixed.environment))
                return subprocess.Popen(fixed.argv, cwd=fixed.cwd, env=dict(fixed.environment),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    close_fds=True, start_new_session=True)

            def cleanup(process):
                try:
                    if os.name == "nt":
                        # Cache actual exit while the retained native handle is
                        # still valid; close_owned_job closes that handle.
                        deadline = monotonic() + 5
                        try:
                            process.terminate()
                            process.wait(timeout=max(0, deadline - monotonic()))
                        finally:
                            process.close_owned_job(max(0, deadline - monotonic()))
                    else:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=5)
                finally:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        stream.close()

            process = session.launch(command, create, cleanup)
            try:
                self.assertIsNone(process.poll())  # Fixture waits for controller stdin EOF.
                session.disconnect()
                self.assertIsNotNone(process.poll())
            finally:
                session.disconnect()
