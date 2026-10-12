"""Stable, versioned Atlas frontend installation without legacy Providers.

Pure Rust still needs separate verified toolchain/scanner preparation. This
module does not download a Provider, discover Node/Serena, or alter projects.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.request import urlopen

from .provider_layout import atlas_data_root
from .release_installation import (
    StableRelease, VersionedInstallation, MAX_WHEEL_BYTES, MAX_MANIFEST_BYTES,
    _safe_relative, _sha256, _verify_wheel, _install_wheel,
    _relocate_environment_scripts, current_platform_target, download_asset,
    parse_checksum_manifest,
)
from .rust_installation import _private_directory


def frontend_root() -> Path:
    return atlas_data_root() / "_frontends" / "v1"


def _load(root: Path, version: str, target: str) -> VersionedInstallation:
    for directory in (root.parent.parent, root.parent, root):
        _private_directory(directory)
    receipt = root / "installation.json"
    if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > MAX_MANIFEST_BYTES:
        raise RuntimeError("Frontend receipt is unsafe")
    value = json.loads(receipt.read_text())
    if (value.get("schema_version") != 1 or value.get("kind") != "atlas-frontend-only"
            or value.get("version") != version or value.get("target") != target
            or not re.fullmatch(r"[0-9a-f]{64}", value.get("wheel_sha256", ""))):
        raise RuntimeError("Frontend receipt identity mismatch")
    python = root / _safe_relative(value["python"])
    executable = root / _safe_relative(value["atlas_executable"])
    if (not python.is_file() or not executable.is_file() or executable.is_symlink()
            or not executable.resolve().is_relative_to(root)
            or _sha256(executable) != value.get("executable_sha256")):
        raise RuntimeError("Frontend installed executable mismatch")
    if os.name == "nt":
        from .windows_private_store import verify_windows_private_path
        verify_windows_private_path(receipt)
        verify_windows_private_path(executable)
    return VersionedInstallation(version, target, root, python, executable, None, "",
                                 value["wheel_sha256"], "")


def load_frontend_installation(version: str) -> VersionedInstallation:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise RuntimeError("Frontend version is invalid")
    target = current_platform_target()
    return _load(frontend_root() / version / target, version, target)


def install_frontend_release(release: StableRelease, *, root: Path | None = None,
                             opener=urlopen, wheel_installer=None,
                             runner=subprocess.run) -> tuple[VersionedInstallation, bool]:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", release.version):
        raise RuntimeError("Frontend version is invalid")
    if release.target != current_platform_target():
        raise RuntimeError("Frontend release platform mismatch")
    parent = (root or frontend_root()).absolute()
    for directory in (parent, parent / release.version):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        _private_directory(directory)
    destination = parent / release.version / release.target
    if destination.exists():
        installed = _load(destination, release.version, release.target)
        if release.wheel.digest and release.wheel.digest != "sha256:" + installed.wheel_sha256:
            raise RuntimeError("Frontend release differs from existing installation")
        return installed, False
    staging = Path(tempfile.mkdtemp(prefix=".frontend-", dir=destination.parent))
    try:
        probe_environment = dict(os.environ)
        for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
            probe_environment.pop(name, None)
        def isolated_runner(command, **kwargs):
            return runner(command, **(kwargs | {"env": probe_environment}))
        wheel = staging / release.wheel.name
        checksum = staging / release.wheel_checksums.name
        download_asset(release.wheel, wheel, maximum_bytes=MAX_WHEEL_BYTES, opener=opener)
        download_asset(release.wheel_checksums, checksum, maximum_bytes=MAX_MANIFEST_BYTES, opener=opener)
        digest = _sha256(wheel)
        if parse_checksum_manifest(checksum.read_bytes()).get(wheel.name) != digest:
            raise RuntimeError("Frontend wheel checksum mismatch")
        _verify_wheel(wheel, release.version)
        installer = wheel_installer or (lambda selected, environment: _install_wheel(selected, environment, runner=isolated_runner))
        python, executable = installer(wheel, staging / "environment")
        for name, path in (("python", python), ("atlas", executable)):
            if not path.is_file() or not path.absolute().is_relative_to(staging) or (name == "atlas" and path.is_symlink()):
                raise RuntimeError("Frontend installer returned an unsafe path")
        _relocate_environment_scripts(staging / "environment", python, destination / python.relative_to(staging))
        checked = isolated_runner([str(python), "-m", "codebase_atlas.cli", "--version"],
                         check=False, capture_output=True, text=True, timeout=30)
        if checked.returncode or json.loads(checked.stdout).get("version") != release.version:
            raise RuntimeError("Frontend installed version mismatch")
        receipt = {"schema_version": 1, "kind": "atlas-frontend-only", "version": release.version,
                   "target": release.target, "python": str(python.relative_to(staging)),
                   "atlas_executable": str(executable.relative_to(staging)),
                   "executable_sha256": _sha256(executable), "wheel_sha256": digest}
        (staging / "installation.json").write_text(json.dumps(receipt), encoding="utf-8")
        (staging / "installation.json").chmod(0o600)
        # No replacement: an installation race must be reviewed, not overwrite.
        if destination.exists():
            raise RuntimeError("Frontend installation appeared during publication")
        from .installation_publication import publish_installation
        installed = publish_installation(staging, destination, receipt="installation.json",
                                        validate=lambda: _load(destination, release.version, release.target))
        return installed, True
    finally:
        if staging.exists():
            shutil.rmtree(staging)
