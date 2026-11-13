"""L4 bounded collaboration context; independent from work and method state.

The six-field layout is this implementation's representation, not an exhaustive
ACCS schema requirement. Composition metadata and query views live outside L4.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)


def _required_str(data: dict, key: str, owner: str) -> str:
    """从 dict 中取出必填字符串字段;缺失或空串抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string, got: {value!r}"
        )
    return value


def _optional_str(data: dict, key: str, owner: str) -> str | None:
    """从 dict 中取出可空字符串字段;缺失或 ``None`` 返回 ``None``。"""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string or null, got: {value!r}"
        )
    return value


def _required_dict(data: dict, key: str, owner: str) -> dict:
    """从 dict 中取出必填对象字段;缺失或非 dict 抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, dict):
        raise ContextCollabValidationError(
            f"{owner}.{key} must be an object, got: {value!r}"
        )
    return value


class ColletState(str, Enum):
    """Collet 大写机器状态词汇(spec §4)。

    与独立 ``Task.status`` 的 ``TaskStatus`` 小写词汇是两套闭合
    词汇:前者服务 G4 ``collet_lifecycle`` 状态机(见
    ``g4_contracts_consistency.context_state_machines``),后者仍是
    独立 Task 的状态。两套词汇不互相渗透/schema 不互认。
    """

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


# Collet 顶层键集与合法 state 值(from_dict 严格解码的两张白名单)。
_COLLET_KEYS = frozenset(
    {
        "collet_id",
        "initiator_ref",
        "state",
        "scope",
        "current_event_id",
        "version",
    }
)
_COLLET_STATES = frozenset({state.value for state in ColletState})


@dataclass
class Collet:
    """L4 协作上下文聚合根(本实现的六字段,spec §3.4)。

    只持有协议身份与顶层生命:聚合标识(``collab_context_id`` 落库列同名
    同值)、发起主体引用(与初始 task.created 事件的 actor_ref 同值)、
    ``collet_lifecycle`` 顶层状态、归属 Space、当前事件指针与聚合版本。
    创建配置、Task 与查询投影不在本模型上;事件事实由 ``ac_context_collab_event`` 表
    (见 ``event_envelope.EventRecord``)承载;不在此复制 Task 或事件历史。
    """

    collet_id: str
    initiator_ref: PrincipalRef
    state: ColletState
    scope: SpaceRef
    current_event_id: str | None
    version: int

    @classmethod
    def from_dict(cls, data: dict) -> "Collet":
        """六字段严格解码;``to_dict`` 结果可无损 round-trip。

        校验:``collet_id`` 非空;``initiator_ref`` / ``scope`` /
        ``state`` / ``version`` 必填;``state`` 必须命中
        ``ColletState`` 值;``version`` 必须是 ≥1 整数;未知顶层键
        (键集 = 六字段名)拒绝。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"Collet must be an object, got: {data!r}"
            )
        unknown = set(data) - _COLLET_KEYS
        if unknown:
            raise ContextCollabValidationError(
                f"Collet has unknown top-level keys: {sorted(unknown)}; "
                f"allowed keys: {sorted(_COLLET_KEYS)}"
            )
        raw_state = data.get("state")
        if not isinstance(raw_state, str) or raw_state not in _COLLET_STATES:
            raise ContextCollabValidationError(f"unknown state value: {raw_state!r}")
        raw_version = data.get("version")
        if (
            isinstance(raw_version, bool)
            or not isinstance(raw_version, int)
            or raw_version < 1
        ):
            raise ContextCollabValidationError(
                f"Collet.version must be an integer >= 1, got: {raw_version!r}"
            )
        return cls(
            collet_id=_required_str(data, "collet_id", "Collet"),
            initiator_ref=PrincipalRef.from_dict(
                _required_dict(data, "initiator_ref", "Collet")
            ),
            state=ColletState(raw_state),
            scope=SpaceRef.from_dict(_required_dict(data, "scope", "Collet")),
            current_event_id=_optional_str(data, "current_event_id", "Collet"),
            version=raw_version,
        )

    def to_dict(self) -> dict:
        return {
            "collet_id": self.collet_id,
            "initiator_ref": self.initiator_ref.to_dict(),
            "state": self.state.value,
            "scope": self.scope.to_dict(),
            "current_event_id": self.current_event_id,
            "version": self.version,
        }
