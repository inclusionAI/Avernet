"""Fixtures for `tests/core/devices/services/*` integration tests.

Task 1.9 (硬门槛) needs four pieces that don't exist as shared fixtures yet:

1. ``world`` / ``app_with_testing_modules`` — the per-test injector backed by
   ``SqliteDB`` with the schema bootstrapped. Re-exported from
   ``tests.community.framework.fixtures`` so callers can request them by name without
   duplicating bootstrap logic.

2. ``desktop_bot_seed`` / ``arca_bot_seed`` — seed an ``ac_bots`` row + the
   matching ``ac_entity_device_binding`` row so a caller can drive
   ``DeviceContextResolver.resolve_for_bot(bot_id, user_id)`` against a real
   ``DeviceBindingRepository.get_active_by_bot_and_owner`` lookup. The two
   seeds only differ in ``device_provider`` + ``device_props`` so the
   byte-equivalent test compares apples to apples.

3. ``fake_baas_service`` — a ``MagicMock`` standing in for ``BaasService``.
   ``LocalHttpClient`` in pytest mode refuses real HTTP and the existing
   conftest auto-mock only covers a subset of endpoints; for byte-equivalent
   we need a single canned ``BotWsConnectionInfoResponse`` that *both* the
   legacy v2 path *and* ``BaasConnInfoBuilder`` consume — otherwise the diff
   would conflate "field schema differs" with "ws_info differs", which the
   spec is not asking about.

4. ``device_service`` / ``device_context_resolver`` — the legacy and new
   entry points pinned to a ``DeviceServiceRouter`` that knows how to route
   ``"baas"`` and ``"arca"`` bindings (the production module wires them; the
   testing module strips them down to local-only, so we restore the routing
   here without poking ``ArcaSandboxFactory``-dependent real code paths).

Once Task 1.10 lands the DI registration for ``DeviceContextResolver``, the
``device_context_resolver`` fixture collapses to ``world.get(...)``.
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401


@dataclass
class BotSeed:
    """Identity for a freshly-seeded bot + binding pair."""

    bot_id: str
    user_id: str
    binding_id: int


def _seed_bot_and_binding(
    world,  # noqa: ARG001 — used via injector lookups below
    *,
    bot_id: str,
    user_id: str,
    device_provider: str,
    device_props: dict,
    bot_type: str = "personal",
) -> BotSeed:
    """Insert an ``ac_entity_device_binding`` + ``ac_bots`` pair atomically.

    The binding row carries ``device_provider`` + ``device_props`` — that's
    what ``DeviceContextResolver`` reads (and the four builders dispatch on).
    The bot row points at it via ``binding_id`` (the FK ``ac_bots.binding_id``
    references ``ac_entity_device_binding.id``) and pins ``owner_id`` to
    ``user_id`` so ``DeviceBindingRepository.get_active_by_bot_and_owner``
    returns the binding for this owner.
    """
    from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from agentclaw.community.plugin_api.models import BotModel

    repo = world.get(DeviceBindingRepository)
    binding_id = repo.insert_binding(
        entity_id=user_id,
        entity_type="staff",
        device_id=f"staff_{user_id}_{bot_id}",
        device_provider=device_provider,
        env="dev",
        device_props=device_props,
        status="ACTIVE",
        apply_reason="byte-equivalent test seed",
        applied_by=user_id,
    )

    db = world.get(DatabasePlugin)
    with db.orm_session() as session:
        bot = BotModel(
            bot_id=bot_id,
            bot_name=f"seed-{bot_id}",
            entity_id=user_id,
            entity_type="staff",
            creator_id=user_id,
            owner_id=user_id,
            status="ACTIVE",
            binding_id=binding_id,
            device_id=f"staff_{user_id}_{bot_id}",
            is_delete=0,
            public="0",
            env="dev",
            bot_type=bot_type,
            active_engine="openclaw",
        )
        session.add(bot)
        session.flush()

    return BotSeed(bot_id=bot_id, user_id=user_id, binding_id=binding_id)


@pytest.fixture
def desktop_bot_seed(world) -> BotSeed:
    """Seed a desktop bot — ``device_provider="baas"``, ``bot_type="desktop"``.

    ``binding.device_provider="baas"`` is the canonical wiring for the
    desktop-bot byte-equivalent gate (see spec §6.5.2): ``resolver`` will
    dispatch to ``BaasConnInfoBuilder`` and the resulting ``ctx.provider``
    must be ``"baas"``.
    """
    return _seed_bot_and_binding(
        world,
        bot_id="desktop-bot-byte-eq",
        user_id="staff-desktop-001",
        device_provider="baas",
        device_props={"engine_type": "openclaw"},
        bot_type="desktop",
    )


@pytest.fixture
def arca_bot_seed(world) -> BotSeed:
    """Seed an arca bot — ``device_provider="arca"``, sandbox-bearing.

    ``device_props.sandbox_id`` is set so ``get_device_connection_v2`` enters
    the ARCA proxy branch (line 1480 in ``device_service.py``).
    """
    return _seed_bot_and_binding(
        world,
        bot_id="arca-bot-byte-eq",
        user_id="staff-arca-001",
        device_provider="arca",
        device_props={"sandbox_id": "ARCA_x@y:1", "engine_type": "openclaw"},
        bot_type="personal",
    )


# ── canned BaaS response ────────────────────────────────────────────────────
# Both the legacy v2 desktop branch (BaasDeviceService.get_device_connection
# →_compose_device_conn_info via baas /ws-info) and BaasConnInfoBuilder
# (baas_service.get_ws_info → build_baas_conn_info_for_http) consume the same
# ``BotWsConnectionInfoResponse`` shape, so pinning it once makes the
# byte-equivalent comparison about *field schema*, not about transport
# randomness.

_CANNED_WS_INFO_KWARGS = dict(
    ws_url="wss://baas.test/proxypass/box-001/api/openclaw/ws",
    token="canned-token",
    target="box-001",
    expires_at="2099-12-31T00:00:00Z",
    paas_device_id="paas-dev-001",
    baas_base_url="https://baas.test",
    engine_port=20003,
    tenant="team_claw",
    bot_uuid="bot-uuid-001",
)


@pytest.fixture
def fake_baas_service():
    """A ``BaasService`` stand-in returning a pinned ws_info.

    ``LocalHttpClient`` in pytest mode hard-fails on uncovered endpoints
    (no real HTTP). Rather than weave a respx route just to back v2 +
    builder, hand both consumers the same in-process mock — the spec's
    byte-equivalent claim is about resolver wiring, not about the BaaS
    transport.
    """
    from agentclaw.community.core.service_bot.services.baas_service import (
        BotWsConnectionInfoResponse,
    )

    svc = MagicMock()
    svc.get_ws_info.return_value = BotWsConnectionInfoResponse(
        **_CANNED_WS_INFO_KWARGS
    )
    return svc


@pytest.fixture
def device_service(world, fake_baas_service):
    """A ``DeviceServiceRouter`` wired with ``BaasDeviceService`` (handles
    desktop ``binding.device_provider="baas"``) + a stub for ``"arca"``.

    The testing override (``TestingDevicesModule.device_service``) only
    registers ``LocalDeviceService`` — a desktop ``"baas"`` binding routed
    through it explodes in ``LocalDeviceService._compose_device_conn_info``
    on the provider sanity check. The byte-equivalent test needs the *real*
    desktop branch of v2 (which is keyed on ``binding.device_provider="baas"``
    routing to ``BaasDeviceService``), so we rebuild a router by hand here.
    """
    from typing import cast

    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.core.devices.protocols import (
        BotQueryProtocol,
        BotSyncProtocol,
        McpSyncProtocol,
    )
    from agentclaw.community.core.devices.repository.protocol import (
        DeviceBindingRepository,
        OssToNasRecordRepository,
    )
    from agentclaw.community.core.devices.services.baas_device_service import (
        BaasDeviceService,
    )
    from agentclaw.community.core.devices.services.device_service_router import (
        DeviceServiceRouter,
    )
    from agentclaw.community.core.devices.services.local_device_service import (
        LocalDeviceService,
    )
    from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
    from agentclaw.community.core.system_config import DeviceConfigService

    repo = world.get(DeviceBindingRepository)
    bot_repository = world.get(BotRepository)
    bot_service = world.get(BotService)
    oss_record_repo = world.get(OssToNasRecordRepository)
    mcp_sync = world.get(MCPSyncService)

    baas_svc = BaasDeviceService(
        repository=repo,
        baas_service=fake_baas_service,
        bot_query=cast(BotQueryProtocol, bot_repository),
        bot_sync=cast(BotSyncProtocol, bot_service),
        oss_record_repo=oss_record_repo,
        mcp_sync=cast(McpSyncProtocol, mcp_sync),
        template_resolver=MagicMock(
            resolve_template_uid=MagicMock(return_value="openclaw_personal_default"),
            resolve_template_uuid=MagicMock(return_value="TEMPLATE-test"),
        ),
    )

    # LocalDeviceService kept as router default so unrecognized providers
    # don't accidentally short-circuit. The byte-equivalent test only drives
    # ``"baas"`` + ``"arca"`` routes; ``"local"`` stays as the safety net.
    local_svc = LocalDeviceService(
        repo,
        baas_service=fake_baas_service,
        publish_poller=MagicMock(),
        config={"aidesktop_root": "/aidesktop"},
        bot_query=cast(BotQueryProtocol, bot_repository),
        bot_sync=cast(BotSyncProtocol, bot_service),
        oss_record_repo=oss_record_repo,
        mcp_sync=cast(McpSyncProtocol, mcp_sync),
    )

    from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient

    return DeviceServiceRouter(
        repository=repo,
        bot_query=cast(BotQueryProtocol, bot_repository),
        providers={
            "baas": baas_svc,
            # arca routes to baas_svc too — get_device_connection there returns
            # type="baas" which v2's ``is_arca_device`` (line 1480) interprets
            # as the ARCA proxy branch when sandbox_id is set on device_props.
            "arca": baas_svc,
            "local": local_svc,
        },
        default_provider_key="local",
        sandbox_client=world.get(SandboxRuntimeClient),
    )


@pytest.fixture
def device_context_resolver(world, fake_baas_service, device_service):
    """Hand-build a ``DeviceContextResolver`` until Task 1.10 wires DI.

    The four builders all share the same ``fake_baas_service`` + the same
    ``device_service`` as the legacy v2 path, so any diff the test reports is
    purely a *schema* diff between v2's branch output and the builder's
    output — not a transport mismatch. Once Task 1.10 registers the resolver
    as a DI provider, this collapses to ``world.get(DeviceContextResolver)``.
    """
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
    from agentclaw.community.core.devices.services.conn_info_builders.arca_builder import (
        ArcaConnInfoBuilder,
    )
    from agentclaw.community.core.devices.services.conn_info_builders.baas_builder import (
        BaasConnInfoBuilder,
    )
    from agentclaw.community.core.devices.services.effective_engine_resolver import (
        ConfiguredEffectiveEngineResolver,
        ClaudeCodeTemplateEffectiveEngineResolver,
    )
    from agentclaw.community.core.devices.services.conn_info_builders.local_builder import (
        LocalConnInfoBuilder,
    )
    from agentclaw.community.core.devices.services.conn_info_builders.teclaw_builder import (
        TeclawConnInfoBuilder,
    )
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )

    repo = world.get(DeviceBindingRepository)
    bot_repo = world.get(BotRepository)

    return DeviceContextResolver(
        binding_repository=repo,
        bot_repository=bot_repo,
        arca_builder=ArcaConnInfoBuilder(device_service=device_service),
        baas_builder=BaasConnInfoBuilder(
            baas_service=fake_baas_service,
            bot_repository=bot_repo,
            device_binding_repository=repo,
            effective_engine_resolver=ConfiguredEffectiveEngineResolver(
                resolvers={
                    "claude_code": ClaudeCodeTemplateEffectiveEngineResolver(),
                },
            ),
        ),
        teclaw_builder=TeclawConnInfoBuilder(baas_service=fake_baas_service),
        local_builder=LocalConnInfoBuilder(device_service=device_service),
    )
