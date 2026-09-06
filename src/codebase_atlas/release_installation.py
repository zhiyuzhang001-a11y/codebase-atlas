"""Trusted stable-Release discovery and versioned installation primitives."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile
import venv

from .provider_layout import atlas_data_root
from .version_check import RELEASE_API, _version_tuple


REPOSITORY_RELEASE_PREFIX = (
    "/zhiyuzhang001-a11y/codebase-atlas/releases/download/"
)
MAX_WHEEL_BYTES = 32 * 1024 * 1024
MAX_PROVIDER_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    digest: str


@dataclass(frozen=True)
class StableRelease:
    version: str
    tag: str
    url: str
    wheel: ReleaseAsset
    wheel_checksums: ReleaseAsset
    provider_archive: ReleaseAsset
    provider_checksum: ReleaseAsset
    provider_checksums: ReleaseAsset
    target: str


@dataclass(frozen=True)
class VersionedInstallation:
    version: str
    target: str
    root: Path
    python: Path
    atlas_executable: Path
    provider_binary: Path
    provider_version: str
    wheel_sha256: str
    provider_sha256: str


def current_platform_target(
    *, system: str | None = None, machine: str | None = None
) -> str:
    selected_system = (system or platform.system()).lower()
    selected_machine = (machine or platform.machine()).lower()
    systems = {"linux": "linux", "darwin": "macos", "windows": "windows"}
    machines = {
        "x86_64": "x86_64", "amd64": "x86_64",
        "arm64": "arm64", "aarch64": "arm64",
    }
    if selected_system not in systems or selected_machine not in machines:
        raise RuntimeError(
            f"unsupported release platform: {selected_system}-{selected_machine}"
        )
    return f"{systems[selected_system]}-{machines[selected_machine]}"


def _asset(value: Any) -> ReleaseAsset:
    if not isinstance(value, dict):
        raise ValueError("release asset must be an object")
    name = value.get("name")
    url = value.get("browser_download_url")
    size = value.get("size")
    digest = value.get("digest", "")
    if (
        not isinstance(name, str) or not name
        or not isinstance(url, str) or not url
        or not isinstance(size, int) or isinstance(size, bool) or size < 0
        or not isinstance(digest, str)
    ):
        raise ValueError("release asset metadata is invalid")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"github.com", "www.github.com"}
        or not parsed.path.startswith(REPOSITORY_RELEASE_PREFIX)
    ):
        raise ValueError(f"release asset URL is outside the trusted repository: {name}")
    if digest and not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(f"release asset digest is invalid: {name}")
    return ReleaseAsset(name, url, size, digest)


def parse_stable_release(
    payload: Any, *, target: str | None = None
) -> StableRelease:
    if not isinstance(payload, dict):
        raise ValueError("GitHub release response must be an object")
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise ValueError("latest GitHub release is not a published stable Release")
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("stable Release tag must be v<major>.<minor>.<patch>")
    version = tag[1:]
    if _version_tuple(version) is None:
        raise ValueError("stable Release version is invalid")
    html_url = payload.get("html_url")
    if not isinstance(html_url, str) or not html_url.startswith(
        "https://github.com/zhiyuzhang001-a11y/codebase-atlas/releases/tag/"
    ):
        raise ValueError("stable Release page is outside the trusted repository")
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list):
        raise ValueError("stable Release assets are missing")
    assets = [_asset(value) for value in raw_assets]
    by_name: dict[str, ReleaseAsset] = {}
    for asset in assets:
        if asset.name in by_name:
            raise ValueError(f"duplicate stable Release asset: {asset.name}")
        by_name[asset.name] = asset
    selected_target = target or current_platform_target()
    wheel_name = f"codebase_atlas-{version}-py3-none-any.whl"
    try:
        wheel = by_name[wheel_name]
        wheel_checksums = by_name["SHA256SUMS.txt"]
        provider_checksums = by_name["PROVIDER_SHA256SUMS.txt"]
    except KeyError as exc:
        raise ValueError(f"stable Release is missing required asset: {exc.args[0]}") from exc
    archive_pattern = re.compile(
        rf"codebase-atlas-provider-(.+)-{re.escape(selected_target)}"
        + (r"\.zip" if selected_target.startswith("windows-") else r"\.tar\.gz")
    )
    matches = [asset for asset in assets if archive_pattern.fullmatch(asset.name)]
    if len(matches) != 1:
        raise ValueError(
            f"stable Release must contain one Provider archive for {selected_target}"
        )
    provider_archive = matches[0]
    sidecar_name = provider_archive.name + ".sha256"
    if sidecar_name not in by_name:
        raise ValueError(f"stable Release is missing required asset: {sidecar_name}")
    return StableRelease(
        version, tag, html_url, wheel, wheel_checksums,
        provider_archive, by_name[sidecar_name], provider_checksums,
        selected_target,
    )


def fetch_stable_release(
    *,
    target: str | None = None,
    timeout_seconds: float = 10.0,
    opener: Callable[..., Any] = urlopen,
) -> StableRelease:
    request = Request(
        RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "codebase-atlas-release-installer",
        },
    )
    with opener(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read(MAX_MANIFEST_BYTES + 1).decode("utf-8"))
    return parse_stable_release(payload, target=target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_asset(
    asset: ReleaseAsset,
    destination: Path,
    *,
    maximum_bytes: int,
    timeout_seconds: float = 60.0,
    opener: Callable[..., Any] = urlopen,
) -> str:
    if asset.size > maximum_bytes:
        raise RuntimeError(f"release asset exceeds size limit: {asset.name}")
    request = Request(
        asset.url,
        headers={"Accept": "application/octet-stream", "User-Agent": "codebase-atlas-installer"},
    )
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    size = 0
    digest = hashlib.sha256()
    try:
        with opener(request, timeout=timeout_seconds) as response, os.fdopen(
            descriptor, "wb"
        ) as stream:
            descriptor = -1
            while True:
                chunk = response.read(min(1024 * 1024, maximum_bytes - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum_bytes:
                    raise RuntimeError(f"release asset exceeds size limit: {asset.name}")
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if size != asset.size:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"release asset size mismatch: {asset.name}")
    actual = digest.hexdigest()
    if asset.digest and asset.digest != f"sha256:{actual}":
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"release asset API digest mismatch: {asset.name}")
    return actual


def parse_checksum_manifest(payload: bytes) -> dict[str, str]:
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("checksum manifest exceeds size limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("checksum manifest is not UTF-8") from exc
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})\s+\*?([^\s]+)", raw_line.strip())
        if match is None:
            raise ValueError("checksum manifest line is invalid")
        digest, name = match.groups()
        if Path(name).name != name or name in result:
            raise ValueError("checksum manifest filename is unsafe or duplicated")
        result[name] = digest
    if not result:
        raise ValueError("checksum manifest is empty")
    return result


def verify_downloaded_release(release: StableRelease, directory: Path) -> dict[str, str]:
    paths = {
        asset.name: directory / asset.name
        for asset in (
            release.wheel, release.wheel_checksums, release.provider_archive,
            release.provider_checksum, release.provider_checksums,
        )
    }
    if not all(path.is_file() and not path.is_symlink() for path in paths.values()):
        raise RuntimeError("downloaded stable Release asset set is incomplete")
    wheel_manifest = parse_checksum_manifest(paths[release.wheel_checksums.name].read_bytes())
    provider_manifest = parse_checksum_manifest(
        paths[release.provider_checksums.name].read_bytes()
    )
    adjacent = parse_checksum_manifest(paths[release.provider_checksum.name].read_bytes())
    wheel_hash = _sha256(paths[release.wheel.name])
    provider_hash = _sha256(paths[release.provider_archive.name])
    if wheel_manifest.get(release.wheel.name) != wheel_hash:
        raise RuntimeError("Atlas wheel checksum mismatch")
    if provider_manifest.get(release.provider_archive.name) != provider_hash:
        raise RuntimeError("Provider aggregate checksum mismatch")
    if adjacent != {release.provider_archive.name: provider_hash}:
        raise RuntimeError("Provider adjacent checksum mismatch")
    return {"wheel_sha256": wheel_hash, "provider_sha256": provider_hash}


def installation_root() -> Path:
    return atlas_data_root() / "_installations" / "v1"


def load_versioned_installation(version: str) -> VersionedInstallation:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError("Atlas installation version is invalid")
    installation = _installation_from_receipt(installation_root() / version)
    if installation.version != version:
        raise RuntimeError("Atlas installation receipt version does not match its directory")
    return installation


def _safe_relative(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise RuntimeError("release archive contains an unsafe path")
    return path


def _write_archive_file(destination: Path, source: Any, size: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    written = 0
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            while True:
                chunk = source.read(min(1024 * 1024, size - written + 1))
                if not chunk:
                    break
                written += len(chunk)
                if written > size:
                    raise RuntimeError("release archive member exceeds declared size")
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if written != size:
        destination.unlink(missing_ok=True)
        raise RuntimeError("release archive member size mismatch")


def safely_extract_provider(archive: Path, destination: Path) -> None:
    """Extract only regular files/directories without trusting archive paths."""
    destination.mkdir(parents=True, exist_ok=False)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                relative = _safe_relative(member.filename)
                target = destination / relative
                unix_type = (member.external_attr >> 16) & 0o170000
                if unix_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise RuntimeError("Provider ZIP contains a non-regular member")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                with bundle.open(member, "r") as source:
                    _write_archive_file(target, source, member.file_size)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                relative = _safe_relative(member.name)
                target = destination / relative
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise RuntimeError("Provider archive contains a non-regular member")
                source = bundle.extractfile(member)
                if source is None:
                    raise RuntimeError("Provider archive member cannot be read")
                with source:
                    _write_archive_file(target, source, member.size)


def _verify_wheel(path: Path, version: str) -> None:
    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        entry_names = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(metadata_names) != 1 or len(entry_names) != 1:
            raise RuntimeError("Atlas wheel metadata is incomplete")
        metadata = wheel.read(metadata_names[0]).decode("utf-8")
        entries = wheel.read(entry_names[0]).decode("utf-8")
        if f"Version: {version}\n" not in metadata:
            raise RuntimeError("Atlas wheel version does not match Release")
        if "atlas = codebase_atlas.simple_cli:main" not in entries:
            raise RuntimeError("Atlas wheel does not contain the lifecycle entry point")


def _provider_bundle(
    extracted: Path, release: StableRelease
) -> tuple[Path, str]:
    root = extracted / release.target
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("Provider archive root does not match current platform")
    binary_name = "codebase-memory-mcp.exe" if release.target.startswith("windows-") else "codebase-memory-mcp"
    expected = {binary_name, "LICENSE", "manifest.json"}
    if {path.name for path in root.iterdir()} != expected:
        raise RuntimeError("Provider archive inventory is invalid")
    binary = root / binary_name
    license_path = root / "LICENSE"
    manifest_path = root / "manifest.json"
    if any(path.is_symlink() or not path.is_file() for path in (binary, license_path, manifest_path)):
        raise RuntimeError("Provider archive contains unsafe bundle files")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Provider manifest is invalid") from exc
    build = manifest.get("build") if isinstance(manifest, dict) else None
    artifact = manifest.get("artifact") if isinstance(manifest, dict) else None
    if not isinstance(build, dict) or not isinstance(artifact, dict):
        raise RuntimeError("Provider manifest schema is invalid")
    version = build.get("managed_version")
    if (
        build.get("platform_arch") != release.target
        or not isinstance(version, str) or not version
        or artifact.get("sha256") != _sha256(binary)
        or artifact.get("size") != binary.stat().st_size
    ):
        raise RuntimeError("Provider manifest identity or digest is invalid")
    if "MIT License" not in license_path.read_text(encoding="utf-8"):
        raise RuntimeError("Provider bundle does not retain its MIT license")
    if os.name != "nt":
        binary.chmod(0o700)
    return binary, version


def _environment_paths(environment: Path) -> tuple[Path, Path]:
    if os.name == "nt":
        scripts = environment / "Scripts"
        return scripts / "python.exe", scripts / "codebase-atlas.exe"
    scripts = environment / "bin"
    return scripts / "python", scripts / "codebase-atlas"


def _install_wheel(
    wheel: Path, environment: Path, *, runner: Callable[..., Any] = subprocess.run
) -> tuple[Path, Path]:
    venv.EnvBuilder(with_pip=True, clear=False, symlinks=os.name != "nt").create(environment)
    python, executable = _environment_paths(environment)
    completed = runner(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        check=False, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Atlas wheel installation failed")
    return python, executable


def _relocate_environment_scripts(
    environment: Path, staged_python: Path, published_python: Path
) -> None:
    """Rewrite POSIX venv launchers before the staged environment is renamed."""
    if os.name == "nt":
        return
    scripts = environment / "bin"
    expected = b"#!" + os.fsencode(staged_python) + b"\n"
    replacement = b"#!" + os.fsencode(published_python) + b"\n"
    for script in scripts.iterdir():
        try:
            metadata = os.lstat(script)
        except OSError:
            continue
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            continue
        payload = script.read_bytes()
        if not payload.startswith(expected):
            continue
        script.write_bytes(replacement + payload[len(expected):])
        script.chmod(stat.S_IMODE(metadata.st_mode))


def _installation_from_receipt(root: Path) -> VersionedInstallation:
    receipt_path = root / "installation.json"
    try:
        if receipt_path.is_symlink() or not receipt_path.is_file():
            raise ValueError("receipt is not a safe regular file")
        value = json.loads(receipt_path.read_text(encoding="utf-8"))
        if value.get("schema_version") != 1:
            raise ValueError("receipt schema is invalid")
        fields = {
            name: value[name]
            for name in (
                "version", "target", "python", "atlas_executable", "provider_binary",
                "provider_version", "wheel_sha256", "provider_sha256",
            )
        }
        if not all(isinstance(item, str) and item for item in fields.values()):
            raise ValueError("receipt fields are invalid")
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("versioned Atlas installation receipt is invalid") from exc
    resolved_root = root.resolve()
    resolved_paths = {}
    for name in ("python", "atlas_executable", "provider_binary"):
        relative = _safe_relative(fields[name])
        literal = (resolved_root / relative).absolute()
        if not literal.is_relative_to(resolved_root) or not literal.is_file():
            raise RuntimeError("versioned Atlas installation paths are invalid")
        resolved_paths[name] = literal
    if any(
        path.is_symlink()
        for name, path in resolved_paths.items()
        if name != "python"
    ):
        raise RuntimeError("versioned Atlas installation paths are invalid")
    return VersionedInstallation(
        fields["version"], fields["target"], resolved_root,
        resolved_paths["python"], resolved_paths["atlas_executable"],
        resolved_paths["provider_binary"], fields["provider_version"],
        fields["wheel_sha256"], fields["provider_sha256"],
    )


def install_stable_release(
    release: StableRelease,
    *,
    root: Path | None = None,
    opener: Callable[..., Any] = urlopen,
    wheel_installer: Callable[[Path, Path], tuple[Path, Path]] | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[VersionedInstallation, bool]:
    """Download, verify and atomically publish one side-by-side installation."""
    parent = (root or installation_root()).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise RuntimeError("Atlas installation root must be a real directory")
    if os.name != "nt":
        parent.chmod(0o700)
    destination = parent / release.version
    if destination.exists():
        installed = _installation_from_receipt(destination)
        if installed.version != release.version or installed.target != release.target:
            raise RuntimeError("existing Atlas installation identity does not match release")
        if installed.version != release.version or installed.target != release.target:
            raise RuntimeError("existing versioned Atlas installation conflicts")
        return installed, False
    stage = Path(tempfile.mkdtemp(prefix=f".{release.version}-", dir=parent))
    try:
        downloads = stage / "downloads"
        downloads.mkdir()
        for asset, limit in (
            (release.wheel, MAX_WHEEL_BYTES),
            (release.wheel_checksums, MAX_MANIFEST_BYTES),
            (release.provider_archive, MAX_PROVIDER_BYTES),
            (release.provider_checksum, MAX_MANIFEST_BYTES),
            (release.provider_checksums, MAX_MANIFEST_BYTES),
        ):
            download_asset(
                asset, downloads / asset.name, maximum_bytes=limit, opener=opener
            )
        hashes = verify_downloaded_release(release, downloads)
        wheel = downloads / release.wheel.name
        _verify_wheel(wheel, release.version)
        extracted = stage / "provider"
        safely_extract_provider(downloads / release.provider_archive.name, extracted)
        provider_binary, provider_version = _provider_bundle(extracted, release)
        provider_checked = runner(
            [str(provider_binary), "--version"],
            check=False, capture_output=True, text=True,
        )
        provider_output = f"{provider_checked.stdout}\n{provider_checked.stderr}"
        if provider_checked.returncode != 0 or provider_version not in provider_output:
            raise RuntimeError("installed Provider version check failed")
        environment = stage / "environment"
        installer = wheel_installer or (
            lambda selected_wheel, selected_environment: _install_wheel(
                selected_wheel, selected_environment, runner=runner
            )
        )
        python, executable = installer(wheel, environment)
        for name, path in (("python", python), ("atlas", executable)):
            literal = path.absolute()
            if (
                not literal.is_file()
                or not literal.is_relative_to(stage)
                or (name != "python" and literal.is_symlink())
            ):
                raise RuntimeError("Atlas wheel installer returned an unsafe executable")
        _relocate_environment_scripts(
            environment,
            python,
            destination / python.relative_to(stage),
        )
        checked = runner(
            [str(python), "-m", "codebase_atlas.cli", "--version"],
            check=False, capture_output=True, text=True,
        )
        if checked.returncode != 0:
            raise RuntimeError(checked.stderr.strip() or "installed Atlas did not start")
        try:
            reported = json.loads(checked.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("installed Atlas version output is invalid") from exc
        if reported.get("version") != release.version:
            raise RuntimeError("installed Atlas version does not match Release")
        receipt = {
            "schema_version": 1,
            "version": release.version,
            "target": release.target,
            "python": str(python.relative_to(stage)),
            "atlas_executable": str(executable.relative_to(stage)),
            "provider_binary": str(provider_binary.relative_to(stage)),
            "provider_version": provider_version,
            **hashes,
        }
        receipt_path = stage / "installation.json"
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            receipt_path.chmod(0o600)
        shutil.rmtree(downloads)
        try:
            os.replace(stage, destination)
        except OSError:
            if destination.exists():
                installed = _installation_from_receipt(destination)
                if installed.version == release.version and installed.target == release.target:
                    return installed, False
            raise
        return _installation_from_receipt(destination), True
    finally:
        if stage.exists():
            shutil.rmtree(stage)
