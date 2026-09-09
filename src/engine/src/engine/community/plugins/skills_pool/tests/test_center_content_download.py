from __future__ import annotations

import hashlib
import io
import socket
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from engine.community.plugins.skills_pool.center_content import (
    DownloadedCenterContentAdapter,
    _PinnedHTTPSConnection,
)
from engine.community.plugins.skills_pool.layout_activation import (
    MappingProjectionStatus,
    MappingSourceLayout,
)
from engine.community.plugins.skills_pool.mapping_contract import (
    apply_logical_mapping_payload,
)


def _zip(files: dict[str, bytes]) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return target.getvalue()


def _ready(package: bytes, *, version: str = "2") -> dict[str, object]:
    return {
        "skill_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "sc_version_number": version,
        "state": "READY",
        "package_sha256": hashlib.sha256(package).hexdigest(),
        "package_size": len(package),
        "signed_url": "https://objects.example.test/exact.zip?signature=secret",
        "expires_at": "2026-09-09T02:02:03Z",
    }


def _mapping(version: str = "2") -> dict[str, str]:
    return {
        "corpus": "center",
        "skill_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "sc_version_number": version,
        "link_name": "writer",
    }


def _download_adapter(fetch) -> DownloadedCenterContentAdapter:
    return DownloadedCenterContentAdapter(
        fetch=fetch,
        allowed_hosts=("objects.example.test",),
        resolve_host=lambda _host: ("93.184.216.34",),
    )


def _settle(execute, *, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while True:
        result = execute()
        codes = {item.code for item in result.items}
        if not codes.intersection(
            {"CENTER_CONTENT_DOWNLOAD_PENDING", "CENTER_CONTENT_DOWNLOAD_CAPACITY"}
        ):
            return result
        if time.monotonic() >= deadline:
            raise AssertionError("Center content preparation did not settle")
        time.sleep(0.01)


def test_downloaded_adapter_publishes_verified_cache_then_applies_symlink(
    tmp_path: Path,
) -> None:
    package = _zip({"SKILL.md": b"# writer\n", "assets/icon.bin": b"\x00\xff"})
    fetches: list[str] = []

    def fetch(
        url: str,
        *,
        expected_size: int,
        max_size: int,
        timeout: float,
        resolved_addresses: tuple[str, ...],
    ) -> bytes:
        fetches.append(url)
        assert expected_size == len(package)
        assert max_size >= expected_size
        assert timeout > 0
        assert resolved_addresses == ("93.184.216.34",)
        return package

    adapter = _download_adapter(fetch)

    def execute(descriptor=None):
        return apply_logical_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.LEGACY,
            mappings_payload=[_mapping()],
            retired_payload=[],
            center_content_payload={
                "contract_version": 1,
                "packages": [descriptor or _ready(package)],
            },
            content_adapter=adapter,
            home=tmp_path,
        )

    first = execute()
    assert first.status is MappingProjectionStatus.PENDING
    result = _settle(execute)

    target = tmp_path / ".openclaw/workspace/skills/writer"
    exact = (
        tmp_path
        / ".openclaw/workspace/skills-pool/skill-center"
        / "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        / "2"
    )
    assert result.status is MappingProjectionStatus.CONVERGED
    assert target.is_symlink() and target.resolve() == exact.resolve()
    assert (exact / "SKILL.md").read_bytes() == b"# writer\n"
    assert (exact / "assets/icon.bin").read_bytes() == b"\x00\xff"
    assert result.evidence["center_content"] == {
        "contract_version": 1,
        "mode": "DOWNLOAD",
        "ready": 1,
        "pending": 0,
        "unavailable": 0,
        "packages": [
            {
                "skill_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "sc_version_number": "2",
                "status": "READY",
            }
        ],
    }
    assert len(fetches) == 1

    refreshed = _ready(package)
    refreshed["signed_url"] = "https://objects.example.test/exact.zip?signature=fresh"
    repeated = execute(refreshed)
    assert repeated.status is MappingProjectionStatus.CONVERGED
    assert len(fetches) == 1


