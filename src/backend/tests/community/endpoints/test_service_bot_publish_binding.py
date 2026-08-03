"""Endpoint tests for GET /api/service-bot/publish/{bot_id}/binding."""
from __future__ import annotations

from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_OWNER = "u_bind"
_BOT_ID = "bind_bot"
_PERSONAL_BOT_ID = "bind_personal_bot"
_BINDING_ID = 123

_ENDPOINT = "/api/service-bot/publish/{bot_id}/binding"


def _seed_binding_happy(world) -> None:
    """Seed a bot and a publish record for binding query."""
    from tests.community.factories.access import make_staff_user
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository

    make_staff_user(world, user_id=_OWNER)

    # Create a bot
    binding_repo = world.get(DeviceBindingRepository)
    binding_id = binding_repo.insert_binding(
        entity_id=_OWNER,
        entity_type="staff",
        device_id="device-verify",
        device_provider="teclaw",
        env="dev",
        device_props={},
        status="ACTIVE",
        apply_reason="seed",
        applied_by=_OWNER,
    )

    bot_repo = world.get(BotRepository)
    bot = bot_repo.insert({
        "bot_id": _BOT_ID,
        "bot_name": "Binding Bot",
        "owner_id": _OWNER,
        "owner_name": _OWNER,
        "bot_type": "service",
        "status": "ACTIVE",
        "entity_id": _OWNER,
        "entity_type": "staff",
        "creator_id": _OWNER,
        "active_engine": "teclaw",
        "binding_id": binding_id,
    })

    # Create a publish record with binding info
    world.get(BotPublishRepositoryProtocol).insert({
        "source_bot_pk": bot["id"],
        "source_bot_id": _BOT_ID,
        "publish_bot_id": _BOT_ID,
        "name": "Binding Bot",
        "owner_id": _OWNER,
        "permission_owner": _OWNER,
        "status": PublishStatus.SUCCESS,
        "version": 1,
        "env": "dev",
        "ext": {
            "binding": {
                "verify": binding_id,
                "online": binding_id,
            }
        },
    })


def _seed_personal_binding_with_runtime_engine(world) -> None:
    """Seed a personal bot whose template explicitly selects its runtime engine."""
    from tests.community.factories.access import make_staff_user
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.bot_management.repository.template_repository_protocol import (
        TemplateRepository,
    )
    from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository

    make_staff_user(world, user_id=_OWNER)
    binding_id = world.get(DeviceBindingRepository).insert_binding(
        entity_id=_OWNER,
        entity_type="staff",
        device_id="personal-baas-device",
        device_provider="baas",
        env="dev",
        device_props={},
        status="ACTIVE",
        apply_reason="seed",
        applied_by=_OWNER,
    )
    world.get(BotRepository).insert({
        "bot_id": _PERSONAL_BOT_ID,
        "bot_name": "Personal Binding Bot",
        "owner_id": _OWNER,
        "owner_name": _OWNER,
        "bot_type": "personal",
        "status": "ACTIVE",
        "entity_id": _OWNER,
        "entity_type": "staff",
        "creator_id": _OWNER,
        "active_engine": "claude_code",
        "template_type": "applicationCoding",
        "binding_id": binding_id,
    })
    world.get(TemplateRepository).insert({
        "bot_id": _PERSONAL_BOT_ID,
        "ext": {"template_runtime_engine_type": " claude_code "},
    })


def _seed_binding_error(world) -> None:
    """Seed without the target bot - triggers BotNotFoundError."""
    from tests.community.factories.access import make_staff_user
    make_staff_user(world, user_id=_OWNER)
    # No bot, no publish record - will cause BotNotFoundError


@endpoint_test(
    method="GET",
    path=_ENDPOINT,
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"owner_id": _OWNER, "stage": "online"},
    ),
    seed=_seed_binding_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def get_binding_happy():
    """Happy path: query binding info for online stage succeeds."""


@endpoint_test(
    method="GET",
    path=_ENDPOINT,
    scenario="personal-explicit-runtime-engine",
    input=CaseInput(
        path_params={"bot_id": _PERSONAL_BOT_ID},
        query_params={"owner_id": _OWNER, "stage": "online"},
    ),
    seed=_seed_personal_binding_with_runtime_engine,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"template_runtime_engine_type": "claude_code"},
        },
    ),
)
def get_personal_binding_explicit_runtime_engine():
    """Personal binding exposes the trimmed template runtime engine field."""


@endpoint_test(
    method="GET",
    path=_ENDPOINT,
    scenario="error",
    input=CaseInput(
        path_params={"bot_id": "nonexistent_bot"},
        query_params={"owner_id": _OWNER, "stage": "online"},
    ),
    seed=_seed_binding_error,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 404}),
)
def get_binding_error():
    """Error path: bot not found returns 404."""