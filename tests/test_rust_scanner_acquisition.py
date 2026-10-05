import copy
import hashlib
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from codebase_atlas.rust_installation import release_lock
from codebase_atlas.cli import main
from codebase_atlas.rust_runtime import RustRuntimeError
from codebase_atlas.rust_scanner_acquisition import (
    PAGE, _ReleaseRedirects, acquire_scanner, fetch_scanner_release,
)
from tests import test_rust_scanner_installation as scanner_tests


class RustScannerAcquisitionTests(unittest.TestCase):
    def setUp(self):
        scanner_tests.RustScannerInstallationTests.setUp(self)
        scanner_tests.RustScannerInstallationTests.bundle(self)
        self.tag = "v0.28.0"  # Synthetic test metadata, not a release decision.
        self.payload = {"tag_name": self.tag, "html_url": PAGE + self.tag,
                        "draft": False, "prerelease": False, "assets": []}
        self.downloads = {}
        archive_bytes = self.archive.read_bytes()
        archive_sha = hashlib.sha256(archive_bytes).hexdigest()
        aggregate = []
        for target in release_lock()["targets"]:
            suffix = ".zip" if target.startswith("windows-") else ".tar.gz"
            name = "codebase-atlas-rust-syntax-0.2.0-" + target + suffix
            self.add_asset(name, archive_bytes)
            self.add_asset(name + ".sha256", (archive_sha + "  " + name + "\n").encode())
            aggregate.append(archive_sha + "  " + name)
        self.add_asset("RUST_SYNTAX_SHA256SUMS.txt", ("\n".join(aggregate) + "\n").encode())
        for name, value in (("codebase_atlas.rust_scanner_acquisition.scanner_store", self.store),
                            ("codebase_atlas.rust_scanner_acquisition.current_platform_target", "macos-arm64")):
            patcher = patch(name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.documents = {"releases/tags/" + self.tag: self.payload,
                          "git/ref/tags/" + self.tag: {"ref": "refs/tags/" + self.tag,
                                                      "object": {"sha": self.commit, "type": "commit"}}}

    def add_asset(self, name, data):
        self.downloads[name] = data
        self.payload["assets"].append({"name": name, "size": len(data),
            "browser_download_url": "https://github.com/zhiyuzhang001-a11y/codebase-atlas/releases/download/" + self.tag + "/" + name,
            "digest": "sha256:" + hashlib.sha256(data).hexdigest()})

    def json(self, path):
        return copy.deepcopy(self.documents[path])

    def download(self, asset, path, **kwargs):
        data = self.downloads[asset.name]
        path.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def fetch(self):
        with patch("codebase_atlas.rust_scanner_acquisition._json", side_effect=self.json):
            return fetch_scanner_release(self.tag, network_authorized=True)

    def acquire(self):
        with patch("codebase_atlas.rust_scanner_acquisition._json", side_effect=self.json), \
                patch("codebase_atlas.rust_scanner_acquisition.download_asset", side_effect=self.download):
            return acquire_scanner(self.repository, self.tag, network_authorized=True)

    def test_network_authority_is_required_before_metadata_or_writes(self):
        with patch("codebase_atlas.rust_scanner_acquisition._json") as request:
            with self.assertRaisesRegex(RustRuntimeError, "network authorization"):
                fetch_scanner_release(self.tag)
            request.assert_not_called()
        self.assertFalse(self.store.exists())

    def test_exact_tag_commit_and_five_platform_inventory_are_required(self):
        release = self.fetch()
        self.assertEqual(release.commit, self.commit)
        self.payload["assets"].pop(0)
        with self.assertRaisesRegex(RustRuntimeError, "complete five-platform"):
            self.fetch()

    def test_draft_prerelease_foreign_page_and_foreign_asset_tag_are_rejected(self):
        for key, value in (("draft", True), ("prerelease", True), ("html_url", PAGE + "v0.27.0")):
            old = self.payload[key]
            self.payload[key] = value
            with self.assertRaisesRegex(RustRuntimeError, "exact published stable"):
                self.fetch()
            self.payload[key] = old
        self.payload["assets"][0]["browser_download_url"] = self.payload["assets"][0]["browser_download_url"].replace(self.tag, "v0.27.0")
        with self.assertRaisesRegex(RustRuntimeError, "exact Release tag"):
            self.fetch()

    def test_annotated_tag_is_resolved_and_cycle_is_refused(self):
        sha = "2" * 40
        reference = self.documents["git/ref/tags/" + self.tag]
        reference["object"] = {"sha": sha, "type": "tag"}
        self.documents["git/tags/" + sha] = {"sha": sha, "object": {"sha": self.commit, "type": "commit"}}
        self.assertEqual(self.fetch().commit, self.commit)
        self.documents["git/tags/" + sha]["object"] = {"sha": sha, "type": "tag"}
        with self.assertRaisesRegex(RustRuntimeError, "object identity"):
            self.fetch()

    def test_installation_verifies_manifest_and_reuses_without_asset_download(self):
        binary = self.acquire()
        self.assertTrue(binary.is_file())
        self.assertEqual(list(self.repository.iterdir()), [])
        with patch("codebase_atlas.rust_scanner_acquisition._json", side_effect=self.json), \
                patch("codebase_atlas.rust_scanner_acquisition.download_asset") as download:
            self.assertEqual(acquire_scanner(self.repository, self.tag, network_authorized=True), binary)
            download.assert_not_called()

    def test_checksum_disagreement_never_creates_installation(self):
        name = next(name for name in self.downloads if name.endswith("macos-arm64.tar.gz.sha256"))
        self.downloads[name] = self.downloads[name].replace(self.downloads[name][:64], b"0" * 64)
        with self.assertRaisesRegex(RustRuntimeError, "checksums disagree"):
            self.acquire()
        self.assertFalse(self.store.exists())

    def test_tag_commit_mismatch_cannot_adopt_old_internal_scanner(self):
        self.documents["git/ref/tags/" + self.tag]["object"]["sha"] = "2" * 40
        with self.assertRaisesRegex(RustRuntimeError, "manifest/source/license"):
            self.acquire()
        self.assertFalse(self.store.exists())

    def test_project_local_store_is_rejected(self):
        with patch("codebase_atlas.rust_scanner_acquisition.scanner_store", return_value=self.repository / "store"):
            with self.assertRaisesRegex(RustRuntimeError, "outside the project"):
                self.acquire()
        self.assertEqual(list(self.repository.iterdir()), [])

    def test_redirects_cannot_fetch_foreign_or_non_https_assets(self):
        for url in ("http://github.com/file", "https://evil.example/file", "https://github.com:444/file"):
            with self.assertRaisesRegex(RustRuntimeError, "trusted HTTPS"):
                _ReleaseRedirects().redirect_request(None, None, 302, "", {}, url)

    def test_cli_gate_is_closed_and_candidate_plan_does_not_download_assets(self):
        args = ["rust-scanner-prepare", "--repo", str(self.repository), "--release-tag", self.tag, "--allow-network"]
        with redirect_stdout(StringIO()), patch("codebase_atlas.rust_scanner_acquisition._json") as request:
            self.assertEqual(main(args + ["--apply"]), 2)
            request.assert_not_called()
        with patch("codebase_atlas.cli.get_language", return_value=SimpleNamespace(public_enabled=True)), \
                patch("codebase_atlas.rust_scanner_acquisition._json", side_effect=self.json), \
                patch("codebase_atlas.rust_scanner_acquisition.download_asset") as download:
            with redirect_stdout(StringIO()):
                self.assertEqual(main(args), 0)
            download.assert_not_called()
        self.assertFalse(self.store.exists())

    def test_api_digest_and_duplicate_asset_metadata_are_required(self):
        self.payload["assets"][0]["digest"] = ""
        with self.assertRaisesRegex(RustRuntimeError, "API SHA256"):
            self.fetch()
        self.payload["assets"].append(self.payload["assets"][0])
        with self.assertRaisesRegex(RustRuntimeError, "duplicate"):
            self.fetch()


if __name__ == "__main__":
    unittest.main()
