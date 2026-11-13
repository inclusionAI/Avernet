"""Spec §12.2 六条验收清单的防退化锚点测试(真仓储 + 引擎直查)。

与按任务拆的测试(A01-A09、endpoints、service 单测)有意保留冗余:
本文件每个用例对应 spec §12.2 的一条验收,逐字钉住验收语义,任何人
改动协议形态(列结构、响应字段、payload 约定)都会先在此断言失败。
全部走真实 service + in-memory SQLite(``db`` fixture),并以引擎直查
两张表断言落库事实,而非只信服务返回值:

- #1 创建接口落 1 行 Collet + 1 行初始 Event;context_spec 携带
  objective/method_kind/executor_ref,current_snapshot 携带 progress;
- #2 创建接口拒绝缺 executor / 缺 responsible principal 的指定执行请求
  (P1 钉结构校验半边:外部 Membership/Grant/Policy 留给 P2 接缝);
- #3 user_interaction.posted 正文直存 payload.content,payload 不出现
  content_ref;
- #4 每个事件都带 parent_event 字段;trunk_events=true 只过滤
  task_progress.events 列表,current_snapshot/current_event/统计不变;
- #5 查询是三段式响应(collab_context_id/context_version +
  current_snapshot + current_event + task_progress.events[]),无 steps;
- #6 查询纯读:get_status 反复调用不推进版本、不改写落库快照。
"""

import dataclasses
import json

import pytest
from sqlalchemy import func, select

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
    ContextCollabEventModel,
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

OBJECTIVE = "整理 Context 协作协议评审结论并提交可评审 PR"
PRINCIPAL = PrincipalRef(principal_type="human", principal_id="u_1001")
EXECUTOR = AgentRef(agent_type="bot", agent_id="bot_coding_001")

# task_progress.events[] 条目的 7 字段投影集(§12.2 #5:不含 steps)。
_EVENT_ENTRY_FIELDS = frozenset(
    {
        "event_id",
        "parent_event",
        "event_kind",
        "primitive_type",
        "status",
        "summary",
        "content",
    }
)


def make_create_command(**overrides) -> ContextCreateCommand:
    """构造创建命令(spec §6.2 指定执行请求示例);可按字段覆盖。"""
    fields = {
        "space": SpaceRef(space_id="space_workspace_001", environment="prod"),
        "principal_ref": PRINCIPAL,
        "product_kind": "team_todo",
        "workflow_kind": "team_todo_execution",
        "method_kind": "Single",
        "executor_ref": EXECUTOR,
        "objective": OBJECTIVE,
        "resource_refs": [
            ResourceRef(
                resource_type="forum_topic", resource_id="topic_001", revision="3"
            )
        ],
    }
    fields.update(overrides)
    return ContextCreateCommand(**fields)


def make_actor() -> ActorContext:
    return ActorContext(
        actor=PrincipalRef(principal_type="human", principal_id="u_1001"),
        source="api",
    )


def make_append_command(event_kind: str, **overrides) -> EventAppendCommand:
    """构造追加事件命令(spec §6.3 示例形);默认可追加的合法事件体。"""
    fields = {
        "expected_version": 1,
        "event_kind": event_kind,
        "primitive_type": "Task",
        "actor_ref": PRINCIPAL,
        "parent_event": None,
        "visibility": "public",
        "payload": {},
    }
    fields.update(overrides)
    return EventAppendCommand(**fields)


@pytest.fixture
def service(db) -> ContextCollabService:
    """挂真 SQLite 仓储的服务;authorizer 走构造默认(结构授权器)。"""
    return ContextCollabService(
        method_store=InMemoryMethodStore(),
        repository=ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG),
        product_catalog=PRODUCT_CATALOG,
    )


def _count(engine, model) -> int:
    """引擎直查行数;每个测试独占一个内存库,count 即本测试写入量。"""
    with engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(model))


def _collet_json(engine, collab_context_id, column) -> dict | str | None:
    """引擎直查 Collet 行的 JSON 列(context_spec / current_snapshot)。"""
    with engine.connect() as conn:
        return conn.scalar(
            select(column).where(
                ContextCollabColletModel.collab_context_id == collab_context_id
            )
        )


def _stored_snapshot(engine, collab_context_id) -> dict:
    return json.loads(
        _collet_json(
            engine, collab_context_id, ContextCollabColletModel.current_snapshot
        )
    )


def _event_payload(engine, collab_context_id, event_id) -> dict:
    """引擎直查事件行 payload 列(原生 JSON,无旁路内容存储)。"""
    with engine.connect() as conn:
        raw = conn.scalar(
            select(ContextCollabEventModel.payload).where(
                ContextCollabEventModel.collab_context_id == collab_context_id,
                ContextCollabEventModel.event_id == event_id,
            )
        )
    return json.loads(raw)


