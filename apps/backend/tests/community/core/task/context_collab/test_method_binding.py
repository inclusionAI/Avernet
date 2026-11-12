"""L5 MethodBinding 值对象单测(Task 4)。

覆盖:
- 五个 wire 取值(Single / Open + Group / Group / Open / Swarm)的
  ``MethodBinding.from_dict/to_dict`` 往返与二次往返相等;
- ``method_names`` 必须与 ``parse_method_kinds`` 投影逐字一致(swap/缺失/非列表拒绝);
- 必填字段缺失或空值拒绝,可选字段缺省(parent_scope_ref/participant_ref/
  role_binding_refs/config/g3_subject_type);
- ``profile_versions`` 键集 == method_names 集且值非空;
- ``BindingState`` 全部 wire 值往返与非法值拒绝;
- ``version`` 正整数校验(0/-1/"1"/True 拒绝);
- ``config.max_compose_depth`` 1..64 整型校验(0/65/"8"/True 拒绝,
  1 与 64 边界接受),缺省 8 不由 from_dict 回填,
  ``effective_max_compose_depth`` 缺省 8 与覆盖读取;
- 未知字段拒绝;审计四字段(§6.3)字符串往返、允许空串;
- 冻结实例改写抛 ``FrozenInstanceError``;``g3_subject_type`` P1 恒 None(§8.4)。
"""
import dataclasses

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    DEFAULT_MAX_COMPOSE_DEPTH,
    BindingState,
    MethodBinding,
    MethodKind,
    effective_max_compose_depth,
    parse_method_kinds,
)

# 全部 wire 取值:顶层 Single / Open + Group 与嵌套 Group/Open/Swarm 均可绑定。
ALL_METHOD_WIRES = tuple(kind.value for kind in MethodKind)


def _binding_dict(method_kind: str = "Single") -> dict:
    """构造一份全量合法的 MethodBinding dict(spec §3.2 + §12 + §6.3 审计)。"""
    names = tuple(kind.value for kind in parse_method_kinds(method_kind))
    return {
        "method_binding_id": "binding-1",
        "collab_context_id": "ctx-1",
        "parent_scope_ref": None,
        "method_kind": method_kind,
        "method_names": list(names),
        "profile_versions": {name: "0.2" for name in names},
        "participant_ref": None,
        "role_binding_refs": [],
        "config": {},
        "state": "BOUND",
        "version": 1,
        "bind_reason": "create_context",
        "g3_subject_type": None,
        # 边最初以 BOUND 创建;时间戳由 store 层管理,这里仅是 ISO 字符串。
        "transitioned_from": "",
        "transition_reason": "initial bind",
        "transitioned_by": "principal-1",
        "transitioned_at": "2026-11-12T10:00:00Z",
    }


# ---------------------------------------------------------------------------
# 往返(全部五个 wire 取值)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method_kind", ALL_METHOD_WIRES)
def test_binding_round_trip_all_wire_values(method_kind):
    data = _binding_dict(method_kind)
    binding = MethodBinding.from_dict(data)
    expected_names = tuple(
        kind.value for kind in parse_method_kinds(method_kind)
    )
    assert binding.method_binding_id == "binding-1"
    assert binding.collab_context_id == "ctx-1"
    assert binding.method_kind == method_kind
    assert binding.method_names == expected_names
    assert binding.profile_versions == {name: "0.2" for name in expected_names}
    assert binding.state is BindingState.BOUND
    assert binding.version == 1
    assert binding.to_dict() == data
    # 二次往返保持相等:from_dict(to_dict(x)) == x。
    assert MethodBinding.from_dict(binding.to_dict()) == binding


# ---------------------------------------------------------------------------
# 可选字段缺省
# ---------------------------------------------------------------------------


def test_binding_defaults_for_optional_fields():
    data = _binding_dict()
    for key in (
        "parent_scope_ref",
        "participant_ref",
        "role_binding_refs",
        "config",
        "g3_subject_type",
    ):
        del data[key]
    binding = MethodBinding.from_dict(data)
    assert binding.parent_scope_ref is None
    assert binding.participant_ref is None
    assert binding.role_binding_refs == ()
    assert binding.config == {}
    assert binding.g3_subject_type is None


# ---------------------------------------------------------------------------
# method_names 投影一致性
# ---------------------------------------------------------------------------


def test_binding_rejects_swapped_method_names():
    # 复合值 Open + Group 的投影是 ("Open", "Group");顺序不能交换。
    data = _binding_dict("Open + Group")
    data["method_names"] = ["Group", "Open"]
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


def test_binding_rejects_incomplete_method_names():
    data = _binding_dict("Open + Group")
    data["method_names"] = ["Open"]
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


