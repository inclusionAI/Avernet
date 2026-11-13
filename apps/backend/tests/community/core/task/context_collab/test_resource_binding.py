"""ResourceBinding 瘦身升级单测(spec 2026-11-12-context-collab-l4-resource-binding)。

覆盖:
- 值对象 round-trip 与未知顶层键拒绝(沿 collet.py 模块风格);
- ``resource_bindings_from_initial_event`` 解析合同:合法 payload → 绑定
  元组;key 缺失 / NULL payload / 非列表 / 列表项非 dict / 非法 ResourceRef
  → ``ContextCollabValidationError`` 响亮失败(不 try/except 兜底伪装存量行);
- 发帖/服务路径断言由 ``test_context_collab_service.py`` 与 repository
  测试适配面覆盖,本文件只钉解析合同。
"""

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.resource_binding import (
    ResourceBinding,
    resource_bindings_from_initial_event,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)

COLLET_ID = "collet_binding_001"
EVENT_ID = "evt_binding_001"
ACTOR = PrincipalRef("human", "u_1001")


def _resource_ref(resource_id: str = "res_001") -> ResourceRef:
    return ResourceRef(resource_type="document", resource_id=resource_id, revision=None)


def _initial_event(payload) -> EventRecord:
    """构造初始事件(task.created);payload 由用例控制。"""
    return EventRecord(
        collab_context_id=COLLET_ID,
        event_id=EVENT_ID,
        parent_event=None,
        event_kind="task.created",
        primitive_type="Task",
        actor_ref=ACTOR,
        visibility="public",
        payload=payload,
        context_version_after=1,
        # EventRecord.occurred_at 是 datetime;解析函数不消费它。
        occurred_at=None,
    )


class TestResourceBindingValueObject:
    def test_round_trip_preserves_shape(self):
        binding = ResourceBinding(resource_ref=_resource_ref())
        restored = ResourceBinding.from_dict(binding.to_dict())
        assert restored == binding
        assert restored.resource_ref.resource_type == "document"

    def test_unknown_top_level_keys_rejected(self):
        with pytest.raises(ContextCollabValidationError):
            ResourceBinding.from_dict({"resource_ref": {}, "grants": []})


class TestParseInitialEvent:
    def test_parses_resource_refs_payload(self):
        event = _initial_event(
            {
                "objective": "整理评审结论",
                "resource_refs": [
                    _resource_ref("res_001").to_dict(),
                    _resource_ref("res_002").to_dict(),
                ],
            }
        )
        bindings = resource_bindings_from_initial_event(event)
        assert bindings == (
            ResourceBinding(resource_ref=_resource_ref("res_001")),
            ResourceBinding(resource_ref=_resource_ref("res_002")),
        )

    def test_empty_refs_payload_returns_empty_tuple(self):
        event = _initial_event({"objective": "无附件讨论", "resource_refs": []})
        assert resource_bindings_from_initial_event(event) == ()

    def test_missing_key_raises_loudly(self):
        """存量行(payload 无 key)不在此兜底——回退属 P2 自愈缝职责。"""
        event = _initial_event({"objective": "legacy row"})
        with pytest.raises(ContextCollabValidationError):
            resource_bindings_from_initial_event(event)

    def test_null_payload_raises_loudly(self):
        event = _initial_event(None)
        with pytest.raises(ContextCollabValidationError):
            resource_bindings_from_initial_event(event)

    def test_non_list_resource_refs_raises(self):
        event = _initial_event({"objective": "x", "resource_refs": {"bad": 1}})
        with pytest.raises(ContextCollabValidationError):
            resource_bindings_from_initial_event(event)

    def test_malformed_item_raises_without_fallback(self):
        """垃圾 payload 响亮失败,不得被伪装成存量行走任何回退。"""
        event = _initial_event(
            {"objective": "x", "resource_refs": [{"resource_type": "document"}]},
        )
        with pytest.raises(ContextCollabValidationError):
            resource_bindings_from_initial_event(event)
