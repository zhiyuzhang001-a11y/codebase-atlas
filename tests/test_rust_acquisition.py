from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from codebase_atlas.rust_acquisition import (
    COMPONENTS, _NoRedirect, _download, acquire_components, component_acquisition_plan,
)
from codebase_atlas.rust_runtime import RustRuntimeError


class RustAcquisitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.repo = self.base / "project"
        self.repo.mkdir()
        self.store = self.base / "cache"
        self.payloads = {}
        identities = {}
        for component in sorted(COMPONENTS):
            payload = component.encode()
            url = "https://static.rust-lang.org/dist/" + component + ".tar.xz"
            self.payloads[url] = payload
            identities[component] = {"url": url, "sha256": hashlib.sha256(payload).hexdigest(), "available": True}
        self.lock = {"targets": {"macos-arm64": {"components": {
            key: value for key, value in identities.items() if key != "rust-src"}}},
            "rust_src": identities["rust-src"]}
        patcher = patch("codebase_atlas.rust_acquisition.release_lock", return_value=self.lock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, url, stream):
        payload = self.payloads[url]
        stream.write(payload)
        return hashlib.sha256(payload).hexdigest()

    def acquire(self, **kwargs):
        return acquire_components(self.repo, "macos-arm64", store=self.store, **kwargs)

    def test_readonly_plan_and_unauthorized_acquisition_do_not_write_or_fetch(self):
        with patch("codebase_atlas.rust_acquisition._download") as fetch:
            plan = component_acquisition_plan(self.repo, "macos-arm64", store=self.store)
            self.assertEqual(set(plan), COMPONENTS)
            self.assertTrue(all(not identity["cached"] for identity in plan.values()))
            for authorization in (False, 1, "yes"):
                with self.assertRaisesRegex(RustRuntimeError, "explicit network authorization"):
                    self.acquire(network_authorized=authorization)
            fetch.assert_not_called()
        self.assertFalse(self.store.exists())

    def test_verified_cache_is_reused_offline_for_another_project(self):
        with patch("codebase_atlas.rust_acquisition._download", side_effect=self.fetch) as fetch:
            paths = self.acquire(network_authorized=True)
            self.assertEqual(fetch.call_count, 5)
        other = self.base / "other-project"
        other.mkdir()
        with patch("codebase_atlas.rust_acquisition._download") as fetch:
            self.assertEqual(acquire_components(other, "macos-arm64", store=self.store), paths)
            fetch.assert_not_called()
        self.assertEqual(list(self.repo.iterdir()), [])
        self.assertEqual(list(other.iterdir()), [])

    def test_checksum_failure_cleans_temporary_bytes_without_publication(self):
        def corrupt(url, stream):
            stream.write(b"bad bytes")
            return "0" * 64
        with patch("codebase_atlas.rust_acquisition._download", side_effect=corrupt):
            with self.assertRaisesRegex(RustRuntimeError, "checksum"):
                self.acquire(network_authorized=True)
        self.assertEqual(list(self.store.iterdir()), [])

    def test_transport_failure_cleans_partial_bytes(self):
        def fail(url, stream):
            stream.write(b"partial")
            raise OSError("transport failed")
        with patch("codebase_atlas.rust_acquisition._download", side_effect=fail):
            with self.assertRaisesRegex(OSError, "transport failed"):
                self.acquire(network_authorized=True)
        self.assertEqual(list(self.store.iterdir()), [])

    def test_corrupted_cache_is_preserved_and_never_redownloaded(self):
        with patch("codebase_atlas.rust_acquisition._download", side_effect=self.fetch):
            paths = self.acquire(network_authorized=True)
        paths["cargo"].write_bytes(b"foreign bytes")
        with patch("codebase_atlas.rust_acquisition._download") as fetch:
            with self.assertRaisesRegex(RustRuntimeError, "checksum"):
                self.acquire(network_authorized=True)
            fetch.assert_not_called()
        self.assertEqual(paths["cargo"].read_bytes(), b"foreign bytes")

    def test_project_local_store_is_rejected_before_writes(self):
        with self.assertRaisesRegex(RustRuntimeError, "outside the project"):
            acquire_components(self.repo, "macos-arm64", store=self.repo / "cache", network_authorized=True)
        self.assertEqual(list(self.repo.iterdir()), [])

    def test_foreign_directory_and_unknown_target_are_rejected(self):
        self.store.mkdir(mode=0o700)
        with patch("codebase_atlas.rust_acquisition._private_directory", side_effect=RustRuntimeError("foreign store")):
            with self.assertRaisesRegex(RustRuntimeError, "foreign store"):
                self.acquire(network_authorized=True)
        with self.assertRaisesRegex(RustRuntimeError, "Unsupported"):
            acquire_components(self.repo, "macos-x86_64", store=self.store)

    def test_source_lock_cannot_select_foreign_origin_or_unavailable_component(self):
        cargo = self.lock["targets"]["macos-arm64"]["components"]["cargo"]
        for url in ("http://static.rust-lang.org/dist/cargo.tar.xz",
                    "https://static.rust-lang.org.evil/dist/cargo.tar.xz",
                    "https://static.rust-lang.org/dist/../cargo.tar.xz",
                    "https://static.rust-lang.org/dist/cargo.tar.xz?override=1"):
            cargo["url"] = url
            with self.assertRaisesRegex(RustRuntimeError, "source lock"):
                self.acquire(network_authorized=True)
        cargo["url"] = "https://static.rust-lang.org/dist/cargo.tar.xz"
        cargo["available"] = False
        with self.assertRaisesRegex(RustRuntimeError, "source lock"):
            self.acquire(network_authorized=True)
        self.assertFalse(self.store.exists())

    def test_concurrent_publication_reuses_identical_content_without_overwrite(self):
        barrier = threading.Barrier(2)
        cargo_url = self.lock["targets"]["macos-arm64"]["components"]["cargo"]["url"]
        def fetch(url, stream):
            if url == cargo_url:
                barrier.wait(timeout=10)
            return self.fetch(url, stream)
        with patch("codebase_atlas.rust_acquisition._download", side_effect=fetch):
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures = [workers.submit(self.acquire, network_authorized=True) for _ in range(2)]
                results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(list(self.store.iterdir())), 5)

    def test_redirect_is_refused_before_destination_request(self):
        with self.assertRaisesRegex(RustRuntimeError, "redirects"):
            _NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example/archive")

    def test_conflicting_race_asset_is_preserved(self):
        cargo = self.lock["targets"]["macos-arm64"]["components"]["cargo"]
        destination = self.store / (cargo["sha256"] + ".tar.xz")
        def fetch(url, stream):
            if url == cargo["url"]:
                destination.write_bytes(b"different publisher")
                destination.chmod(0o600)
            return self.fetch(url, stream)
        with patch("codebase_atlas.rust_acquisition._download", side_effect=fetch):
            with self.assertRaisesRegex(RustRuntimeError, "checksum"):
                self.acquire(network_authorized=True)
        self.assertEqual(destination.read_bytes(), b"different publisher")
        self.assertFalse(any(path.name.startswith(".component-") for path in self.store.iterdir()))

    def test_later_failure_preserves_only_already_verified_components(self):
        calls = 0
        def fetch(url, stream):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("later failure")
            return self.fetch(url, stream)
        with patch("codebase_atlas.rust_acquisition._download", side_effect=fetch):
            with self.assertRaisesRegex(OSError, "later failure"):
                self.acquire(network_authorized=True)
        self.assertEqual(len(list(self.store.iterdir())), 1)
        with patch("codebase_atlas.rust_acquisition._download", side_effect=self.fetch) as fetch:
            self.acquire(network_authorized=True)
            self.assertEqual(fetch.call_count, 4)

    def test_download_deadline_failure_does_not_accept_bytes(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        url = next(iter(self.payloads))
        response.geturl.return_value = url
        response.status = 200
        opener = Mock()
        opener.open.return_value = response
        stream = Mock()
        with patch("codebase_atlas.rust_acquisition.build_opener", return_value=opener), \
                patch("codebase_atlas.rust_acquisition.time.monotonic", side_effect=[0, 0, 181]):
            with self.assertRaisesRegex(RustRuntimeError, "deadline"):
                _download(url, stream)
        stream.write.assert_not_called()

    def test_transport_identity_and_size_are_bounded(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status = 200
        url = next(iter(self.payloads))
        response.geturl.return_value = url
        response.read.side_effect = [b"oversized", b""]
        opener = Mock()
        opener.open.return_value = response
        with patch("codebase_atlas.rust_acquisition.build_opener", return_value=opener), \
                patch("codebase_atlas.rust_acquisition.MAX_DOWNLOAD_BYTES", 2):
            with self.assertRaisesRegex(RustRuntimeError, "oversized"):
                _download(url, Mock())
        response.geturl.return_value = "https://evil.example/archive"
        with patch("codebase_atlas.rust_acquisition.build_opener", return_value=opener):
            with self.assertRaisesRegex(RustRuntimeError, "identity"):
                _download(url, Mock())


if __name__ == "__main__":
    unittest.main()
