from __future__ import annotations

import json
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import Mock, patch

from codebase_atlas.providers.rust_analyzer import (
    PROVIDER_NAME,
    PROVIDER_VERSION,
    RustAnalyzerError,
    RustAnalyzerProvider,
    _read_lsp_frame,
)
from codebase_atlas.refresh_planner import build_generation_manifest
from codebase_atlas.rust_runtime import RustRuntimeError


def git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args], check=True, capture_output=True
    )


FAKE_ANALYZER = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time

if len(sys.argv) == 2 and sys.argv[1] == "--version":
    print("rust-analyzer 1.98.0 (fake)")
    raise SystemExit(0)

def read_message():
    length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            raise SystemExit(0)
        if line in (b"\n", b"\r\n"):
            break
        name, value = line.split(b":", 1)
        if name.lower() == b"content-length":
            length = int(value.strip())
    return json.loads(sys.stdin.buffer.read(length))

def write_message(value):
    payload = json.dumps(value, separators=(",", ":")).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
    sys.stdout.buffer.flush()

attempts = 0
while True:
    message = read_message()
    method = message.get("method")
    if method == "initialize":
        log = {
            "initializationOptions": message["params"]["initializationOptions"],
            "offline": os.environ.get("CARGO_NET_OFFLINE"),
        }
        with open(os.environ["FAKE_RA_LOG"], "w", encoding="utf-8") as stream:
            json.dump(log, stream)
        write_message({"jsonrpc":"2.0", "id":message["id"], "result":{"capabilities":{}}})
    elif method in ("initialized", "textDocument/didOpen"):
        continue
    elif method == "rust-analyzer/analyzerStatus":
        write_message({"jsonrpc":"2.0", "id":message["id"],
                       "result":"Workspaces:\nLoaded 1 packages across 1 workspace."})
    elif method == "rust-analyzer/viewCrateGraph":
        write_message({"jsonrpc":"2.0", "id":message["id"],
                       "result":"digraph rust_analyzer_crate_graph {\n  0 [label=fixture];\n}"})
    elif method in ("textDocument/definition", "textDocument/references"):
        if os.environ.get("FAKE_RA_MODE") == "timeout":
            marker = os.environ.get("FAKE_RA_CHILD_MARKER")
            if marker:
                subprocess.Popen([
                    sys.executable, "-c",
                    "import pathlib,time; time.sleep(.5); "
                    f"pathlib.Path({marker!r}).write_text('survived')",
                ])
            time.sleep(10)
            continue
        attempts += 1
        if attempts == 1 and os.environ.get("FAKE_RA_MODE") == "retry":
            write_message({"jsonrpc":"2.0", "id":message["id"],
                           "error":{"code":-32801,"message":"Content modified"}})
            continue
        uri = message["params"]["textDocument"]["uri"]
        location = {"uri":uri,"range":{"start":{"line":0,"character":7},
                                         "end":{"line":0,"character":10}}}
        write_message({"jsonrpc":"2.0", "id":message["id"], "result":[location]})
    elif method == "shutdown":
        write_message({"jsonrpc":"2.0", "id":message["id"], "result":None})
    elif method == "exit":
        raise SystemExit(0)
