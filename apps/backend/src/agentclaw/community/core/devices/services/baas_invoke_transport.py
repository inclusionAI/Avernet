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

import threading
import time
from abc import abstractmethod
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import httpx

from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.services.baas_service import (
        BaasService,
        HttpConnectionInfo,
    )

logger = get_logger()


# BaaS mints ``http_url`` + ``token`` per (bind_id, port, path). A package upload
# POSTs every file to the SAME path (``/api/file/upload``), so one resolution serves
# the whole batch; resolving per file cost a binding DB lookup plus a BaaS round trip
# each, doubling the request count of every multi-file write. This TTL keeps a reused
# token comfortably inside its lifetime, and a token that expires anyway is recovered
# by the refresh-and-retry in :meth:`BaasInvokeTransport._invoke`.
_HTTP_INFO_TTL_SECONDS = 30.0

# The proxypass gateway answers 401 when it rejects the token it was handed — the one
# verdict that means "this cached http_info is no longer usable".
#
# 403 is deliberately excluded. It does not identify a token problem: the engine's file
# router maps ``PermissionError`` to 403 (``engine/community/api/file/router.py``) and
# the proxy forwards upstream responses through unchanged
# (``sandboxproxy/community/adapters/web/routes.py``), so a 403 is the device's own
# authorization answer. Replaying it would re-run a mutating upload/delete and, with no
# ``device_uuid`` pinning the instance, possibly run it against a *different* device —
# while hiding the engine's actual verdict from the caller.
_STALE_TOKEN_STATUS = 401

# The proxypass gateway answers one of these when its routing *target* cannot be
# reached — the sandbox gateway fails the connect (e.g. an ARCA sandbox whose sync
# channel is dead answers 502 immediately, with no downstream span) before any
# engine code runs. That verdict is about the selected device, not about the
# request: replaying the same read against another ACTIVE instance of the same bot
# is the same read. Only callers that know the operation is a side-effect-free
# read may explicitly opt in to rotation; the transport never infers this from
# the endpoint path or HTTP verb.
_GATEWAY_UNAVAILABLE_STATUS = frozenset({502, 503, 504})


def _paas_device_id_from_target(target: str) -> str | None:
    """``{TYPE}_{paas_device_id}:{port}`` → ``paas_device_id``.

    An empty or unshaped target (the secbaas transparent-proxy URL style carries
    none) returns ``None`` — without knowing which instance failed, a rotation
    could pick that very instance again, so it is skipped instead.
    """
    if not target:
        return None
    _, _, paas_device_id = target.rsplit(":", 1)[0].partition("_")
    return paas_device_id or None


def is_not_directory_error(exc: Exception) -> bool:
    """Classify Engine's ``list(file_path)`` transport verdict.

    The Engine file API maps ``NotADirectoryError`` to HTTP 400. Keeping that
    translation here lets filesystem behavior avoid inspecting status codes while
    preserving the original exception for callers that need to propagate it.
    """
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code == 400
    )


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

    @abstractmethod
    def post(
        self, path: str, *, json: Any | None = None, allow_device_failover: bool = False
    ) -> httpx.Response:
        """POST; opt in only for a side-effect-free read safe on another device."""
        ...

    @abstractmethod
    def post_multipart(
        self, path: str, *, files: Any, data: Any
    ) -> httpx.Response: ...


