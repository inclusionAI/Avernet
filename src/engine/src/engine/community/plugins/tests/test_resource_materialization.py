from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from engine.community.core.resource_materialization.models import (
    ChatAttachmentMaterializationRequest,
    MaterializationRequest,
)
from engine.community.plugins.resource_materialization import (
    HttpTemporaryUrlPullClient,
    SessionFileBaasMaterializationClient,
)


def _request(**overrides) -> MaterializationRequest:
    values = {
        "resource_id": "sr_001",
        "transfer_id": "transfer-001",
        "task_id": "task-001",
        "task_version": 1,
        "scope_key_hash": "scope_abc",
        "session_key_hash": "session_abc",
        "transfer_api_version": "session_v2",
        "tenant": "team_claw",
        "session_id": "session/value",
        "workspace_relative_path": (
            ".teamclaw/session-files/scope_abc/session_abc/sr_001/report.txt"
        ),
        "filename": "report.txt",
    }
    values.update(overrides)
    return MaterializationRequest(**values)


@pytest.mark.asyncio
async def test_session_pull_uses_share_link_and_never_forwards_control_headers(
    tmp_path: Path,
):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "baas.example":
            assert request.headers["x-control-token"] == "control-secret"
            assert "/sessions/team_claw/" in request.url.path
            assert request.url.path.endswith("/transfers/transfer-001/share-link")
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "share_url": "https://oss.example/object?signature=redacted"
                    },
                },
            )
        assert request.url.host == "oss.example"
        assert "x-control-token" not in request.headers
        return httpx.Response(200, content=b"materialized bytes")

    client = SessionFileBaasMaterializationClient(
        baas_base_url="https://baas.example",
        control_headers={"x-control-token": "control-secret"},
        allowed_share_hosts=frozenset({"oss.example"}),
        transport=httpx.MockTransport(handler),
    )
    destination = tmp_path / "download.part"

    await client.pull(_request(), destination)

    assert destination.read_bytes() == b"materialized bytes"
    assert [request.method for request in requests] == ["POST", "GET"]


@pytest.mark.asyncio
async def test_session_pull_rejects_non_allowlisted_share_host_before_download(
    tmp_path: Path,
):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"code": 0, "data": {"share_url": "https://blocked.example/object"}},
        )

    client = SessionFileBaasMaterializationClient(
        baas_base_url="https://baas.example",
        control_headers={},
        allowed_share_hosts=frozenset({"oss.example"}),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ValueError, match="untrusted Session File share-link URL"):
        await client.pull(_request(), tmp_path / "download.part")

    assert [request.method for request in requests] == ["POST"]