def test_one_exact_package_is_prepared_and_acknowledged_once_for_multiple_links(
    tmp_path: Path,
) -> None:
    package = _zip({"SKILL.md": b"# shared\n"})
    fetches = 0

    def fetch(*_args, **_kwargs):
        nonlocal fetches
        fetches += 1
        return package

    first = _mapping()
    second = {**_mapping(), "link_name": "writer-alias"}
    adapter = _download_adapter(fetch)

    def execute():
        return apply_logical_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.LEGACY,
            mappings_payload=[first, second],
            retired_payload=[],
            center_content_payload={
                "contract_version": 1,
                "packages": [_ready(package)],
            },
            content_adapter=adapter,
            home=tmp_path,
        )

    result = _settle(execute)

    assert result.status is MappingProjectionStatus.CONVERGED
    assert fetches == 1
    assert result.evidence["center_content"]["ready"] == 1
    assert len(result.evidence["center_content"]["packages"]) == 1
    active = tmp_path / ".openclaw/workspace/skills"
    assert (active / "writer").resolve() == (active / "writer-alias").resolve()


def test_verified_cache_survives_restart_without_url_or_dns_availability(
    tmp_path: Path,
) -> None:
    package = _zip({"SKILL.md": b"# cached\n"})
    adapter = _download_adapter(lambda *_args, **_kwargs: package)

    def prepare_cache():
        return apply_logical_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.LEGACY,
            mappings_payload=[_mapping()],
            retired_payload=[],
            center_content_payload={
                "contract_version": 1,
                "packages": [_ready(package)],
            },
            content_adapter=adapter,
            home=tmp_path,
        )

    first = _settle(prepare_cache)
    assert first.status is MappingProjectionStatus.CONVERGED

    descriptor = _ready(package)
    descriptor["signed_url"] = "https://expired.example.invalid/exact.zip"
    restarted = DownloadedCenterContentAdapter(
        fetch=lambda *_args, **_kwargs: pytest.fail("cache hit must not fetch"),
        allowed_hosts=(),
        resolve_host=lambda _host: pytest.fail("cache hit must not resolve DNS"),
    )
    second = apply_logical_mapping_payload(
        engine="openclaw",
        source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[_mapping()],
        retired_payload=[],
        center_content_payload={"contract_version": 1, "packages": [descriptor]},
        content_adapter=restarted,
        home=tmp_path,
    )

    assert second.status is MappingProjectionStatus.CONVERGED
    assert second.evidence["center_content"]["ready"] == 1


def test_pending_center_does_not_block_ready_local_mapping(tmp_path: Path) -> None:
    local = tmp_path / ".openclaw/workspace/skills/skills-local/local-writer"
    local.mkdir(parents=True)
    (local / "SKILL.md").write_text("local")
    local_mapping = {
        "corpus": "local",
        "relative_path": "local-writer",
        "link_name": "local-writer",
    }
    pending = {
        "skill_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "sc_version_number": "2",
        "state": "PENDING",
    }

    result = apply_logical_mapping_payload(
        engine="openclaw",
        source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[local_mapping, _mapping()],
        retired_payload=[],
        center_content_payload={"contract_version": 1, "packages": [pending]},
        content_adapter=_download_adapter(lambda *_a, **_kw: b""),
        home=tmp_path,
    )

    assert result.status is MappingProjectionStatus.PENDING
    assert (tmp_path / ".openclaw/workspace/skills/local-writer").is_symlink()
    outcomes = {item.mapping["link_name"]: item for item in result.items}
    assert outcomes["local-writer"].status is MappingProjectionStatus.CONVERGED
    assert outcomes["writer"].code == "CENTER_CONTENT_PACKAGE_PENDING"


def test_digest_mismatch_is_permanent_and_never_publishes(tmp_path: Path) -> None:
    package = _zip({"SKILL.md": b"ok"})
    descriptor = _ready(package)
    descriptor["package_sha256"] = "0" * 64

    adapter = _download_adapter(lambda *_a, **_kw: package)
    result = _settle(
        lambda: apply_logical_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.LEGACY,
            mappings_payload=[_mapping()],
            retired_payload=[],
            center_content_payload={
                "contract_version": 1,
                "packages": [descriptor],
            },
            content_adapter=adapter,
            home=tmp_path,
        )
    )

    assert result.status is MappingProjectionStatus.DEGRADED
    assert result.items[0].code == "CENTER_CONTENT_INTEGRITY_MISMATCH"


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/exact.zip",
        "https://user@objects.example.test/exact.zip",
        "https://untrusted.example/exact.zip",
    ],
)
def test_untrusted_download_url_is_rejected_before_network(
    tmp_path: Path, url: str
) -> None:
    package = _zip({"SKILL.md": b"ok"})
    descriptor = _ready(package)
    descriptor["signed_url"] = url
    fetches = 0

    def fetch(*_args, **_kwargs):
        nonlocal fetches
        fetches += 1
        return package

    result = apply_logical_mapping_payload(
        engine="openclaw",
        source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[_mapping()],
        retired_payload=[],
        center_content_payload={"contract_version": 1, "packages": [descriptor]},
        content_adapter=_download_adapter(fetch),
        home=tmp_path,
    )

    assert result.status is MappingProjectionStatus.DEGRADED
    assert result.items[0].code == "CENTER_CONTENT_DESCRIPTOR_INVALID"
    assert fetches == 0


