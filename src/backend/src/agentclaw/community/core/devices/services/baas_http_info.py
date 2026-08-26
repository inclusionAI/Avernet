"""Shared BaaS ``get_http_info`` resolution for the per-file device transports.

Every device transport that reaches a container through ``BaasService`` resolves
an ``http_url`` + token before each call. That resolution costs a binding DB
lookup plus a BaaS ``/http-info`` round trip, and the transports issue their
calls **per file** — so a package of N files paid N resolutions on top of its N
writes.

The batching those callers now do makes that worse rather than better: the files
go out concurrently, so the redundant resolutions arrive as a burst instead of a
queue. Sharing one resolution across a batch is what turns the fan-out into an
actual saving.

This module owns that sharing once, for both transports that need it:

- :class:`~agentclaw.community.core.devices.services.baas_invoke_transport.BaasInvokeTransport`
  — cloud baas containers;
- :class:`~agentclaw.community.core.devices.services.teclaw_device_filesystem.TeclawDeviceFileSystem`
  — teclaw containers, which call ``invoke_http`` directly rather than through a
  transport object.

Both reach their container through the **agentclawproxy** ``/proxypass`` gateway
and authenticate with ``x-proxypass-token``, so the header default lives here too.
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.services.baas_service import (
        BaasService,
        HttpConnectionInfo,
    )


# BaaS mints ``http_url`` + ``token`` per (bind_id, port, path). A package upload
# POSTs every file to the SAME path (``/api/file/upload``), so one resolution serves
# the whole batch; resolving per file cost a binding DB lookup plus a BaaS round trip
# each, doubling the request count of every multi-file write. This TTL keeps a reused
# token comfortably inside its lifetime, and a token that expires anyway is recovered
# by the refresh-and-retry in :meth:`SharedHttpInfo.invoke`.
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

#: The agentclawproxy ``/proxypass`` gateway authenticates with this header, not the
#: secbaas invoke-http tunnel's ``openclawToken`` (which it answers 401 to).
PROXYPASS_AUTH_HEADER = "x-proxypass-token"


class SharedHttpInfo:
    """One ``get_http_info`` resolution per path, reused by every call on an owner.

    The owner supplies the resolution inputs that are fixed for its lifetime
    (``bind_id`` / ``engine_port`` / ``tenant`` / ``device_uuid``); only ``path``
    varies per call, so only ``path`` is in the cache key.

    Instances are built per device-filesystem dispatch, and a dispatch serves one
    request, so a cached entry never outlives the request that resolved it — there
    is no cross-request staleness to reason about.
    """

    def __init__(
        self,
        *,
        baas_service: "BaasService",
        bind_id: int,
        engine_port: int,
        tenant: str,
        device_uuid: str | None = None,
        auth_header: str = PROXYPASS_AUTH_HEADER,
    ) -> None:
        self._baas_service = baas_service
        self._bind_id = bind_id
        self._engine_port = engine_port
        self._tenant = tenant
        # 多实例 service bot 场景，caller 通过 device_uuid 锁定具体实例；透传给
        # invoke_http → get_http_info → BaaS /http-info?device_uuid=。``None`` 表示
        # 单实例 / 未指定，走 BaaS 自动选活跃实例的老行为。
        self._device_uuid = device_uuid
        self._auth_header = auth_header
        # Guards the cache against the worker threads ``asyncio.to_thread`` fans
        # device I/O out across. Deliberately held across the ``get_http_info``
        # call itself, so a batch of concurrent writes coalesces onto ONE
        # resolution instead of every thread missing the cache and resolving its
        # own — the whole point of caching here.
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, "HttpConnectionInfo"]] = {}

    def resolve(
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
        with self._lock:
            cached = self._cache.get(path)
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
            self._cache[path] = (time.monotonic() + _HTTP_INFO_TTL_SECONDS, info)
            return info

    def invoke(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue one container call against this owner's cached ``http_info``.

        Retries once against a re-resolved ``http_info`` when the gateway answers
        401: a cached token can expire (or its device be reallocated) mid-batch, and
        a caller must never see a spurious auth failure caused by this cache
        choosing to reuse a token. Replay is safe because every payload we send is
        already materialised (``json`` dicts, ``files`` bytes) rather than a consumed
        stream. A genuine misconfiguration simply fails twice.

        Every other status — 403 included, see :data:`_STALE_TOKEN_STATUS` — is the
        device's own answer and is returned untouched.
        """
        call = dict(
            bind_id=self._bind_id,
            port=self._engine_port,
            path=path,
            method=method,
            tenant=self._tenant,
            auth_header=self._auth_header,
            device_uuid=self._device_uuid,
            **kwargs,
        )
        info = self.resolve(path)
        response = self._baas_service.invoke_http(**call, http_info=info)
        if response.status_code == _STALE_TOKEN_STATUS:
            return self._baas_service.invoke_http(
                **call, http_info=self.resolve(path, stale=info)
            )
        return response


__all__ = [
    "PROXYPASS_AUTH_HEADER",
    "SharedHttpInfo",
]