@pytest.mark.asyncio
async def test_temporary_url_pull_checks_host_dns_and_size(tmp_path: Path):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "files.example"
        assert request.extensions["sni_hostname"] == "files.example"
        return httpx.Response(200, content=b"file-bytes")

    client = HttpTemporaryUrlPullClient(
        max_bytes=32,
        transport=httpx.MockTransport(handler),
    )
    request = ChatAttachmentMaterializationRequest(
        attachment_id="att-1",
        session_key="session-1",
        filename="file.txt",
        temporary_url="https://files.example/object?token=secret",
        scope_key_hash="a" * 64,
    )
    destination = tmp_path / "file.part"

    with patch(
        "engine.community.plugins.resource_materialization.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    ):
        await client.pull(request, destination)

    assert destination.read_bytes() == b"file-bytes"


@pytest.mark.asyncio
async def test_temporary_url_pull_pins_validated_ip_before_request(tmp_path: Path):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"safe")

    client = HttpTemporaryUrlPullClient(
        transport=httpx.MockTransport(handler),
    )
    with patch(
        "engine.community.plugins.resource_materialization.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    ) as resolve:
        await client.pull(_chat_request(), tmp_path / "file.part")

    resolve.assert_called_once()
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "files.example"
    assert requests[0].extensions["sni_hostname"] == "files.example"


@pytest.mark.asyncio
async def test_temporary_url_pull_accepts_any_public_https_host(tmp_path: Path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"file")

    client = HttpTemporaryUrlPullClient(
        transport=httpx.MockTransport(handler),
    )
    request = ChatAttachmentMaterializationRequest(
        attachment_id="att-1",
        session_key="session-1",
        filename="file.txt",
        temporary_url="https://another-public.example/object",
        scope_key_hash="a" * 64,
    )

    with patch(
        "engine.community.plugins.resource_materialization.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    ):
        await client.pull(request, tmp_path / "file.part")

    assert len(requests) == 1
    assert requests[0].headers["host"] == "another-public.example"
    assert (tmp_path / "file.part").read_bytes() == b"file"


@pytest.mark.asyncio
async def test_temporary_url_pull_accepts_public_http_host_on_port_80(tmp_path: Path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"file")

    client = HttpTemporaryUrlPullClient(
        transport=httpx.MockTransport(handler),
    )
    request = ChatAttachmentMaterializationRequest(
        attachment_id="att-1",
        session_key="session-1",
        filename="file.txt",
        temporary_url="http://files.example/object?token=secret",
        scope_key_hash="a" * 64,
    )

    with patch(
        "engine.community.plugins.resource_materialization.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 80))],
    ) as resolve:
        await client.pull(request, tmp_path / "file.part")

    resolve.assert_called_once_with("files.example", 80, type=1)
    assert len(requests) == 1
    assert requests[0].url.scheme == "http"
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "files.example"
    assert (tmp_path / "file.part").read_bytes() == b"file"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"max_bytes": 0}, "limits must be positive"),
        (
            {
                "timeout_seconds": 0,
            },
            "limits must be positive",
        ),
    ],
)
def test_temporary_url_pull_rejects_invalid_configuration(kwargs, message):
    with pytest.raises(ValueError, match=message):
        HttpTemporaryUrlPullClient(**kwargs)


def _chat_request() -> ChatAttachmentMaterializationRequest:
    return ChatAttachmentMaterializationRequest(
        attachment_id="att-1",
        session_key="session-1",
        filename="file.txt",
        temporary_url="https://files.example/object?token=secret",
        scope_key_hash="a" * 64,
    )


