"""L5 Methods:ACCS-105 wire 权威的协作方法枚举、投影与 MethodBinding 值对象。

BBS 公开讨论使用 ``Open + Group``,团队代办使用 ``Single``;
``Group`` / ``Open`` / ``Swarm`` 为核心方法单值,按 spec §12.4 仅经嵌套
bind 进入(顶层 wire 拒绝在 G1 接缝裁决),G3 演进对象暂不引入,
未知 wire 值抛 ``ContextCollabValidationError``。

``MethodBinding`` 是方法绑定的冻结值对象(spec §3.2 字段 + §12 + §6.3
审计四字段):``method_kind`` 经 ``MethodKind.from_wire`` 校验,
``method_names`` 必须与 ``parse_method_kinds`` 投影逐字一致,
``profile_versions`` 键集与 method_names 集合相等;``config`` 只做
str 键与 ``max_compose_depth`` 1..64 整型校验,缺省深度 8 由服务构造层
注入,读取侧经 ``effective_max_compose_depth`` 取生效上限(§12 第 3 条)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)

# 复合 wire 值的分隔符,与 ``Open + Group`` 逐字一致(空格环绕加号)。
_COMPOSITE_SEPARATOR = " + "


class MethodKind(str, Enum):
    """L5 方法取值;wire 值与三个北向 API 的 ``method_kind`` 字段逐字一致。"""

    SINGLE = "Single"
    # 产品唯一声明的复合 wire 值:BBS 公开讨论 = Open + Group。
    OPEN_GROUP = "Open + Group"
    # 核心方法单值(ACCS-301–304),嵌套绑定使用。
    GROUP = "Group"
    OPEN = "Open"
    SWARM = "Swarm"

    @staticmethod
    def from_wire(value: str) -> "MethodKind":
        """从 API wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return MethodKind(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown method_kind wire value: {value!r}"
            ) from None


def parse_method_kinds(wire: str) -> tuple[MethodKind, ...]:
    """把 ``method_kind`` wire 值投影为核心方法元组(spec §3.1)。

    单值直接映射 ``MethodKind``;复合值必须是产品声明的 wire 组合
    (即枚举中带 ``" + "`` 的取值,当前仅 ``Open + Group``),按分隔符
    拆分后逐项映射;未声明的组合或未知片段抛
    ``ContextCollabValidationError``。
    """
    if _COMPOSITE_SEPARATOR not in wire:
        return (MethodKind.from_wire(wire),)
    declared_composites = {
        kind.value for kind in MethodKind if _COMPOSITE_SEPARATOR in kind.value
    }
    if wire not in declared_composites:
        raise ContextCollabValidationError(f"unknown method_kind wire value: {wire!r}")
    return tuple(
        MethodKind.from_wire(part) for part in wire.split(_COMPOSITE_SEPARATOR)
    )


class BindingState(str, Enum):
    """L5 方法绑定生命周期状态(spec §4);wire 值与北向 API 逐字一致。"""

    BOUND = "BOUND"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"

    @classmethod
    def from_wire(cls, value: str) -> "BindingState":
        """从 wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return cls(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown binding state wire value: {value!r}"
            ) from None


# spec §12 第 3 条:组合深度上限缺省 8,创建时由服务构造层写入 config;
# 值对象 from_dict 不回填缺省,读取侧经 effective_max_compose_depth 取生效值。
DEFAULT_MAX_COMPOSE_DEPTH = 8


def effective_max_compose_depth(config: dict) -> int:
    """取生效的组合深度上限(spec §12 第 3 条)。

    ``config`` 缺少 ``max_compose_depth`` 时返回缺省 8;存在时按
    1..64 整型规则校验(与 ``from_dict`` 同一裁断),非法值不静默
    回退缺省,直接抛 ``ContextCollabValidationError``。
    """
    if "max_compose_depth" not in config:
        return DEFAULT_MAX_COMPOSE_DEPTH
    return _checked_max_compose_depth(config["max_compose_depth"], "config")


def _checked_max_compose_depth(value: object, owner: str) -> int:
    """校验 max_compose_depth:必须为 1..64 整型(bool 不是合法整数)。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContextCollabValidationError(
            f"{owner}.max_compose_depth must be an integer in 1..64, got: {value!r}"
        )
    if not 1 <= value <= 64:
        raise ContextCollabValidationError(
            f"{owner}.max_compose_depth must be within 1..64, got: {value!r}"
        )
    return value


