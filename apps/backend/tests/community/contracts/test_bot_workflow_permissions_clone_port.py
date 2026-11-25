"""Rule 25 conformance — ``BotWorkflowPermissionsClonePort`` ↔ its transport.

The ``task_nav`` ``bot_config`` kind converges by POSTing one clone request to
clawweb's ``/api/internal/bot-workflow-permissions/clone`` endpoint through
the ``clawweb``-qualified :class:`HttpClient`. The local impl
(:class:`LocalHttpClient`) is the executable spec here: an unstubbed call
raises (no silent network), a stubbed response flows through, and the request
is recorded for assertion. The port surface this pins — the path, the exact
body the clawweb clone handler reads, and the absence of any auth header — is
what the handler's fire-and-log contract rests on.

These cases exercise :class:`HttpBotWorkflowPermissionsClonePort` directly with
a ``LocalHttpClient``; the upper-layer consumer
(:class:`~agentclaw.community.core.apply_engine.materialisers.bot_config_task_nav_handler.TaskNavSyncHandler`)
is exercised through the DI ``world`` fixture in the handler's own test.
"""
from __future__ import annotations

from typing import Annotated
from unittest.mock import Mock

import httpx
import pytest

from agentclaw.community.core.bot_management.bot_workflow_permissions_clone_port import (
    BotWorkflowPermissionsClonePort,
    HttpBotWorkflowPermissionsClonePort,
)
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.materialisers.bot_config_kind_registry import (
    BotConfigKindRegistry,
)
from agentclaw.community.core.apply_engine.materialisers.bot_config_task_nav_handler import (
    TaskNavSyncHandler,
)
from agentclaw.community.plugin_api.http_client import (
    HttpClient,
    QUALIFIER_CLAWWEB,
)
from agentclaw.community.plugins.local.http_client import (
    HttpNotConfiguredError,
    LocalHttpClient,
)

_CLONE_PATH = "/api/internal/bot-workflow-permissions/clone"


def _ok() -> Mock:
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {"success": True, "granted": 1, "rows": []}
    resp.text = ""
    resp.raise_for_status.return_value = None
    return resp


def _status(code: int) -> Mock:
    resp = Mock()
    resp.status_code = code
    resp.json.return_value = {"error": "Bad Request"}
    resp.text = "boom"
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"{code} error", request=httpx.Request("POST", "http://x"), response=httpx.Response(code)
    )
    return resp


def _port(http: HttpClient) -> HttpBotWorkflowPermissionsClonePort:
    return HttpBotWorkflowPermissionsClonePort(http)


# ── the call shape the clawweb clone endpoint is read against ──────────────


def test_unstubbed_call_raises_never_hits_network() -> None:
    """No silent network: an unstubbed clone POST raises, like every verb."""
    port = _port(LocalHttpClient(base_url="https://clawweb.test"))
    with pytest.raises(HttpNotConfiguredError):
        port.clone(
            source_bot_id="src", source_owner_id="os",
            target_bot_id="dst", target_owner_id="ot",
            scope="exact", dry_run=False,
        )


def test_clone_posts_the_exact_body_clawweb_reads_with_no_auth_header() -> None:
    """The body is the six fields the clone handler consumes; no token/header
    is sent (verified against the live pre/prod endpoint, which accepts the
    call as-is). An ``Authorization``/``X-Service-Token`` header would be a
    regression."""
    http = LocalHttpClient(base_url="https://clawweb.test")
    http.set_response("post", _ok())
    port = _port(http)

    result = port.clone(
        source_bot_id="20261010_i0nsz1lc", source_owner_id="165528",
        target_bot_id="20261111_dst", target_owner_id="165528",
        scope="exact", dry_run=False,
    )

    assert result is None  # fire-and-log: the handler does not read the return
    call = http.calls_to("post")[0]
    assert call.args[0] == _CLONE_PATH
    assert call.kwargs["json"] == {
        "source_bot_id": "20261010_i0nsz1lc",
        "source_owner_id": "165528",
        "target_bot_id": "20261111_dst",
        "target_owner_id": "165528",
        "scope": "exact",
        "dry_run": False,
    }
    # No auth surface: the call carries no Authorization / X-Service-Token.
    headers = call.kwargs.get("headers")
    assert not headers or "Authorization" not in headers
    assert not headers or "X-Service-Token" not in headers


