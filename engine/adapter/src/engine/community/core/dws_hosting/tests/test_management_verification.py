"""Only the configured Backend's positive, exact response authorizes a command."""

import httpx
import pytest

from engine.community.plugins.dws_hosting.verification import BackendManagementVerifier
from engine.community.kernel.dws_runtime import RolloutUnavailable


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://backend.test",
        "ftp://backend.test",
        "https://user@backend.test",
        "https://backend.test/path",
        "https://backend.test?url=other",
        "https://backend.test#fragment",
        "http://127.0.0.1.evil.test",
    ],
)
def test_untrusted_or_ambiguous_destinations_rejected(url):
    with pytest.raises(ValueError, match="trusted_management"):
        BackendManagementVerifier(url)


async def test_fixed_route_exact_bytes_and_no_secret():
    def handler(request):
        assert (
            str(request.url)
            == "https://backend.test/internal/dws-hosting/verify-command"
        )
        assert request.read() == b'{"payload":"command","signature":"proof"}'
        return httpx.Response(200, json={"verified": True})

    verifier = BackendManagementVerifier(
        "https://backend.test/", transport=httpx.MockTransport(handler)
    )
    assert await verifier.verify("command", "proof") is True


@pytest.mark.parametrize(
    "status,body",
    [
        (403, {"verified": True}),
        (503, {"verified": True}),
        (302, {"verified": True}),
        (200, {"verified": False}),
        (200, {"verified": 1}),
        (200, {"verified": "true"}),
        (200, {"verified": True, "unexpected": "field"}),
        (200, []),
        (200, None),
    ],
)
async def test_errors_redirects_and_bad_results_fail_closed(status, body):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(
            status, json=body, headers={"location": "https://other.test"}
        )

    verifier = BackendManagementVerifier(
        "https://backend.test", transport=httpx.MockTransport(handler)
    )
    assert await verifier.verify("command", "proof") is False
    assert count == 1


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_network_failure_never_authorizes(error):
    def handler(request):
        raise error("private-details")

    verifier = BackendManagementVerifier(
        "http://127.0.0.1:8888", transport=httpx.MockTransport(handler)
    )
    assert await verifier.verify("command", "proof") is False


async def test_non_json_login_page_does_not_authorize():
    verifier = BackendManagementVerifier(
        "https://backend.test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text="<html>login</html>")
        ),
    )
    assert await verifier.verify("command", "proof") is False


@pytest.mark.parametrize(
    "value,expected", [(True, True), (False, False), (1, False), ("true", False)]
)
async def test_runtime_eligibility_uses_scoped_backend_route(value, expected):
    def handler(request):
        assert request.url.path == "/internal/dws-hosting/runtime-eligibility"
        return httpx.Response(200, json={"allowed": value})

    verifier = BackendManagementVerifier(
        "https://backend.test", transport=httpx.MockTransport(handler)
    )
    if type(value) is bool:
        assert await verifier.runtime_eligible("command", "proof") is expected
    else:
        with pytest.raises(RolloutUnavailable):
            await verifier.runtime_eligible("command", "proof")
