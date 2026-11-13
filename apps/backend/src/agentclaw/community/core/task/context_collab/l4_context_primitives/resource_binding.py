"""L4 ResourceBinding:资源被带入 Collet 的上下文绑定(ACCS-104 §2)。

正主是事件流本身:创建时的资源绑定事实随初始事件 payload 持久
(``resource_refs`` 键,spec 2026-11-12-context-collab-l4-resource-binding),
本模块只提供值对象与解析纯函数——store/自愈缝/读接线留给第一个真读方
(预留顺位:bbs_help_request 产品面 > Artifact 操作族 > G1 策略审计)。

协议边界(ACCS-104 §8.4):**ResourceBinding 不是 Grant**。G1 资源授权
永远逐资源回所属 Space 的 Grant/Policy 计算,本对象自身不产生任何权限。
P1 是创世事实的解析片段:无独立 ID/版本/状态(bind/unbind 操作族与
绑定级审计属 P2);模块只有纯值对象与纯函数,不做任何 IO。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
)

if TYPE_CHECKING:  # 运行时零依赖,事件类型仅用于签名。
    from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
        EventRecord,
    )

# 初始事件 payload 携带创建资源绑定事实的键名(与 service 创建路径逐字一致)。
RESOURCE_REFS_PAYLOAD_KEY = "resource_refs"

# ResourceBinding 顶层键集:单键 resource_ref(from_dict 严格解码白名单)。
_BINDING_KEYS = frozenset({"resource_ref"})


@dataclass(frozen=True)
class ResourceBinding:
    """Resource 被带入 Collet 的上下文绑定(不是 Grant);P1 解析片段。"""

    resource_ref: ResourceRef

    @classmethod
    def from_dict(cls, data: dict) -> "ResourceBinding":
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"ResourceBinding must be an object, got: {data!r}"
            )
        unknown = set(data) - _BINDING_KEYS
        if unknown:
            raise ContextCollabValidationError(
                f"ResourceBinding has unknown top-level keys: {sorted(unknown)}; "
                f"allowed keys: {sorted(_BINDING_KEYS)}"
            )
        raw_ref = data.get("resource_ref")
        if not isinstance(raw_ref, dict):
            raise ContextCollabValidationError(
                f"ResourceBinding.resource_ref must be an object, got: {raw_ref!r}"
            )
        return cls(resource_ref=ResourceRef.from_dict(raw_ref))

    def to_dict(self) -> dict:
        return {"resource_ref": self.resource_ref.to_dict()}


def resource_bindings_from_initial_event(event: "EventRecord") -> tuple[
    ResourceBinding, ...
]:
    """从初始事件 payload 解出创建时的绑定元组(解析合同,P1 先钉死)。

    只认事件事实:``payload`` 必须是 dict 且 ``resource_refs`` 为列表;
    key 缺失 / payload 为 NULL / 列表项非法 → ``ContextCollabValidationError``
    响亮失败,**不**对存量行(无 key 的旧行)做 ``context_spec`` 回退——
    该回退属 P2 自愈缝职责,届时须以 ``"resource_refs" in payload``
    探针区分存量与新行,禁止 try/except 把垃圾 payload 伪装成存量行。
    """
    payload = event.payload
    if not isinstance(payload, dict):
        raise ContextCollabValidationError(
            "initial event payload must be an object to project resource "
            f"bindings from, got: {payload!r}"
        )
    raw_refs = payload.get(RESOURCE_REFS_PAYLOAD_KEY)
    if not isinstance(raw_refs, list):
        raise ContextCollabValidationError(
            f"initial event payload must carry a list under "
            f"{RESOURCE_REFS_PAYLOAD_KEY!r} to project resource bindings, "
            f"got: {raw_refs!r}"
        )
    # payload item 形态 = 裸 ResourceRef dict(service 创建路径逐字写入
    # spec.resource_refs 的 to_dict),不是 ResourceBinding 的包装 dict。
    bindings = []
    for item in raw_refs:
        if not isinstance(item, dict):
            raise ContextCollabValidationError(
                f"resource_refs payload item must be an object, got: {item!r}"
            )
        bindings.append(ResourceBinding(resource_ref=ResourceRef.from_dict(item)))
    return tuple(bindings)
