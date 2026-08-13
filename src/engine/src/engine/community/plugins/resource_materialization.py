"""Fail-closed materialization transports used until external contracts bind."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import socket
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import aiofiles
import httpx
from engine.community.core.resource_materialization.models import (
    ChatAttachmentMaterializationRequest,
    MaterializationRequest,
    MaterializationResult,
)

log = logging.getLogger("engine.resource_materialization")


class NotConfiguredBaasMaterializationClient:
    async def pull(
        self,
        request: MaterializationRequest,
        destination: Path,
    ) -> None:
        raise RuntimeError("baas_materialization_not_configured")


class NotConfiguredTemporaryUrlPullClient:
    async def pull(
        self,
        request: ChatAttachmentMaterializationRequest,
        destination: Path,
    ) -> None:
        raise RuntimeError("temporary_url_pull_not_configured")


class SessionFileBaasMaterializationClient:
    """Session File Sharing pull implementation configured by an Engine profile.

    The profile supplies the BaaS control-plane URL, its internal credentials,
    and the allowlisted OSS share-link hosts. The transfer URL is never stored
    and control-plane credentials are deliberately not sent to OSS.
    """

    def __init__(
        self,
        *,
        baas_base_url: str,
        control_headers: Mapping[str, str],
        allowed_share_hosts: frozenset[str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(baas_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("invalid BaaS base URL")
        if not allowed_share_hosts:
            raise ValueError("allowed_share_hosts is required")
        self._baas_base_url = baas_base_url.rstrip("/")
        self._control_headers = dict(control_headers)
        self._allowed_share_hosts = {host.lower() for host in allowed_share_hosts}
        self._transport = transport

    async def pull(
        self,
        request: MaterializationRequest,
        destination: Path,
    ) -> None:
        if request.transfer_api_version != "session_v2":
            raise RuntimeError("legacy_materialization_client_required")
        if not request.tenant or not request.session_id:
            raise ValueError("session_v2 request is missing BaaS identity")
        log.info(
            "engine.resource_materialize.share_link.pull.start resource_id=%s transfer_hash=%s",
            request.resource_id,
            self._transfer_hash(request.transfer_id),
        )
        share_url = await self._create_share_link(request)
        self._validate_share_url(share_url)
        # COSEC: OSS is a separately authenticated presigned-URL hop. A fresh
        # client prevents BaaS internal credentials from crossing that boundary.
        timeout = httpx.Timeout(connect=10.0, read=3600.0, write=30.0, pool=30.0)
        async with (
            httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                transport=self._transport,
            ) as client,
            client.stream("GET", share_url) as response,
        ):
            response.raise_for_status()
            async with aiofiles.open(destination, "wb") as stream:
                async for chunk in response.aiter_bytes():
                    await stream.write(chunk)
        log.info(
            "engine.resource_materialize.share_link.pull.done resource_id=%s transfer_hash=%s",
            request.resource_id,
            self._transfer_hash(request.transfer_id),
        )

    async def _create_share_link(self, request: MaterializationRequest) -> str:
        if not request.tenant or not request.session_id:
            raise ValueError("session_v2 request is missing BaaS identity")
        path = "/api/v1/sessions/{}/{}/files/transfers/{}/share-link".format(
            quote(request.tenant, safe=""),
            quote(request.session_id, safe=""),
            quote(request.transfer_id, safe=""),
        )
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)
        async with httpx.AsyncClient(
            base_url=self._baas_base_url,
            headers=self._control_headers,
            timeout=timeout,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            response = await client.post(
                path,
                json={"expire_seconds": 600, "show": False, "operator": "engine"},
            )
        response.raise_for_status()
        payload = response.json()
        if (
            not isinstance(payload, dict)
            or payload.get("code") not in {None, 0}
            or not isinstance(payload.get("data"), dict)
        ):
            raise ValueError("invalid Session File share-link response")
        share_url = payload["data"].get("share_url")
        if not isinstance(share_url, str) or not share_url:
            raise ValueError("Session File share-link response is missing share_url")
        return share_url

    def _validate_share_url(self, share_url: str) -> None:
        parsed = urlsplit(share_url)
        host = parsed.hostname.lower() if parsed.hostname else ""
        # COSEC: the BaaS-issued URL may use an OSS host, but that host must be
        # profile-allowlisted and redirects are disabled before any GET occurs.
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or host not in self._allowed_share_hosts
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("untrusted Session File share-link URL")

    @staticmethod
    def _transfer_hash(transfer_id: str) -> str:
        return hashlib.sha256(transfer_id.encode("utf-8")).hexdigest()[:16]


class HttpTemporaryUrlPullClient:
    """Guarded downloader for provider-issued, short-lived chat URLs."""

    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str],
        max_bytes: int = 100 * 1024 * 1024,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not allowed_hosts:
            raise ValueError("temporary URL allowed_hosts is required")
        if max_bytes <= 0 or timeout_seconds <= 0:
            raise ValueError("temporary URL limits must be positive")
        self._allowed_hosts = {
            host.strip().lower() for host in allowed_hosts if host.strip()
        }
        if not self._allowed_hosts:
            raise ValueError("temporary URL allowed_hosts is required")
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def pull(
        self,
        request: ChatAttachmentMaterializationRequest,
        destination: Path,
    ) -> None:
        host, port = self._validate_url(request.temporary_url)
        resolved_ips = await self._resolve_public_ips(host, port)
        pinned_ip = min(resolved_ips)
        parsed_url = urlsplit(request.temporary_url)
        pinned_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        host_header = parsed_url.netloc
        pinned_url = urlunsplit(
            (
                parsed_url.scheme,
                f"{pinned_host}:{port}",
                parsed_url.path,
                parsed_url.query,
                "",
            )
        )
        timeout = httpx.Timeout(
            connect=min(self._timeout_seconds, 10.0),
            read=self._timeout_seconds,
            write=30.0,
            pool=30.0,
        )
        # COSEC: connect to the already-validated public IP so DNS cannot be
        # rebound between validation and the first request byte. Host and SNI
        # retain the allowlisted hostname for HTTP routing and certificate checks.
        async with (
            httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                transport=self._transport,
            ) as client,
            client.stream(
                "GET",
                pinned_url,
                headers={"host": host_header},
                extensions={"sni_hostname": host},
            ) as response,
        ):
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise ValueError("invalid temporary URL content length") from exc
                if declared_size > self._max_bytes:
                    raise ValueError("temporary URL response exceeds size limit")
            observed = 0
            async with aiofiles.open(destination, "wb") as stream:
                async for chunk in response.aiter_bytes():
                    observed += len(chunk)
                    if observed > self._max_bytes:
                        raise ValueError("temporary URL response exceeds size limit")
                    await stream.write(chunk)

    def _validate_url(self, value: str) -> tuple[str, int]:
        parsed = urlsplit(value)
        host = parsed.hostname.lower() if parsed.hostname else ""
        if (
            parsed.scheme != "https"
            or not host
            or host not in self._allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("untrusted temporary URL")
        return host, parsed.port or 443

    @staticmethod
    async def _resolve_public_ips(host: str, port: int) -> frozenset[str]:
        try:
            addresses = await asyncio.to_thread(
                socket.getaddrinfo,
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ValueError("temporary URL host could not be resolved") from exc
        if not addresses:
            raise ValueError("temporary URL host could not be resolved")
        resolved: set[str] = set()
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                raise ValueError("temporary URL host resolved to a non-public address")
            resolved.add(str(ip))
        return frozenset(resolved)


class NotConfiguredBackendMaterializationCallbackClient:
    async def report(self, result: MaterializationResult) -> None:
        raise RuntimeError("backend_materialization_callback_not_configured")