def _append_message(service, ctx_id, *, expected_version, parent_event, content) -> str:
    result = service.append_event(
        ctx_id,
        make_append_command(
            "user_interaction.posted",
            expected_version=expected_version,
            primitive_type="Message",
            parent_event=parent_event,
            payload={"content": content},
        ),
        make_actor(),
    )
    return result.event_id


class TestSpecAcceptance:
    """spec §12.2 六条验收,逐条一个锚点测试。"""

    def test_1_create_persists_one_collet_row_with_spec_and_progress(
        self, service, engine
    ):
        """§12.2 #1:创建落 1 行 Collet + 1 行初始 Event;目标/方法/执行者
        与进度都在 Collet JSON 快照中(引擎直查列证,非服务回声)。"""
        created = service.create_context(make_create_command(), make_actor())

        assert _count(engine, ContextCollabColletModel) == 1
        assert _count(engine, ContextCollabEventModel) == 1
        spec = json.loads(
            _collet_json(
                engine, created.collab_context_id, ContextCollabColletModel.context_spec
            )
        )
        # context_spec 携带任务目标、方法与执行者(引用值,不复制注册表)。
        assert spec["objective"] == OBJECTIVE
        assert spec["method_kind"] == "Single"
        assert spec["executor_ref"] == EXECUTOR.to_dict()
        assert spec["principal_ref"] == PRINCIPAL.to_dict()
        # current_snapshot 携带进度(§3.4.2 ProgressSnapshot 五字段)。
        snapshot = _stored_snapshot(engine, created.collab_context_id)
        assert snapshot["objective"] == OBJECTIVE
        assert snapshot["method_kind"] == "Single"
        progress = snapshot["progress"]
        assert progress["percent"] == 0
        assert progress["completed_count"] == 0
        assert progress["active_count"] == 0
        assert progress["blocked_count"] == 0
        # 初始事件行:task.created / Task、无 parent。
        with engine.connect() as conn:
            initial = conn.execute(
                select(
                    ContextCollabEventModel.event_kind,
                    ContextCollabEventModel.primitive_type,
                    ContextCollabEventModel.parent_event,
                ).where(
                    ContextCollabEventModel.collab_context_id
                    == created.collab_context_id
                )
            ).one()
        assert initial.event_kind == "task.created"
        assert initial.primitive_type == "Task"
        assert initial.parent_event is None

    def test_2_create_rejects_missing_executor_and_missing_principal(
        self, service, engine
    ):
        """§12.2 #2:创建接口拒绝缺 executor、缺 responsible 的指定执行请求。

        P1 钉结构校验半边(G1 接缝):method=Single 而 executor_ref=None
        由服务路径拒绝;principal 缺失在 wire→command 的取值路径
        (PrincipalRef.from_dict,delivery to_domain 同路径)拒绝为
        领域校验错。Capability/外部授权由 P2 接缝补充,不在 P1 范围。
        """
        with pytest.raises(ContextCollabValidationError, match="executor_ref"):
            service.create_context(make_create_command(executor_ref=None), make_actor())

        # responsible(principal_ref)在请求中缺失 → wire 解析为命令字段的
        # 取值路径即拒绝;不允许装配出无责任主体的创建命令。
        with pytest.raises(ContextCollabValidationError, match="PrincipalRef"):
            PrincipalRef.from_dict({})

        # 两条拒绝路径都不落任何行(不写半态创建)。
        assert _count(engine, ContextCollabColletModel) == 0
        assert _count(engine, ContextCollabEventModel) == 0

    def test_3_user_interaction_posted_stores_inline_content_without_ref(
        self, service, engine
    ):
        """§12.2 #3:user_interaction.posted 正文直接 payload.content,
        不出现 content_ref(引擎直查落库 payload,非服务回声)。"""
        created = service.create_context(make_create_command(), make_actor())
        content = "评审结论:G4 合约一致性优先落地"

        msg_event_id = _append_message(
            service,
            created.collab_context_id,
            expected_version=created.context_version,
            parent_event=created.current_event["event_id"],
            content=content,
        )

        payload = _event_payload(engine, created.collab_context_id, msg_event_id)
        assert payload["content"] == content
        assert "content_ref" not in payload
        # top-level 仅 content 一键:正文内联直存,没有任何内容引用键。
        assert set(payload) == {"content"}

    def test_4_every_event_carries_parent_event_and_trunk_only_filters(
        self, service, engine
    ):
        """§12.2 #4:事件都带 parent_event 字段;trunk_events=true 只筛选
        主干列表,current_snapshot / current_event / 统计两视角一致。"""
        created = service.create_context(make_create_command(), make_actor())
        initial_event_id = created.current_event["event_id"]
        main_event_id = _append_message(  # 主干延续:parent=当前链头(初始事件)。
            service,
            created.collab_context_id,
            expected_version=created.context_version,
            parent_event=initial_event_id,
            content="主干消息",
        )
        branch_event_id = _append_message(  # 分支:parent 挂更早的初始事件。
            service,
            created.collab_context_id,
            expected_version=created.context_version + 1,
            parent_event=initial_event_id,
            content="分支补充",
        )
        # R9:活动链取 current 沿 parent 回溯;分支追加后要把链头引回主干,
        # 分支才会落在链外(trunk_events=true 被滤除)。
        _append_message(
            service,
            created.collab_context_id,
            expected_version=created.context_version + 2,
            parent_event=main_event_id,
            content="回到主干",
        )

        full = service.get_status(
            created.collab_context_id, trunk_events=False, context=make_actor()
        )
        trunk = service.get_status(
            created.collab_context_id, trunk_events=True, context=make_actor()
        )

        # 每个事件条目都带 parent_event 键(初始事件为 None 但键存在)。
        for entry in full.task_progress.events:
            assert "parent_event" in entry
        assert full.task_progress.events[0]["parent_event"] is None
        # 分支事件落库行携带其因果 parent(更早的初始事件,非链头)。
        with engine.connect() as conn:
            branch_parent = conn.scalar(
                select(ContextCollabEventModel.parent_event).where(
                    ContextCollabEventModel.collab_context_id
                    == created.collab_context_id,
                    ContextCollabEventModel.event_id == branch_event_id,
                )
            )
        assert branch_parent == initial_event_id

        # trunk 视角:列表更短,只含主干事件(分支事件被活动链滤除)。
        assert len(trunk.task_progress.events) < len(full.task_progress.events)
        assert len(full.task_progress.events) == 4
        assert len(trunk.task_progress.events) == 3
        # 统计与状态不变(R6:推导先于 trunk 过滤):快照(含 progress)、
        # current_event、版本两视角逐字一致。
        assert trunk.current_snapshot == full.current_snapshot
        assert trunk.current_event == full.current_event
        assert trunk.context_version == full.context_version
        assert trunk.current_snapshot["progress"] == full.current_snapshot["progress"]

    def test_5_status_shape_is_three_sections_without_steps(self, service):
        """§12.2 #5:查询返回 current_snapshot / current_event /
        task_progress.events[],不使用 steps[] 字段(顶层键集逐字钉死)。"""
        created = service.create_context(make_create_command(), make_actor())
        _append_message(
            service,
            created.collab_context_id,
            expected_version=created.context_version,
            parent_event=created.current_event["event_id"],
            content="进度与结构一并钉住",
        )
        status = service.get_status(
            created.collab_context_id, trunk_events=False, context=make_actor()
        )

        # 顶层恰好五键:标识 + 版本 + 快照 + 当前事件 + 进度;无多余段。
        assert {field.name for field in dataclasses.fields(status)} == {
            "collab_context_id",
            "context_version",
            "current_snapshot",
            "current_event",
            "task_progress",
        }
        # task_progress 顶层恰好两键,事件列表字段集恰为 7 字段投影。
        assert {field.name for field in dataclasses.fields(status.task_progress)} == {
            "total_events",
            "events",
        }
        assert status.task_progress.events
        for entry in status.task_progress.events:
            assert set(entry) == set(_EVENT_ENTRY_FIELDS)
            assert "steps" not in entry
        # 快照内携带 progress,不含 steps 字段。
        assert "progress" in status.current_snapshot
        assert "steps" not in status.current_snapshot

    def test_6_get_status_does_not_rewrite_collet(self, service, engine):
        """§12.2 #6:查询纯读:连续 get_status 不推进 context_version、
        不反向改写落库的 Collet 快照(引擎直查列证)。"""
        created = service.create_context(make_create_command(), make_actor())
        _append_message(
            service,
            created.collab_context_id,
            expected_version=created.context_version,
            parent_event=created.current_event["event_id"],
            content="第二次查询前的固定消息",
        )
        with engine.connect() as conn:
            version_before, snapshot_raw_before = conn.execute(
                select(
                    ContextCollabColletModel.context_version,
                    ContextCollabColletModel.current_snapshot,
                ).where(
                    ContextCollabColletModel.collab_context_id
                    == created.collab_context_id
                )
            ).one()

        first = service.get_status(
            created.collab_context_id, trunk_events=False, context=make_actor()
        )
        second = service.get_status(
            created.collab_context_id, trunk_events=True, context=make_actor()
        )

        # 两次查询(含 trunk 视角)不推进聚合版本。
        assert second.context_version == first.context_version
        assert first.context_version == int(version_before)
        # 落库快照字节不动:查询响应是实时投影,不回写 Collet 行。
        with engine.connect() as conn:
            version_after, snapshot_raw_after = conn.execute(
                select(
                    ContextCollabColletModel.context_version,
                    ContextCollabColletModel.current_snapshot,
                ).where(
                    ContextCollabColletModel.collab_context_id
                    == created.collab_context_id
                )
            ).one()
        assert version_after == version_before
        assert snapshot_raw_after == snapshot_raw_before
        # 两次查询投影一致且 equal 落库快照(实时投影不改写权威行)。
        assert second.current_snapshot == first.current_snapshot
        assert _stored_snapshot(engine, created.collab_context_id) == json.loads(
            snapshot_raw_before
        )
