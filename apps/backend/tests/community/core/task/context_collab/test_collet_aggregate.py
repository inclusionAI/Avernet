"""Legacy local six-field Collet shape and strict decoding, not a closed ACCS schema."""

from __future__ import annotations


import json

import pytest
from sqlalchemy import insert as sql_insert

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from tests.community.core.task.context_collab.creation_facts import creation_facts
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
)
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG


def _collet_dict(**overrides) -> dict:
    """六字段聚合根的最小合法 wire 形态;可按字段覆盖。"""
    data = {
        "collet_id": "collet_todo_001",
        "initiator_ref": {"principal_type": "human", "principal_id": "u_1001"},
        "state": "ACTIVE",
        "scope": {"space_id": "space_workspace_001", "environment": "prod"},
        "current_event_id": "evt_004",
        "version": 4,
    }
    data.update(overrides)
    return data


def test_round_trip_preserves_field_names():
    collet = Collet.from_dict(_collet_dict())
    # 键集 = 六字段名;state 以 ColletState 大写值往返。
    assert set(collet.to_dict()) == {
        "collet_id",
        "initiator_ref",
        "state",
        "scope",
        "current_event_id",
        "version",
    }
    assert collet.to_dict()["state"] == "ACTIVE"
    assert Collet.from_dict(collet.to_dict()) == collet
    for value in ("CREATED", "ACTIVE", "SUSPENDED", "COMPLETED", "ARCHIVED"):
        assert Collet.from_dict(_collet_dict(state=value)).state is ColletState(value)


def test_initiator_and_scope_and_state_required():
    # 必填键缺失一律拒绝。
    for key in ("initiator_ref", "scope", "state"):
        data = {k: v for k, v in _collet_dict().items() if k != key}
        with pytest.raises(ContextCollabValidationError):
            Collet.from_dict(data)
    # 非法形态:state 未命中 ColletState 值、initiator_ref 缺字段、
    # collet_id 空串。
    with pytest.raises(ContextCollabValidationError):
        Collet.from_dict(_collet_dict(state="active"))
    with pytest.raises(ContextCollabValidationError):
        Collet.from_dict(_collet_dict(initiator_ref={"principal_type": "human"}))
    with pytest.raises(ContextCollabValidationError):
        Collet.from_dict(_collet_dict(collet_id="  "))


def test_version_must_be_positive_int():
    # 必填且为 ≥1 整数;bool 虽是 int 子类也不得通过。
    for bad in (None, 0, -1, "4", 4.0, True):
        with pytest.raises(ContextCollabValidationError):
            Collet.from_dict(_collet_dict(version=bad))


def test_unknown_top_level_keys_rejected():
    # 旧聚合键(collab_context_id / context_spec / ...)已是未知键。
    with pytest.raises(ContextCollabValidationError):
        Collet.from_dict(_collet_dict(collab_context_id="collet_todo_001"))
    with pytest.raises(ContextCollabValidationError):
        Collet.from_dict(_collet_dict(creation_facts={}))
    with pytest.raises(ContextCollabValidationError):
        Collet.from_dict(_collet_dict(extra_key="x"))


def _row_spec() -> dict:
    """裸 SQL 插行所需的最小创建参数 JSON。"""
    return creation_facts(
        space=SpaceRef(space_id="space_workspace_001", environment="prod"),
        principal_ref=PrincipalRef(principal_type="human", principal_id="u_1001"),
        method_kind="Single",
        executor_ref=None,
        objective="整理 Context 协作协议评审结论并提交可评审 PR",
        resource_refs=(),
        product_kind=None,
        workflow_kind=None,
    )


def test_null_initiator_row_decode_raises(db, engine):
    """严格解码(Ruling 方案 i):``actor_ref IS NULL`` 的聚合行不再容忍,
    ``get_current_status`` 抛 ``ContextCollabValidationError``,不返回成功;
    错误消息携带 collab_context_id。"""
    context_id = "collet_partial_null_actor"
    with engine.connect() as conn:
        conn.execute(
            sql_insert(ContextCollabColletModel).values(
                collab_context_id=context_id,
                actor_ref=None,
                scope=json.dumps(_collet_dict()["scope"]),
                creation_facts=json.dumps(_row_spec(), ensure_ascii=False),
                current_snapshot=None,
                context_version=1,
            )
        )
        conn.commit()

    with pytest.raises(ContextCollabValidationError, match=context_id):
        ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG).get_current_status(
            context_id
        )
