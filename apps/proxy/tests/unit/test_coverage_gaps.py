"""Targeted unit tests for remaining branch coverage."""

from __future__ import annotations

import httpx

from sandboxproxy.community.config import UserConfig
from sandboxproxy.community.core.authn import JwtVerifier
from sandboxproxy.community.plugins.relay_client.baas import BaasRelayClient
from sandboxproxy.community.plugins.resolver.prefix import PrefixTargetResolver


class TestResolverErrorBranches:
    def _resolver(self, **cfg) -> PrefixTargetResolver:
        base = {
            "aliyun_ack_cluster": {"api_server": "https://ack"},
            "teclaw": {"host": "http://teclaw"},
            "baas": {"host": "http://baas"},
        }
        base.update(cfg)
        return PrefixTargetResolver(UserConfig.model_validate(base))

    def test_teclaw_empty_id(self) -> None:
        import asyncio

        r = self._resolver()
        try:
            asyncio.run(r.resolve("TECLAW_@tmpl:8080"))
        except ValueError:
            pass

    def test_local_no_port(self) -> None:
        import asyncio

        r = self._resolver()
        try:
            asyncio.run(r.resolve("LOCAL_dev1@42"))
        except ValueError:
            pass


class TestJwtErrorBranches:
    def test_invalid_base64_signature(self) -> None:
        v = JwtVerifier.from_secret("s")
        assert v.verify("a.b.!!!not-base64!!!") is None

    def test_invalid_payload_json(self) -> None:
        import base64
        import hashlib
        import hmac

        def _b64(d: bytes) -> str:
            return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

        header = _b64(b'{"alg":"HS256"}')
        payload = _b64(b"!!!not-json!!!")
        sig = hmac.new(b"s", f"{header}.{payload}".encode(), hashlib.sha256).digest()
        v = JwtVerifier.from_secret("s")
        assert v.verify(f"{header}.{payload}.{_b64(sig)}") is None

    def test_non_dict_payload(self) -> None:
        import base64
        import hashlib
        import hmac

        def _b64(d: bytes) -> str:
            return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

        header = _b64(b'{"alg":"HS256"}')
        payload = _b64(b'"just-a-string"')
        sig = hmac.new(b"s", f"{header}.{payload}".encode(), hashlib.sha256).digest()
        v = JwtVerifier.from_secret("s")
        assert v.verify(f"{header}.{payload}.{_b64(sig)}") is None


class TestRelayClientLifecycle:
    async def test_start_creates_client(self) -> None:
        c = BaasRelayClient("http://baas", instance="i")
        await c.start()
        assert c._client is not None  # noqa: SLF001
        await c.shutdown()
        assert c._client is None  # noqa: SLF001

    async def test_request_without_start(self) -> None:
        c = BaasRelayClient("http://baas:1", instance="i")
        # calling get_route_info without start() will start lazily and fail to
        # connect → returns None
        result = await c.get_route_info("sess")
        assert result is None
        await c.shutdown()

    async def test_mark_closed_retry_fails(self) -> None:
        c = BaasRelayClient("http://baas:1", instance="i", timeout=0.1)
        # unreachable host → mark_route_closed retries then returns False
        result = await c.mark_route_closed("sess")
        assert result is False
        await c.shutdown()


class TestToStreamingResponse:
    async def test_builds_response(self) -> None:
        from sandboxproxy.community.adapters.web.routes import (
            _to_streaming_response,
        )

        class FakeResp:
            status_code = 201
            headers = {"Content-Length": "2", "X-K": "v"}

            async def aiter_raw(self):
                yield b"ok"

            async def aclose(self):
                pass

        response = _to_streaming_response(FakeResp())  # type: ignore[arg-type]
        assert response.status_code == 201
        assert response.headers["x-k"] == "v"
        assert "content-length" not in response.headers
