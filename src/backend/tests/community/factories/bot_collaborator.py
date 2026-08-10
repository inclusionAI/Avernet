"""Seed helpers for the ``bot_collaborator`` domain.

Provides factory functions to create bots and collaborators for testing.
"""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
    CollaboratorRole,
)
from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol
from tests.community.framework.world import World


def make_bot(
    world: World,
    *,
    bot_id: str = "bot_test",
    owner_id: str = "u_owner",
    owner_name: str | None = None,
    bot_type: str = "service",
    status: str = "ACTIVE",
    binding_id: int | None = None,
) -> dict:
    """Seed a bot and return the resulting record.

    Parameters:
        bot_id: Bot identifier
        owner_id: Owner user ID
        owner_name: Owner display name (optional)
        bot_type: Bot type (service, desktop, personal)
        status: Bot status (ACTIVE, PENDING, etc.)
        binding_id: Device binding ID to link (for relay/cron flows that
            resolve a device connection). Seed it via
            ``tests.community.factories.devices.make_active_local_device`` first.
    """
    bot_repo = world.get(BotRepository)
    return bot_repo.insert(
        {
            "bot_id": bot_id,
            "bot_name": f"Bot {bot_id}",
            "owner_id": owner_id,
            "owner_name": owner_name,
            "bot_type": bot_type,
            "status": status,
            "entity_id": owner_id,
            "entity_type": "user",
            "creator_id": owner_id,
            "binding_id": binding_id,
        }
    )


def make_collaborator(
    world: World,
    *,
    bot_id: str = "bot_test",
    owner_id: str = "u_owner",
    user_id: str = "u_collab",
    user_name: str | None = None,
    role: str = "admin",
    operator_id: str = "u_owner",
) -> dict:
    """Seed a collaborator and return the resulting record.

    Note: Requires bot to exist first (call make_bot before this).

    Parameters:
        bot_id: Bot identifier
        owner_id: Owner user ID
        user_id: Collaborator user ID
        user_name: Collaborator display name (optional)
        role: Collaborator role (admin, member)
        operator_id: User performing the operation
    """
    svc = world.get(CollaboratorService)
    return svc.add_collaborator(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        user_name=user_name,
        role=CollaboratorRole(role),
        operator_id=operator_id,
    )


def make_collaborator_record(
    world: World,
    *,
    bot_pk: int,
    bot_id: str = "bot_test",
    owner_id: str = "u_owner",
    user_id: str = "u_collab",
    role: str = "admin",
    operator_id: str = "u_owner",
) -> dict:
    """Directly insert a collaborator record via repository.

    Use this when you need to bypass service-level validation.
    """
    repo = world.get(CollaboratorRepositoryProtocol)
    record = repo.insert(
        {
            "bot_pk": bot_pk,
            "bot_id": bot_id,
            "owner_id": owner_id,
            "user_id": user_id,
            "role": role,
            "operator_id": operator_id,
        }
    )
    return record