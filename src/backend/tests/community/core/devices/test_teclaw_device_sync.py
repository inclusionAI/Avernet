"""TeclawDeviceSyncService — whole-artifact runtime delivery over the device-sync seam.

Every ``sync_*`` method re-composes the bot's full ``BotConfigArtifact`` and
POSTs it to ``/api/v1/bot/apply`` on the running container; there is no
per-domain delta. These tests pin the compose→enrich→POST chain, the identity
fields the composer and the binding lookup each read, and the error mapping
that keeps a transport failure from crashing callers.
"""
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.config_compose.models import ComposeOccasion
from agentclaw.community.core.devices.services.teclaw_device_sync import (
    TECLAW_BOT_APPLY_PATH,
    TeclawDeviceSyncService,
    build_bot_apply_body,
    extract_teclaw_bot_id,
)


TARGET = "TECLAW_b_01KV5KRZG3FGC3H06RE5T9YRT0@4:20003"
HTTP_URL = f"https://agentclawproxy/proxypass/{TARGET}{TECLAW_BOT_APPLY_PATH}"


@dataclass(frozen=True)
class _Artifact:
    """Stand-in for ``BotConfigArtifact`` — only ``engine_ext`` + ``to_dict``
    are exercised, and ``dataclasses.replace`` needs a real dataclass."""

    engine_ext: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"engine_ext": self.engine_ext, "skills": []}


def _conn_info() -> dict:
    return {
        "bot_type": "service",
        "engine_port": 20003,
        "tenant": "team_claw",
    }


def _ok_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        json={"success": True},
        request=httpx.Request("POST", HTTP_URL),
    )


def _baas_service() -> MagicMock:
    baas = MagicMock()
    # Deliberately a *different* id from the ``binding_id`` the service is
    # constructed with: every delivery must address the container by the
    # threaded-through context id, never by a fresh lookup.
    baas.get_bind_id.return_value = 999
    baas.get_http_info.return_value = SimpleNamespace(
        http_url=HTTP_URL, token="tok-xyz", target=TARGET
    )
    return baas


def _make_service(**overrides) -> tuple[TeclawDeviceSyncService, dict[str, Any]]:
    """Build a service over mocks; return it plus the mocks the tests assert on."""
    composer = MagicMock()
    composer.compose.return_value = _Artifact()
    http_client = MagicMock()
    http_client.post.return_value = _ok_response()
    baas = _baas_service()
    recorder = MagicMock()

    kwargs: dict[str, Any] = {
        "conn_info": _conn_info(),
        "bot_id": "bot7",
        "bot_name": "GY服务助手",
        "binding_id": 42,
        "user_id": "u1",
        "owner_id": "u1",
        "entity_id": "staff_u1",
        "engine_type": "teclaw",
        "entity_type": "staff",
        "composer_provider": lambda: composer,
        "baas_service": baas,
        "http_client": http_client,
        "draft_recorder": lambda: recorder,
    }
    kwargs.update(overrides)
    service = TeclawDeviceSyncService(**kwargs)
    return service, {
        "composer": composer,
        "http_client": http_client,
        "baas": baas,
        "recorder": recorder,
    }


# ── pure helpers ─────────────────────────────────────────────────────────


def test_extract_teclaw_bot_id_takes_substring_between_prefix_and_at():
    assert extract_teclaw_bot_id(TARGET) == "b_01KV5KRZG3FGC3H06RE5T9YRT0"


def test_extract_teclaw_bot_id_tolerates_missing_prefix_and_suffix():
    assert extract_teclaw_bot_id("b_plain") == "b_plain"


def test_build_bot_apply_body_shapes_the_update_contract():
    body = build_bot_apply_body("b_1", _Artifact(engine_ext={"k": "v"}))

    assert body == {
        "bot_id": "b_1",
        "operation": "UPDATE",
        "bot_config": {"engine_ext": {"k": "v"}, "skills": []},
    }


# ── delivery ─────────────────────────────────────────────────────────────


def test_sync_symlinks_composes_and_posts_the_whole_artifact():
    service, m = _make_service()

    result = service.sync_symlinks([{"source": "a", "target": "b"}])

    assert result == {"success": True, "message": "teclaw artifact delivered"}
    m["http_client"].post.assert_called_once()
    args, kwargs = m["http_client"].post.call_args
    assert args[0] == HTTP_URL
    assert kwargs["headers"]["x-proxypass-token"] == "tok-xyz"
    assert kwargs["json"]["bot_id"] == "b_01KV5KRZG3FGC3H06RE5T9YRT0"
    assert kwargs["json"]["operation"] == "UPDATE"


