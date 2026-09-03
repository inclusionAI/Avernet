"""Provider routing tests for the AICoding workspace Relay calls."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from agentclaw.community.core.aicoding.services.workspace_service import WorkspaceService
from agentclaw.community.kernel.device_dto import ProxyRequest


def _service(provider: str, *, device_props: dict | None = None):
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {"binding_id": 41}
    device_service = MagicMock()
    device_service.get_device.return_value = SimpleNamespace(
        id=41,
        device_provider=provider,
        device_props=device_props or {},
    )
    sandbox_client = MagicMock()
    baas_service = MagicMock()
    service = WorkspaceService(
        bot_provider=bot_service,
        device_provider=device_service,
        path_factory=MagicMock(),
        sandbox_client=sandbox_client,
        baas_service=baas_service,
    )
    return service, sandbox_client, baas_service


def _response(payload: object) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("POST", "https://proxy.example/api/git/clone"),
    )


def test_baas_clone_uses_binding_without_requiring_sandbox_id():
    service, sandbox_client, baas_service = _service("baas")
    baas_service.invoke_http.return_value = _response({"success": True})

    asyncio.run(
        service._clone_repository(
            "https://git.example/repo.git",
            "/workspace/repo",
            "main",
            "bot-1",
            "user-1",
        )
    )

    baas_service.invoke_http.assert_called_once_with(
        bind_id=41,
        port=18900,
        path="/api/git/clone",
        method="POST",
        json={
            "url": "https://git.example/repo.git",
            "target_dir": "/workspace/repo",
            "branch": "main",
        },
        device_affinity="user-1",
        auth_header="x-proxypass-token",
        timeout=300.0,
    )
    sandbox_client.build_proxy_request.assert_not_called()


def test_arca_clone_keeps_existing_sandbox_proxy_request():
    service, sandbox_client, baas_service = _service(
        "arca", device_props={"sandbox_id": "sandbox-1"}
    )
    sandbox_client.build_proxy_request.return_value = ProxyRequest(
        url="https://arca.example/proxypass/target/api/git/clone",
        headers={"x-proxypass-token": "token-1"},
    )
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=_response({"success": True}))

    with patch("httpx.AsyncClient", return_value=client):
        asyncio.run(
            service._clone_repository(
                "https://git.example/repo.git",
                "/workspace/repo",
                None,
                "bot-1",
                "user-1",
            )
        )

    sandbox_client.build_proxy_request.assert_called_once_with(
        sandbox_id="sandbox-1", api_path="/api/git/clone", port=18900
    )
    client.post.assert_awaited_once_with(
        "https://arca.example/proxypass/target/api/git/clone",
        json={
            "url": "https://git.example/repo.git",
            "target_dir": "/workspace/repo",
            "branch": None,
        },
        headers={"x-proxypass-token": "token-1"},
    )
    baas_service.invoke_http.assert_not_called()
