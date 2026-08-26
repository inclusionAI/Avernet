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

from agentclaw.community.core.devices.services.baas_http_info import (
    PROXYPASS_AUTH_HEADER,
    SharedHttpInfo,
)

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.services.baas_service import BaasService




def build_desktop_client() -> httpx.Client:
    """Build the pooled client :class:`DesktopBaasInvokeTransport` sends through.

    Built once by the (singleton) device-filesystem resolver and injected into every
    desktop transport it mints, so its lifetime is the process and its ownership is
    the injector's — not a module-level lazy singleton.

    Pooling is the point: a transport used to open a fresh ``httpx.Client`` per call,
    forcing a TCP + TLS handshake for every file in a package upload. One shared
    client turns that into one handshake per pooled connection. Sharing is safe —
    ``httpx.Client`` is thread-safe (device I/O runs on ``asyncio.to_thread`` worker
    threads) and every call already carries its own absolute URL and headers.
    """
    return httpx.Client(
        timeout=30.0,
        # Comfortably above the device-I/O fan-out so pool waits never become the
        # new bottleneck, while still bounding total sockets.
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
    )


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

    Resolution (``get_http_info``) is cached per path for the life of this
    instance by the shared :class:`SharedHttpInfo`. The resolver builds one
    transport per dispatch, so that lifetime is a single request and there is no
    cross-request staleness to reason about.
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
        # 云上 baas 容器经 agentclawproxy ``/proxypass`` 网关访问，网关用
        # ``x-proxypass-token`` 鉴权（与 teclaw/arca 同一网关）；不传则默认
        # ``openclawToken``，proxypass 网关会 401。``info.token`` 即网关 token，
        # 仅 header 名不同。
        #
        # 多实例 service bot 场景，caller 通过 device_uuid 锁定具体实例；透传给
        # invoke_http → get_http_info → BaaS /http-info?device_uuid=。``None`` 表示
        # 单实例 / 未指定，走 BaaS 自动选活跃实例的老行为。
        self._http_info = SharedHttpInfo(
            baas_service=baas_service,
            bind_id=bind_id,
            engine_port=engine_port,
            tenant=tenant,
            device_uuid=device_uuid,
            auth_header=PROXYPASS_AUTH_HEADER,
        )

    def _invoke(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue one container call against this transport's shared resolution.

        Resolution, its TTL and the refresh-and-retry on a rejected token all live
        in :class:`SharedHttpInfo`, which the teclaw filesystem shares.
        """
        return self._http_info.invoke(method, path, **kwargs)

    def post(self, path: str, *, json: Any | None = None) -> httpx.Response:
        return self._invoke("POST", path, json=json)

    def get(self, path: str) -> httpx.Response:
        return self._invoke("GET", path)

    def put(self, path: str, *, json: Any | None = None) -> httpx.Response:
        return self._invoke("PUT", path, json=json)

    def delete(self, path: str) -> httpx.Response:
        return self._invoke("DELETE", path)

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

        Every file in a package upload lands here on the same ``path``, so all of
        them share one cached ``http_info`` resolution.
        """
        return self._invoke("POST", path, files=files, data=data)


class DesktopBaasInvokeTransport:
    """自拼 secbaas transparent proxy URL,直接 httpx POST。

    适用 desktop bot(VM 在用户机器,BaaS get_http_info 返 localhost 拨不通)。

    Requests go through the injected pooled ``client`` (built by
    :func:`build_desktop_client`) so a multi-file write reuses keep-alive
    connections instead of paying a TLS handshake per file.
    """

    def __init__(
        self,
        *,
        baas_base_url: str,
        tenant: str,
        bot_uuid: str,
        engine_port: int,
        headers: dict[str, str],
        client: httpx.Client,
    ):
        self._baas_base_url = baas_base_url
        self._tenant = tenant
        self._bot_uuid = bot_uuid
        self._engine_port = engine_port
        self._headers = headers
        self._client = client

    def post(self, path: str, *, json: Any | None = None) -> httpx.Response:
        return self._client.post(
            self._invoke_url(path), json=json, headers=self._headers
        )

    def get(self, path: str) -> httpx.Response:
        return self._client.get(
            self._invoke_url(path), headers=self._headers
        )

    def put(self, path: str, *, json: Any | None = None) -> httpx.Response:
        return self._client.put(
            self._invoke_url(path), json=json, headers=self._headers
        )

    def delete(self, path: str) -> httpx.Response:
        return self._client.delete(
            self._invoke_url(path), headers=self._headers
        )

    def post_multipart(
        self,
        path: str,
        *,
        files: Any,
        data: Any,
    ) -> httpx.Response:
        return self._client.post(
            self._invoke_url(path), files=files, data=data, headers=self._headers
        )

    def _invoke_url(self, api_path: str) -> str:
        if not api_path.startswith("/"):
            api_path = "/" + api_path
        return (
            f"{self._baas_base_url}/api/v1/bots/{self._tenant}/"
            f"{self._bot_uuid}/invoke-http/{self._engine_port}{api_path}"
        )
