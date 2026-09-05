from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from codebase_atlas.provider_transport import (
    CodebaseMemoryMcpTransport,
    MAX_STDERR_BYTES,
    ProviderInitializeTimeout,
    _read_frame,
)


FAKE_PROVIDER = r'''#!/usr/bin/env python3
import json
import os
import sys
import time

behavior = os.environ.get("ATLAS_FAKE_PROVIDER_BEHAVIOR", "happy")

def read_frame():
    first = sys.stdin.buffer.readline()
    if not first:
        return None
    length = int(first.split(b":", 1)[1])
    while sys.stdin.buffer.readline() not in {b"\n", b"\r\n"}:
        pass
    return json.loads(sys.stdin.buffer.read(length))

def send(value):
    payload = json.dumps(value, separators=(",", ":")).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
    sys.stdout.buffer.flush()

while True:
    message = read_frame()
    if message is None:
        if behavior == "shutdown_hang":
            time.sleep(60)
        break
    if "id" not in message:
        continue
    request_id = message["id"]
    if message["method"] == "initialize":
        if behavior == "slow_initialize":
            time.sleep(0.05)
        elif behavior == "initialize_timeout":
            time.sleep(60)
        send({"jsonrpc": "2.0", "id": request_id, "result": {"serverInfo": {"name": "fake"}}})
        continue
    if behavior == "mismatch":
        send({"jsonrpc": "2.0", "id": request_id + 1, "result": {}})
    elif behavior == "malformed":
        sys.stdout.buffer.write(b"not-an-mcp-frame\n")
        sys.stdout.buffer.flush()
    elif behavior == "oversized":
        sys.stdout.buffer.write(b"Content-Length: 9000000\r\n\r\n")
        sys.stdout.buffer.flush()
    elif behavior == "exit":
        sys.exit(7)
    elif behavior == "timeout":
        time.sleep(60)
    elif behavior == "tool_error":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"isError": True, "content": []}})
    else:
        if behavior == "stderr_flood":
            sys.stderr.buffer.write(b"x" * (2 * 1024 * 1024))
            sys.stderr.buffer.flush()
        send({"jsonrpc": "2.0", "id": request_id, "result": {
            "isError": False,
            "structuredContent": {"status": "ok", "files": [], "matched_terms": [],
                "budget": {"provider_queries": 1, "max_internal_rows": 60, "max_files": 2}}
        }})
'''