@pytest.mark.parametrize(
    "temporary_url",
    [
        "ftp://files.example/object",
        "http://user@files.example/object",
        "https://user:password@files.example/object",
    ],
)
def test_chat_attachment_rejects_unsupported_or_userinfo_url(
    temporary_url: str,
) -> None:
    with pytest.raises(ValueError, match="HTTP or HTTPS URL without userinfo"):
        ChatAttachmentMaterializationRequest(
            attachment_id="att-1",
            session_key="session-1",
            filename="file.txt",
            temporary_url=temporary_url,
            scope_key_hash="a" * 64,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_length, content, message",
    [
        ("invalid", b"file", "invalid temporary URL content length"),
        ("5", b"file!", "temporary URL response exceeds size limit"),
        (None, b"file!", "temporary URL response exceeds size limit"),
    ],
)
async def test_temporary_url_pull_enforces_declared_and_observed_size(
    tmp_path: Path,
    content_length: str | None,
    content: bytes,
    message: str,
):
    headers = {"content-length": content_length} if content_length is not None else {}
    client = HttpTemporaryUrlPullClient(
        max_bytes=4,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers=headers, content=content)
        ),
    )

    with (
        patch(
            "engine.community.plugins.resource_materialization.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ),
        pytest.raises(ValueError, match=message),
    ):
        await client.pull(_chat_request(), tmp_path / "file.part")


@pytest.mark.asyncio
async def test_temporary_url_pull_enforces_request_specific_lower_limit(
    tmp_path: Path,
):
    client = HttpTemporaryUrlPullClient(
        max_bytes=32,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"five!")
        ),
    )
    request = _chat_request().model_copy(update={"download_max_bytes": 4})

    with (
        patch(
            "engine.community.plugins.resource_materialization.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ),
        pytest.raises(ValueError, match="exceeds size limit"),
    ):
        await client.pull(request, tmp_path / "image.part")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses, side_effect, message",
    [
        (None, OSError("dns failed"), "host could not be resolved"),
        ([], None, "host could not be resolved"),
        ([(2, 1, 6, "", ("127.0.0.1", 443))], None, "non-public address"),
    ],
)
async def test_temporary_url_pull_rejects_unverifiable_or_private_dns(
    addresses,
    side_effect,
    message,
):
    client = HttpTemporaryUrlPullClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )

    with (
        patch(
            "engine.community.plugins.resource_materialization.socket.getaddrinfo",
            return_value=addresses,
            side_effect=side_effect,
        ),
        pytest.raises(ValueError, match=message),
    ):
        await client.pull(_chat_request(), Path("unused.part"))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "location", ["https://storage.example/image?sig=test", "/image?sig=test"]
)
async def test_temporary_url_redirect_downloads_with_fresh_pinned_request(
    tmp_path: Path, status: int, location: str
):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                status,
                headers={"location": location, "set-cookie": "session=private; Path=/"},
            )
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
        assert request.url.path == "/image"
        assert request.url.query == b"sig=test"
        assert request.url.host == "93.184.216.35"
        expected_host = (
            "storage.example" if location.startswith("https:") else "files.example"
        )
        assert request.headers["host"] == expected_host
        assert request.extensions["sni_hostname"] == expected_host
        return httpx.Response(200, content=b"image bytes")

    client = HttpTemporaryUrlPullClient(transport=httpx.MockTransport(handler))
    destination = tmp_path / "image.part"
    with patch(
        "engine.community.plugins.resource_materialization.socket.getaddrinfo",
        side_effect=[
            [(2, 1, 6, "", (ip, 443))] for ip in ["93.184.216.34", "93.184.216.35"]
        ],
    ) as resolve:
        await client.pull(_chat_request(), destination)
    assert resolve.call_count == 2
    assert len(requests) == 2
    assert destination.read_bytes() == b"image bytes"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location,reason",
    [
        ("", "missing Location"),
        ("http://files.example/image", "downgrade HTTPS"),
        ("ftp://files.example/image", "untrusted temporary URL"),
        ("https://user:password@files.example/image", "untrusted temporary URL"),
    ],
)
async def test_temporary_url_rejects_unsafe_redirect_before_request(
    tmp_path, location, reason
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(303, headers={"location": location})

    client = HttpTemporaryUrlPullClient(transport=httpx.MockTransport(handler))
    with (
        patch(
            "engine.community.plugins.resource_materialization.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ),
        pytest.raises(ValueError, match=reason),
    ):
        await client.pull(_chat_request(), tmp_path / "image.part")
    assert len(requests) == 1
    assert not (tmp_path / "image.part").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "private_ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"]
)
async def test_temporary_url_redirect_rechecks_dns_before_connecting(
    tmp_path, private_ip
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(303, headers={"location": "/image"})

    client = HttpTemporaryUrlPullClient(transport=httpx.MockTransport(handler))
    with (
        patch(
            "engine.community.plugins.resource_materialization.socket.getaddrinfo",
            side_effect=[
                [(2, 1, 6, "", ("93.184.216.34", 443))],
                [
                    (2, 1, 6, "", ("93.184.216.34", 443)),
                    (2, 1, 6, "", (private_ip, 443)),
                ],
            ],
        ),
        pytest.raises(ValueError, match="non-public address"),
    ):
        await client.pull(_chat_request(), tmp_path / "image.part")
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("redirects", [5, 6])
async def test_temporary_url_redirect_limit(tmp_path, redirects):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) <= redirects:
            return httpx.Response(302, headers={"location": "/image"})
        return httpx.Response(200, content=b"image")

    client = HttpTemporaryUrlPullClient(transport=httpx.MockTransport(handler))
    destination = tmp_path / "image.part"
    with patch(
        "engine.community.plugins.resource_materialization.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    ):
        if redirects == 6:
            with pytest.raises(ValueError, match="redirect limit exceeded"):
                await client.pull(_chat_request(), destination)
            assert not destination.exists()
        else:
            await client.pull(_chat_request(), destination)
            assert destination.read_bytes() == b"image"
    assert len(requests) == 6


@pytest.mark.asyncio
async def test_temporary_url_redirect_preserves_size_limit(tmp_path):
    responses = iter(
        [
            httpx.Response(303, headers={"location": "/image"}),
            httpx.Response(200, content=b"oversized"),
        ]
    )
    client = HttpTemporaryUrlPullClient(
        transport=httpx.MockTransport(lambda request: next(responses)),
    )
    with (
        patch(
            "engine.community.plugins.resource_materialization.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ),
        pytest.raises(ValueError, match="exceeds size limit"),
    ):
        await client.pull(
            _chat_request().model_copy(update={"download_max_bytes": 4}),
            tmp_path / "image.part",
        )


@pytest.mark.asyncio
async def test_temporary_url_deadline_includes_redirect_dns(tmp_path):
    calls = 0

    async def resolve(host, port):
        nonlocal calls
        calls += 1
        if calls == 2:
            await asyncio.sleep(10)
        return frozenset({"93.184.216.34"})

    client = HttpTemporaryUrlPullClient(
        timeout_seconds=0.05,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(303, headers={"location": "/image"})
        ),
    )
    with (
        patch.object(client, "_resolve_public_ips", AsyncMock(side_effect=resolve)),
        pytest.raises(TimeoutError),
    ):
        await client.pull(_chat_request(), tmp_path / "image.part")
    assert calls == 2


@pytest.mark.asyncio
async def test_temporary_url_redirect_limits_stream_without_content_length(tmp_path):
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"1234"
            yield b"5"

    responses = iter(
        [
            httpx.Response(303, headers={"location": "/image"}),
            httpx.Response(200, stream=Body()),
        ]
    )
    client = HttpTemporaryUrlPullClient(
        max_bytes=4, transport=httpx.MockTransport(lambda request: next(responses))
    )
    with (
        patch(
            "engine.community.plugins.resource_materialization.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ),
        pytest.raises(ValueError, match="exceeds size limit"),
    ):
        await client.pull(_chat_request(), tmp_path / "image.part")


@pytest.mark.asyncio
async def test_temporary_url_redirect_propagates_final_http_error(tmp_path):
    responses = iter(
        [
            httpx.Response(303, headers={"location": "/image"}),
            httpx.Response(403),
        ]
    )
    client = HttpTemporaryUrlPullClient(
        transport=httpx.MockTransport(lambda request: next(responses))
    )
    with (
        patch(
            "engine.community.plugins.resource_materialization.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await client.pull(_chat_request(), tmp_path / "image.part")
    assert not (tmp_path / "image.part").exists()


@pytest.mark.asyncio
async def test_temporary_url_honors_configured_ca_without_environment_proxy(
    tmp_path, monkeypatch
):
    import ssl

    import certifi

    # Use one real CA rather than the default bundle so ignoring the override
    # is observable even with a mocked network transport.
    bundle = Path(certifi.where()).read_text()
    pem = bundle[bundle.index("-----BEGIN CERTIFICATE-----") :]
    pem = pem[
        : pem.index("-----END CERTIFICATE-----") + len("-----END CERTIFICATE-----")
    ]
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text(pem)
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b"image")

    def transport(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs.get("proxy") is None
        context = kwargs["verify"]
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
        assert context.get_ca_certs(binary_form=True) == [ssl.PEM_cert_to_DER_cert(pem)]
        return httpx.MockTransport(handler)

    client = HttpTemporaryUrlPullClient()
    with (
        patch("httpx._client.AsyncHTTPTransport", side_effect=transport),
        patch(
            "engine.community.plugins.resource_materialization.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ),
    ):
        await client.pull(_chat_request(), tmp_path / "image.part")
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
