"""ACCS L6 product-form E2E flows through the real context-collab core.

These tests are intentionally repository-backed rather than fake-repository-only:
both cases exercise the full in-process domain path from L6 profile/L5 method,
L2/L3 references, L4 Collet/Task/Event, G1 access filtering, G2 optimistic
concurrency and event parent checks, G3 progress projection, and G4 schema/state
machine resolution. They use the real SQLite-backed repository and do not touch
HTTP delivery, which is outside ACCS layers L1-L6 in this implementation.
"""

import json

import pytest

from sqlalchemy import select

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    ContextCreateCommand,
    EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.services.context_collab_service import (
    ContextCollabService,
)
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

SPACE = SpaceRef(space_id="bbs_community_space", environment="prod")
OWNER = PrincipalRef(principal_type="human", principal_id="u_topic_owner")
PARTICIPANT = PrincipalRef(principal_type="human", principal_id="u_forum_1001")
HELPER = AgentRef(agent_type="bot", agent_id="bot_help_helper_001")


def _actor(principal: PrincipalRef) -> ActorContext:
    return ActorContext(actor=principal, source="api")


def _create_command(**overrides) -> ContextCreateCommand:
    fields = {
        "space": SPACE,
        "principal_ref": OWNER,
        "product_kind": "bbs_public_discussion",
        "workflow_kind": "bbs_public_discussion",
        "method_kind": "Open + Group",
        "executor_ref": None,
        "objective": "收集 ACCS 公开讨论协议的社区反馈",
        "resource_refs": [
            ResourceRef(
                resource_type="forum_topic", resource_id="topic_001", revision="3"
            )
        ],
    }
    fields.update(overrides)
    return ContextCreateCommand(**fields)


def _append(
    service: ContextCollabService,
    *,
    collab_context_id: str,
    expected_version: int,
    event_kind: str,
    primitive_type: str,
    actor: PrincipalRef,
    parent_event: str | None,
    **payload,
):
    return service.append_event(
        collab_context_id,
        EventAppendCommand(
            expected_version=expected_version,
            event_kind=event_kind,
            primitive_type=primitive_type,
            actor_ref=actor,
            parent_event=parent_event,
            visibility=payload.pop("visibility", "public"),
            payload=payload,
        ),
        _actor(actor),
    )


def _context_spec(engine, collab_context_id: str) -> dict:
    with engine.connect() as conn:
        value = conn.scalar(
            select(ContextCollabColletModel.context_spec).where(
                ContextCollabColletModel.collab_context_id == collab_context_id
            )
        )
    assert value is not None
    return json.loads(value)


