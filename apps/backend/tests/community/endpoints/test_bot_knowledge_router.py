"""Registered HTTP cases for current Bot knowledge selection.

Use real services and persistence; only the external catalog uses its local DI
seam. These cases complement the lifecycle flow and satisfy the endpoint gate.
"""

from __future__ import annotations

from agentclaw.community.api.bot_knowledge_service import BotKnowledgeServiceProtocol
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.kernel.knowledge import BotKnowledgeScope
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.knowledge_catalog import KnowledgeCatalogPlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.framework.world import World


_BOT_ID = "knowledge_bot"
_OWNER_ID = "knowledge_owner"
_READER_ID = "knowledge_reader"
_KB_ID = "knowledge_base"


def _seed_bot(world: World) -> None:
    env = get_current_env()
    for user_id in (_OWNER_ID, _READER_ID):
        make_staff_user(world, user_id=user_id)
    with world.get(DatabasePlugin).orm_session() as session:
        space = SpaceModel(
            space_code="knowledge-endpoint",
            space_type="TEAM",
            name="Knowledge endpoint",
            env=env,
            created_by=_OWNER_ID,
            updated_by=_OWNER_ID,
        )
        session.add(space)
        session.flush()
        session.add_all(
            SpaceMemberModel(
                space_id=space.id,
                user_id=user_id,
                role="MEMBER",
                env=env,
                created_by=_OWNER_ID,
            )
            for user_id in (_OWNER_ID, _READER_ID)
        )
        session.add(
            BotModel(
                bot_id=_BOT_ID,
                bot_name="Knowledge Bot",
                owner_id=_OWNER_ID,
                creator_id=_OWNER_ID,
                entity_id=_OWNER_ID,
                entity_type="staff",
                space_id=space.id,
                env=env,
                bot_type="personal",
            )
        )
    world.get(KnowledgeCatalogPlugin).set_response("list_authorized", (_KB_ID,))


def _seed_enabled(world: World) -> None:
    _seed_bot(world)
    world.get(BotKnowledgeServiceProtocol).set_enabled(
        scope=BotKnowledgeScope(_BOT_ID, _OWNER_ID, get_current_env()),
        actor_id=_OWNER_ID,
        kb_id=_KB_ID,
        enabled=True,
        expected_revision=0,
    )


def _seed_not_granted(world: World) -> None:
    _seed_bot(world)
    world.get(KnowledgeCatalogPlugin).set_response("list_authorized", ())


def _assert_enabled_persisted(_response, world: World) -> None:
    with world.get(DatabasePlugin).orm_session() as session:
        row = session.query(BotKnowledgeBindingModel).one()
        assert row.kb_id == _KB_ID
        assert row.enabled is True
        assert row.revision == 1
        assert row.updated_by == _OWNER_ID


def _assert_no_selection(_response, world: World) -> None:
    with world.get(DatabasePlugin).orm_session() as session:
        assert session.query(BotKnowledgeBindingModel).count() == 0


@endpoint_test(
    method="GET",
    path="/api/bot-knowledge/{bot_id}",
    scenario="owner_reads_current_selection",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"owner_id": _OWNER_ID},
        headers={"x-user-id": _OWNER_ID},
    ),
    seed=_seed_enabled,
    expect=ExpectSuccess(
        json_contains={
            "items": [{"kb_id": _KB_ID, "enabled": True, "revision": 1}],
            "next_cursor": None,
            "runtime_selection": "current",
        }
    ),
)
def get_current_selection_ok():
    """The owner can read persisted current selection through the real route."""


@endpoint_test(
    method="GET",
    path="/api/bot-knowledge/{bot_id}",
    scenario="space_member_without_bot_editor_is_forbidden",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"owner_id": _OWNER_ID},
        headers={"x-user-id": _READER_ID},
    ),
    seed=_seed_bot,
    expect=ExpectError(
        status=403, json_contains={"detail": {"code": "BOT_EDITOR_REQUIRED"}}
    ),
)
def get_current_selection_forbidden():
    """Space membership alone does not allow reading another Bot's selection."""


@endpoint_test(
    method="PUT",
    path="/api/bot-knowledge/{bot_id}/{kb_id}",
    scenario="owner_enables_granted_knowledge",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "kb_id": _KB_ID},
        headers={"x-user-id": _OWNER_ID},
        json_body={"owner_id": _OWNER_ID, "enabled": True, "expected_revision": 0},
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        json_contains={"kb_id": _KB_ID, "enabled": True, "revision": 1}
    ),
    extra_assertions=(_assert_enabled_persisted,),
)
def put_current_selection_ok():
    """Enabling authorized knowledge persists the first current-state revision."""


@endpoint_test(
    method="PUT",
    path="/api/bot-knowledge/{bot_id}/{kb_id}",
    scenario="ungranted_knowledge_is_forbidden_without_writing",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "kb_id": _KB_ID},
        headers={"x-user-id": _OWNER_ID},
        json_body={"owner_id": _OWNER_ID, "enabled": True, "expected_revision": 0},
    ),
    seed=_seed_not_granted,
    expect=ExpectError(
        status=403, json_contains={"detail": {"code": "KNOWLEDGE_NOT_GRANTED"}}
    ),
    extra_assertions=(_assert_no_selection,),
)
def put_current_selection_not_granted():
    """Even the Bot owner cannot enable knowledge absent from platform grants."""
