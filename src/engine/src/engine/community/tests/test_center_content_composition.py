import hashlib
import sys
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest
import engine

from engine.community.config import load_center_content_allowed_hosts
from engine.community.api.skills.router import _center_content_package
from engine.community.api.skills.schemas import (
    CenterContentPendingPackageSchema,
    CenterContentReadyPackageSchema,
    CenterContentUnavailablePackageSchema,
)
from engine.community.engines import center_content as center_content_composition
from engine.community.engines.center_content import build_center_content_adapter
from engine.community.kernel.center_content import (
    CenterContentPendingPackage,
    CenterContentPreparationStatus,
    CenterContentReadyPackage,
    CenterContentRequest,
    CenterContentUnavailablePackage,
)
from engine.community.plugins.skills_pool.center_content import (
    DownloadedCenterContentAdapter,
    MountedCenterContentAdapter,
)


def test_cloud_composition_selects_mounted_adapter(monkeypatch) -> None:
    monkeypatch.delenv("MAC_CONTAINER", raising=False)

    assert isinstance(build_center_content_adapter(), MountedCenterContentAdapter)


def test_agentbox_composition_requires_explicit_trusted_hosts(monkeypatch) -> None:
    monkeypatch.setenv("MAC_CONTAINER", "true")
    monkeypatch.setenv(
        "ENGINE_CENTER_CONTENT_ALLOWED_HOSTS",
        "objects-a.example.test, objects-b.example.test",
    )

    adapter = build_center_content_adapter()

    assert isinstance(adapter, DownloadedCenterContentAdapter)
    assert set(load_center_content_allowed_hosts()) == {
        "objects-a.example.test",
        "objects-b.example.test",
    }


def test_engine_rebuild_reuses_the_same_bounded_download_coordinator(
    tmp_path: Path, monkeypatch
) -> None:
    host = "restart-budget.example.test"
    release = threading.Event()
    started = [threading.Event() for _index in range(3)]
    body = b"not-a-zip"

    def fetch(url: str, **_kwargs) -> bytes:
        index = int(url.rsplit("/", 1)[-1])
        started[index].set()
        assert release.wait(2)
        return body

    created: list[DownloadedCenterContentAdapter] = []

    def create_adapter(*, allowed_hosts):
        adapter = DownloadedCenterContentAdapter(
            fetch=fetch,
            allowed_hosts=allowed_hosts,
            resolve_host=lambda _host: ("93.184.216.34",),
            max_concurrent_downloads=2,
        )
        created.append(adapter)
        return adapter

    monkeypatch.setenv("MAC_CONTAINER", "true")
    monkeypatch.setenv("ENGINE_CENTER_CONTENT_ALLOWED_HOSTS", host)
    monkeypatch.setattr(
        center_content_composition,
        "DownloadedCenterContentAdapter",
        create_adapter,
    )

    before_restart = build_center_content_adapter()
    after_restart = build_center_content_adapter()
    assert len(created) == 1
    assert before_restart is created[0]
    assert after_restart is before_restart

    def package(index: int) -> CenterContentReadyPackage:
        return CenterContentReadyPackage(
            skill_uuid=f"skill-{index}",
            sc_version_number="1",
            package_sha256=hashlib.sha256(body).hexdigest(),
            package_size=len(body),
            signed_url=f"https://{host}/{index}",
            expires_at="2026-09-09T02:02:03Z",
        )

    first = before_restart.prepare(center_root=tmp_path, package=package(0))
    second = before_restart.prepare(center_root=tmp_path, package=package(1))
    try:
        assert started[0].wait(1) and started[1].wait(1)
        third = after_restart.prepare(center_root=tmp_path, package=package(2))
        assert first.status is CenterContentPreparationStatus.PENDING
        assert second.status is CenterContentPreparationStatus.PENDING
        assert third.code == "CENTER_CONTENT_DOWNLOAD_CAPACITY"
        assert not started[2].is_set()
    finally:
        release.set()


def test_center_hosts_fall_back_to_corp_defaults(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_CENTER_CONTENT_ALLOWED_HOSTS", raising=False)
    defaults = SimpleNamespace(
        CENTER_CONTENT_ALLOWED_HOSTS=(
            "Objects.Example.Test.",
            "objects.example.test",
            " ",
        ),
        SKILLS_REPO_META_URL_TEMPLATE="",
    )
    monkeypatch.setitem(
        sys.modules,
        "engine.internal_defaults",
        defaults,
    )
    monkeypatch.setattr(engine, "internal_defaults", defaults, raising=False)

    assert load_center_content_allowed_hosts() == ("objects.example.test",)


def test_center_hosts_fall_back_to_repo_metadata_host(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_CENTER_CONTENT_ALLOWED_HOSTS", raising=False)
    defaults = SimpleNamespace(
        CENTER_CONTENT_ALLOWED_HOSTS=(),
        SKILLS_REPO_META_URL_TEMPLATE="https://metadata.example.test/repo.json",
    )
    monkeypatch.setitem(
        sys.modules,
        "engine.internal_defaults",
        defaults,
    )
    monkeypatch.setattr(engine, "internal_defaults", defaults, raising=False)

    assert load_center_content_allowed_hosts() == ("metadata.example.test",)


def test_center_hosts_reject_wildcards(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_CENTER_CONTENT_ALLOWED_HOSTS", "*.example.test")

    with pytest.raises(ValueError, match="invalid Center content allowed host"):
        load_center_content_allowed_hosts()


def test_center_content_union_serializes_each_state() -> None:
    request = CenterContentRequest(
        (
            CenterContentReadyPackage(
                "skill-id",
                "1",
                package_sha256="a" * 64,
                package_size=10,
                signed_url="https://objects.example.test/exact.zip",
                expires_at="2026-09-09T02:02:03Z",
            ),
            CenterContentPendingPackage("pending-id", "2"),
            CenterContentUnavailablePackage(
                "unavailable-id", "3", code="NOT_READY", retryable=True
            ),
        )
    )

    assert [package["state"] for package in request.to_data()["packages"]] == [
        "READY",
        "PENDING",
        "UNAVAILABLE",
    ]


def test_http_center_content_union_maps_each_state_to_core() -> None:
    packages = (
        CenterContentReadyPackageSchema(
            skill_uuid="skill-id",
            sc_version_number="1",
            state="READY",
            package_sha256="a" * 64,
            package_size=10,
            signed_url="https://objects.example.test/exact.zip",
            expires_at="2026-09-09T02:02:03Z",
        ),
        CenterContentPendingPackageSchema(
            skill_uuid="pending-id", sc_version_number="2", state="PENDING"
        ),
        CenterContentUnavailablePackageSchema(
            skill_uuid="unavailable-id",
            sc_version_number="3",
            state="UNAVAILABLE",
            code="NOT_READY",
            retryable=True,
        ),
    )

    assert [
        _center_content_package(package).state.value for package in packages
    ] == ["READY", "PENDING", "UNAVAILABLE"]

    with pytest.raises(AssertionError, match="Pydantic must reject unknown"):
        _center_content_package(
            SimpleNamespace(model_dump=lambda: {"state": "BROKEN"})
        )