class TestBbsProductE2E:
    def test_bbs_public_discussion_e2e(self, db, engine):
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG),
            product_catalog=PRODUCT_CATALOG,
        )
        created = service.create_context(_create_command(), _actor(OWNER))

        assert created.current_snapshot["method_kind"] == "Open + Group"
        assert created.current_snapshot["executor_ref"] is None
        spec = _context_spec(engine, created.collab_context_id)
        assert spec["product_kind"] == "bbs_public_discussion"
        assert spec["workflow_kind"] == "bbs_public_discussion"

        # A BBS body is a root Message, never a reply to the Task skeleton.
        parent_event = None
        version = created.context_version

        owner_post = _append(
            service,
            collab_context_id=created.collab_context_id,
            expected_version=version,
            event_kind="user_interaction.posted",
            primitive_type="Message",
            actor=OWNER,
            parent_event=parent_event,
            content="欢迎大家评审 ACCS 协议的信封与事件语义。",
        )
        assert owner_post.current_status == "created"

        participant_post = _append(
            service,
            collab_context_id=created.collab_context_id,
            expected_version=owner_post.context_version,
            event_kind="user_interaction.posted",
            primitive_type="Message",
            actor=PARTICIPANT,
            parent_event=owner_post.event_id,
            content="建议公开讨论只把 Message 视作用户互动，不把点赞写入 L4。",
        )
        assert participant_post.context_version == 3

        status = service.get_status(
            created.collab_context_id,
            trunk_events=False,
            context=_actor(PARTICIPANT),
        )
        assert [event["content"] for event in status.task_progress.events] == [
            None,
            "欢迎大家评审 ACCS 协议的信封与事件语义。",
            "建议公开讨论只把 Message 视作用户互动，不把点赞写入 L4。",
        ]
        assert status.current_event["event_id"] == participant_post.event_id
        assert status.current_snapshot["status"] == "created"
        assert status.task_progress.total_events == 3

        # Public Open+Group Message is visible to an unrelated authenticated reader.
        reader_status = service.get_status(
            created.collab_context_id,
            trunk_events=True,
            context=_actor(PrincipalRef("human", "u_reader_9999")),
        )
        assert [event["event_id"] for event in reader_status.task_progress.events] == [
            owner_post.event_id,
            participant_post.event_id,
        ]

    def test_bbs_help_request_with_goal_e2e(self, db, engine):
        service = ContextCollabService(
            method_store=InMemoryMethodStore(),
            repository=ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG),
            product_catalog=PRODUCT_CATALOG,
        )
        created = service.create_context(
            _create_command(
                product_kind="bbs_help_request",
                workflow_kind="bbs_help_request_goal",
                executor_ref=HELPER,
                objective="解决 BBS 求助帖的目录权限错误并提交可验证修复",
            ),
            _actor(OWNER),
        )
        assert created.current_snapshot["status"] == "created"
        spec = _context_spec(engine, created.collab_context_id)
        assert spec["product_kind"] == "bbs_help_request"
        assert spec["workflow_kind"] == "bbs_help_request_goal"

        parent_event = created.current_event["event_id"]
        version = created.context_version

        context_description = _append(
            service,
            collab_context_id=created.collab_context_id,
            expected_version=version,
            event_kind="user_interaction.posted",
            primitive_type="Message",
            actor=OWNER,
            parent_event=parent_event,
            content="目标：修复 prod 环境目录只读错误，验收需附测试输出。",
        )
        public_hint = _append(
            service,
            collab_context_id=created.collab_context_id,
            expected_version=context_description.context_version,
            event_kind="user_interaction.posted",
            primitive_type="Message",
            actor=PARTICIPANT,
            parent_event=context_description.event_id,
            content="我之前遇到过，看起来可能是挂载策略覆盖了目录权限。",
        )

        # Open admission does not grant public participants task authority:
        # unrelated participants may reply publicly but cannot advance the goal.
        with pytest.raises(ContextCollabValidationError, match="only the principal"):
            service.append_event(
                created.collab_context_id,
                EventAppendCommand(
                    expected_version=public_hint.context_version,
                    event_kind="task.started",
                    primitive_type="Task",
                    actor_ref=PARTICIPANT,
                    parent_event=public_hint.event_id,
                    visibility="public",
                    payload={},
                ),
                _actor(PARTICIPANT),
            )

        # L2 helper-agent actor is mapped as the L4 designated executor in G1.
        started = _append(
            service,
            collab_context_id=created.collab_context_id,
            expected_version=public_hint.context_version,
            event_kind="task.started",
            primitive_type="Task",
            actor=PrincipalRef("bot", HELPER.agent_id),
            parent_event=public_hint.event_id,
        )
        assert started.current_status == "in_progress"

        progress = _append(
            service,
            collab_context_id=created.collab_context_id,
            expected_version=started.context_version,
            event_kind="task.progress.updated",
            primitive_type="Activity",
            actor=PrincipalRef("bot", HELPER.agent_id),
            parent_event=started.event_id,
            percent=50,
            summary="复现问题并定位挂载策略。",
        )
        assert progress.current_status == "in_progress"

        submitted = _append(
            service,
            collab_context_id=created.collab_context_id,
            expected_version=progress.context_version,
            event_kind="task.submitted",
            primitive_type="Task",
            actor=PrincipalRef("bot", HELPER.agent_id),
            parent_event=progress.event_id,
            artifacts=[
                {"resource_type": "pr", "resource_id": "pr_help_001", "revision": "1"}
            ],
        )
        assert submitted.current_status == "reviewing"

        accepted = _append(
            service,
            collab_context_id=created.collab_context_id,
            expected_version=submitted.context_version,
            event_kind="task.accepted",
            primitive_type="Task",
            actor=OWNER,
            parent_event=submitted.event_id,
        )
        assert accepted.current_status == "done"

        status = service.get_status(
            created.collab_context_id,
            trunk_events=True,
            context=_actor(PARTICIPANT),
        )
        assert status.current_snapshot["status"] == "done"
        assert status.context_version == accepted.context_version
        assert status.task_progress.total_events == 7
        assert status.current_snapshot["progress"]["percent"] == 100
        assert status.current_event["event_kind"] == "task.accepted"
        assert [event["event_kind"] for event in status.task_progress.events] == [
            "task.created",
            "user_interaction.posted",
            "user_interaction.posted",
            "task.started",
            "task.progress.updated",
            "task.submitted",
            "task.accepted",
        ]

        # Open admission admits public replies, not private writes from
        # unrelated participants; permission to post is not blanket write access.
        with pytest.raises(ContextCollabValidationError):
            service.append_event(
                created.collab_context_id,
                EventAppendCommand(
                    expected_version=accepted.context_version,
                    event_kind="user_interaction.posted",
                    primitive_type="Message",
                    actor_ref=PARTICIPANT,
                    parent_event=accepted.event_id,
                    visibility="private",
                    payload={"content": "这条不应由公开参与者写入 private 范围。"},
                ),
                _actor(PARTICIPANT),
            )
