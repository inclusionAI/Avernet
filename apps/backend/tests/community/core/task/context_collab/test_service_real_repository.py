"""真仓储集成验收(create→append→get 流,A01-A09)+ DI 装配冒烟。

与 Task 7/10/11 的 fake-repo 单测互补:这里 ``ContextCollabService`` 挂
真实 ``ContextCollabRepository`` + in-memory SQLite(``db`` fixture),验收
以引擎直查两张表钉行数/列值,而非只信服务返回值。A01-A09 语义:

- A01 创建后 collet/event 各恰好 1 行;落库 snapshot objective 携带
  spec §6.2 示例文本;
- A02 追加 user_interaction.posted:事件 +1、payload 原生 JSON 直存、
  collet.context_version=2;
- A03 task.started( actor=principal)→ 落库 snapshot status=in_progress;
- A04 task.completed 接纳成功但状态不越迁(无规则),事件行数 +1;
- A06 started → submitted(artifacts 非空)→ reviewing → accepted
  (actor=principal)→ done;
- A07/08 分支事件(parent=更早事件)在 trunk_events=True/False 两视角间
  只差 task_progress.events 列表,current_snapshot/current_event 一致;
- A09 并发:逐字重发被 CAS 拒绝(追加无幂等键,Ruling R8)
  均不落新行;stale expected_version 抛 Conflict 且不落半态;
- DI 冒烟:测试 profile 组合(TestingDatabaseModule + TaskPersistenceModule
  + TaskModule)与完整 ``build_injector(DeployProfile.TEST)`` 均能解析
  ``ContextCollabServiceProtocol`` 单例(镜像
  ``tests/community/repository/task/test_task_persistence_module.py``)。
"""

import json
from datetime import datetime, timezone

import pytest
from injector import Injector
from sqlalchemy import func, insert as sql_insert, select, update as sql_update

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
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
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    ContextCollabServiceProtocol,
    ContextCreateCommand,
    EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.services.context_collab_service import (
    ContextCollabService,
)
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.di.modules.task_persistence_module import (
    TaskPersistenceModule,
)
from agentclaw.community.di.modules.testing_database_module import (
    TestingDatabaseModule,
)

OBJECTIVE = "整理 Context 协作协议评审结论并提交可评审 PR"
PRINCIPAL = PrincipalRef(principal_type="human", principal_id="u_1001")
EXECUTOR = AgentRef(agent_type="bot", agent_id="bot_coding_001")


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


def make_actor(
    principal_type: str = "human", principal_id: str = "u_1001"
) -> ActorContext:
    return ActorContext(
        actor=PrincipalRef(principal_type, principal_id), source="api"
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
    return ContextCollabService(repository=ContextCollabRepository(db))


def _count(engine, model) -> int:
    """引擎直查行数;每个测试独占一个内存库,count 即本测试写入量。"""
    with engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(model))


def _collet_column(engine, collab_context_id, column):
    with engine.connect() as conn:
        return conn.scalar(
            select(column).where(
                ContextCollabColletModel.collab_context_id == collab_context_id
            )
        )


def _collet_snapshot(engine, collab_context_id) -> dict:
    return json.loads(
        _collet_column(
            engine, collab_context_id, ContextCollabColletModel.current_snapshot
        )
    )


def _event_column(engine, collab_context_id, event_id, column):
    with engine.connect() as conn:
        return conn.scalar(
            select(column).where(
                ContextCollabEventModel.collab_context_id == collab_context_id,
                ContextCollabEventModel.event_id == event_id,
            )
        )


def _event_payload(engine, collab_context_id, event_id) -> dict:
    return json.loads(
        _event_column(
            engine, collab_context_id, event_id, ContextCollabEventModel.payload
        )
    )