@pytest.mark.parametrize("bad", ["Single", ["Single", 1], None])
def test_binding_rejects_non_matching_method_names(bad):
    data = _binding_dict("Open + Group")
    data["method_names"] = bad
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


def test_binding_rejects_invalid_composite_method_kind():
    # 未声明的组合 wire 值(Group + Open)在投影解析处即被拒绝。
    data = _binding_dict()
    data["method_kind"] = "Group + Open"
    data["method_names"] = ["Group", "Open"]
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


# ---------------------------------------------------------------------------
# 必填字段缺失 / 空值
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    [
        "method_binding_id",
        "collab_context_id",
        "method_kind",
        "method_names",
        "profile_versions",
        "state",
        "version",
        "bind_reason",
        "transitioned_from",
        "transition_reason",
        "transitioned_by",
        "transitioned_at",
    ],
)
def test_binding_requires_pinned_fields(missing):
    data = _binding_dict()
    del data[missing]
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


@pytest.mark.parametrize(
    "key,value",
    [
        ("method_binding_id", ""),
        ("method_binding_id", "  "),
        ("method_binding_id", None),
        ("method_binding_id", 123),
        ("collab_context_id", ""),
        ("collab_context_id", None),
        ("method_kind", "Team"),
        ("method_kind", ""),
        ("method_kind", None),
    ],
)
def test_binding_rejects_invalid_required_values(key, value):
    data = _binding_dict()
    data[key] = value
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


# ---------------------------------------------------------------------------
# profile_versions
# ---------------------------------------------------------------------------


def test_binding_profile_versions_must_match_method_names():
    data = _binding_dict("Open + Group")
    # 缺 Open 的 Profile 版本。
    data["profile_versions"] = {"Group": "0.2"}
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)
    # 多余键。
    data["profile_versions"] = {"Open": "0.2", "Group": "0.2", "Swarm": "0.2"}
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)
    # 空版本串。
    data["profile_versions"] = {"Open": "", "Group": "0.2"}
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)
    # 非 dict。
    data["profile_versions"] = ["Open", "Group"]
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


# ---------------------------------------------------------------------------
# BindingState
# ---------------------------------------------------------------------------


def test_binding_state_wire_round_trip():
    for state in BindingState:
        data = _binding_dict()
        data["state"] = state.value
        binding = MethodBinding.from_dict(data)
        assert binding.state is state
        assert binding.to_dict()["state"] == state.value


@pytest.mark.parametrize("bad", ["bound", "BOUND ", "", None, 1])
def test_binding_rejects_invalid_state(bad):
    data = _binding_dict()
    data["state"] = bad
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


def test_binding_state_from_wire_rejects_unknown():
    for value in ("PAUSED", "active", ""):
        with pytest.raises(ContextCollabValidationError):
            BindingState.from_wire(value)
    assert BindingState.from_wire("SUSPENDED") is BindingState.SUSPENDED


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, "1", True, 1.0, None])
def test_binding_rejects_invalid_version(bad):
    data = _binding_dict()
    data["version"] = bad
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


@pytest.mark.parametrize("good", [1, 2, 99])
def test_binding_accepts_positive_versions(good):
    data = _binding_dict()
    data["version"] = good
    assert MethodBinding.from_dict(data).version == good


# ---------------------------------------------------------------------------
# config / max_compose_depth / effective_max_compose_depth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, 65, "8", True, 1.5, None])
def test_binding_rejects_invalid_max_compose_depth(bad):
    data = _binding_dict()
    data["config"] = {"max_compose_depth": bad}
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


@pytest.mark.parametrize("depth", [1, 8, 64])
def test_binding_accepts_max_compose_depth_bounds(depth):
    data = _binding_dict()
    data["config"] = {"max_compose_depth": depth}
    binding = MethodBinding.from_dict(data)
    assert binding.config["max_compose_depth"] == depth
    assert effective_max_compose_depth(binding.config) == depth


def test_binding_does_not_backfill_default_depth():
    # 缺省 8 由服务构造层生效;from_dict 只往返已有内容(spec §12 第 3 条)。
    data = _binding_dict()
    binding = MethodBinding.from_dict(data)
    assert binding.config == {}
    assert "max_compose_depth" not in binding.config
    assert binding.to_dict()["config"] == {}


def test_binding_config_rejects_non_dict_and_non_str_keys():
    data = _binding_dict()
    data["config"] = ["not-a-dict"]
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)
    data["config"] = {1: "x"}
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