def test_private_resolution_is_rejected_before_network(tmp_path: Path) -> None:
    package = _zip({"SKILL.md": b"ok"})
    fetches = 0

    def fetch(*_args, **_kwargs):
        nonlocal fetches
        fetches += 1
        return package

    adapter = DownloadedCenterContentAdapter(
        fetch=fetch,
        allowed_hosts=("objects.example.test",),
        resolve_host=lambda _host: ("127.0.0.1",),
    )
    result = _settle(
        lambda: apply_logical_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.LEGACY,
            mappings_payload=[_mapping()],
            retired_payload=[],
            center_content_payload={
                "contract_version": 1,
                "packages": [_ready(package)],
            },
            content_adapter=adapter,
            home=tmp_path,
        )
    )

    assert result.items[0].code == "CENTER_CONTENT_DESCRIPTOR_INVALID"
    assert fetches == 0


def test_temporary_dns_error_is_retryable(tmp_path: Path) -> None:
    package = _zip({"SKILL.md": b"ok"})

    def temporary_failure(_host: str) -> tuple[str, ...]:
        raise socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")

    adapter = DownloadedCenterContentAdapter(
        fetch=lambda *_args, **_kwargs: package,
        allowed_hosts=("objects.example.test",),
        resolve_host=temporary_failure,
    )
    result = _settle(
        lambda: apply_logical_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.LEGACY,
            mappings_payload=[_mapping()],
            retired_payload=[],
            center_content_payload={
                "contract_version": 1,
                "packages": [_ready(package)],
            },
            content_adapter=adapter,
            home=tmp_path,
        )
    )

    assert result.status is MappingProjectionStatus.PENDING
    assert result.items[0].code == "CENTER_CONTENT_DNS_UNAVAILABLE"
    assert result.items[0].retryable is True


def test_slow_dns_does_not_block_ready_local_mapping(tmp_path: Path) -> None:
    local = tmp_path / ".openclaw/workspace/skills/skills-local/local-writer"
    local.mkdir(parents=True)
    (local / "SKILL.md").write_text("local", encoding="utf-8")
    package = _zip({"SKILL.md": b"center"})
    started = threading.Event()
    release = threading.Event()

    def resolve(_host: str) -> tuple[str, ...]:
        started.set()
        assert release.wait(2)
        return ("93.184.216.34",)

    adapter = DownloadedCenterContentAdapter(
        fetch=lambda *_args, **_kwargs: package,
        allowed_hosts=("objects.example.test",),
        resolve_host=resolve,
    )
    result = apply_logical_mapping_payload(
        engine="openclaw",
        source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[
            {
                "corpus": "local",
                "relative_path": "local-writer",
                "link_name": "local-writer",
            },
            _mapping(),
        ],
        retired_payload=[],
        center_content_payload={"contract_version": 1, "packages": [_ready(package)]},
        content_adapter=adapter,
        home=tmp_path,
    )
    try:
        assert started.wait(1)
        assert result.status is MappingProjectionStatus.PENDING
        assert (tmp_path / ".openclaw/workspace/skills/local-writer").is_symlink()
    finally:
        release.set()


def test_https_connection_uses_validated_ip_with_hostname_tls_verification() -> None:
    raw_socket = MagicMock()
    tls_socket = MagicMock()
    context = MagicMock()
    context.wrap_socket.return_value = tls_socket

    with (
        patch(
            "engine.community.plugins.skills_pool.center_content.ssl.create_default_context",
            return_value=context,
        ),
        patch(
            "engine.community.plugins.skills_pool.center_content.socket.create_connection",
            return_value=raw_socket,
        ) as create_connection,
    ):
        connection = _PinnedHTTPSConnection(
            "objects.example.test",
            resolved_address="93.184.216.34",
            port=443,
            timeout=10,
        )
        connection.connect()

    create_connection.assert_called_once_with(
        ("93.184.216.34", 443), 10, None
    )
    context.wrap_socket.assert_called_once_with(
        raw_socket, server_hostname="objects.example.test"
    )
    assert connection.sock is tls_socket