def _create_started(service) -> tuple[str, str, int]:
    """创建上下文并追加 task.started(actor=principal)到 in_progress。

    返回 (collab_context_id, initial_event_id, 版本 after started)。
    """
    created = service.create_context(make_create_command(), make_actor())
    started = service.append_event(
        created.collab_context_id,
        make_append_command(
            "task.started",
            expected_version=created.context_version,
            parent_event=created.current_event["event_id"],
        ),
        make_actor(),
    )
    assert started.current_status == "in_progress"
    return (
        created.collab_context_id,
        created.current_event["event_id"],
        started.context_version,
    )


class TestAcceptanceFlows:
    def test_a01_create_persists_one_collet_and_one_event(self, service, engine):
        created = service.create_context(make_create_command(), make_actor())

        assert created.collab_context_id.startswith("collet_")
        assert _count(engine, ContextCollabColletModel) == 1
        assert _count(engine, ContextCollabEventModel) == 1
        # 落库 snapshot 携带 spec §6.2 示例 objective 文本。
        snapshot = _collet_snapshot(engine, created.collab_context_id)
        assert snapshot["objective"] == OBJECTIVE
        assert snapshot["status"] == "created"
        # 初始事件行:task.created / Task / 无 parent(已无 sequence/trunk 列)。
        assert (
            _event_column(
                engine,
                created.collab_context_id,
                created.current_event["event_id"],
                ContextCollabEventModel.event_kind,
            )
            == "task.created"
        )
        assert (
            _event_column(
                engine,
                created.collab_context_id,
                created.current_event["event_id"],
                ContextCollabEventModel.primitive_type,
            )
            == "Task"
        )
        assert (
            _event_column(
                engine,
                created.collab_context_id,
                created.current_event["event_id"],
                ContextCollabEventModel.parent_event,
            )
            is None
        )

    def test_a02_append_user_interaction_posted_native_payload_and_version(
        self, service, engine
    ):
        created = service.create_context(make_create_command(), make_actor())
        content = "评审结论:G4 合约一致性优先落地"

        result = service.append_event(
            created.collab_context_id,
            make_append_command(
                "user_interaction.posted",
                expected_version=created.context_version,
                primitive_type="Message",
                parent_event=created.current_event["event_id"],
                payload={"content": content},
            ),
            make_actor(),
        )

        assert result.accepted is True
        assert result.context_version == 2
        assert result.event_id != created.current_event["event_id"]
        assert _count(engine, ContextCollabEventModel) == 2
        assert (
            _collet_column(
                engine,
                created.collab_context_id,
                ContextCollabColletModel.current_event_id,
            )
            == result.event_id
        )
        # collet 行版本 CAS 推进到 2;payload 原生 JSON 直存(ctx 无旁路内容表)。
        assert (
            _collet_column(
                engine,
                created.collab_context_id,
                ContextCollabColletModel.context_version,
            )
            == 2
        )
        assert result.event_id.startswith("evt_")
        assert _event_payload(
            engine, created.collab_context_id, result.event_id
        ) == {"content": content}

    def test_a03_task_started_moves_snapshot_to_in_progress(self, service, engine):
        created = service.create_context(make_create_command(), make_actor())

        result = service.append_event(
            created.collab_context_id,
            make_append_command(
                "task.started",
                expected_version=created.context_version,
                parent_event=created.current_event["event_id"],
            ),
            make_actor(),
        )

        assert result.accepted is True
        assert result.current_status == "in_progress"
        assert _count(engine, ContextCollabEventModel) == 2
        # 引擎直查落库 snapshot:状态机裁决写回行,而非仅服务返回值。
        snapshot = _collet_snapshot(engine, created.collab_context_id)
        assert snapshot["status"] == "in_progress"

    def test_a04_task_completed_accepted_but_status_unchanged(self, service, engine):
        ctx_id, _, started_version = _create_started(service)

        result = service.append_event(
            ctx_id,
            make_append_command(
                "task.completed",
                expected_version=started_version,
            ),
            make_actor(),
        )

        # 接纳成功(事件 +1、版本 CAS)但状态机无规则 → status 不越迁。
        assert result.accepted is True
        assert result.current_status == "in_progress"
        assert _count(engine, ContextCollabEventModel) == 3
        snapshot = _collet_snapshot(engine, ctx_id)
        assert snapshot["status"] == "in_progress"

    def test_a06_submitted_then_accepted_reaches_done(self, service, engine):
        created = service.create_context(make_create_command(), make_actor())
        started = service.append_event(
            created.collab_context_id,
            make_append_command(
                "task.started",
                expected_version=created.context_version,
                parent_event=created.current_event["event_id"],
            ),
            make_actor(),
        )

        submitted = service.append_event(
            created.collab_context_id,
            make_append_command(
                "task.submitted",
                expected_version=started.context_version,
                payload={
                    "artifacts": [
                        {
                            "resource_type": "pr",
                            "resource_id": "pr_101",
                            "revision": "1",
                        }
                    ]
                },
            ),
            make_actor(),
        )
        assert submitted.current_status == "reviewing"

        accepted = service.append_event(
            created.collab_context_id,
            make_append_command(
                "task.accepted",
                expected_version=submitted.context_version,
            ),
            make_actor(),  # actor = principal,过 actor_is_responsible_principal
        )
        assert accepted.current_status == "done"
        # 引擎直查终态:create→started→submitted→accepted 共 4 事件、done 落库。
        assert _count(engine, ContextCollabEventModel) == 4
        assert _collet_snapshot(engine, created.collab_context_id)["status"] == "done"

    def test_a07_a08_trunk_events_flag_lists_only_trunk(self, service, engine):
        created = service.create_context(make_create_command(), make_actor())
        initial_event_id = created.current_event["event_id"]
        started = service.append_event(
            created.collab_context_id,
            make_append_command(
                "task.started",
                expected_version=created.context_version,
                parent_event=initial_event_id,
            ),
            make_actor(),
        )
        # 分支事件:parent 挂更早的初始事件(非当前链头)。
        branch = service.append_event(
            created.collab_context_id,
            make_append_command(
                "user_interaction.posted",
                expected_version=started.context_version,
                primitive_type="Message",
                parent_event=initial_event_id,
                payload={"content": "分支补充:时间线以主干为准"},
            ),
            make_actor(),
        )
        # 续链事件:把链头重新引回 started 路径 → 分支隐入链外(R9)。
        service.append_event(
            created.collab_context_id,
            make_append_command(
                "task.progress.updated",
                expected_version=branch.context_version,
                primitive_type="Activity",
                parent_event=started.event_id,
                payload={"summary": "继续主干推进"},
            ),
            make_actor(),
        )

        full = service.get_status(
            created.collab_context_id, trunk_events=False, context=make_actor()
        )
        trunk = service.get_status(
            created.collab_context_id, trunk_events=True, context=make_actor()
        )

        full_ids = [entry["event_id"] for entry in full.task_progress.events]
        trunk_ids = [entry["event_id"] for entry in trunk.task_progress.events]
        assert len(full_ids) == 4
        assert len(trunk_ids) == 3  # 活动链不含分支事件
        assert set(full_ids) - set(trunk_ids) == {branch.event_id}
        # 两视角的 current_snapshot / current_event / 版本一致(§12.2 #4):
        # trunk 开关只过滤 task_progress.events 列表。
        assert trunk.current_snapshot == full.current_snapshot
        assert trunk.current_event == full.current_event
        assert trunk.context_version == full.context_version
        assert trunk.task_progress.total_events == len(trunk_ids)

    def test_a09_repeated_create_makes_distinct_contexts(self, service, engine):
        """创建不设幂等键(spec 作者裁决):重复调用各建一个上下文。"""
        first = service.create_context(make_create_command(), make_actor())
        second = service.create_context(make_create_command(), make_actor())

        assert first.collab_context_id != second.collab_context_id
        assert second.context_version == 1
        assert _count(engine, ContextCollabColletModel) == 2
        assert _count(engine, ContextCollabEventModel) == 2

    def test_a09_verbatim_resubmit_rejected_by_cas(self, service, engine):
        """追加无幂等键(Ruling R8):同版本逐字重发被 CAS 拒绝,
        不落第二条事件;带新版本号才能追加新事件。"""
        created = service.create_context(make_create_command(), make_actor())
        first = service.append_event(
            created.collab_context_id,
            make_append_command(
                "user_interaction.posted",
                expected_version=created.context_version,
                primitive_type="Message",
                parent_event=created.current_event["event_id"],
                payload={"content": "唯一消息"},
            ),
            make_actor(),
        )

        # 逐字重发(仍带原调用所用的 expected_version=1,当前已是 2):
        # 无幂等键可重放,由 CAS 直接拒绝,不落第二条事件。
        with pytest.raises(ContextCollabVersionConflict):
            service.append_event(
                created.collab_context_id,
                make_append_command(
                    "user_interaction.posted",
                    expected_version=created.context_version,
                    primitive_type="Message",
                    parent_event=created.current_event["event_id"],
                    payload={"content": "重发内容不入链"},
                ),
                make_actor(),
            )
        assert _count(engine, ContextCollabEventModel) == 2
        assert _event_payload(
            engine, created.collab_context_id, first.event_id
        ) == {"content": "唯一消息"}

        second = service.append_event(
            created.collab_context_id,
            make_append_command(
                "user_interaction.posted",
                expected_version=first.context_version,
                primitive_type="Message",
                parent_event=first.event_id,
                payload={"content": "续链消息"},
            ),
            make_actor(),
        )
        assert second.accepted is True
        assert second.event_id != first.event_id
        assert _count(engine, ContextCollabEventModel) == 3

    def test_a09_stale_expected_version_conflicts_without_partial_writes(
        self, service, engine
    ):
        created = service.create_context(make_create_command(), make_actor())
        started = service.append_event(
            created.collab_context_id,
            make_append_command(
                "task.started",
                expected_version=created.context_version,
                parent_event=created.current_event["event_id"],
            ),
            make_actor(),
        )

        with pytest.raises(ContextCollabVersionConflict):
            service.append_event(
                created.collab_context_id,
                make_append_command(
                    "task.progress.updated",
                    expected_version=created.context_version,  # 旧版本(当前 2)
                    payload={"percent": 40},
                ),
                make_actor(),
            )

        # 冲突路径不落半态:行数与版本停在冲突前快照。
        assert _count(engine, ContextCollabColletModel) == 1
        assert _count(engine, ContextCollabEventModel) == 2
        assert (
            _collet_column(
                engine,
                created.collab_context_id,
                ContextCollabColletModel.context_version,
            )
            == started.context_version
        )