def test_compose_scopes_by_entity_id_and_stays_distinct_from_owner_id():
    """The two identity fields are distinct and must not be conflated: the
    composer collects by ``ac_bots.entity_id``, while ``ac_bots.owner_id`` is
    the identity the binding was resolved under."""
    service, m = _make_service(owner_id="u1", entity_id="staff_u1")

    service.sync_symlinks([])

    req = m["composer"].compose.call_args.args[0]
    assert req.entity_id == "staff_u1"
    assert req.user_id == "u1"
    assert req.bot_id == "bot7"
    assert req.engine_type == "teclaw"
    assert req.entity_type == "staff"


def test_a_runtime_edit_composes_for_the_default_occasion_and_a_manifest_apply_says_so():
    """Ownership follows the operation (W8): every runtime edit composes for
    ``RUNTIME``; the closing redeliver of a manifest apply is the one call
    that composes for ``MANIFEST_APPLY``."""
    service, m = _make_service()

    service.sync_symlinks([])
    assert m["composer"].compose.call_args.args[0].occasion is ComposeOccasion.RUNTIME

    result = service.deliver_manifest_apply()
    assert result == {"success": True, "message": "teclaw artifact delivered"}
    req = m["composer"].compose.call_args.args[0]
    assert req.occasion is ComposeOccasion.MANIFEST_APPLY
    assert req.bot_id == "bot7"


def test_an_already_resolved_mcp_set_rides_on_the_compose_request():
    """The caller's effective MCP set reaches the composer instead of a re-read.

    Capability projection resolves this set before it decides anything — the
    projected codes and the Passport scope come out of it — and the compose
    here would otherwise put the same ``collect_bot_active_mcps`` query to the
    same database again. Threading it is what removes the second read; the
    collector still enriches and merges each entry.
    """
    service, m = _make_service()

    service.sync_symlinks([], effective_mcps=[{"server_code": "a"}])

    req = m["composer"].compose.call_args.args[0]
    assert req.effective_mcps == ({"server_code": "a"},)
    assert req.bot_id == "bot7"


def test_resolved_exact_center_skills_ride_on_the_compose_request():
    service, m = _make_service()
    desired = [
        {
            "id": "10",
            "name": "center-weather",
            "git_path": "center://public-weather",
            "skill_uuid": "00000000-0000-4000-8000-000000000010",
            "sc_version_number": "1.0.0",
        }
    ]

    service.sync_symlinks([], desired_skills=desired)

    req = m["composer"].compose.call_args.args[0]
    assert req.desired_skills == tuple(desired)


def test_a_caller_with_no_resolved_mcp_set_leaves_the_collector_to_read_it():
    """``None``, not ``()``: the collector must still do its own read.

    Every non-projection entry point (a channel edit, an MCP edit, the
    device-activated listener) composes without having collected anything.
    Handing those an empty set would compose an artifact with no MCP servers
    at all.
    """
    service, m = _make_service()

    service.sync_symlinks([])

    assert m["composer"].compose.call_args.args[0].effective_mcps is None


def test_entity_id_defaults_to_owner_id_when_omitted():
    """Back-compat for a call site that predates the entity_id/owner_id split."""
    service, m = _make_service(owner_id="org_7", entity_id=None)

    service.sync_symlinks([])

    assert m["composer"].compose.call_args.args[0].entity_id == "org_7"


def test_engine_ext_is_enriched_with_identity_and_draft_stage():
    service, m = _make_service()

    service.sync_symlinks([])

    engine_ext = m["http_client"].post.call_args.kwargs["json"]["bot_config"]["engine_ext"]
    assert engine_ext["bot_id"] == "bot7"
    # owner_id tracks the compose user_id, not the entity — mirrors the producer.
    assert engine_ext["owner_id"] == "u1"
    assert engine_ext["bot_name"] == "GY服务助手"
    assert engine_ext["stage"] == "draft"


def test_http_info_is_resolved_per_delivery_with_the_apply_path():
    service, m = _make_service()

    service.sync_symlinks([])

    m["baas"].get_http_info.assert_called_once_with(
        bind_id=42,
        port=20003,
        path=TECLAW_BOT_APPLY_PATH,
        tenant="team_claw",
        device_affinity="u1",
    )


# ── bind_id threading ────────────────────────────────────────────────────
# ``binding_id`` is required, mirroring the non-optional ``DeviceContext``
# field it comes from, so there is no absent-binding case to pin here: a bot
# with no active binding raises ``DeviceNotBoundError`` in the resolver and
# never reaches this constructor (``test_device_context_resolver.py::
# test_bot_no_active_binding_raises_device_not_bound``).


