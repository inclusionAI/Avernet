"""Endpoint coverage for GET /api/public/bots/{bot_id}/binding."""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot import BotRepository
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


BOT_ID = "public_binding_bot"
PATH = "/api/public/bots/{bot_id}/binding"


def _seed_bot(world) -> None:
    world.get(BotRepository).insert(
        {
            "bot_id": BOT_ID,
            "bot_name": "Public Binding Bot",
            "owner_id": "owner_001",
            "owner_name": "Owner One",
            "creator_id": "owner_001",
            "entity_id": "owner_001",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "status": "ACTIVE",
            "device_id": "device_001",
            "binding_id": 1001,
            "ext": {
                "arch_domain": "测试架构域",
                "iam_token": "must-not-leak",
                "token": "must-not-leak",
            },
        }
    )


def _assert_public_projection(response, _world) -> None:
    data = response.json()["data"]
    assert data["device_id"] == "device_001"
    assert data["binding_id"] == 1001
    assert "template_config" not in data
    assert "iam_token" not in data["ext"]
    assert "token" not in data["ext"]


@endpoint_test(
    method="GET",
    path=PATH,
    scenario="happy",
    input=CaseInput(path_params={"bot_id": BOT_ID}),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "bot_id": BOT_ID,
                "device_id": "device_001",
                "binding_id": 1001,
            },
        },
    ),
    extra_assertions=(_assert_public_projection,),
)
def get_public_bot_binding_happy():
    """Happy path returns binding identifiers without credentials."""


@endpoint_test(
    method="GET",
    path=PATH,
    scenario="error_not_found",
    input=CaseInput(path_params={"bot_id": "missing_bot"}),
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "error_code": 404,
            "data": None,
        },
    ),
)
def get_public_bot_binding_error():
    """Error path returns the not-found API envelope."""
