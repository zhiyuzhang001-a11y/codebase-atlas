import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse
import zipfile

from codebase_atlas.frontend_installation import install_frontend_release
from codebase_atlas.release_installation import StableRelease, ReleaseAsset


class FrontendInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "frontends"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as wheel:
            wheel.writestr("codebase_atlas-1.2.3.dist-info/METADATA", "Version: 1.2.3\n")
            wheel.writestr("codebase_atlas-1.2.3.dist-info/entry_points.txt", "atlas = codebase_atlas.simple_cli:main\n")
        wheel_name = "codebase_atlas-1.2.3-py3-none-any.whl"
        checksum = hashlib.sha256(buffer.getvalue()).hexdigest()
        self.payloads = {wheel_name: buffer.getvalue(), "SHA256SUMS.txt": f"{checksum}  {wheel_name}\n".encode()}
        def asset(name):
            payload = self.payloads.get(name, b"")
            return ReleaseAsset(name, "https://github.com/zhiyuzhang001-a11y/codebase-atlas/releases/download/v1.2.3/" + name,
                                len(payload), "sha256:" + hashlib.sha256(payload).hexdigest())
        self.release = StableRelease("1.2.3", "v1.2.3", "https://github.com/example/release",
                                     asset(wheel_name), asset("SHA256SUMS.txt"), asset("unused-provider"),
                                     asset("unused-adjacent"), asset("unused-aggregate"), "macos-arm64")
        self.requests = []
        self.runner = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({"version": "1.2.3"}), stderr=""))
        patcher = patch("codebase_atlas.frontend_installation.current_platform_target", return_value="macos-arm64")
        patcher.start()
        self.addCleanup(patcher.stop)

    def opener(self, request, **_kwargs):
        name = Path(urlparse(request.full_url).path).name
        self.requests.append(name)
        return io.BytesIO(self.payloads[name])

    def wheel_installer(self, _wheel, environment):
        directory = environment / "bin"
        directory.mkdir(parents=True)
        python, executable = directory / "python", directory / "atlas"
        python.write_bytes(b"python sentinel")
        executable.write_bytes(b"#!/" + str(python).encode() + b"\natlas sentinel")
        return python, executable

    def install(self):
        return install_frontend_release(self.release, root=self.root, opener=self.opener,
                                        wheel_installer=self.wheel_installer, runner=self.runner)

    def test_frontend_only_downloads_wheel_and_reuses_verified_installation(self):
        installed, created = self.install()
        self.assertTrue(created)
        self.assertIsNone(installed.provider_binary)
        self.assertEqual(set(self.requests), set(self.payloads))
        installed_again, created = self.install()
        self.assertFalse(created)
        self.assertEqual(installed_again, installed)
        self.assertEqual(len(self.requests), 2)
        self.runner.assert_called_once()

    def test_tampered_frontend_executable_is_not_reused(self):
        installed, _created = self.install()
        installed.atlas_executable.write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "executable mismatch"):
            self.install()

    def test_invalid_checksum_fails_before_install_or_execution(self):
        self.payloads["SHA256SUMS.txt"] = b"invalid checksum"
        with self.assertRaises((RuntimeError, ValueError)):
            self.install()
        self.runner.assert_not_called()
        self.assertFalse((self.root / "1.2.3/macos-arm64").exists())


if __name__ == "__main__":
    unittest.main()