class ProviderTransportTests(unittest.TestCase):
    def test_windows_uses_line_delimited_json_and_accepts_line_response(self) -> None:
        stream = BytesIO()
        message = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        with mock.patch("codebase_atlas.provider_transport.os.name", "nt"):
            CodebaseMemoryMcpTransport._write(stream, message)
        self.assertEqual(stream.getvalue(), json.dumps(
            message, separators=(",", ":")
        ).encode("utf-8") + b"\n")
        stream.seek(0)
        self.assertEqual(_read_frame(stream), message)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="atlas-provider-transport-")
        self.root = Path(self.temporary.name)
        self.script = self.root / "fake_provider.py"
        self.script.write_text(textwrap.dedent(FAKE_PROVIDER))
        self.binary = Path(sys.executable)
        self.arguments = (str(self.script),)
        self.repository = self.root / "repo"
        self.cache = self.root / "cache"
        self.repository.mkdir()
        self.cache.mkdir()
        self.environment = mock.patch.dict(os.environ, {}, clear=False)
        self.environment.start()

    def tearDown(self) -> None:
        os.environ.pop("ATLAS_FAKE_PROVIDER_BEHAVIOR", None)
        self.environment.stop()
        self.temporary.cleanup()

    def transport(self, behavior: str = "happy", observer=None) -> CodebaseMemoryMcpTransport:
        os.environ["ATLAS_FAKE_PROVIDER_BEHAVIOR"] = behavior
        return CodebaseMemoryMcpTransport(
            self.binary, self.repository, self.cache, exclusive=False, client_version="test",
            observer=observer, arguments=self.arguments,
        )

    @staticmethod
    def call(transport: CodebaseMemoryMcpTransport, timeout_ms: int = 1000):
        return transport.call("locate_files", {
            "project": "p", "intent": "target", "max_files": 2, "max_internal_rows": 60,
        }, timeout_ms=timeout_ms)

    def test_happy_path_reuses_one_child(self) -> None:
        transport = self.transport()
        try:
            first = self.call(transport)
            assert transport.process is not None
            pid = transport.process.pid
            second = self.call(transport)
            self.assertEqual(first, second)
            assert transport.process is not None
            self.assertEqual(transport.process.pid, pid)
        finally:
            transport.close()
        self.assertIsNone(transport.process)

    def test_observer_correlates_request_lifecycle_without_changing_result(self) -> None:
        events = []
        transport = self.transport(observer=events.append)
        try:
            result = self.call(transport)
        finally:
            transport.close()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            [event["sequence"] for event in events],
            list(range(1, len(events) + 1)),
        )
        writes = [event for event in events if event["event"] == "transport_request_write"]
        reads = [event for event in events if event["event"] == "transport_response_read"]
        self.assertEqual([event["request_id"] for event in writes], [1, 2])
        self.assertEqual([event["request_id"] for event in reads], [1, 2])
        payload = next(event for event in events if event["event"] == "provider_payload_received")
        self.assertEqual(payload["request_id"], 2)
        self.assertEqual(payload["structured_payload"], result)
        self.assertEqual(events[-1]["event"], "cleanup_complete")
        self.assertEqual(events[-1]["process_state"], "absent")

    def test_observer_exception_cannot_change_transport_semantics(self) -> None:
        def broken_observer(_event):
            raise RuntimeError("diagnostic sink failed")

        transport = self.transport(observer=broken_observer)
        try:
            self.assertEqual(self.call(transport)["status"], "ok")
        finally:
            transport.close()

    def test_separate_initialize_deadline_can_exceed_lock_deadline(self) -> None:
        transport = self.transport("slow_initialize")
        try:
            self.assertTrue(transport.start_for_request(
                lock_timeout_seconds=0.01,
                initialize_timeout_seconds=1.0,
            ))
            self.assertEqual(self.call(transport)["status"], "ok")
        finally:
            transport.close()

    @mock.patch("codebase_atlas.provider_transport.KILL_TIMEOUT_SECONDS", 0.05)
    @mock.patch("codebase_atlas.provider_transport.TERMINATE_TIMEOUT_SECONDS", 0.05)
    @mock.patch("codebase_atlas.provider_transport.EOF_CLOSE_TIMEOUT_SECONDS", 0.05)
    def test_initialize_timeout_is_distinct_and_closes_child(self) -> None:
        transport = self.transport("initialize_timeout")
        with self.assertRaises(ProviderInitializeTimeout):
            transport.start_for_request(
                lock_timeout_seconds=0.01,
                initialize_timeout_seconds=0.02,
            )
        self.assertIsNone(transport.process)

    def test_separate_start_preserves_lock_admission_timeout(self) -> None:
        class BusyLock:
            timeout = None

            def acquire(self, *, timeout_seconds):
                self.timeout = timeout_seconds
                raise TimeoutError("busy")

            def release(self):
                pass

        lock = BusyLock()
        transport = CodebaseMemoryMcpTransport(
            self.binary, self.repository, self.cache, exclusive=True,
            client_version="test", lock=lock, arguments=self.arguments,
        )
        with self.assertRaisesRegex(TimeoutError, "busy"):
            transport.start_for_request(
                lock_timeout_seconds=0.012,
                initialize_timeout_seconds=1.0,
            )
        self.assertEqual(lock.timeout, 0.012)
        self.assertIsNone(transport.process)

    def test_mismatched_response_id_closes_child(self) -> None:
        transport = self.transport("mismatch")
        with self.assertRaisesRegex(RuntimeError, "id mismatch"):
            self.call(transport)
        self.assertIsNone(transport.process)

    def test_malformed_frame_closes_child(self) -> None:
        transport = self.transport("malformed")
        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            self.call(transport)
        self.assertIsNone(transport.process)

    def test_oversized_frame_closes_child(self) -> None:
        transport = self.transport("oversized")
        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            self.call(transport)
        self.assertIsNone(transport.process)

    def test_child_exit_closes_child(self) -> None:
        transport = self.transport("exit")
        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            self.call(transport)
        self.assertIsNone(transport.process)

    @mock.patch("codebase_atlas.provider_transport.KILL_TIMEOUT_SECONDS", 0.05)
    @mock.patch("codebase_atlas.provider_transport.TERMINATE_TIMEOUT_SECONDS", 0.05)
    @mock.patch("codebase_atlas.provider_transport.EOF_CLOSE_TIMEOUT_SECONDS", 0.05)
    def test_request_timeout_closes_child(self) -> None:
        transport = self.transport("timeout")
        with self.assertRaises(TimeoutError):
            self.call(transport, timeout_ms=20)
        self.assertIsNone(transport.process)

    def test_tool_error_closes_child(self) -> None:
        transport = self.transport("tool_error")
        with self.assertRaisesRegex(RuntimeError, "Provider tool failed"):
            self.call(transport)
        transport.close()
        self.assertIsNone(transport.process)

    def test_stderr_is_drained_and_bounded(self) -> None:
        transport = self.transport("stderr_flood")
        try:
            self.call(transport, timeout_ms=5000)
            deadline = time.monotonic() + 2.0
            while len(transport.stderr_text) < MAX_STDERR_BYTES and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(transport.stderr_text), MAX_STDERR_BYTES)
        finally:
            transport.close()

    @mock.patch("codebase_atlas.provider_transport.KILL_TIMEOUT_SECONDS", 0.05)
    @mock.patch("codebase_atlas.provider_transport.TERMINATE_TIMEOUT_SECONDS", 0.05)
    @mock.patch("codebase_atlas.provider_transport.EOF_CLOSE_TIMEOUT_SECONDS", 0.05)
    def test_shutdown_has_terminate_fallback(self) -> None:
        transport = self.transport("shutdown_hang")
        self.call(transport)
        transport.close()
        self.assertIsNone(transport.process)


if __name__ == "__main__":
    unittest.main()