class TestDiWiring:
    def test_modules_bind_service_and_repository_singletons(self):
        """镜像 test_task_persistence_module 的轻量组装:两模块 + 测试 DB。

        TaskPersistenceModule 绑 repo 协议、TaskModule 绑 service 协议;
        均为 singleton。authorizer 协议绑定随 service 落在 TaskModule
        (python-injector 对带默认值的注解参数也注入,默认实例不生效)。
        """
        injector = Injector(
            [TestingDatabaseModule(), TaskPersistenceModule(), TaskModule()]
        )

        repository = injector.get(ContextCollabRepositoryProtocol)
        assert isinstance(repository, ContextCollabRepository)
        assert (
            injector.get(ContextCollabRepositoryProtocol)
            is repository
        )

        service = injector.get(ContextCollabServiceProtocol)
        assert isinstance(service, ContextCollabService)
        assert isinstance(service, ContextCollabServiceProtocol)
        assert injector.get(ContextCollabServiceProtocol) is service
        assert isinstance(service._authorizer, StructuralContextCollabAuthorizer)

    def test_test_profile_injector_serves_context_collab_service(self):
        """完整测试 profile 组合根也能装配出 ContextCollabService 单例。"""
        injector = build_injector(profile=DeployProfile.TEST)

        service = injector.get(ContextCollabServiceProtocol)
        assert isinstance(service, ContextCollabService)
        assert injector.get(ContextCollabServiceProtocol) is service
        assert isinstance(service._repository, ContextCollabRepository)


