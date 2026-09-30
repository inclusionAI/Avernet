"""Mounted and downloaded adapters for exact Skill Center content."""

from __future__ import annotations

import fcntl
import hashlib
import http.client
import io
import ipaddress
import json
import os
import shutil
import socket
import ssl
import stat
import tempfile
import threading
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from engine.community.kernel.center_content import (
    CenterContentPackage,
    CenterContentPendingPackage,
    CenterContentPreparation,
    CenterContentPreparationStatus,
    CenterContentReadyPackage,
    CenterContentUnavailablePackage,
)
from engine.community.plugins.skills_pool.center_mount import (
    CenterMountStatus,
    inspect_center_mount,
    inspect_center_version,
)


_MAX_PACKAGE_BYTES = 100 * 1024 * 1024
_MAX_EXPANDED_BYTES = 512 * 1024 * 1024
_MAX_ENTRIES = 10_000
_DOWNLOAD_TIMEOUT_SECONDS = 60.0
_READY_MARKER = ".teamclaw-center-content.json"


class MountedCenterContentAdapter:
    mode = "MOUNT"

    def __init__(self, *, is_mounted: Callable[[Path], bool] = os.path.ismount) -> None:
        self._is_mounted = is_mounted

    def prepare(
        self,
        *,
        center_root: Path,
        package: CenterContentPackage,
    ) -> CenterContentPreparation:
        mount = inspect_center_mount(center_root, is_mounted=self._is_mounted)
        if mount.status is not CenterMountStatus.READY:
            return CenterContentPreparation(
                CenterContentPreparationStatus.PENDING,
                "CENTER_MOUNT_NOT_READY"
                if mount.status is CenterMountStatus.NOT_READY
                else "CENTER_MOUNT_UNAVAILABLE",
                True,
            )
        inspection = inspect_center_version(
            center_root,
            skill_uuid=package.skill_uuid,
            version=package.sc_version_number,
        )
        if not inspection.ready:
            return CenterContentPreparation(
                CenterContentPreparationStatus.PENDING,
                inspection.code or "CENTER_VERSION_NOT_READY",
                True,
            )
        return CenterContentPreparation(CenterContentPreparationStatus.READY)


