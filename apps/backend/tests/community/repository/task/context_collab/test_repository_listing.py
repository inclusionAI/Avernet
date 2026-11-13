"""``ContextCollabRepository`` 两读方法:域类型直出、零发明 record。

覆盖(spec bbs-public-discussion-product §4):
- ``list_collets_by_product``:按 product_kind 过滤(Python 侧)、
  gmt_create 倒序、解码失败外部行(NULL actor_ref)跳过不炸整页;
- ``count_message_events``:单条 GROUP BY 按 Collet 计 Message 事件数,
  Task 类事件不计,未知 id 不出现在结果键。
"""

import json
from datetime import timedelta

from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
)
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

PRINCIPAL = PrincipalRef(principal_type="human", principal_id="u_1001")
EXECUTOR = AgentRef(agent_type="bot", agent_id="bot_coding_001")


def _spec(product_kind: str, objective: str = "整理评审结论") -> ContextSpec:
    from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
        ResourceRef,
    )

    return ContextSpec(
        space=SpaceRef(space_id="space_workspace_001", environment="prod"),
        principal_ref=PRINCIPAL,
        method_kind="Open + Group",
        executor_ref=None if product_kind == "bbs_public_discussion" else EXECUTOR,
        objective=objective,
        resource_refs=(
            ResourceRef(resource_type="forum_topic", resource_id="t_1", revision=None),
        ),
        product_kind=product_kind,
        workflow_kind=product_kind,
    )


def _create(db, product_kind: str, objective: str = "整理评审结论"):
    from agentclaw.community.core.task.context_collab.persistence.repository import (
        CreateContextRecordRequest,
        InitialEventDraft,
    )

    repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
    return repo.create_context(
        CreateContextRecordRequest(
            context_spec=_spec(product_kind, objective),
            initial_event=InitialEventDraft(
                event_kind="task.created",
                primitive_type="Task",
                actor_ref=PRINCIPAL,
                payload={"objective": objective, "resource_refs": []},
            ),
        )
    )


class TestListColletsByProduct:
    def test_lists_only_matching_product_kind(self, db, engine):
        _create(db, "bbs_public_discussion", objective="公开讨论帖")
        _create(db, "team_todo", objective="待办任务帖")

        repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
        rows = repo.list_collets_by_product("bbs_public_discussion")

        assert len(rows) == 1
        collet, spec, gmt_create = rows[0]
        assert spec.objective == "公开讨论帖"
        assert spec.product_kind == "bbs_public_discussion"
        assert collet.collet_id.startswith("collet_")
        assert gmt_create is not None

    def test_list_orders_by_gmt_create_desc(self, db, engine):
        first = _create(db, "bbs_public_discussion", objective="早帖")
        # 手工把首行时间拨早,避免同秒插入的时钟分辨率问题。
        with db.orm_session() as session:
            row = (
                session.query(ContextCollabColletModel)
                .filter(
                    ContextCollabColletModel.collab_context_id == first.collet.collet_id
                )
                .one()
            )
            row.gmt_create = row.gmt_create - timedelta(minutes=10)
        second = _create(db, "bbs_public_discussion", objective="晚帖")

        rows = ContextCollabRepository(
            db, product_catalog=PRODUCT_CATALOG
        ).list_collets_by_product("bbs_public_discussion")

        assert [r[1].objective for r in rows] == ["晚帖", "早帖"]
        assert rows[0][0].collet_id == second.collet.collet_id

    def test_list_skips_undecodable_rows(self, db, engine):
        good = _create(db, "bbs_public_discussion", objective="好帖")
        # 裸 SQL 插入 actor_ref 为 NULL 的外部部分行:方案 i 要求单点读
        # 响亮失败,列表必须跳过并继续返回好行(spec §4)。
        with db.orm_session() as session:
            bad = ContextCollabColletModel(
                collab_context_id="collet_partial_row_1",
                actor_ref=None,
                context_spec=json.dumps(
                    {
                        "space": {
                            "space_id": "s_x",
                            "environment": "prod",
                        },
                        "principal_ref": {
                            "principal_type": "human",
                            "principal_id": "u_9",
                        },
                        "method_kind": "Single",
                        "executor_ref": None,
                        "objective": "外部行",
                        "resource_refs": [],
                        "product_kind": "bbs_public_discussion",
                        "workflow_kind": "bbs_public_discussion",
                    }
                ),
                current_snapshot=None,
                current_event_id=None,
                context_version=1,
            )
            session.add(bad)

        rows = ContextCollabRepository(
            db, product_catalog=PRODUCT_CATALOG
        ).list_collets_by_product("bbs_public_discussion")

        assert len(rows) == 1
        assert rows[0][0].collet_id == good.collet.collet_id


class TestCountMessageEvents:
    def test_groups_messages_in_one_query(self, db, engine):
        from agentclaw.community.core.task.context_collab.persistence.repository import (
            AppendEventRecordRequest,
        )

        repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
        a = _create(db, "bbs_public_discussion", objective="帖 A")
        b = _create(db, "bbs_public_discussion", objective="帖 B")

        def append(collet_id: str, version: int, primitive: str, kind: str):
            repo.append_event(
                collet_id,
                AppendEventRecordRequest(
                    expected_version=version,
                    event_kind=kind,
                    primitive_type=primitive,
                    actor_ref=PRINCIPAL,
                    parent_event=None,
                    visibility="public",
                    payload={"content": "回帖正文"} if primitive == "Message" else {},
                    snapshot_updater=(lambda snapshot: snapshot),
                ),
            )

        # A: 初始已有 1 条 task.created(Task);追加 2 条 Message。
        append(a.collet.collet_id, 1, "Message", "user_interaction.posted")
        append(a.collet.collet_id, 2, "Message", "user_interaction.posted")
        # B: 追加 1 条 Message。
        append(b.collet.collet_id, 1, "Message", "user_interaction.posted")

        repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
        counts = repo.count_message_events(
            [a.collet.collet_id, b.collet.collet_id, "collet_missing"]
        )

        assert counts == {a.collet.collet_id: 2, b.collet.collet_id: 1}
        assert "collet_missing" not in counts

    def test_message_events_only_no_task_primitives(self, db, engine):
        a = _create(db, "bbs_public_discussion")
        # 只有初始 task.created(Task)事件,无 Message → 不出现在结果键里;
        # 消费方以 ``.get(collab_context_id, 0)`` 取零计数(合同)。
        counts = ContextCollabRepository(
            db, product_catalog=PRODUCT_CATALOG
        ).count_message_events([a.collet.collet_id])
        assert counts == {}
