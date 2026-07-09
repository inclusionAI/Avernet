"""BaaS invoke-http Transport (strategy pattern).

两个 transport 类共享 duck-typed 接口 ``post(path, *, json) -> httpx.Response``
和 ``post_multipart(path, *, files, data) -> httpx.Response``:

- :class:`BaasInvokeTransport` — 走 :meth:`BaasService.invoke_http`
  (内部调 get_http_info 拿 http_url 后 POST)。适用云上 baas 容器:
  未来 personal+baas / service+baas(隐舟灰度上线)。BaaS 端 get_http_info
  对云上 bot 返公网可达 URL,带动态 token / 负载分配 / device_affinity。

- :class:`DesktopBaasInvokeTransport` — 自拼 secbaas transparent proxy URL,
  直接 httpx POST(REL20260610 写法)。适用 desktop bot:agentbox VM 在用户
  机器,BaaS get_http_info 对 desktop 返 ``http://localhost:20003/...`` 裸 url
  backend 拨不通;只能走 ``{baas_base_url}/api/v1/bots/{tenant}/{bot_uuid}/
  invoke-http/{port}{path}`` 这条 secbaas transparent proxy router,
  secbaas 网关按 bot_uuid 路由到目标 VM。

设计文档:
docs/superpowers/specs/2026-06-17-baas-transport-bot-type-strategy-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import httpx

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.services.baas_service import BaasService


@runtime_checkable
class BaasTransport(Protocol):
    """Typed transport contract the two BaaS invoke strategies share.

    :class:`BaasDeviceFileSystem` depends on this Protocol — not a concrete class —
    so the dispatcher can inject either strategy without the filesystem leaf knowing
    which one:

    - :class:`BaasInvokeTransport` — cloud baas containers, via
      ``BaasService.invoke_http`` (get_http_info → http_url → POST).
    - :class:`DesktopBaasInvokeTransport` — desktop bots, via the secbaas
      transparent-proxy URL (direct httpx POST).
    """

    def post(self, path: str, *, json: Any | None = None) -> httpx.Response: ...

    def post_multipart(
        self, path: str, *, files: Any, data: Any
    ) -> httpx.Response: ...


class BaasInvokeTransport:
    """通过 BaaSService.invoke_http(get_http_info → http_url → POST)。

    适用云上 baas 容器(未来 personal+baas / service+baas)。
    """

    def __init__(
        self,
        *,
        bind_id: int,
        engine_port: int,
        tenant: str,
        baas_service: "BaasService",
        device_uuid: str | None = None,
    ):
        self._bind_id = bind_id
        self._engine_port = engine_port
        self._tenant = tenant
        self._baas_service = baas_service
        # 多实例 service bot 场景，caller 通过 device_uuid 锁定具体实例；透传给
        # invoke_http → get_http_info → BaaS /http-info?device_uuid=。``None`` 表示
        # 单实例 / 未指定，走 BaaS 自动选活跃实例的老行为。
        self._device_uuid = device_uuid

    def post(self, path: str, *, json: Any | None = None) -> httpx.Response:
        return self._baas_service.invoke_http(
            bind_id=self._bind_id,
            port=self._engine_port,
            path=path,
            json=json,
            tenant=self._tenant,
            # 云上 baas 容器经 agentclawproxy ``/proxypass`` 网关访问，网关用
            # ``x-proxypass-token`` 鉴权（与 teclaw/arca 同一网关）；不传则默认
            # ``openclawToken``，proxypass 网关会 401。``info.token`` 即网关 token，
            # 仅 header 名不同。
            auth_header="x-proxypass-token",
            device_uuid=self._device_uuid,
        )

    def get(self, path: str) -> httpx.Response:
        return self._baas_service.invoke_http(
            bind_id=self._bind_id, port=self._engine_port, path=path,
            method="GET", tenant=self._tenant,
            auth_header="x-proxypass-token",
            device_uuid=self._device_uuid,
        )

    def put(self, path: str, *, json: Any | None = None) -> httpx.Response:
        return self._baas_service.invoke_http(
            bind_id=self._bind_id, port=self._engine_port, path=path,
            method="PUT", json=json, tenant=self._tenant,
            auth_header="x-proxypass-token",
            device_uuid=self._device_uuid,
        )

    def delete(self, path: str) -> httpx.Response:
        return self._baas_service.invoke_http(
            bind_id=self._bind_id, port=self._engine_port, path=path,
            method="DELETE", tenant=self._tenant,
            auth_header="x-proxypass-token",
            device_uuid=self._device_uuid,
        )

    def post_multipart(
        self,
        path: str,
        *,
        files: Any,
        data: Any,
    ) -> httpx.Response:
        """Multipart POST(对应 BaasDeviceFileSystem.write_file)。

        invoke_http 通过 files/data 参数走 multipart 路径,内部用
        ``general_http_client.post(url, files=..., data=...)``。
        """
        return self._baas_service.invoke_http(
            bind_id=self._bind_id,
            port=self._engine_port,
            path=path,
            method="POST",
            files=files,
            data=data,
            tenant=self._tenant,
            # 同 post：proxypass 网关鉴权 header（写文件 multipart 链路同样需要）。
            auth_header="x-proxypass-token",
            device_uuid=self._device_uuid,
        )


class DesktopBaasInvokeTransport:
    """自拼 secbaas transparent proxy URL,直接 httpx POST。

    适用 desktop bot(VM 在用户机器,BaaS get_http_info 返 localhost 拨不通)。
    """

    def __init__(
        self,
        *,
        baas_base_url: str,
        tenant: str,
        bot_uuid: str,
        engine_port: int,
        headers: dict[str, str],
    ):
        self._baas_base_url = baas_base_url
        self._tenant = tenant
        self._bot_uuid = bot_uuid
        self._engine_port = engine_port
        self._headers = headers

    def post(self, path: str, *, json: Any | None = None) -> httpx.Response:
        url = self._invoke_url(path)
        with httpx.Client(timeout=30.0) as client:
            return client.post(url, json=json, headers=self._headers)

    def get(self, path: str) -> httpx.Response:
        with httpx.Client(timeout=30.0) as client:
            return client.get(self._invoke_url(path), headers=self._headers)

    def put(self, path: str, *, json: Any | None = None) -> httpx.Response:
        with httpx.Client(timeout=30.0) as client:
            return client.put(self._invoke_url(path), json=json, headers=self._headers)

    def delete(self, path: str) -> httpx.Response:
        with httpx.Client(timeout=30.0) as client:
            return client.delete(self._invoke_url(path), headers=self._headers)

    def post_multipart(
        self,
        path: str,
        *,
        files: Any,
        data: Any,
    ) -> httpx.Response:
        url = self._invoke_url(path)
        with httpx.Client(timeout=30.0) as client:
            return client.post(url, files=files, data=data, headers=self._headers)

    def _invoke_url(self, api_path: str) -> str:
        if not api_path.startswith("/"):
            api_path = "/" + api_path
        return (
            f"{self._baas_base_url}/api/v1/bots/{self._tenant}/"
            f"{self._bot_uuid}/invoke-http/{self._engine_port}{api_path}"
        )
