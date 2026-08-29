from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest

from codebase_atlas.version_check import VersionNotifier, fetch_release_status


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class VersionCheckTests(unittest.TestCase):
    def test_newer_release_notifies_without_installing_and_uses_cache(self) -> None:
        calls = []

        def opener(_request, **kwargs):
            calls.append(kwargs)
            return Response(json.dumps({
                "tag_name": "v0.21.0",
                "html_url": "https://example.test/release",
                "assets": [{"name": "codebase_atlas.whl.sha256"}],
            }).encode())

        with tempfile.TemporaryDirectory() as raw:
            data = Path(raw)
            first = fetch_release_status("0.20.0", data, opener=opener, now=1000)
            second = fetch_release_status("0.20.0", data, opener=opener, now=1001)
        self.assertEqual(first["status"], "update_available")
        self.assertFalse(first["install_attempted"])
        self.assertTrue(first["checksum_available"])
        self.assertEqual(second["source"], "cache")
        self.assertEqual(len(calls), 1)

    def test_network_failure_is_nonfatal_and_cached(self) -> None:
        def opener(_request, **_kwargs):
            raise OSError("offline")

        with tempfile.TemporaryDirectory() as raw:
            result = fetch_release_status("0.20.0", Path(raw), opener=opener, now=1000)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["ok"])
        self.assertFalse(result["install_attempted"])

    def test_release_checksum_manifest_is_recognized(self) -> None:
        def opener(_request, **_kwargs):
            return Response(json.dumps({
                "tag_name": "v0.20.0",
                "html_url": "https://example.test/release",
                "assets": [{"name": "SHA256SUMS.txt"}],
            }).encode())

        with tempfile.TemporaryDirectory() as raw:
            result = fetch_release_status("0.20.0", Path(raw), opener=opener, now=1000)
        self.assertEqual(result["status"], "current")
        self.assertTrue(result["checksum_available"])

    def test_legacy_cache_is_refreshed_after_schema_change(self) -> None:
        calls = []

        def opener(_request, **_kwargs):
            calls.append(True)
            return Response(json.dumps({
                "tag_name": "v0.20.0",
                "html_url": "https://example.test/release",
                "assets": [{"name": "SHA256SUMS.txt"}],
            }).encode())

        with tempfile.TemporaryDirectory() as raw:
            data = Path(raw)
            (data / "version-check.json").write_text(json.dumps({
                "checked_epoch": 999,
                "ttl_seconds": 86_400,
                "status": "current",
                "checksum_available": False,
            }), encoding="utf-8")
            result = fetch_release_status(
                "0.20.0", data, opener=opener, now=1000
            )
        self.assertEqual(result["source"], "network")
        self.assertTrue(result["checksum_available"])
        self.assertEqual(len(calls), 1)

    def test_cache_is_refreshed_after_installed_version_changes(self) -> None:
        calls = []

        def opener(_request, **_kwargs):
            calls.append(True)
            return Response(json.dumps({
                "tag_name": "v0.20.0",
                "html_url": "https://example.test/release",
                "assets": [{"name": "SHA256SUMS.txt"}],
            }).encode())

        with tempfile.TemporaryDirectory() as raw:
            data = Path(raw)
            old = fetch_release_status(
                "0.20.0", data, opener=opener, now=1000
            )
            upgraded = fetch_release_status(
                "0.21.0", data, opener=opener, now=1001
            )
        self.assertEqual(old["current_version"], "0.20.0")
        self.assertEqual(upgraded["current_version"], "0.21.0")
        self.assertEqual(upgraded["latest_version"], "0.20.0")
        self.assertEqual(upgraded["status"], "current")
        self.assertEqual(upgraded["source"], "network")
        self.assertEqual(len(calls), 2)

    def test_notifier_can_be_disabled_without_fetch(self) -> None:
        called = []
        notifier = VersionNotifier(
            "0.20.0", Path("unused"), enabled=False,
            fetcher=lambda *_args: called.append(True),
        )
        notifier.start()
        self.assertEqual(notifier.current()["status"], "disabled")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
