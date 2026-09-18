from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from agentclaw.community.core.skill_center.services.local_skill_package_runtime import (
    LocalSkillPackageRuntime,
)
from agentclaw.community.core.skill_center.errors import LocalSkillStorageError
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
)


class _Resolver:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def resolve_for_bot(self, bot_id: str, owner_id: str):
        return SimpleNamespace(
            provider=self.provider,
            conn_info={"binding_id": 7, "engine_port": 20003},
            bot_id=bot_id,
            user_id=owner_id,
        )


class _Transport:
    def __init__(self, *, provider: str, package: bytes) -> None:
        self.provider = provider
        self.package = package
        self.calls: list[tuple] = []

    async def invoke(
        self, conn_info, method, path, body=None, params=None, *, timeout=None
    ):
        self.calls.append(("json", method, path))
        assert path == "/api/engine/capabilities"
        return {
            "success": True,
            "data": {
                "supported": ["skills.local_package.apply.v1"],
                "limited": {},
                "fallback": {},
            },
        }

    async def invoke_multipart(
        self, conn_info, path, *, files, data, headers=None, timeout=None
    ):
        self.calls.append(("multipart", path, files, data, headers))
        digest = hashlib.sha256(self.package).hexdigest()
        if self.provider == "teclaw":
            return {
                "success": True,
                "skill_name": "weather",
                "action": "replaced",
                "sha256": digest,
                "target_path": "/workspace/skills-local/weather",
            }
        return {
            "success": True,
            "data": {
                "skill_name": "weather",
                "action": "created",
                "content_digest": f"sha256:{digest}",
                "target_path": "/runtime/skills-local/weather",
            },
        }


@pytest.mark.asyncio
async def test_standard_engine_negotiates_then_sends_one_complete_package() -> None:
    package = b"canonical-zip"
    transport = _Transport(provider="baas", package=package)
    runtime = LocalSkillPackageRuntime(_Resolver("baas"), transport)

    result = await runtime.apply(
        bot_id="bot-1",
        owner_id="owner-1",
        skill_name="weather",
        layout="POOL",
        package=package,
    )

    assert result is not None
    assert result.action == "created"
    assert [call[0] for call in transport.calls] == ["json", "multipart"]
    _, path, files, data, headers = transport.calls[1]
    assert path == "/api/skills/local/apply"
    assert files["file"][1] == package
    assert data == {"skill_name": "weather", "layout": "POOL"}
    assert headers is None


@pytest.mark.asyncio
async def test_teclaw_skips_capability_and_translates_its_wire() -> None:
    package = b"canonical-zip"
    transport = _Transport(provider="teclaw", package=package)
    runtime = LocalSkillPackageRuntime(_Resolver("teclaw"), transport)

    result = await runtime.apply(
        bot_id="bot-1",
        owner_id="owner-1",
        skill_name="weather",
        layout="LEGACY",
        package=package,
    )

    assert result is not None
    assert result.content_digest == "sha256:" + hashlib.sha256(package).hexdigest()
    assert len(transport.calls) == 1
    _, path, _files, data, headers = transport.calls[0]
    assert path == "/api/v1/file/skill-package"
    assert data == {"skills_name": "weather"}
    assert headers == {"x-target-bot-id": "bot-1"}


@pytest.mark.asyncio
async def test_capability_absent_chooses_legacy_before_any_write() -> None:
    package = b"canonical-zip"
    transport = _Transport(provider="baas", package=package)

    async def absent(*_args, **_kwargs):
        transport.calls.append(("json", "GET", "/api/engine/capabilities"))
        return {
            "success": True,
            "data": {"supported": [], "limited": {}, "fallback": {}},
        }

    transport.invoke = absent
    result = await LocalSkillPackageRuntime(_Resolver("baas"), transport).apply(
        bot_id="bot-1",
        owner_id="owner-1",
        skill_name="weather",
        layout="LEGACY",
        package=package,
    )

    assert result is None
    assert [call[0] for call in transport.calls] == ["json"]


