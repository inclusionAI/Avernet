"""DWS-only proof verification; no signing secret is installed in Bot workloads."""

from urllib.parse import urlsplit

import httpx


class BackendManagementVerifier:
    def __init__(
        self,
        backend_url: str,
        *,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        parsed = urlsplit(backend_url)
        # COSEC: URL comes only from trusted deployment/profile configuration.
        # Require verified TLS except loopback development; never follow redirects
        # or accept a caller-provided destination/verification result.
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or not (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http"
                    and parsed.hostname in ("127.0.0.1", "::1", "localhost")
                )
            )
        ):
            raise ValueError("trusted_management_backend_url_required")
        self._url = backend_url.rstrip("/") + "/internal/dws-hosting/verify-command"
        self._headers = headers
        self._transport = transport

    async def verify(self, payload: str, signature: str) -> bool:
        return await self._check(self._url, "verified", payload, signature)

    async def runtime_eligible(self, payload: str, signature: str) -> bool:
        return await self._check(
            self._url.removesuffix("verify-command") + "runtime-eligibility",
            "allowed",
            payload,
            signature,
        )

    async def _check(self, url, field, payload, signature):
        try:
            async with httpx.AsyncClient(
                timeout=5.0, follow_redirects=False, transport=self._transport
            ) as client:
                response = await client.post(
                    url,
                    headers=self._headers,
                    json={"payload": payload, "signature": signature},
                )
            if response.status_code != 200:
                return False
            result = response.json()
            return (
                isinstance(result, dict)
                and set(result) == {field}
                and result[field] is True
            )
        except (httpx.HTTPError, ValueError):
            return False