def test_slow_center_download_does_not_block_ready_local_mapping(
    tmp_path: Path,
) -> None:
    local = tmp_path / ".openclaw/workspace/skills/skills-local/local-writer"
    local.mkdir(parents=True)
    (local / "SKILL.md").write_text("local", encoding="utf-8")
    package = _zip({"SKILL.md": b"center"})
    started = threading.Event()
    release = threading.Event()

    def fetch(*_args, **_kwargs):
        started.set()
        assert release.wait(2)
        return package

    result = apply_logical_mapping_payload(
        engine="openclaw",
        source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[
            {
                "corpus": "local",
                "relative_path": "local-writer",
                "link_name": "local-writer",
            },
            _mapping(),
        ],
        retired_payload=[],
        center_content_payload={"contract_version": 1, "packages": [_ready(package)]},
        content_adapter=_download_adapter(fetch),
        home=tmp_path,
    )
    try:
        assert started.wait(1)
        assert result.status is MappingProjectionStatus.PENDING
        assert (tmp_path / ".openclaw/workspace/skills/local-writer").is_symlink()
    finally:
        release.set()


def test_failed_v2_download_retains_existing_v1_link(tmp_path: Path) -> None:
    center = tmp_path / ".openclaw/workspace/skills-pool/skill-center"
    v1 = center / "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/1"
    v1.mkdir(parents=True)
    (v1 / "SKILL.md").write_text("v1")
    active = tmp_path / ".openclaw/workspace/skills/writer"
    active.parent.mkdir(parents=True)
    active.symlink_to(v1, target_is_directory=True)
    package = _zip({"SKILL.md": b"v2"})

    def fail(*_args, **_kwargs):
        raise TimeoutError("expired credential")

    adapter = _download_adapter(fail)

    def execute():
        return apply_logical_mapping_payload(
            engine="openclaw",
            source_layout=MappingSourceLayout.LEGACY,
            mappings_payload=[_mapping("2")],
            retired_payload=[_mapping("1")],
            center_content_payload={
                "contract_version": 1,
                "packages": [_ready(package)],
            },
            content_adapter=adapter,
            home=tmp_path,
        )

    result = _settle(execute)

    assert result.status is MappingProjectionStatus.PENDING
    assert active.is_symlink() and active.resolve() == v1.resolve()
    assert result.items[0].code == "CENTER_CONTENT_DOWNLOAD_FAILED"
    assert result.items[0].retryable is True


def test_deactivate_pending_v2_retires_the_retained_v1_link(tmp_path: Path) -> None:
    center = tmp_path / ".openclaw/workspace/skills-pool/skill-center"
    v1 = center / "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/1"
    v1.mkdir(parents=True)
    (v1 / "SKILL.md").write_text("v1", encoding="utf-8")
    active = tmp_path / ".openclaw/workspace/skills/writer"
    active.parent.mkdir(parents=True)
    active.symlink_to(v1, target_is_directory=True)
    adapter = _download_adapter(lambda *_args, **_kwargs: b"")
    pending = {
        "skill_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "sc_version_number": "2",
        "state": "PENDING",
    }

    prepared = apply_logical_mapping_payload(
        engine="openclaw",
        source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[_mapping("2")],
        retired_payload=[],
        center_content_payload={"contract_version": 1, "packages": [pending]},
        content_adapter=adapter,
        home=tmp_path,
    )
    retired = apply_logical_mapping_payload(
        engine="openclaw",
        source_layout=MappingSourceLayout.LEGACY,
        mappings_payload=[],
        retired_payload=[_mapping("2")],
        content_adapter=adapter,
        home=tmp_path,
    )

    assert prepared.status is MappingProjectionStatus.PENDING
    assert retired.status is MappingProjectionStatus.CONVERGED
    assert not active.is_symlink()


def test_unsafe_archive_never_becomes_visible(tmp_path: Path) -> None:
    package = _zip({"SKILL.md": b"ok", "../escape": b"bad"})

    adapter = _download_adapter(lambda *_a, **_kw: package)
    result = _settle(
        lambda: apply_logical_mapping_payload(
            engine="hermes",
            source_layout=MappingSourceLayout.POOL,
            mappings_payload=[_mapping()],
            retired_payload=[],
            center_content_payload={
                "contract_version": 1,
                "packages": [_ready(package)],
            },
            content_adapter=adapter,
            home=tmp_path,
        )
    )

    assert result.status is MappingProjectionStatus.DEGRADED
    assert result.items[0].code == "CENTER_CONTENT_ARCHIVE_INVALID"
    assert not (tmp_path / "escape").exists()
    assert not (
        tmp_path
        / ".hermes/workspace/skills-pool/skill-center"
        / "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/2"
    ).exists()
