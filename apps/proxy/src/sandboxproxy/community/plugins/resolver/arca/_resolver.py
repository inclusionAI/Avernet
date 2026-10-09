"""ARCA target resolver — resolves ``ARCA_`` targets into a sandbox pod IP.

Target format: ``ARCA_{provider_device_id}[:{port}]``.

The ``provider_device_id`` (e.g. ``ALIYUN_ACK_DEFAULT-46a7115b08ab@0``) is looked
up against the upstream BaaS ``provider_device_props`` to obtain the sandbox
pod's ``ip_addr``. The resolved upstream forwards directly to
``http(s)://<ip_addr>:<port>``, skipping the cluster gateway.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, cast

import httpx

from sandboxproxy.community.config import UserConfig
from sandboxproxy.community.logger import get_logger

logger = get_logger("resolver-arca")

_DEFAULT_PORT = "8080"
_DEFAULT_PROPS_PATH = "/api/v1/devices/provider-device/{provider_device_id}/props"
_DEFAULT_SCHEME = "http"
_CACHE_TTL_SECONDS = 60.0

_CacheEntry = tuple[float, str | None]


class ArcaTargetResolver:
    """Resolve ``ARCA_`` targets into a direct sandbox pod-IP upstream."""

    prefix = "arca"

    def __init__(
        self,
        config: UserConfig | Mapping[str, Any],
        *,
        cache_ttl: float = _CACHE_TTL_SECONDS,
    ) -> None:
        self._config = (
            config
            if isinstance(config, UserConfig)
            else UserConfig.model_validate(config)
        )
        self._cache_ttl = cache_ttl
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, _CacheEntry] = {}

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def resolve(self, target_host: str) -> dict[str, str]:
        if not target_host.startswith("ARCA_"):
            raise ValueError(f"Not an ARCA_ target: {target_host!r}")
        rest = target_host[len("ARCA_") :]
        if not rest:
            raise ValueError("ARCA_ target has no provider device id")

        provider_device_id, sandbox_port = _parse_rest(rest)

        baas_host = self._config.baas.get("host", "")
        if not baas_host:
            raise RuntimeError(
                "baas host is not configured; cannot resolve ARCA target"
            )
        props_path = self._config.baas.get("device_props_path", _DEFAULT_PROPS_PATH)

        ip_addr = await self._lookup_ip(baas_host, props_path, provider_device_id)
        if not ip_addr:
            raise RuntimeError(f"no ip_addr for provider device {provider_device_id!r}")

        scheme = self._config.baas.get("device_scheme", _DEFAULT_SCHEME)

        return {
            "pod_ip": ip_addr,
            "pod_port": sandbox_port,
            "provider_device_id": provider_device_id,
            "pod_scheme": scheme,
        }

    async def _lookup_ip(
        self,
        baas_host: str,
        props_path: str,
        provider_device_id: str,
    ) -> str:
        cached = self._cache.get(provider_device_id)
        if cached is not None:
            timestamp, ip_addr = cached
            if time.monotonic() - timestamp < self._cache_ttl:
                return ip_addr or ""
            del self._cache[provider_device_id]

        if self._client is None:
            await self.start()

        assert self._client is not None
        url = baas_host.rstrip("/") + props_path.format(
            provider_device_id=provider_device_id
        )
        ip_addr = ""
        try:
            resp = await self._client.get(url)
            if resp.status_code < 400:
                try:
                    data = cast(dict[str, Any], resp.json())
                except (ValueError, AttributeError) as exc:
                    logger.warning("provider-device props malformed response: %s", exc)
                    data = {}
                body = cast(dict[str, Any], data.get("data")) or {}
                props = cast(dict[str, Any], body.get("provider_device_props")) or {}
                metadata = cast(dict[str, Any], props.get("metadata")) or {}
                candidate = metadata.get("ip_addr")
                ip_addr = candidate if isinstance(candidate, str) else ""
            else:
                logger.warning(
                    "provider-device props lookup %s returned %s",
                    url,
                    resp.status_code,
                )
        except httpx.HTTPError as exc:
            logger.warning("provider-device props lookup failed: %s", exc)
            raise RuntimeError(
                f"provider-device lookup failed for {provider_device_id!r}"
            ) from exc

        self._cache[provider_device_id] = (time.monotonic(), ip_addr or None)
        return ip_addr


def _parse_rest(rest: str) -> tuple[str, str]:
    """Parse the portion after ``ARCA_`` into ``(provider_device_id, port)``.

    Supported shapes:
        ``ALIYUN_ACK_DEFAULT-abc@0``          → id=..., port=8080
        ``ALIYUN_ACK_DEFAULT-abc@0:9090``     → id=..., port=9090
    """
    provider_device_id, sep, port = rest.partition(":")
    if not provider_device_id:
        raise ValueError("ARCA_ target has no provider device id")
    if sep and not port:
        raise ValueError("ARCA_ target has an empty port")
    sandbox_port = port if sep else _DEFAULT_PORT
    return provider_device_id, sandbox_port
