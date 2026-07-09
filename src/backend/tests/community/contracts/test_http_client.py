"""Rule 25 conformance — ``HttpClient`` ↔ its consumers.

The local impl (:class:`LocalHttpClient`) is the executable spec: an unstubbed
call raises (no silent network), ``set_response`` / ``set_override`` drive
results, and ``.calls_to(...)`` records the request. We exercise it through a
real upper-layer consumer (:class:`BcnService`) and confirm the DI container
hands out the **qualified** singletons (``Annotated[HttpClient, "baas"|"bcn"]``)
that injection tests rely on.
"""
from __future__ import annotations

from typing import Annotated
from unittest.mock import Mock

import httpx
import pytest

from agentclaw.community.core.bot_management.services.bcn_service import (
    BcnService,
    BcnServiceError,
)
from agentclaw.community.di.config import BcnConfig
from agentclaw.community.plugin_api.http_client import (
    HttpClient,
    QUALIFIER_BAAS,
    QUALIFIER_BCN,
)
from agentclaw.community.plugins.local.http_client import (
    HttpNotConfiguredError,
    LocalHttpClient,
)


def _ok(body: dict) -> Mock:
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = body
    resp.text = ""
    resp.raise_for_status.return_value = None
    return resp


# ── the local impl IS the spec ───────────────────────────────────────────────


def test_unstubbed_call_raises_never_hits_network() -> None:
    client = LocalHttpClient(base_url="http://svc.test")
    # Every verb raises when unstubbed — no silent network on get/post/put/delete.
    with pytest.raises(HttpNotConfiguredError):
        client.post("/api/v1/bots", json={"x": 1})
    with pytest.raises(HttpNotConfiguredError):
        client.get("/api/v1/bots")
    with pytest.raises(HttpNotConfiguredError):
        client.put("/api/v1/bots/1", json={"x": 1})
    with pytest.raises(HttpNotConfiguredError):
        client.delete("/api/v1/bots/1")


def test_set_response_drives_a_consumer_and_records_the_call() -> None:
    """A stubbed response flows verbatim through ``BcnService``; the request the
    service issued is recorded on the client for assertions."""
    http = LocalHttpClient(base_url="http://bcn.test")
    http.set_response("post", _ok({"success": True, "data": {"bot_id": "b1"}}))
    service = BcnService(
        http_client=http,
        config=BcnConfig(
            provider_id_pre="prv_40354c8a",
            provider_admin_token_pre="test-bcn-token-pre",
        ),
        timeout=5.0,
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
            lambda: "pre",
        )
        data = service.switch_bot(
            teamclaw_bot_uuid="u1", owner_workno="42", name="Bot", summary="s",
        )

    assert data == {"bot_id": "b1"}
    call = http.calls_to("post")[0]
    assert call.args[0] == "/providers/prv_40354c8a/delivery/switch-bot"
    assert call.kwargs["json"]["provider_bot_ref"] == "u1:42"


def test_set_override_can_simulate_a_transport_error() -> None:
    http = LocalHttpClient(base_url="http://bcn.test")

    def _boom(*_a, **_k):
        raise httpx.TimeoutException("slow")

    http.set_override("post", _boom)
    service = BcnService(
        http_client=http,
        config=BcnConfig(
            provider_id_pre="prv_40354c8a",
            provider_admin_token_pre="test-bcn-token-pre",
        ),
        timeout=5.0,
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "agentclaw.community.core.bot_management.services.bcn_service.get_current_env",
            lambda: "pre",
        )
        with pytest.raises(BcnServiceError):
            service.switch_bot(
                teamclaw_bot_uuid="u1", owner_workno="42", name="Bot", summary="s",
            )


# ── DI hands out qualified, base_url-scoped singletons ───────────────────────


def test_di_binds_distinct_qualified_local_clients(world) -> None:
    baas = world.get(Annotated[HttpClient, QUALIFIER_BAAS])
    bcn = world.get(Annotated[HttpClient, QUALIFIER_BCN])

    assert isinstance(baas, LocalHttpClient)
    assert isinstance(bcn, LocalHttpClient)
    assert baas is not bcn                       # one client per upstream


def test_consumer_shares_the_singleton_a_test_can_configure(world) -> None:
    """The qualified client a test fetches is the SAME instance ``BcnService``
    holds — so stubbing it in a test drives the service under the endpoint."""
    bcn_client = world.get(Annotated[HttpClient, QUALIFIER_BCN])
    service = world.get(BcnService)
    assert service._http is bcn_client
