"""Real release state and signed transport with local external HTTP seams."""

from typing import Annotated
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    NoEncryption,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_BAAS
from agentclaw.community.plugin_api.publish_ignore_runtime import PublishIgnoreRuntime
from agentclaw.community.plugins.community.publish_ignore_runtime import (
    HttpPublishIgnoreRuntime,
)
from tests.community.factories.access import make_staff_user
from tests.community.framework import http_envelope_response


def seed_publish_ignore(world, *, engine_success=True, stage="online"):
    make_staff_user(world, user_id="ignore_owner")
    binding_id = world.get(DeviceBindingRepository).insert_binding(
        entity_id="ignore_owner",
        entity_type="staff",
        device_id="ignore-runtime",
        device_provider="baas",
        env="dev",
        device_props={"bolt_id": "ignore-bot"},
        status="ACTIVE",
        apply_reason="test",
        applied_by="ignore_owner",
    )
    draft_binding_id = binding_id
    if stage != "draft":
        draft_binding_id = world.get(DeviceBindingRepository).insert_binding(
            entity_id="ignore_owner", entity_type="staff", device_id="draft-runtime",
            device_provider="baas", env="dev", device_props={}, status="ACTIVE",
            apply_reason="test", applied_by="ignore_owner",
        )
    bot = world.get(BotRepository).insert(
        {
            "bot_id": "ignore-bot",
            "bot_name": "Ignore Bot",
            "owner_id": "ignore_owner",
            "owner_name": "Owner",
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": "ignore_owner",
            "entity_type": "staff",
            "creator_id": "ignore_owner",
            "binding_id": draft_binding_id,
            "device_id": "ignore-runtime",
        }
    )
    if stage != "draft":
        world.get(BotPublishRepositoryProtocol).insert(
            {
                "source_bot_pk": bot["id"],
                "source_bot_id": "ignore-bot",
                "publish_bot_id": "ignore-botpub3",
                "name": "Ignore Bot",
                "owner_id": "ignore_owner",
                "permission_owner": "ignore_owner",
                "status": "validating" if stage == "verify" else "success",
                "version": 3,
                "env": "dev",
                "ext": {"binding": {stage: binding_id}},
            }
        )
    world.get(Annotated[HttpClient, QUALIFIER_BAAS]).set_response(
        "get",
        http_envelope_response(data=[{"items": [{"uuid": "replica-a"}]}]),
    )
    world.get(DeviceAdapterTransport).set_response(
        "invoke",
        {
            "success": engine_success,
            "data": {"changed": True, "entry_count": 1, "revision": "a" * 64},
        },
    )
    original = world.get(PublishIgnoreRuntime)
    key = (
        Ed25519PrivateKey.generate()
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )
    runtime = HttpPublishIgnoreRuntime(
        original.baas, original.resolver, original.transport, original.http, key
    )
    world.injector.binder.bind(PublishIgnoreRuntime, to=runtime)


def assert_ignore_engine_called(response, world):
    calls = world.get(DeviceAdapterTransport).calls_to("invoke")
    assert len(calls) == 1
    assert calls[0].args[0]["device_uuid"] == "replica-a"
    assert calls[0].args[0]["bot_uuid"] == "ignore-runtime"
    assert calls[0].args[2] == "/api/bot/publish-ignore"
    assert "version" not in calls[0].kwargs["body"]["expected_target"]
    assert len(calls[0].kwargs["body"]["authorization"]["signature"]) == 88
