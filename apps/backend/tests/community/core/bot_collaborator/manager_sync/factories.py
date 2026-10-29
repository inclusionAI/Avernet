"""Seeding factories for the manager-sync test families.

Extracted from ``tests/community/repository/bot/test_manager_sync_reader.py``
so the unit suites (service / dispatcher / bounds / failure isolation)
and the ``tests/community/contracts`` conformance suites seed the same
in-memory SQLite rows through one identical helper — existing ORM
models only, no DDL, writes under the tenant guard so ``BotModel`` rows
carry their stamping tenant.

The ``seed_sync_facts`` signature is the one frozen by the Task 4
reader-suite brief; it is kept verbatim and re-exported here for unit
and contract suites alike — a suite must never re-implement it.
"""

from __future__ import annotations

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.kernel.bot_manager_sync import SyncKey
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


# ── seeding helpers (existing ORM models only, no DDL) ──────────────


def seed_sync_facts(
    db,
    *,
    tenant,
    env,
    bot_pk,
    entity_id,
    owner_id,
    rows=(),
    space_id=None,
    bot_id=None,
):
    """Insert one Bot plus its explicit collaborator rows, return the SyncKey."""
    bot_id = bot_id if bot_id is not None else f"bot-{entity_id}"
    with avernet_tenant_scope(tenant), db.orm_session() as session:
        session.add(
            BotModel(
                id=bot_pk,
                bot_id=bot_id,
                entity_id=entity_id,
                entity_type="staff",
                creator_id=owner_id,
                owner_id=owner_id,
                owner_name=owner_id,
                env=env,
                space_id=space_id,
                status="ACTIVE",
                is_delete=0,
            )
        )
        for user_id, role in rows:
            session.add(
                BotCollaboratorModel(
                    bot_pk=bot_pk,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    user_id=user_id,
                    user_name=user_id,
                    role=role,
                    operator_id=owner_id,
                    env=env,
                )
            )
    return SyncKey(tenant, env, bot_pk)


def seed_space(
    db,
    *,
    space_id,
    env,
    space_type="TEAM",
    space_code=None,
    name="space",
    personal_owner_id=None,
):
    with db.orm_session() as session:
        session.add(
            SpaceModel(
                id=space_id,
                space_code=space_code or f"spc-{space_id}",
                space_type=space_type,
                name=name,
                personal_owner_id=personal_owner_id,
                env=env,
                created_by="creator",
                updated_by="creator",
            )
        )
    return space_id


def seed_members(db, *, space_id, env, user_ids, status="ACTIVE", role="MEMBER"):
    with db.orm_session() as session:
        for user_id in user_ids:
            session.add(
                SpaceMemberModel(
                    space_id=space_id,
                    user_id=user_id,
                    user_name=user_id,
                    role=role,
                    status=status,
                    env=env,
                    created_by="creator",
                )
            )
    return user_ids


def seed_space_bots(db, *, tenant, env, space_id, bot_pks, user_id="collab"):
    """Bulk-seed live bots inside one Space, each with one explicit row."""
    with avernet_tenant_scope(tenant), db.orm_session() as session:
        for pk in bot_pks:
            session.add(
                BotModel(
                    id=pk,
                    bot_id=f"bot-{pk}",
                    entity_id=f"entity-{pk}",
                    entity_type="staff",
                    creator_id="owner",
                    owner_id="owner",
                    env=env,
                    space_id=space_id,
                    status="ACTIVE",
                    is_delete=0,
                )
            )
            session.add(
                BotCollaboratorModel(
                    bot_pk=pk,
                    bot_id=f"bot-{pk}",
                    owner_id="owner",
                    user_id=f"{user_id}-{pk}",
                    user_name=f"{user_id}-{pk}",
                    role="admin",
                    operator_id="owner",
                    env=env,
                )
            )
    return bot_pks
