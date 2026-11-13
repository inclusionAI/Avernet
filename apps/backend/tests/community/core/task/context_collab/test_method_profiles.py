"""L5 MethodKind 投影与 MethodProfile 注册表单测(Task 1)。

覆盖 ``parse_method_kinds`` 复合/单值 wire 投影与未知组合拒绝、
``METHOD_PROFILE_REGISTRY`` 四核心方法覆盖、Profile 声明字段自洽
(profile_version / composable_relations / primitives_used)与
``get_profile`` 查询/拒绝。
"""
import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
    parse_method_kinds,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    METHOD_PROFILE_REGISTRY,
    get_profile,
)


def test_parse_method_kinds_composite_and_single():
    assert parse_method_kinds("Open + Group") == (MethodKind.OPEN, MethodKind.GROUP)
    assert parse_method_kinds("Single") == (MethodKind.SINGLE,)


def test_parse_method_kinds_maps_core_single_values():
    # Group/Open/Swarm 裸值合法,但仅经嵌套 bind 进入(顶层拒绝在 G1 接缝落)。
    assert parse_method_kinds("Group") == (MethodKind.GROUP,)
    assert parse_method_kinds("Open") == (MethodKind.OPEN,)
    assert parse_method_kinds("Swarm") == (MethodKind.SWARM,)


@pytest.mark.parametrize(
    "wire",
    ["Swarm + Group", "Open + Swarm"],
)
def test_parse_method_kinds_rejects_unknown(wire):
    # 未声明的复合 wire 值一律拒绝,含以 Open 开头的组合。
    with pytest.raises(ContextCollabValidationError):
        parse_method_kinds(wire)


def test_registry_covers_four_core_methods():
    assert set(METHOD_PROFILE_REGISTRY) == {"Single", "Group", "Open", "Swarm"}


def test_profile_declares_composition_relations_and_primitives():
    for name, profile in METHOD_PROFILE_REGISTRY.items():
        assert profile.profile_version == "0.2"
        assert profile.composable_relations  # 非空，与 Task 3 矩阵行一致（Task 3 再钉）
        assert set(profile.primitives_used) <= {"Task", "Message", "Artifact", "Decision", "Query", "Activity"}


def test_profile_declares_participation_model_and_capabilities():
    for name, profile in METHOD_PROFILE_REGISTRY.items():
        assert profile.participation_model in {"selected", "roster", "stage", "admission"}
        assert profile.method_kind == name
        assert profile.capabilities


def test_get_profile_returns_registry_entry():
    for name, profile in METHOD_PROFILE_REGISTRY.items():
        assert get_profile(name) is profile


def test_get_profile_rejects_unknown_method():
    with pytest.raises(ContextCollabValidationError):
        get_profile("Unknown")
