from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse
import zipfile

from codebase_atlas.release_installation import (
    ReleaseAsset,
    current_platform_target,
    download_asset,
    install_stable_release,
    load_versioned_installation,
    parse_checksum_manifest,
    parse_stable_release,
    verify_downloaded_release,
)


def asset(name: str, payload: bytes = b"") -> dict[str, object]:
    return {
        "name": name,
        "browser_download_url": (
            "https://github.com/zhiyuzhang001-a11y/codebase-atlas/releases/"
            f"download/v1.2.3/{name}"
        ),
        "size": len(payload),
        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
    }


def release_payload() -> dict[str, object]:
    provider = "codebase-atlas-provider-9.8.7-test-linux-x86_64.tar.gz"
    return {
        "draft": False,
        "prerelease": False,
        "tag_name": "v1.2.3",
        "html_url": "https://github.com/zhiyuzhang001-a11y/codebase-atlas/releases/tag/v1.2.3",
        "assets": [
            asset("codebase_atlas-1.2.3-py3-none-any.whl"),
            asset("SHA256SUMS.txt"),
            asset(provider),
            asset(provider + ".sha256"),
            asset("PROVIDER_SHA256SUMS.txt"),
        ],
    }


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ReleaseInstallationTests(unittest.TestCase):
    def test_version_selector_rejects_receipt_from_another_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            selected = Path(raw) / "1.2.3"
            selected.mkdir()
            (selected / "python").write_text("", encoding="utf-8")
            (selected / "atlas").write_text("", encoding="utf-8")
            (selected / "provider").write_text("", encoding="utf-8")
            (selected / "installation.json").write_text(json.dumps({
                "schema_version": 1,
                "version": "9.9.9",
                "target": "linux-x86_64",
                "python": "python",
                "atlas_executable": "atlas",
                "provider_binary": "provider",
                "provider_version": "provider-1",
                "wheel_sha256": "a" * 64,
                "provider_sha256": "b" * 64,
            }), encoding="utf-8")
            with (
                patch(
                    "codebase_atlas.release_installation.installation_root",
                    return_value=Path(raw),
                ),
                self.assertRaisesRegex(RuntimeError, "does not match"),
            ):
                load_versioned_installation("1.2.3")

    def test_platform_aliases_are_exact(self) -> None:
        cases = {
            ("Linux", "x86_64"): "linux-x86_64",
            ("Linux", "aarch64"): "linux-arm64",
            ("Darwin", "AMD64"): "macos-x86_64",
            ("Darwin", "arm64"): "macos-arm64",
            ("Windows", "x86_64"): "windows-x86_64",
            ("Windows", "ARM64"): "windows-arm64",
        }
        for (system, machine), expected in cases.items():
            with self.subTest(system=system, machine=machine):
                self.assertEqual(
                    current_platform_target(system=system, machine=machine), expected
                )
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            current_platform_target(system="Plan9", machine="mips")

    def test_release_requires_published_stable_exact_assets(self) -> None:
        release = parse_stable_release(release_payload(), target="linux-x86_64")
        self.assertEqual(release.version, "1.2.3")
        self.assertIn("linux-x86_64", release.provider_archive.name)
        draft = release_payload() | {"draft": True}
        with self.assertRaisesRegex(ValueError, "stable"):
            parse_stable_release(draft, target="linux-x86_64")
        foreign = release_payload()
        foreign["assets"] = list(foreign["assets"])
        foreign["assets"][0] = dict(foreign["assets"][0]) | {
            "browser_download_url": "https://example.test/foreign.whl"
        }
        with self.assertRaisesRegex(ValueError, "trusted repository"):
            parse_stable_release(foreign, target="linux-x86_64")

    def test_checksum_manifest_rejects_paths_and_duplicates(self) -> None:
        digest = "a" * 64
        self.assertEqual(parse_checksum_manifest(f"{digest} file.whl\n".encode()), {"file.whl": digest})
        with self.assertRaisesRegex(ValueError, "unsafe"):
            parse_checksum_manifest(f"{digest} ../file.whl\n".encode())
        with self.assertRaisesRegex(ValueError, "duplicated"):
            parse_checksum_manifest(
                f"{digest} file.whl\n{digest} file.whl\n".encode()
            )

    def test_download_enforces_size_and_api_digest(self) -> None:
        payload = b"verified"
        metadata = ReleaseAsset(
            "asset.bin",
            "https://github.com/zhiyuzhang001-a11y/codebase-atlas/releases/download/v1.2.3/asset.bin",
            len(payload),
            "sha256:" + hashlib.sha256(payload).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw) / metadata.name
            actual = download_asset(
                metadata, destination, maximum_bytes=100,
                opener=lambda *_args, **_kwargs: Response(payload),
            )
            self.assertEqual(actual, hashlib.sha256(payload).hexdigest())
            self.assertEqual(destination.read_bytes(), payload)

    def test_downloaded_release_requires_all_three_checksum_proofs(self) -> None:
        wheel = b"wheel"
        provider = b"provider"
        wheel_hash = hashlib.sha256(wheel).hexdigest()
        provider_hash = hashlib.sha256(provider).hexdigest()
        wheel_name = "codebase_atlas-1.2.3-py3-none-any.whl"
        provider_name = "codebase-atlas-provider-9.8.7-test-linux-x86_64.tar.gz"
        payload = release_payload()
        payload["assets"] = [
            asset(wheel_name, wheel),
            asset("SHA256SUMS.txt", f"{wheel_hash}  {wheel_name}\n".encode()),
            asset(provider_name, provider),
            asset(provider_name + ".sha256", f"{provider_hash}  {provider_name}\n".encode()),
            asset("PROVIDER_SHA256SUMS.txt", f"{provider_hash}  {provider_name}\n".encode()),
        ]
        release = parse_stable_release(payload, target="linux-x86_64")
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            contents = {
                wheel_name: wheel,
                "SHA256SUMS.txt": f"{wheel_hash}  {wheel_name}\n".encode(),
                provider_name: provider,
                provider_name + ".sha256": f"{provider_hash}  {provider_name}\n".encode(),
                "PROVIDER_SHA256SUMS.txt": f"{provider_hash}  {provider_name}\n".encode(),
            }
            for name, content in contents.items():
                (directory / name).write_bytes(content)
            verified = verify_downloaded_release(release, directory)
            self.assertEqual(verified["wheel_sha256"], wheel_hash)
            (directory / "PROVIDER_SHA256SUMS.txt").write_text(
                f"{'0' * 64}  {provider_name}\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "aggregate"):
                verify_downloaded_release(release, directory)

    def test_release_installation_is_versioned_verified_and_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            wheel_path = workspace / "source.whl"
            with zipfile.ZipFile(wheel_path, "w") as wheel:
                wheel.writestr(
                    "codebase_atlas-1.2.3.dist-info/METADATA",
                    "Metadata-Version: 2.1\nName: codebase-atlas\nVersion: 1.2.3\n",
                )
                wheel.writestr(
                    "codebase_atlas-1.2.3.dist-info/entry_points.txt",
                    "[console_scripts]\natlas = codebase_atlas.simple_cli:main\n",
                )
            binary = b"provider-binary"
            provider_version = "9.8.7-test"
            provider_name = (
                f"codebase-atlas-provider-{provider_version}-linux-x86_64.tar.gz"
            )
            provider_path = workspace / provider_name
            bundle = workspace / "bundle" / "linux-x86_64"
            bundle.mkdir(parents=True)
            (bundle / "codebase-memory-mcp").write_bytes(binary)
            (bundle / "LICENSE").write_text("MIT License\n", encoding="utf-8")
            (bundle / "manifest.json").write_text(json.dumps({
                "build": {
                    "managed_version": provider_version,
                    "platform_arch": "linux-x86_64",
                },
                "artifact": {
                    "sha256": hashlib.sha256(binary).hexdigest(),
                    "size": len(binary),
                },
            }), encoding="utf-8")
            with tarfile.open(provider_path, "w:gz") as archive:
                archive.add(bundle, arcname="linux-x86_64")
            wheel_bytes = wheel_path.read_bytes()
            provider_bytes = provider_path.read_bytes()
            wheel_name = "codebase_atlas-1.2.3-py3-none-any.whl"
            wheel_hash = hashlib.sha256(wheel_bytes).hexdigest()
            provider_hash = hashlib.sha256(provider_bytes).hexdigest()
            contents = {
                wheel_name: wheel_bytes,
                "SHA256SUMS.txt": f"{wheel_hash}  {wheel_name}\n".encode(),
                provider_name: provider_bytes,
                provider_name + ".sha256": (
                    f"{provider_hash}  {provider_name}\n".encode()
                ),
                "PROVIDER_SHA256SUMS.txt": (
                    f"{provider_hash}  {provider_name}\n".encode()
                ),
            }
            payload = {
                "draft": False,
                "prerelease": False,
                "tag_name": "v1.2.3",
                "html_url": "https://github.com/zhiyuzhang001-a11y/codebase-atlas/releases/tag/v1.2.3",
                "assets": [asset(name, content) for name, content in contents.items()],
            }
            release = parse_stable_release(payload, target="linux-x86_64")

            def opener(request, **_kwargs):
                name = unquote(Path(urlparse(request.full_url).path).name)
                return Response(contents[name])

            def installer(_wheel: Path, environment: Path):
                scripts = environment / "bin"
                scripts.mkdir(parents=True)
                python = scripts / "python"
                executable = scripts / "codebase-atlas"
                python.write_bytes(b"python")
                executable.write_bytes(
                    b"#!" + os.fsencode(python) + b"\nprint('atlas')\n"
                )
                return python, executable

            def runner(command, **_kwargs):
                output = (
                    provider_version
                    if command[-1] == "--version" and "provider" in command[0]
                    else json.dumps({"name": "codebase-atlas", "version": "1.2.3"})
                )
                return SimpleNamespace(returncode=0, stdout=output, stderr="")
            root = workspace / "installations"
            installed, mutated = install_stable_release(
                release, root=root, opener=opener,
                wheel_installer=installer, runner=runner,
            )
            reused, reused_mutated = install_stable_release(
                release, root=root, opener=opener,
                wheel_installer=installer, runner=runner,
            )
            self.assertTrue(mutated)
            self.assertFalse(reused_mutated)
            self.assertEqual(installed, reused)
            self.assertEqual(installed.provider_version, provider_version)
            self.assertTrue(installed.provider_binary.is_file())
            if os.name != "nt":
                self.assertEqual(
                    installed.atlas_executable.read_bytes().splitlines()[0],
                    b"#!" + os.fsencode(installed.python),
                )
            self.assertFalse((installed.root / "downloads").exists())


if __name__ == "__main__":
    unittest.main()
