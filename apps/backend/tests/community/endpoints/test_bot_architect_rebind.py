"""Endpoint test for PUT /api/bots/{architect_bot_id}/architect-rebind.

Exercises the real handler → real ``ArchitectRebindService.rebind_architect_bot_batch``
→ real ``BotRepository`` / ``TemplateRepository`` against the per-test SQLite
DB: a domain architect bot, an application-coding bot (with its template
linking to a *different* architect) are inserted directly, then the endpoint
rebinds the coding bot onto the architect. No service mocking — assertions
pin the HTTP envelope plus the per-batch summary the service returns.
"""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot import TemplateRepository

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

ARCHITECT_BOT_ID = "arch_bot_rebind_arch"
CODING_BOT_ID = "app_bot_rebind_app"
OTHER_ARCHITECT_BOT_ID = "other_arch"
TARGET_ARCHITECT_BOT_ID = "target_arch"


def _seed_architect_and_coding_bot(world):
    """Insert a domain architect bot + an application-coding bot + its template."""
    bot_repo = world.get(BotRepository)
    # Domain architect bot owned by the caller (ext.is_domain_bot == true).
    bot_repo.insert(
        {
            "bot_id": ARCHITECT_BOT_ID,
            "bot_name": "Architect Bot (rebind)",
            "owner_id": "test_user",
            "owner_name": "test_user",
            "creator_id": "test_user",
            "entity_id": "test_user",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "status": "ACTIVE",
            "ext": {"is_domain_bot": True},
        }
    )
    # Target domain architect bot (may be owned by someone else; rebind
    # only requires the *source* architect to be owned by the caller).
    bot_repo.insert(
        {
            "bot_id": TARGET_ARCHITECT_BOT_ID,
            "bot_name": "Target Architect Bot (rebind)",
            "owner_id": "other_user",
            "owner_name": "other_user",
            "creator_id": "other_user",
            "entity_id": "other_user",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "status": "ACTIVE",
            "ext": {"is_domain_bot": True},
        }
    )
    # Application-coding bot (template_type column drives the service gate).
    bot_repo.insert(
        {
            "bot_id": CODING_BOT_ID,
            "bot_name": "App Coding Bot (rebind)",
            "owner_id": "test_user",
            "owner_name": "test_user",
            "creator_id": "test_user",
            "entity_id": "test_user",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "status": "ACTIVE",
            "template_type": "applicationCoding",
        }
    )
    # Template currently bound to a *different* architect → rebind is a real change.
    world.get(TemplateRepository).insert(
        {
            "bot_id": CODING_BOT_ID,
            "ext": {
                "architect_bot_id": OTHER_ARCHITECT_BOT_ID,
                "template": "appcoding",
            },
        }
    )


@endpoint_test(
    method="PUT",
    path="/api/bots/{architect_bot_id}/architect-rebind",
    scenario="ok_rebind_coding_bot",
    input=CaseInput(
        path_params={"architect_bot_id": ARCHITECT_BOT_ID},
        headers={"x-user-id": "test_user"},
        json_body={"target_architect_bot_id": TARGET_ARCHITECT_BOT_ID, "coding_bot_ids": [CODING_BOT_ID]},
    ),
    seed=_seed_architect_and_coding_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "source_architect_bot_id": ARCHITECT_BOT_ID,
                "target_architect_bot_id": TARGET_ARCHITECT_BOT_ID,
                "total": 1,
                "succeeded": 1,
                "failed": 0,
            },
        },
    ),
)
def rebind_architect_bot_ok():
    """Happy path: rebind one app-coding bot onto the architect bot (owner-scoped)."""


@endpoint_test(
    method="PUT",
    path="/api/bots/{architect_bot_id}/architect-rebind",
    scenario="anonymous_user",
    input=CaseInput(
        path_params={"architect_bot_id": ARCHITECT_BOT_ID},
        headers={"x-user-id": "anonymous"},
        json_body={"target_architect_bot_id": TARGET_ARCHITECT_BOT_ID, "coding_bot_ids": [CODING_BOT_ID]},
    ),
    seed=_seed_architect_and_coding_bot,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def rebind_architect_bot_anonymous():
    """Error path: anonymous operator → success=False + error_code=400 (real guard)."""