def test_delivery_addresses_the_threaded_binding_id_without_a_lookup():
    """The resolved ``DeviceContext`` already carried this binding; re-deriving
    it cost a BaaS round trip per delivery for a value the caller was handed."""
    service, m = _make_service(binding_id=1382508)

    service.sync_symlinks([])

    m["baas"].get_bind_id.assert_not_called()
    assert m["baas"].get_http_info.call_args.kwargs["bind_id"] == 1382508


def test_no_get_bind_id_round_trip_on_any_sync_entry_point():
    """Every ``sync_*`` funnels through ``_compose_and_deliver``; none of them
    may reintroduce the lookup."""
    for method, args in (
        ("sync_symlinks", ([],)),
        ("sync_bot_config", ("bot7", 42, "1", "OWNER", "u1", "nick")),
        ("sync_all_mcp_servers", ([],)),
        ("sync_single_mcp", ({"server_code": "s1"},)),
        ("sync_remove_mcp", ("s1",)),
    ):
        service, m = _make_service()

        getattr(service, method)(*args)

        m["baas"].get_bind_id.assert_not_called()


def test_a_stale_get_bind_id_answer_cannot_redirect_the_delivery():
    """Guards the container the artifact lands in: even when ``get_bind_id``
    would answer with a different binding, delivery stays on the one the
    context was resolved against."""
    service, m = _make_service(binding_id=42)
    m["baas"].get_bind_id.return_value = 777

    service.sync_symlinks([])

    assert m["baas"].get_http_info.call_args.kwargs["bind_id"] == 42


def test_sync_bot_config_redelivers_the_whole_artifact():
    """ROLE/VISIBILITY is already persisted to DB; teclaw re-pulls the whole
    artifact rather than receiving the flags as a delta."""
    service, m = _make_service()

    result = service.sync_bot_config("bot7", 42, "1", "OWNER", "u1", "nick")

    assert result == {"success": True, "message": "teclaw artifact delivered"}
    m["http_client"].post.assert_called_once()


@pytest.mark.parametrize(
    "method, args",
    [
        ("sync_all_mcp_servers", ([{"server_code": "s1"}],)),
        ("sync_single_mcp", ({"server_code": "s1"},)),
        ("sync_remove_mcp", ("s1",)),
    ],
)
def test_mcp_methods_redeliver_the_whole_artifact_and_return_bool(method, args):
    service, m = _make_service()

    assert getattr(service, method)(*args) is True
    m["http_client"].post.assert_called_once()


def test_has_mcp_always_true_so_the_batch_never_skips_a_whole_artifact_device():
    service, _ = _make_service()

    assert service.has_mcp("anything") is True


def test_successful_delivery_records_the_delivered_artifact_on_the_draft_row():
    service, m = _make_service()

    service.sync_symlinks([])

    m["recorder"].record_draft_artifact.assert_called_once()
    kwargs = m["recorder"].record_draft_artifact.call_args.kwargs
    assert kwargs["bot_id"] == "bot7"
    assert kwargs["artifact"]["engine_ext"]["stage"] == "draft"


def test_recorder_failure_does_not_flip_a_successful_delivery():
    service, m = _make_service()
    m["recorder"].record_draft_artifact.side_effect = RuntimeError("db down")

    assert service.sync_symlinks([])["success"] is True


def test_no_recorder_is_a_no_op():
    service, m = _make_service(draft_recorder=None)

    assert service.sync_symlinks([])["success"] is True


# ── error mapping ────────────────────────────────────────────────────────


def test_http_status_error_maps_to_a_failure_dict():
    service, m = _make_service()
    response = httpx.Response(
        status_code=502,
        text="bad gateway",
        request=httpx.Request("POST", HTTP_URL),
    )
    m["http_client"].post.return_value = response

    result = service.sync_symlinks([])

    assert result == {"success": False, "message": "HTTP 错误: 502"}
    m["recorder"].record_draft_artifact.assert_not_called()


def test_request_error_maps_to_a_failure_dict():
    service, m = _make_service()
    m["http_client"].post.side_effect = httpx.ConnectError("no route")

    result = service.sync_symlinks([])

    assert result["success"] is False
    assert result["message"].startswith("请求失败")


def test_compose_failure_is_contained_in_the_result_dict():
    service, m = _make_service()
    m["composer"].compose.side_effect = RuntimeError("collector exploded")

    result = service.sync_symlinks([])

    assert result["success"] is False
    assert "投递失败" in result["message"]
    m["http_client"].post.assert_not_called()


def test_mcp_methods_return_false_on_failure():
    service, m = _make_service()
    m["http_client"].post.side_effect = httpx.ConnectError("no route")

    assert service.sync_remove_mcp("s1") is False