@pytest.mark.asyncio
async def test_standard_route_404_falls_back_only_after_health_succeeds() -> None:
    package = b"canonical-zip"
    transport = _Transport(provider="baas", package=package)

    async def legacy(conn_info, method, path, body=None, params=None, *, timeout=None):
        transport.calls.append(("json", method, path))
        if path == "/api/engine/capabilities":
            raise DeviceAdapterEndpointNotFoundError(
                '{"detail":"Not Found"}', standard_route_missing=True
            )
        assert path == "/health"
        return {"status": "ok"}

    transport.invoke = legacy
    result = await LocalSkillPackageRuntime(_Resolver("baas"), transport).apply(
        bot_id="bot-1",
        owner_id="owner-1",
        skill_name="weather",
        layout="LEGACY",
        package=package,
    )

    assert result is None
    assert [call[2] for call in transport.calls] == [
        "/api/engine/capabilities",
        "/health",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        DeviceAdapterHTTPStatusError(500, '{"error":"boom"}'),
        ValueError("invalid capability payload"),
    ],
)
async def test_capability_failure_never_falls_back(failure: Exception) -> None:
    package = b"canonical-zip"
    transport = _Transport(provider="baas", package=package)

    async def fail(*_args, **_kwargs):
        transport.calls.append(("json", "GET", "/api/engine/capabilities"))
        if isinstance(failure, ValueError) and not isinstance(
            failure, DeviceAdapterHTTPStatusError
        ):
            return {"success": True, "data": {"supported": "invalid"}}
        raise failure

    transport.invoke = fail
    with pytest.raises(LocalSkillStorageError):
        await LocalSkillPackageRuntime(_Resolver("baas"), transport).apply(
            bot_id="bot-1",
            owner_id="owner-1",
            skill_name="weather",
            layout="LEGACY",
            package=package,
        )

    assert [call[0] for call in transport.calls] == ["json"]


@pytest.mark.asyncio
async def test_legacy_route_404_with_unhealthy_runtime_never_falls_back() -> None:
    package = b"canonical-zip"
    transport = _Transport(provider="baas", package=package)

    async def unhealthy(conn_info, method, path, body=None, params=None, *, timeout=None):
        transport.calls.append(("json", method, path))
        if path == "/api/engine/capabilities":
            raise DeviceAdapterEndpointNotFoundError(
                '{"detail":"Not Found"}', standard_route_missing=True
            )
        return {"status": "starting"}

    transport.invoke = unhealthy
    with pytest.raises(LocalSkillStorageError):
        await LocalSkillPackageRuntime(_Resolver("baas"), transport).apply(
            bot_id="bot-1",
            owner_id="owner-1",
            skill_name="weather",
            layout="LEGACY",
            package=package,
        )

    assert [call[2] for call in transport.calls] == [
        "/api/engine/capabilities",
        "/health",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        TimeoutError("unknown outcome"),
        {"success": True, "data": "malformed"},
        {
            "success": True,
            "data": {"skill_name": "weather", "action": "created"},
        },
    ],
)
async def test_post_send_unknown_never_falls_back(response: object) -> None:
    package = b"canonical-zip"
    transport = _Transport(provider="baas", package=package)

    async def unknown(conn_info, path, *, files, data, headers=None, timeout=None):
        transport.calls.append(("multipart", path, files, data, headers))
        if isinstance(response, BaseException):
            raise response
        return response

    transport.invoke_multipart = unknown
    with pytest.raises(LocalSkillStorageError):
        await LocalSkillPackageRuntime(_Resolver("baas"), transport).apply(
            bot_id="bot-1",
            owner_id="owner-1",
            skill_name="weather",
            layout="LEGACY",
            package=package,
        )

    assert [call[0] for call in transport.calls] == ["json", "multipart"]


@pytest.mark.asyncio
async def test_digest_mismatch_is_unknown_and_never_falls_back() -> None:
    package = b"canonical-zip"
    transport = _Transport(provider="baas", package=package)

    async def mismatch(conn_info, path, *, files, data, headers=None, timeout=None):
        transport.calls.append(("multipart", path, files, data, headers))
        return {
            "success": True,
            "data": {
                "skill_name": "weather",
                "action": "created",
                "content_digest": "sha256:" + "0" * 64,
            },
        }

    transport.invoke_multipart = mismatch
    with pytest.raises(LocalSkillStorageError):
        await LocalSkillPackageRuntime(_Resolver("baas"), transport).apply(
            bot_id="bot-1",
            owner_id="owner-1",
            skill_name="weather",
            layout="LEGACY",
            package=package,
        )

    assert [call[0] for call in transport.calls] == ["json", "multipart"]
