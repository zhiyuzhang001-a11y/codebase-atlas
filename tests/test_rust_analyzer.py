from __future__ import annotations

import json
from io import BytesIO
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

from codebase_atlas.providers.rust_analyzer import (
    PROVIDER_NAME,
    PROVIDER_VERSION,
    RustAnalyzerError,
    RustAnalyzerProvider,
    _read_lsp_frame,
)
from codebase_atlas.refresh_planner import build_generation_manifest


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

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def provider(self) -> RustAnalyzerProvider:
        return RustAnalyzerProvider(
            self.analyzer,
            self.repository,
            "rust-project",
            self.generation,
            readiness_seconds=1,
        )

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


if __name__ == "__main__":
    unittest.main()