'''


class RustAnalyzerProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repo"
        (self.repository / "src").mkdir(parents=True)
        (self.repository / "Cargo.toml").write_text(
            '[package]\nname="ra-fixture"\nversion="0.1.0"\n'
            '[features]\ndefault=[]\nfast=[]\n', encoding="utf-8"
        )
        (self.repository / "src/lib.rs").write_text(
            "pub fn run() {}\npub fn call() { run(); }\n", encoding="utf-8"
        )
        git(self.repository, "init", "-q")
        git(self.repository, "config", "user.email", "atlas@example.invalid")
        git(self.repository, "config", "user.name", "Atlas Test")
        git(self.repository, "add", ".")
        git(self.repository, "commit", "-qm", "initial")
        self.analyzer = self.root / "rust-analyzer"
        self.analyzer.write_text(textwrap.dedent(FAKE_ANALYZER), encoding="utf-8")
        self.analyzer.chmod(0o755)
        self.log = self.root / "ra-log.json"
        self.generation = build_generation_manifest(
            self.repository,
            "rust-project",
            "rust",
            generation_id="generation-1",
            provider_identity={"status": "t1"},
            sidecar_identity={"status": "t1"},
            created_at="generation:generation-1",
        )

    def provider(self) -> RustAnalyzerProvider:
        provider = RustAnalyzerProvider(
            Path(sys.executable),
            self.repository,
            "rust-project",
            self.generation,
            arguments=(str(self.analyzer),),
            readiness_seconds=1,
        )
        self.addCleanup(provider.close)
        return provider

    def test_verified_runtime_preflight_blocks_version_and_spawn(self):
        runtime = Mock()
        runtime.analyzer.path = Path(sys.executable).resolve()
        runtime.environment.side_effect = RustRuntimeError("unsafe configuration")
        version = Mock()
        provider = RustAnalyzerProvider(
            runtime.analyzer.path, self.repository, "rust-project", self.generation,
            runtime=runtime, version_runner=version,
        )
        with patch("codebase_atlas.providers.rust_analyzer.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RustRuntimeError, "unsafe configuration"):
                provider.start()
            version.assert_not_called()
            spawn.assert_not_called()

    def test_verified_runtime_rejects_extra_executable_arguments(self):
        runtime = Mock()
        runtime.analyzer.path = Path(sys.executable).resolve()
        with self.assertRaisesRegex(RustAnalyzerError, "verified runtime"):
            RustAnalyzerProvider(
                runtime.analyzer.path, self.repository, "rust-project", self.generation,
                runtime=runtime, arguments=(str(self.analyzer),),
            )

    def test_result_uri_round_trips_native_path(self) -> None:
        provider = self.provider()
        self.assertEqual(
            provider._relative_uri((self.repository / "src/lib.rs").as_uri()),
            "src/lib.rs",
        )
        with self.assertRaisesRegex(RustAnalyzerError, "outside the repository"):
            provider._relative_uri("file://foreign.invalid/src/lib.rs")
        with self.assertRaisesRegex(RustAnalyzerError, "unavailable"):
            provider._relative_uri((self.repository / "src/missing.rs").as_uri())

    def test_start_uses_one_deadline_for_version_initialize_and_readiness(self) -> None:
        clock = [0.0]
        initialize_budgets = []

        def version_runner(*args, **kwargs):
            self.assertLessEqual(kwargs["timeout"], 1.0)
            clock[0] = 0.4
            return subprocess.CompletedProcess(args[0], 0, "rust-analyzer 1.98.0 (fake)", "")

        def request(method, params, timeout_seconds):
            if method == "initialize":
                initialize_budgets.append(timeout_seconds)
                clock[0] = 1.1
                return {"capabilities": {}}
            self.fail("readiness must not run after the shared deadline")

        provider = self.provider()
        provider.version_runner = version_runner
        process = Mock()
        process.poll.return_value = None
        with patch("codebase_atlas.providers.rust_analyzer.monotonic", side_effect=lambda: clock[0]), \
                patch("codebase_atlas.providers.rust_analyzer.subprocess.Popen", return_value=process), \
                patch("codebase_atlas.providers.rust_analyzer.threading.Thread"), \
                patch.object(provider, "_request", side_effect=request), \
                patch.object(provider, "_notify"), \
                patch.object(provider, "_terminate") as terminate:
            with self.assertRaises(TimeoutError):
                provider.start(timeout_seconds=1.0)
            self.assertAlmostEqual(initialize_budgets[0], 0.6)
            terminate.assert_called_once()
        provider._process = None

    def test_version_timeout_is_a_timeout_not_an_uncaught_subprocess_error(self) -> None:
        provider = self.provider()
        provider.version_runner = Mock(side_effect=subprocess.TimeoutExpired("rust-analyzer", 0.1))
        with self.assertRaises(TimeoutError), patch(
            "codebase_atlas.providers.rust_analyzer.subprocess.Popen"
        ) as spawn:
            provider.start(timeout_seconds=0.1)
        spawn.assert_not_called()

    def test_startup_lock_contention_cannot_wait_outside_request_budget(self):
        provider = self.provider()
        provider._state_lock = Mock()
        provider._state_lock.acquire.return_value = False
        provider.version_runner = Mock()
        with self.assertRaises(TimeoutError):
            provider.start(timeout_seconds=0.01)
        self.assertLessEqual(provider._state_lock.acquire.call_args.kwargs["timeout"], 0.01)
        provider._state_lock.release.assert_not_called()
        provider.version_runner.assert_not_called()

    def test_unicode_positions_convert_public_codepoints_to_lsp_utf16(self) -> None:
        provider = self.provider()
        source = 'pub fn call() { let _ = "🦀"; run(); }'
        column = source.index("run") + 1
        requests = []

        def request(method, params, timeout):
            requests.append(params["position"])
            return []

        provider._semantic_ready = True
        with patch.object(provider, "_assert_fresh"), patch.object(provider, "_open", return_value=source), \
                patch.object(provider, "_request", side_effect=request):
            provider.query("definition", "run", source_path="src/lib.rs",
                           source_line=1, source_column=column)
        self.assertEqual(requests, [{"line": 0, "character": column}])

    def test_unicode_result_range_converts_utf16_and_rejects_split_surrogate(self) -> None:
        provider = self.provider()
        source = 'let _ = "🦀"; run();'
        start = len(source[:source.index("run")].encode("utf-16-le")) // 2
        raw = {"uri": (self.repository / "src/lib.rs").as_uri(), "range": {
            "start": {"line": 0, "character": start},
            "end": {"line": 0, "character": start + 3},
        }}
        with patch.object(provider, "_open", return_value=source):
            node = provider._nodes([raw], "definition", "run")[0]
            self.assertEqual(node.location.start_column, source.index("run") + 1)
            self.assertEqual(node.location.end_column, source.index("run") + 4)
            raw["range"]["start"]["character"] = source.index("🦀") + 1
            with self.assertRaises(RustAnalyzerError):
                provider._nodes([raw], "definition", "run")

    def test_lsp_reader_accumulates_fragmented_payload(self) -> None:
        class FragmentedStream(BytesIO):
            def read(self, size: int = -1) -> bytes:
                return super().read(min(size, 7) if size >= 0 else 7)

        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": ["x" * 100]}).encode()
        stream = FragmentedStream(
            f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload
        )

        self.assertEqual(_read_lsp_frame(stream)["result"], ["x" * 100])

    def test_definition_retries_readiness_and_returns_exact_t2_provenance(self) -> None:
        with patch.dict(os.environ, {
            "FAKE_RA_LOG": str(self.log), "FAKE_RA_MODE": "retry",
        }):
            provider = self.provider()
            provider.start(timeout_seconds=1)
            nodes = provider.query(
                "definition", "run", source_path="src/lib.rs",
                source_line=2, source_column=17, timeout_ms=1000,
            )
            provider.close()
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node.provider, PROVIDER_NAME)
        self.assertEqual(node.location.path, "src/lib.rs")
        self.assertEqual(node.location.start_line, 1)
        self.assertEqual(node.location.start_column, 8)
        self.assertEqual(node.provenance.fact_tier, "T2")
        self.assertEqual(node.provenance.provider_version, PROVIDER_VERSION)
        self.assertEqual(node.provenance.completeness, "complete_exact")
        settings = json.loads(self.log.read_text(encoding="utf-8"))
        self.assertEqual(settings["offline"], "true")
        options = settings["initializationOptions"]
        self.assertFalse(options["cargo"]["buildScripts"]["enable"])
        self.assertEqual(options["cargo"]["features"], "all")
        self.assertTrue(options["cargo"]["noDeps"])
        self.assertEqual(options["cargo"]["cfgs"], ["feature=default", "feature=fast"])
        self.assertFalse(options["procMacro"]["enable"])
        self.assertFalse(options["cachePriming"]["enable"])

    def test_timeout_terminates_owned_process(self) -> None:
        marker = self.root / "child-survived"
        with patch.dict(os.environ, {
            "FAKE_RA_LOG": str(self.log), "FAKE_RA_MODE": "timeout",
            "FAKE_RA_CHILD_MARKER": str(marker),
        }):
            provider = self.provider()
            provider.start(timeout_seconds=1)
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                provider.query(
                    "references", "run", source_path="src/lib.rs",
                    source_line=2, source_column=17, timeout_ms=50,
                )
            self.assertFalse(provider.running)
        time.sleep(0.7)
        self.assertFalse(marker.exists())

    def test_rejects_stale_generation_and_out_of_scope_path(self) -> None:
        with patch.dict(os.environ, {
            "FAKE_RA_LOG": str(self.log), "FAKE_RA_MODE": "normal",
        }):
            provider = self.provider()
            provider.start(timeout_seconds=1)
            with self.assertRaisesRegex(RustAnalyzerError, "outside the generation scope"):
                provider.query(
                    "definition", "run", source_path="other.rs",
                    source_line=1, source_column=1,
                )
            (self.repository / "src/lib.rs").write_text(
                "pub fn changed() {}\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RustAnalyzerError, "stale"):
                provider.query(
                    "definition", "run", source_path="src/lib.rs",
                    source_line=1, source_column=8,
                )
            provider.close()

    def test_version_mismatch_fails_before_session_start(self) -> None:
        def wrong_version(*_args, **_kwargs):
            return subprocess.CompletedProcess([], 0, "rust-analyzer 9.9.9 (fake)\n", "")

        provider = RustAnalyzerProvider(
            self.analyzer,
            self.repository,
            "rust-project",
            self.generation,
            version_runner=wrong_version,
        )
        with self.assertRaisesRegex(RustAnalyzerError, "version mismatch"):
            provider.start(timeout_seconds=1)
        self.assertFalse(provider.running)

    def test_windows_timeout_cleanup_targets_owned_process_tree(self) -> None:
        provider = self.provider()
        process = Mock(pid=12345, stdin=None, stdout=None, stderr=None)
        process.poll.return_value = None
        provider._process = process
        with patch("codebase_atlas.providers.rust_analyzer.os.name", "nt"), patch(
            "codebase_atlas.providers.rust_analyzer.subprocess.run"
        ) as kill_tree:
            provider._terminate()
        kill_tree.assert_called_once_with(
            ["taskkill", "/PID", "12345", "/T", "/F"],
            check=False, capture_output=True, timeout=3,
        )
        process.wait.assert_called_once_with(timeout=3)
        self.assertIsNone(provider._process)


if __name__ == "__main__":
    unittest.main()