def _required_str(data: dict, key: str, owner: str) -> str:
    """取必填非空字符串字段;缺失或空串抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string, got: {value!r}"
        )
    return value


def _optional_str(data: dict, key: str, owner: str) -> str | None:
    """取可空字符串字段;缺失或 ``None`` 返回 ``None``,空串拒绝。"""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string or null, got: {value!r}"
        )
    return value


def _audit_str(data: dict, key: str, owner: str) -> str:
    """取审计字段(§6.3);键必须存在且为字符串,允许空串(如绑定直接以
    BOUND 创建时 transitioned_from 无前序绑定状态)。"""
    value = data.get(key)
    if not isinstance(value, str):
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a string, got: {value!r}"
        )
    return value


def _str_tuple(data: dict, key: str, owner: str) -> tuple[str, ...]:
    """取可选字符串元组字段;缺失返回空元组,元素必须是非空字符串。"""
    if key not in data:
        return ()
    value = data[key]
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a list of non-empty strings, got: {value!r}"
        )
    return tuple(value)


def _plain_dict(data: dict, key: str, owner: str) -> dict:
    """取 dict 字段并复制为本对象持有的普通 dict(冻结友好)。

    仅做结构校验:键必须是字符串;``max_compose_depth`` 若存在必须满足
    1..64 整型规则。其余键值不建平行类型,原样保留(缺省深度 8 由服务
    构造层写入,此处不回填)。
    """
    value = data.get(key)
    if not isinstance(value, dict):
        raise ContextCollabValidationError(
            f"{owner}.{key} must be an object, got: {value!r}"
        )
    for config_key in value:
        if not isinstance(config_key, str):
            raise ContextCollabValidationError(
                f"{owner}.{key} keys must be strings, got: {config_key!r}"
            )
    effective_max_compose_depth(value)
    return dict(value)


# from_dict 接受的封闭字段集;未知字段拒绝(spec §4 test_method_binding)。
_BINDING_FIELDS = (
    "method_binding_id",
    "collab_context_id",
    "parent_scope_ref",
    "method_kind",
    "method_names",
    "profile_versions",
    "participant_ref",
    "role_binding_refs",
    "config",
    "state",
    "version",
    "bind_reason",
    "g3_subject_type",
    "transitioned_from",
    "transition_reason",
    "transitioned_by",
    "transitioned_at",
)


@dataclass(frozen=True)
class MethodBinding:
    """L5 方法绑定值对象(spec §3.2 YAML + §12 字段 + §6.3 审计四字段)。

    ``method_kind`` 为 wire 字符串;``method_names`` 是其核心方法投影,
    必须与 ``parse_method_kinds`` 逐字一致,不能自由组合;
    ``profile_versions`` 记录各核心方法 Profile 的版本,键集与
    method_names 集合相等;``config`` 只做结构校验,不建平行类型;
    ``g3_subject_type`` 保留 G3 词位,P1 恒 ``None``(spec §8.4);
    ``transitioned_at`` 为 ISO 字符串,时间由 store 层管理,本对象
    不做时钟假设。
    """

    method_binding_id: str
    collab_context_id: str
    method_kind: str
    method_names: tuple[str, ...]
    profile_versions: dict[str, str]
    parent_scope_ref: str | None = None
    participant_ref: str | None = None
    role_binding_refs: tuple[str, ...] = ()
    config: dict = field(default_factory=dict)
    state: BindingState = BindingState.BOUND
    version: int = 1
    bind_reason: str = ""
    g3_subject_type: str | None = None
    transitioned_from: str = ""
    transition_reason: str = ""
    transitioned_by: str = ""
    transitioned_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "MethodBinding":
        """从 dict 恢复;字段集封闭(未知字段拒绝),校验规则:

        - ``method_binding_id`` / ``collab_context_id`` 非空;
        - ``method_kind`` 经 ``MethodKind.from_wire``;
        - ``method_names`` 必须与 ``parse_method_kinds`` 投影逐字一致;
        - ``profile_versions`` 键集 == method_names 集,值非空;
        - ``state`` 经 ``BindingState.from_wire``;``version`` 正整数;
        - 审计四字段(§6.3)必须为字符串,允许空串;
        - ``config`` 只做 str 键与 max_compose_depth 1..64 校验,
          缺省深度 8 不在此回填(由服务构造层写入)。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"MethodBinding must be an object, got: {data!r}"
            )
        unknown = [key for key in data if key not in _BINDING_FIELDS]
        if unknown:
            raise ContextCollabValidationError(
                f"unknown field(s) in MethodBinding: {sorted(unknown)}"
            )
        method_kind = MethodKind.from_wire(
            _required_str(data, "method_kind", "MethodBinding")
        ).value
        expected_names = tuple(kind.value for kind in parse_method_kinds(method_kind))
        raw_names = data.get("method_names")
        if not isinstance(raw_names, list) or not all(
            isinstance(name, str) for name in raw_names
        ):
            raise ContextCollabValidationError(
                f"MethodBinding.method_names must be a list of strings, "
                f"got: {raw_names!r}"
            )
        if tuple(raw_names) != expected_names:
            raise ContextCollabValidationError(
                f"MethodBinding.method_names {tuple(raw_names)!r} must match "
                f"method_kind {method_kind!r} projection {expected_names!r}"
            )
        raw_versions = data.get("profile_versions")
        if not isinstance(raw_versions, dict):
            raise ContextCollabValidationError(
                f"MethodBinding.profile_versions must be an object, "
                f"got: {raw_versions!r}"
            )
        if set(raw_versions) != set(expected_names):
            raise ContextCollabValidationError(
                f"MethodBinding.profile_versions keys {sorted(raw_versions)!r} "
                f"must match method_names {list(expected_names)!r}"
            )
        for profile_name, profile_version in raw_versions.items():
            if not isinstance(profile_version, str) or not profile_version.strip():
                raise ContextCollabValidationError(
                    f"MethodBinding.profile_versions[{profile_name!r}] must "
                    f"be a non-empty string, got: {profile_version!r}"
                )
        raw_version = data.get("version")
        if (
            isinstance(raw_version, bool)
            or not isinstance(raw_version, int)
            or raw_version < 1
        ):
            raise ContextCollabValidationError(
                f"MethodBinding.version must be a positive integer, "
                f"got: {raw_version!r}"
            )
        return cls(
            method_binding_id=_required_str(data, "method_binding_id", "MethodBinding"),
            collab_context_id=_required_str(data, "collab_context_id", "MethodBinding"),
            method_kind=method_kind,
            method_names=tuple(raw_names),
            profile_versions=dict(raw_versions),
            parent_scope_ref=_optional_str(data, "parent_scope_ref", "MethodBinding"),
            participant_ref=_optional_str(data, "participant_ref", "MethodBinding"),
            role_binding_refs=_str_tuple(data, "role_binding_refs", "MethodBinding"),
            config=(
                _plain_dict(data, "config", "MethodBinding") if "config" in data else {}
            ),
            state=BindingState.from_wire(_required_str(data, "state", "MethodBinding")),
            version=raw_version,
            bind_reason=_audit_str(data, "bind_reason", "MethodBinding"),
            g3_subject_type=_optional_str(data, "g3_subject_type", "MethodBinding"),
            transitioned_from=_audit_str(data, "transitioned_from", "MethodBinding"),
            transition_reason=_audit_str(data, "transition_reason", "MethodBinding"),
            transitioned_by=_audit_str(data, "transitioned_by", "MethodBinding"),
            transitioned_at=_audit_str(data, "transitioned_at", "MethodBinding"),
        )

    def to_dict(self) -> dict:
        """输出全部声明字段;枚举取 wire 值,元组降为列表,dict 复制为
        普通 dict,保证 ``from_dict(to_dict(x)) == x``。"""
        return {
            "method_binding_id": self.method_binding_id,
            "collab_context_id": self.collab_context_id,
            "parent_scope_ref": self.parent_scope_ref,
            "method_kind": self.method_kind,
            "method_names": list(self.method_names),
            "profile_versions": dict(self.profile_versions),
            "participant_ref": self.participant_ref,
            "role_binding_refs": list(self.role_binding_refs),
            "config": dict(self.config),
            "state": self.state.value,
            "version": self.version,
            "bind_reason": self.bind_reason,
            "g3_subject_type": self.g3_subject_type,
            "transitioned_from": self.transitioned_from,
            "transition_reason": self.transition_reason,
            "transitioned_by": self.transitioned_by,
            "transitioned_at": self.transitioned_at,
        }


def top_level_binding_id(collab_context_id: str) -> str:
    """Stable top-level MethodBinding reference; also used by L6 instance records."""
    return f"mb_top_{collab_context_id}"
