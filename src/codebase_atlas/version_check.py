"""Non-blocking, notify-only release awareness with a contained local cache."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from threading import Lock, Thread
import time
from typing import Any, Callable
from urllib.request import Request, urlopen


RELEASE_API = "https://api.github.com/repos/zhiyuzhang001-a11y/codebase-atlas/releases/latest"
CACHE_SECONDS = 86_400
FAILURE_CACHE_SECONDS = 3_600
CACHE_SCHEMA_VERSION = 1


def _version_tuple(value: str) -> tuple[int, ...] | None:
    raw = value.removeprefix("v").split("+", 1)[0].split("-", 1)[0]
    try:
        parts = tuple(int(part) for part in raw.split("."))
    except ValueError:
        return None
    return parts if len(parts) >= 2 else None


def _cache_path(data_dir: Path) -> Path:
    return data_dir / "version-check.json"


def _read_cache(data_dir: Path, now: float) -> dict[str, Any] | None:
    path = _cache_path(data_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    checked_epoch = value.get("checked_epoch")
    ttl = value.get("ttl_seconds")
    if (
        value.get("schema_version") != CACHE_SCHEMA_VERSION
        or
        not isinstance(checked_epoch, (int, float))
        or not isinstance(ttl, int)
        or now - checked_epoch >= ttl
    ):
        return None
    value["source"] = "cache"
    return value


def _write_cache(data_dir: Path, value: dict[str, Any]) -> None:
    destination = _cache_path(data_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".version-check-", suffix=".json", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fetch_release_status(
    current_version: str,
    data_dir: Path,
    *,
    timeout_seconds: float = 2.0,
    opener: Callable[..., Any] = urlopen,
    now: float | None = None,
) -> dict[str, Any]:
    timestamp = time.time() if now is None else now
    cached = _read_cache(data_dir, timestamp)
    if cached is not None:
        return cached
    try:
        request = Request(
            RELEASE_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"codebase-atlas/{current_version}",
            },
        )
        with opener(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        latest = str(payload["tag_name"]).removeprefix("v")
        latest_tuple = _version_tuple(latest)
        current_tuple = _version_tuple(current_version)
        if latest_tuple is None or current_tuple is None:
            raise ValueError("release version is invalid")
        assets = payload.get("assets", [])
        checksum_available = any(
            isinstance(asset, dict)
            and (
                str(asset.get("name", "")).lower().endswith(".sha256")
                or "sha256sum" in str(asset.get("name", "")).lower()
            )
            for asset in assets
        )
        status = "update_available" if latest_tuple > current_tuple else "current"
        value = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "status": status,
            "ok": True,
            "current_version": current_version,
            "latest_version": latest,
            "release_url": str(payload.get("html_url", "")),
            "checksum_available": checksum_available,
            "install_attempted": False,
            "checked_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
            "checked_epoch": timestamp,
            "ttl_seconds": CACHE_SECONDS,
            "source": "network",
        }
    except Exception as exc:
        value = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "status": "unavailable",
            "ok": False,
            "current_version": current_version,
            "latest_version": "",
            "release_url": "",
            "checksum_available": False,
            "install_attempted": False,
            "reason": str(exc),
            "checked_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
            "checked_epoch": timestamp,
            "ttl_seconds": FAILURE_CACHE_SECONDS,
            "source": "network",
        }
    _write_cache(data_dir, value)
    return value


class VersionNotifier:
    def __init__(
        self,
        current_version: str,
        data_dir: Path,
        *,
        enabled: bool,
        fetcher: Callable[..., dict[str, Any]] = fetch_release_status,
    ) -> None:
        self.current_version = current_version
        self.data_dir = data_dir
        self.enabled = enabled and os.environ.get("CODEBASE_ATLAS_NO_UPDATE_CHECK") != "1"
        self.fetcher = fetcher
        self._lock = Lock()
        self._started = False
        self._status: dict[str, Any] = (
            {"status": "pending", "ok": True, "install_attempted": False}
            if self.enabled
            else {
                "status": "disabled", "ok": True, "install_attempted": False,
                "reason": "software_update_check_not_enabled",
            }
        )

    def start(self) -> None:
        if not self.enabled or self._started:
            return
        self._started = True

        def refresh() -> None:
            result = self.fetcher(self.current_version, self.data_dir)
            with self._lock:
                self._status = result

        Thread(target=refresh, name="codebase-atlas-version-check", daemon=True).start()

    def current(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)