class TestNullablePartialRows:
    """Ruling R10:可空列的容忍语义(engine 直查)。

    - current_snapshot 可空:GET 返回 null;追加事件保持 NULL、只推进版本与指针;
    - event 的 actor_ref/visibility/payload/context_version_after 可空:
      解码不崩,visibility=NULL 按 private 处理(fail-safe)。
    """

    def _null_snapshot_collet(self, engine, collab_context_id) -> None:
        """模拟外部写入:把聚合行的 current_snapshot 置 NULL。"""
        with engine.connect() as conn:
            conn.execute(
                sql_update(ContextCollabColletModel).where(
                    ContextCollabColletModel.collab_context_id == collab_context_id
                ).values(current_snapshot=None)
            )
            conn.commit()

    def test_snapshotless_collet_gets_null_snapshot_but_full_progress(
        self, service, engine
    ):
        created = service.create_context(make_create_command(), make_actor())
        self._null_snapshot_collet(engine, created.collab_context_id)

        result = service.get_status(
            created.collab_context_id, trunk_events=False, context=make_actor()
        )

        assert result.current_snapshot is None
        # 事件链照常投影:初始事件仍在,统计只数非 Message。
        assert result.task_progress.total_events == 1
        assert result.current_event["event_kind"] == "task.created"

    def test_append_onto_snapshotless_collet_keeps_null_snapshot(
        self, service, engine
    ):
        created = service.create_context(make_create_command(), make_actor())
        self._null_snapshot_collet(engine, created.collab_context_id)

        appended = service.append_event(
            created.collab_context_id,
            make_append_command(
                "task.started",
                expected_version=created.context_version,
                primitive_type="Task",
                parent_event=created.current_event["event_id"],
                payload={},
            ),
            make_actor(),
        )

        assert appended.accepted is True
        assert appended.current_status is None
        assert appended.context_version == 2
        with engine.connect() as conn:
            stored = conn.scalar(
                select(ContextCollabColletModel.current_snapshot).where(
                    ContextCollabColletModel.collab_context_id
                    == created.collab_context_id
                )
            )
        assert stored is None  # 保持 NULL,不虚构快照

    def test_partial_event_row_decodes_and_null_visibility_is_private(
        self, service, engine
    ):
        created = service.create_context(make_create_command(), make_actor())
        # 外部写入的部分事件行:actor_ref/visibility/payload/version_after 全 NULL。
        with engine.connect() as conn:
            conn.execute(
                sql_insert(ContextCollabEventModel).values(
                    collab_context_id=created.collab_context_id,
                    event_id="evt_partial_manual",
                    parent_event=created.current_event["event_id"],
                    event_kind="task.progress.updated",
                    primitive_type="Activity",
                    actor_ref=None,
                    visibility=None,
                    payload=None,
                    context_version_after=None,
                    occurred_at=datetime.now(timezone.utc),
                )
            )
            conn.commit()

        principal_view = service.get_status(
            created.collab_context_id, trunk_events=False, context=make_actor()
        )
        ids = [e["event_id"] for e in principal_view.task_progress.events]
        assert "evt_partial_manual" in ids
        entry = next(
            e for e in principal_view.task_progress.events
            if e["event_id"] == "evt_partial_manual"
        )
        # payload NULL → summary/content None;计数按 primitive 正常参与。
        assert entry["summary"] is None
        assert entry["content"] is None

        outsider_view = service.get_status(
            created.collab_context_id,
            trunk_events=False,
            context=make_actor(principal_id="u_9999"),
        )
        outsider_ids = [e["event_id"] for e in outsider_view.task_progress.events]
        # visibility NULL 按 private:非 principal 看不到,不泄露。
        assert "evt_partial_manual" not in outsider_ids