def test_binding_config_round_trips_extra_keys():
    # 除 max_compose_depth 外的键值不建平行类型,原样保留。
    data = _binding_dict()
    data["config"] = {"max_compose_depth": 3, "coordination_style": "roster"}
    binding = MethodBinding.from_dict(data)
    assert binding.config == {"max_compose_depth": 3, "coordination_style": "roster"}


def test_effective_max_compose_depth_defaults_to_8():
    assert DEFAULT_MAX_COMPOSE_DEPTH == 8
    assert effective_max_compose_depth({}) == 8
    assert effective_max_compose_depth({"other": 1}) == 8


def test_effective_max_compose_depth_override():
    assert effective_max_compose_depth({"max_compose_depth": 16}) == 16
    assert effective_max_compose_depth({"max_compose_depth": 1}) == 1


@pytest.mark.parametrize("bad", [0, 65, "8", True])
def test_effective_max_compose_depth_rejects_out_of_range(bad):
    # 范围裁断与 from_dict 同一规则;非法值不静默回退缺省。
    with pytest.raises(ContextCollabValidationError):
        effective_max_compose_depth({"max_compose_depth": bad})


# ---------------------------------------------------------------------------
# 未知字段拒绝
# ---------------------------------------------------------------------------


def test_binding_rejects_unknown_fields():
    data = _binding_dict()
    data["extra"] = 1
    with pytest.raises(ContextCollabValidationError, match="unknown field"):
        MethodBinding.from_dict(data)
    data["another_extra"] = 2
    with pytest.raises(ContextCollabValidationError, match="unknown field"):
        MethodBinding.from_dict(data)


def test_binding_rejects_non_dict_payload():
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(["not-a-dict"])


# ---------------------------------------------------------------------------
# 审计四字段(§6.3)
# ---------------------------------------------------------------------------


def test_binding_audit_fields_round_trip_and_allow_empty():
    data = _binding_dict()
    data["transitioned_from"] = ""
    data["transition_reason"] = ""
    binding = MethodBinding.from_dict(data)
    assert binding.transitioned_from == ""
    assert binding.transition_reason == ""
    assert binding.transitioned_by == "principal-1"
    assert binding.transitioned_at == "2026-11-12T10:00:00Z"
    assert binding.to_dict() == data


@pytest.mark.parametrize(
    "key,value",
    [
        ("transitioned_from", None),
        ("transition_reason", 123),
        ("transitioned_by", ["principal-1"]),
        ("transitioned_at", None),
    ],
)
def test_binding_rejects_non_string_audit_fields(key, value):
    data = _binding_dict()
    data[key] = value
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


def test_binding_bind_reason_round_trip():
    data = _binding_dict()
    data["bind_reason"] = "delegate"
    binding = MethodBinding.from_dict(data)
    assert binding.bind_reason == "delegate"
    assert binding.to_dict()["bind_reason"] == "delegate"


# ---------------------------------------------------------------------------
# 引用与冻结 / g3_subject_type
# ---------------------------------------------------------------------------


def test_binding_optional_refs_round_trip():
    data = _binding_dict()
    data["parent_scope_ref"] = "scope-1"
    data["participant_ref"] = "agent-1"
    data["role_binding_refs"] = ["rb-1", "rb-2"]
    binding = MethodBinding.from_dict(data)
    assert binding.parent_scope_ref == "scope-1"
    assert binding.participant_ref == "agent-1"
    assert binding.role_binding_refs == ("rb-1", "rb-2")
    assert binding.to_dict() == data


@pytest.mark.parametrize(
    "key,value",
    [
        ("parent_scope_ref", ""),
        ("parent_scope_ref", "  "),
        ("participant_ref", ""),
        ("role_binding_refs", "rb-1"),
        ("role_binding_refs", [""]),
        ("role_binding_refs", [1]),
    ],
)
def test_binding_rejects_invalid_refs(key, value):
    data = _binding_dict()
    data[key] = value
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


def test_binding_is_frozen():
    binding = MethodBinding.from_dict(_binding_dict())
    with pytest.raises(dataclasses.FrozenInstanceError):
        binding.state = BindingState.ACTIVE


def test_binding_g3_subject_type_none_by_default():
    # G3 词位保留但 P1 恒 None(spec §8.4);值对象不做演进校验。
    binding = MethodBinding.from_dict(_binding_dict())
    assert binding.g3_subject_type is None
    data = _binding_dict()
    data["g3_subject_type"] = ""
    with pytest.raises(ContextCollabValidationError):
        MethodBinding.from_dict(data)


def test_binding_accepts_string_g3_subject_type():
    data = _binding_dict()
    data["g3_subject_type"] = "subject-1"
    binding = MethodBinding.from_dict(data)
    assert binding.g3_subject_type == "subject-1"
    assert binding.to_dict()["g3_subject_type"] == "subject-1"