class BaasInvokeTransport(BaasTransport):
    """通过 BaaSService.invoke_http(get_http_info → http_url → POST)。

    适用云上 baas 容器(未来 personal+baas / service+baas)。

    Resolution (``get_http_info``) is cached per path for the life of this
    instance — see :meth:`_http_info`. The resolver builds one transport per
    dispatch, so that lifetime is a single request and there is no cross-request
    staleness to reason about.
    """

    def __init__(
        self,
        *,
        bind_id: int,
        engine_port: int,
        tenant: str,
        baas_service: "BaasService",
        device_uuid: str | None = None,
        bot_uuid: str | None = None,
    ):
        self._bind_id = bind_id
        self._engine_port = engine_port
        self._tenant = tenant
        self._baas_service = baas_service
        # 多实例 service bot 场景，caller 通过 device_uuid 锁定具体实例；透传给
        # invoke_http → get_http_info → BaaS /http-info?device_uuid=。``None`` 表示
        # 单实例 / 未指定，走 BaaS 自动选活跃实例的老行为。
        self._device_uuid = device_uuid
        # Read failover only: the BaaS bot the binding resolves to — the key
        # ``list_devices_by_bot_uuid`` needs to find another ACTIVE instance to
        # rotate to when the gateway says the selected one is unreachable. Callers
        # that cannot name a BaaS bot (e.g. teclaw, whose ``paas_device_id`` is not
        # a bot uuid) pass ``None`` and failover stays disabled.
        self._bot_uuid = bot_uuid
        # Guards the cache against the worker threads ``asyncio.to_thread`` fans
        # device I/O out across. Deliberately held across the ``get_http_info``
        # call itself, so a batch of concurrent writes coalesces onto ONE
        # resolution instead of every thread missing the cache and resolving its
        # own — the whole point of caching here.
        self._http_info_lock = threading.Lock()
        self._http_info_cache: dict[str, tuple[float, "HttpConnectionInfo"]] = {}

    def _http_info(
        self, path: str, *, stale: "HttpConnectionInfo | None" = None
    ) -> "HttpConnectionInfo":
        """Resolve ``http_info`` for ``path``, reusing a fresh cached one.

        Keyed by ``path`` alone because every other input BaaS resolves against
        (``bind_id`` / ``engine_port`` / ``tenant`` / ``device_uuid``) is fixed for
        this instance. Path must stay in the key: the returned ``http_url`` embeds
        it, so reusing one path's entry for another would POST to the wrong route.

        ``stale`` names the entry the caller was just rejected on, which makes a
        refresh a compare-and-swap: it re-resolves only while the cache still holds
        that exact entry, and otherwise hands back whatever replaced it. An
        unconditional refresh would resolve once per in-flight request when a
        concurrent batch is rejected together — reinstating, on the failure path,
        the very per-file fan-out this cache exists to remove.
        """
        with self._http_info_lock:
            cached = self._http_info_cache.get(path)
            if (
                cached is not None
                and cached[0] > time.monotonic()
                and cached[1] is not stale
            ):
                return cached[1]
            info = self._baas_service.get_http_info(
                bind_id=self._bind_id,
                port=self._engine_port,
                path=path,
                tenant=self._tenant,
                device_uuid=self._device_uuid,
            )
            self._http_info_cache[path] = (
                time.monotonic() + _HTTP_INFO_TTL_SECONDS,
                info,
            )
            return info

    def _http_info_for_device(
        self,
        path: str,
        *,
        stale: "HttpConnectionInfo",
        device_uuid: str,
    ) -> "HttpConnectionInfo":
        """Resolve ``http_info`` for ``path`` pinned to one specific device.

        The failover path needs its own resolve because :meth:`_http_info`
        always resolves with this transport's fixed ``device_uuid``, while a
        rotation hands the failed read to a *different* instance of the same
        bot. Shares the same cache slot and the same compare-and-swap
        discipline (``stale`` must name the entry the caller just saw fail), so
        a batch of concurrent readers rejected together coalesces onto one
        rotation instead of each rotating on its own.
        """
        with self._http_info_lock:
            cached = self._http_info_cache.get(path)
            if (
                cached is not None
                and cached[0] > time.monotonic()
                and cached[1] is not stale
            ):
                return cached[1]
            info = self._baas_service.get_http_info(
                bind_id=self._bind_id,
                port=self._engine_port,
                path=path,
                tenant=self._tenant,
                device_uuid=device_uuid,
            )
            self._http_info_cache[path] = (
                time.monotonic() + _HTTP_INFO_TTL_SECONDS,
                info,
            )
            return info

    def _alternate_device_uuid(
        self, failed_paas_device_id: str | None
    ) -> str | None:
        """Another ACTIVE BaaS instance of this bot, excluding the failed one.

        Listing devices costs one BaaS round trip, paid only on the failure
        path. Any problem (no bot uuid, list failure, no surviving instance)
        means "no rotation", and the caller keeps the gateway's own verdict.
        """
        if not self._bot_uuid:
            return None
        try:
            devices = self._baas_service.list_devices_by_bot_uuid(
                self._bot_uuid, tenant=self._tenant
            )
        except Exception as e:
            logger.warning(
                "[BaasInvokeTransport] read failover skipped: listing devices "
                "of bot %s failed: %s",
                self._bot_uuid,
                e,
            )
            return None
        for device in devices:
            if not isinstance(device, dict):
                continue
            # Same case-normalized status read as ``baas_device_header_updater``:
            # a BaaS reply whose casing varies must not silently disable rotation.
            if str(device.get("status", "")).upper() != "ACTIVE":
                continue
            provider_device_id = device.get("provider_device_id")
            device_uuid = device.get("device_uuid")
            if not provider_device_id or not device_uuid:
                continue
            if (
                failed_paas_device_id
                and provider_device_id == failed_paas_device_id
            ):
                continue
            return device_uuid
        return None

    def _rotate_once(
        self,
        path: str,
        call: dict[str, Any],
        failed_info: "HttpConnectionInfo",
        failed_response: httpx.Response,
    ) -> httpx.Response:
        """One bounded hand-off of a gateway-unavailable read to another instance.

        Bounded to a single attempt; every miss (unknown failed instance, no
        other ACTIVE device, resolution error) returns the original response
        so the caller still observes the gateway's verdict. Rotation is
        disabled when the caller pinned a ``device_uuid``: an explicitly
        targeted instance is a caller decision this transport must not
        second-guess.
        """
        target = getattr(failed_info, "target", None)
        failed_paas_device_id = (
            _paas_device_id_from_target(target)
            if isinstance(target, str)
            else None
        )
        if not failed_paas_device_id:
            # Nothing to exclude, so a "rotation" could land right back on the
            # same dead instance — a wasted duplicate call, not a recovery.
            return failed_response
        alternate = self._alternate_device_uuid(failed_paas_device_id)
        if not alternate:
            return failed_response
        try:
            alt_info = self._http_info_for_device(
                path, stale=failed_info, device_uuid=alternate
            )
        except Exception as e:
            logger.warning(
                "[BaasInvokeTransport] read failover skipped: resolving %s "
                "on device %s failed: %s",
                path,
                alternate,
                e,
            )
            return failed_response
        response = self._baas_service.invoke_http(
            **dict(call, device_uuid=alternate), http_info=alt_info
        )
        logger.info(
            "[BaasInvokeTransport] read failover: %s answered HTTP %d on "
            "device %s, retried once on device_uuid=%s -> HTTP %d",
            path,
            failed_response.status_code,
            failed_paas_device_id,
            alternate,
            response.status_code,
        )
        return response

    def _invoke(
        self, method: str, path: str, *, allow_device_failover: bool = False, **kwargs: Any
    ) -> httpx.Response:
        """Issue one container call against this transport's cached ``http_info``.

        Retries once against a re-resolved ``http_info`` when the gateway answers
        401: a cached token can expire (or its device be reallocated) mid-batch, and
        a caller must never see a spurious auth failure caused by this cache
        choosing to reuse a token. Replay is safe because every payload we send is
        already materialised (``json`` dicts, ``files`` bytes) rather than a consumed
        stream. A genuine misconfiguration simply fails twice.

        Every other status — 403 included, see :data:`_STALE_TOKEN_STATUS` — is the
        device's own answer and is returned untouched, with one exception: a
        gateway-unavailable 502/503/504 on an opted-in, auto-selected read
        rotates once to another ACTIVE instance (see :meth:`_rotate_once`).
        """
        call = dict(
            bind_id=self._bind_id,
            port=self._engine_port,
            path=path,
            method=method,
            tenant=self._tenant,
            # 云上 baas 容器经 agentclawproxy ``/proxypass`` 网关访问，网关用
            # ``x-proxypass-token`` 鉴权（与 teclaw/arca 同一网关）；不传则默认
            # ``openclawToken``，proxypass 网关会 401。``info.token`` 即网关 token，
            # 仅 header 名不同。
            auth_header="x-proxypass-token",
            device_uuid=self._device_uuid,
            **kwargs,
        )
        info = self._http_info(path)
        response = self._baas_service.invoke_http(**call, http_info=info)
        if response.status_code == _STALE_TOKEN_STATUS:
            return self._baas_service.invoke_http(
                **call, http_info=self._http_info(path, stale=info)
            )
        if (
            response.status_code in _GATEWAY_UNAVAILABLE_STATUS
            and allow_device_failover
            and self._bot_uuid
            and self._device_uuid is None
        ):
            return self._rotate_once(path, call, info, response)
        return response

    def post(
        self, path: str, *, json: Any | None = None, allow_device_failover: bool = False
    ) -> httpx.Response:
        return self._invoke(
            "POST", path, json=json, allow_device_failover=allow_device_failover
        )

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


class DesktopBaasInvokeTransport(BaasTransport):
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

    def post(
        self, path: str, *, json: Any | None = None, allow_device_failover: bool = False
    ) -> httpx.Response:
        # Desktop has no alternate BaaS device selection.
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