class DownloadedCenterContentAdapter:
    mode = "DOWNLOAD"

    def __init__(
        self,
        *,
        fetch: Callable[..., bytes] | None = None,
        allowed_hosts: tuple[str, ...] = (),
        resolve_host: Callable[[str], tuple[str, ...]] | None = None,
        max_package_bytes: int = _MAX_PACKAGE_BYTES,
        max_expanded_bytes: int = _MAX_EXPANDED_BYTES,
        max_entries: int = _MAX_ENTRIES,
        timeout_seconds: float = _DOWNLOAD_TIMEOUT_SECONDS,
        max_concurrent_downloads: int = 2,
    ) -> None:
        if max_concurrent_downloads <= 0:
            raise ValueError("max_concurrent_downloads must be positive")
        self._fetch = fetch or _fetch_https
        self._allowed_hosts = frozenset(
            host.lower().rstrip(".") for host in allowed_hosts
        )
        self._resolve_host = resolve_host or _resolve_host
        self._max_package_bytes = max_package_bytes
        self._max_expanded_bytes = max_expanded_bytes
        self._max_entries = max_entries
        self._timeout_seconds = timeout_seconds
        self._download_slots = threading.BoundedSemaphore(max_concurrent_downloads)
        self._download_state_lock = threading.Lock()
        self._inflight: set[tuple[str, str, str, str, int]] = set()
        self._outcomes: dict[
            tuple[str, str, str, str, int], CenterContentPreparation
        ] = {}

    def prepare(
        self,
        *,
        center_root: Path,
        package: CenterContentPackage,
    ) -> CenterContentPreparation:
        if isinstance(package, CenterContentPendingPackage):
            return CenterContentPreparation(
                CenterContentPreparationStatus.PENDING,
                "CENTER_CONTENT_PACKAGE_PENDING",
                True,
            )
        if isinstance(package, CenterContentUnavailablePackage):
            return CenterContentPreparation(
                CenterContentPreparationStatus.PENDING
                if package.retryable
                else CenterContentPreparationStatus.UNAVAILABLE,
                package.code,
                package.retryable,
            )
        if not isinstance(package, CenterContentReadyPackage):
            return CenterContentPreparation(
                CenterContentPreparationStatus.UNAVAILABLE,
                "CENTER_CONTENT_DESCRIPTOR_INVALID",
                False,
            )
        target = center_root / package.skill_uuid / package.sc_version_number
        if self._cache_ready(target, package):
            return CenterContentPreparation(CenterContentPreparationStatus.READY)
        key = (
            str(center_root.absolute()),
            package.skill_uuid,
            package.sc_version_number,
            package.package_sha256,
            package.package_size,
        )
        with self._download_state_lock:
            outcome = self._outcomes.get(key)
            if outcome is not None:
                if outcome.status is CenterContentPreparationStatus.PENDING:
                    self._outcomes.pop(key, None)
                return outcome
            if key in self._inflight:
                return CenterContentPreparation(
                    CenterContentPreparationStatus.PENDING,
                    "CENTER_CONTENT_DOWNLOAD_PENDING",
                    True,
                )
        if not self._static_descriptor_valid(package):
            return self._invalid_descriptor()
        if target.exists() or target.is_symlink():
            return CenterContentPreparation(
                CenterContentPreparationStatus.UNAVAILABLE,
                "CENTER_CONTENT_CACHE_CONFLICT",
                False,
            )
        if not self._download_slots.acquire(blocking=False):
            return CenterContentPreparation(
                CenterContentPreparationStatus.PENDING,
                "CENTER_CONTENT_DOWNLOAD_CAPACITY",
                True,
            )
        with self._download_state_lock:
            if key in self._inflight:
                self._download_slots.release()
                return CenterContentPreparation(
                    CenterContentPreparationStatus.PENDING,
                    "CENTER_CONTENT_DOWNLOAD_PENDING",
                    True,
                )
            self._inflight.add(key)
        try:
            threading.Thread(
                target=self._prepare_in_background,
                args=(key, center_root, target, package),
                name=f"center-content-{package.skill_uuid}-{package.sc_version_number}",
                daemon=True,
            ).start()
        except RuntimeError:
            with self._download_state_lock:
                self._inflight.discard(key)
            self._download_slots.release()
            return CenterContentPreparation(
                CenterContentPreparationStatus.PENDING,
                "CENTER_CONTENT_DOWNLOAD_START_FAILED",
                True,
            )
        return CenterContentPreparation(
            CenterContentPreparationStatus.PENDING,
            "CENTER_CONTENT_DOWNLOAD_PENDING",
            True,
        )

    def _prepare_in_background(
        self,
        key: tuple[str, str, str, str, int],
        center_root: Path,
        target: Path,
        package: CenterContentReadyPackage,
    ) -> None:
        try:
            resolved_addresses, resolution_failure = self._resolve_download_addresses(
                package
            )
            if resolution_failure is not None:
                outcome = resolution_failure
            else:
                assert resolved_addresses is not None
                outcome = self._download_with_lock(
                    center_root, target, package, resolved_addresses
                )
        except Exception:
            outcome = CenterContentPreparation(
                CenterContentPreparationStatus.PENDING,
                "CENTER_CONTENT_DOWNLOAD_FAILED",
                True,
            )
        finally:
            with self._download_state_lock:
                self._inflight.discard(key)
                if outcome.status is not CenterContentPreparationStatus.READY:
                    self._outcomes[key] = outcome
            self._download_slots.release()

    def _download_with_lock(
        self,
        center_root: Path,
        target: Path,
        package: CenterContentReadyPackage,
        resolved_addresses: tuple[str, ...],
    ) -> CenterContentPreparation:
        target.parent.mkdir(parents=True, exist_ok=True)
        control = center_root.parent / ".center-content-control"
        control.mkdir(parents=True, exist_ok=True)
        lock_path = control / f"{package.skill_uuid}-{package.sc_version_number}.lock"
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                if self._cache_ready(target, package):
                    return CenterContentPreparation(
                        CenterContentPreparationStatus.READY
                    )
                if target.exists() or target.is_symlink():
                    return CenterContentPreparation(
                        CenterContentPreparationStatus.UNAVAILABLE,
                        "CENTER_CONTENT_CACHE_CONFLICT",
                        False,
                    )
                return self._download_and_publish(
                    center_root, target, package, resolved_addresses
                )
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _download_and_publish(
        self,
        center_root: Path,
        target: Path,
        package: CenterContentReadyPackage,
        resolved_addresses: tuple[str, ...],
    ) -> CenterContentPreparation:
        temporary_root = center_root.parent / ".center-content-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="download-", dir=temporary_root))
        try:
            try:
                archive_bytes = self._fetch(
                    package.signed_url,
                    expected_size=package.package_size,
                    max_size=self._max_package_bytes,
                    timeout=self._timeout_seconds,
                    resolved_addresses=resolved_addresses,
                )
            except Exception:
                return CenterContentPreparation(
                    CenterContentPreparationStatus.PENDING,
                    "CENTER_CONTENT_DOWNLOAD_FAILED",
                    True,
                )
            if (
                len(archive_bytes) != package.package_size
                or hashlib.sha256(archive_bytes).hexdigest() != package.package_sha256
            ):
                return CenterContentPreparation(
                    CenterContentPreparationStatus.UNAVAILABLE,
                    "CENTER_CONTENT_INTEGRITY_MISMATCH",
                    False,
                )
            staging = work / "content"
            staging.mkdir()
            try:
                self._extract(archive_bytes, staging)
            except (RuntimeError, ValueError, zipfile.BadZipFile):
                return CenterContentPreparation(
                    CenterContentPreparationStatus.UNAVAILABLE,
                    "CENTER_CONTENT_ARCHIVE_INVALID",
                    False,
                )
            skill_md = staging / "SKILL.md"
            if (
                not skill_md.is_file()
                or skill_md.is_symlink()
                or skill_md.stat().st_size == 0
            ):
                return CenterContentPreparation(
                    CenterContentPreparationStatus.UNAVAILABLE,
                    "CENTER_CONTENT_ENTRY_INVALID",
                    False,
                )
            (staging / _READY_MARKER).write_text(
                json.dumps(
                    {
                        "contract_version": 1,
                        "skill_uuid": package.skill_uuid,
                        "sc_version_number": package.sc_version_number,
                        "package_sha256": package.package_sha256,
                        "package_size": package.package_size,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
            return CenterContentPreparation(CenterContentPreparationStatus.READY)
        except OSError:
            return CenterContentPreparation(
                CenterContentPreparationStatus.UNAVAILABLE,
                "CENTER_CONTENT_CACHE_WRITE_FAILED",
                False,
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _extract(self, archive_bytes: bytes, staging: Path) -> None:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            entries = archive.infolist()
            if len(entries) > self._max_entries:
                raise ValueError("too many archive entries")
            expanded = 0
            seen: set[str] = set()
            for entry in entries:
                path = _safe_archive_path(entry.filename)
                if path in seen:
                    raise ValueError("duplicate archive entry")
                seen.add(path)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError("archive symlink is forbidden")
                if entry.is_dir():
                    (staging / path).mkdir(parents=True, exist_ok=True)
                    continue
                expanded += entry.file_size
                if expanded > self._max_expanded_bytes:
                    raise ValueError("archive expands beyond limit")
                destination = staging / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)

    def _static_descriptor_valid(
        self, package: CenterContentReadyPackage
    ) -> bool:
        digest = package.package_sha256
        url = package.signed_url
        parsed = urlsplit(url or "")
        try:
            port = parsed.port
        except ValueError:
            return False
        hostname = (parsed.hostname or "").lower().rstrip(".")
        return (
            isinstance(digest, str)
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
            and isinstance(package.package_size, int)
            and not isinstance(package.package_size, bool)
            and 0 < package.package_size <= self._max_package_bytes
            and isinstance(url, str)
            and isinstance(package.expires_at, str)
            and bool(package.expires_at)
            and parsed.scheme == "https"
            and hostname in self._allowed_hosts
            and parsed.username is None
            and parsed.password is None
            and port in {None, 443}
            and not parsed.fragment
        )

    def _resolve_download_addresses(
        self, package: CenterContentReadyPackage
    ) -> tuple[tuple[str, ...] | None, CenterContentPreparation | None]:
        hostname = (urlsplit(package.signed_url).hostname or "").lower().rstrip(".")
        try:
            resolved_addresses = self._resolve_host(hostname)
        except OSError:
            return None, CenterContentPreparation(
                CenterContentPreparationStatus.PENDING,
                "CENTER_CONTENT_DNS_UNAVAILABLE",
                True,
            )
        except ValueError:
            return None, self._invalid_descriptor()
        try:
            trusted = bool(resolved_addresses) and all(
                ipaddress.ip_address(value).is_global for value in resolved_addresses
            )
        except ValueError:
            trusted = False
        if not trusted:
            return None, self._invalid_descriptor()
        return tuple(resolved_addresses), None

    @staticmethod
    def _invalid_descriptor() -> CenterContentPreparation:
        return CenterContentPreparation(
            CenterContentPreparationStatus.UNAVAILABLE,
            "CENTER_CONTENT_DESCRIPTOR_INVALID",
            False,
        )

    @staticmethod
    def _cache_ready(target: Path, package: CenterContentReadyPackage) -> bool:
        if not target.is_dir() or target.is_symlink():
            return False
        skill_md = target / "SKILL.md"
        marker = target / _READY_MARKER
        if not skill_md.is_file() or skill_md.is_symlink() or not marker.is_file():
            return False
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
            return skill_md.stat().st_size > 0 and value == {
                "contract_version": 1,
                "skill_uuid": package.skill_uuid,
                "sc_version_number": package.sc_version_number,
                "package_sha256": package.package_sha256,
                "package_size": package.package_size,
            }
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False


def _safe_archive_path(raw: str) -> str:
    if not raw or raw.startswith("/") or "\\" in raw or "\x00" in raw:
        raise ValueError("unsafe archive path")
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("unsafe archive path")
    normalized = path.as_posix().rstrip("/")
    if not normalized:
        raise ValueError("unsafe archive path")
    return normalized


def parse_center_content(
    payload: object,
) -> tuple[int, Mapping[tuple[str, str], CenterContentPackage]]:
    if not isinstance(payload, dict) or frozenset(payload) != {
        "contract_version",
        "packages",
    }:
        raise ValueError("center_content must contain contract_version and packages")
    if payload["contract_version"] != 1 or not isinstance(payload["packages"], list):
        raise ValueError("unsupported center_content contract")
    packages: dict[tuple[str, str], CenterContentPackage] = {}
    for raw in payload["packages"]:
        package = _parse_package(raw)
        key = (package.skill_uuid, package.sc_version_number)
        if key in packages:
            raise ValueError("duplicate Center content package")
        packages[key] = package
    return 1, packages


def _parse_package(raw: object) -> CenterContentPackage:
    if not isinstance(raw, dict):
        raise ValueError("Center content package must be an object")
    state = raw.get("state")
    base = {"skill_uuid", "sc_version_number", "state"}
    expected = (
        base | {"package_sha256", "package_size", "signed_url", "expires_at"}
        if state == "READY"
        else base
        if state == "PENDING"
        else base | {"code", "retryable"}
        if state == "UNAVAILABLE"
        else set()
    )
    if set(raw) != expected or any(
        not isinstance(raw.get(field), str) or not raw.get(field)
        for field in ("skill_uuid", "sc_version_number", "state")
    ):
        raise ValueError("invalid Center content package")
    common = {
        "skill_uuid": str(raw["skill_uuid"]),
        "sc_version_number": str(raw["sc_version_number"]),
    }
    if state == "READY":
        return CenterContentReadyPackage(
            **common,
            package_sha256=str(raw["package_sha256"]),
            package_size=int(raw["package_size"]),
            signed_url=str(raw["signed_url"]),
            expires_at=str(raw["expires_at"]),
        )
    if state == "PENDING":
        return CenterContentPendingPackage(**common)
    return CenterContentUnavailablePackage(
        **common,
        code=str(raw["code"]),
        retryable=bool(raw["retryable"]),
    )


def _fetch_https(
    url: str,
    *,
    expected_size: int,
    max_size: int,
    timeout: float,
    resolved_addresses: tuple[str, ...],
) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Center content URL must use HTTPS")
    if expected_size > max_size:
        raise ValueError("Center content package exceeds configured limit")
    if not resolved_addresses:
        raise ValueError("Center content host has no trusted address")
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    connection = _PinnedHTTPSConnection(
        parsed.hostname,
        resolved_address=resolved_addresses[0],
        port=parsed.port or 443,
        timeout=timeout,
    )
    try:
        connection.request("GET", target)
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Center content download returned a non-success status")
        length = response.headers.get("Content-Length")
        if length is not None and int(length) != expected_size:
            raise ValueError("Center content response size differs from descriptor")
        content = response.read(max_size + 1)
    finally:
        connection.close()
    if len(content) > max_size:
        raise ValueError("Center content response exceeds configured limit")
    return content


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a validated IP while retaining hostname TLS verification."""

    def __init__(self, host: str, *, resolved_address: str, **kwargs) -> None:
        super().__init__(host, context=ssl.create_default_context(), **kwargs)
        self._resolved_address = resolved_address

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._resolved_address, self.port), self.timeout, self.source_address
        )
        if self._tunnel_host:
            raw_socket.close()
            raise ValueError("Center content proxy tunnels are forbidden")
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


def _resolve_host(hostname: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item[4][0]
            for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        )
    )


__all__ = [
    "DownloadedCenterContentAdapter",
    "MountedCenterContentAdapter",
    "parse_center_content",
]