def test_clone_surfaces_a_non_2xx_so_the_handler_can_log_it() -> None:
    """``raise_for_status`` turns a 5xx into an ``HTTPStatusError`` the handler's
    fire-and-log ``except`` records; the apply is not aborted (the handler still
    returns ``unchanged``), but the failure is visible in the log rather than
    silently swallowed."""
    http = LocalHttpClient(base_url="https://clawweb.test")
    http.set_response("post", _status(503))
    port = _port(http)

    with pytest.raises(httpx.HTTPStatusError):
        port.clone(
            source_bot_id="src", source_owner_id="os",
            target_bot_id="dst", target_owner_id="ot",
            scope="exact", dry_run=False,
        )


def test_the_port_seam_is_the_concrete_base_not_a_protocol_branch() -> None:
    """The handler depends on the ``BotWorkflowPermissionsClonePort`` base; the
    one real impl subclasses it. A test double subclasses it too (or passes a
    ``Mock(spec=...)``), never a second runtime impl — env switching is the
    HttpClient seam's job, not this port's."""
    port = _port(LocalHttpClient(base_url="https://clawweb.test"))
    assert isinstance(port, BotWorkflowPermissionsClonePort)


# ── DI hands out the clawweb-qualified client the port injects ──────────────


def test_di_binds_the_clawweb_qualified_local_client(world) -> None:
    """The ``clawweb`` qualifier is bound (no-network in the test column) so a
    handler that injects ``Annotated[HttpClient, QUALIFIER_CLAWWEB]`` resolves
    the same singleton a test can stub."""
    clawweb_http = world.get(Annotated[HttpClient, QUALIFIER_CLAWWEB])
    assert isinstance(clawweb_http, LocalHttpClient)


# ── Rule 25: the upper-layer consumer (the task_nav handler) via the local impl ─


def _ok_clone_response() -> Mock:
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {"success": True, "granted": 2, "rows": []}
    resp.text = ""
    resp.raise_for_status.return_value = None
    return resp


def test_the_registry_built_handler_posts_through_the_clawweb_client(world) -> None:
    """The DI registry builds the real ``TaskNavSyncHandler`` over the real
    ``HttpBotWorkflowPermissionsClonePort``, whose ``clawweb`` HttpClient is the
    test column's no-network ``LocalHttpClient``. Stubbing that client drives
    the clone POST the handler fans out — the Rule-25 upper-layer-consumer
    contract for the ``task_nav`` kind."""
    registry = world.get(BotConfigKindRegistry)
    handler = registry.resolve("task_nav")
    assert isinstance(handler, TaskNavSyncHandler)

    clawweb_http = world.get(Annotated[HttpClient, QUALIFIER_CLAWWEB])
    clawweb_http.set_response("post", _ok_clone_response())

    ctx = ApplyContext(
        bot_id="b_dst", owner_id="o_dst", actor_id="a", entity_id="e",
        env="dev", tenant="", engine_type="openclaw", bot_type="arca",
        bot={}, capabilities=object(),
    )
    handler.sync_kind(
        ctx=ctx,
        entries=[{"type": "bot", "source_bot_id": "src", "source_owner_id": "os"}],
        is_empty=False,
    )

    call = clawweb_http.calls_to("post")[0]
    assert call.args[0] == _CLONE_PATH
    assert call.kwargs["json"]["source_bot_id"] == "src"
    assert call.kwargs["json"]["target_bot_id"] == "b_dst"
    assert call.kwargs["json"]["scope"] == "exact"
    assert call.kwargs["json"]["dry_run"] is False

