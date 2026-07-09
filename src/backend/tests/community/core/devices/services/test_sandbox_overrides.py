"""Tests for agentclaw.community.core.devices.services.sandbox_overrides.SandboxOverrides."""
from __future__ import annotations

import pytest

from agentclaw.community.kernel.device_dto import ResourceSpecification
from agentclaw.community.core.devices.services.sandbox_overrides import (
    InvalidSandboxOverridesError,
    SandboxOverrides,
)


# ---------------------------------------------------------------------------
# TestFromTemplateConfig
# ---------------------------------------------------------------------------


class TestFromTemplateConfig:
    """SandboxOverrides.from_template_config extracts fields from a dict."""

    def test_none_returns_empty(self) -> None:
        so = SandboxOverrides.from_template_config(None)
        assert so.is_empty()

    def test_empty_dict_returns_empty(self) -> None:
        so = SandboxOverrides.from_template_config({})
        assert so.is_empty()

    def test_all_default_returns_empty(self) -> None:
        so = SandboxOverrides.from_template_config({"image": None, "command": None})
        assert so.image is None
        assert so.command is None
        assert so.is_empty()

    def test_image_only(self) -> None:
        so = SandboxOverrides.from_template_config({"image": "img:v1"})
        assert so.image == "img:v1"
        assert so.command is None
        assert so.envs == {}
        assert so.resource_spec is None

    def test_command_only_string(self) -> None:
        so = SandboxOverrides.from_template_config({"command": "python main.py"})
        assert so.image is None
        assert so.command == "python main.py"
        assert so.envs == {}
        assert so.resource_spec is None

    def test_command_list_joined_with_spaces(self) -> None:
        """Backward compat: if command is a list, join with spaces."""
        so = SandboxOverrides.from_template_config({"command": ["sh", "-c", "echo hi"]})
        assert so.command == "sh -c echo hi"

    def test_envs_only(self) -> None:
        so = SandboxOverrides.from_template_config({"envs": {"FOO": "bar"}})
        assert so.image is None
        assert so.command is None
        assert so.envs == {"FOO": "bar"}
        assert so.resource_spec is None

    def test_resource_spec_only(self) -> None:
        so = SandboxOverrides.from_template_config({"resource_spec": {"cpu": 4, "memory": 8}})
        assert so.image is None
        assert so.command is None
        assert so.envs == {}
        assert isinstance(so.resource_spec, ResourceSpecification)
        assert so.resource_spec.cpu == 4
        assert so.resource_spec.memory == 8

    def test_resource_spec_with_disk(self) -> None:
        so = SandboxOverrides.from_template_config({"resource_spec": {"cpu": 2, "memory": 4, "disk": 100}})
        assert so.resource_spec is not None
        assert so.resource_spec.cpu == 2
        assert so.resource_spec.memory == 4
        assert so.resource_spec.disk == 100  # disk is optional, passed through

    def test_resource_spec_invalid_dict_returns_none(self) -> None:
        """Invalid resource_spec dict (missing keys) results in None — validate() catches it."""
        so = SandboxOverrides.from_template_config({"resource_spec": {"cpu": 4}})
        assert so.resource_spec is None

    def test_resource_spec_non_dict_ignored(self) -> None:
        so = SandboxOverrides.from_template_config({"resource_spec": "4C8G"})
        assert so.resource_spec is None

    def test_all_fields(self) -> None:
        tc = {
            "image": "img:v2",
            "command": "python -m bot",
            "envs": {"A": "1", "B": "2"},
            "resource_spec": {"cpu": 2, "memory": 4},
        }
        so = SandboxOverrides.from_template_config(tc)
        assert so.image == "img:v2"
        assert so.command == "python -m bot"
        assert so.envs == {"A": "1", "B": "2"}
        assert isinstance(so.resource_spec, ResourceSpecification)
        assert so.resource_spec.cpu == 2
        assert so.resource_spec.memory == 4

    def test_unknown_fields_ignored(self) -> None:
        tc = {"image": "img:v1", "unknownField": 42, "another": True}
        so = SandboxOverrides.from_template_config(tc)
        assert so.image == "img:v1"
        assert so.is_empty() is False  # image is set

    def test_envs_value_coercion_int_to_str(self) -> None:
        so = SandboxOverrides.from_template_config({"envs": {"PORT": 8080, "DEBUG": 1}})
        assert so.envs == {"PORT": "8080", "DEBUG": "1"}

    def test_command_int_coerced_to_str(self) -> None:
        so = SandboxOverrides.from_template_config({"command": 42})
        assert so.command == "42"

    def test_envs_non_dict_ignored(self) -> None:
        so = SandboxOverrides.from_template_config({"envs": "not-a-dict"})
        assert so.envs == {}

    def test_envs_empty_dict(self) -> None:
        so = SandboxOverrides.from_template_config({"envs": {}})
        assert so.envs == {}


# ---------------------------------------------------------------------------
# TestIsEmpty
# ---------------------------------------------------------------------------


class TestIsEmpty:
    """SandboxOverrides.is_empty() returns True only when all fields are default."""

    def test_all_none_is_empty(self) -> None:
        assert SandboxOverrides().is_empty()

    def test_image_set_not_empty(self) -> None:
        assert SandboxOverrides(image="img").is_empty() is False

    def test_command_set_not_empty(self) -> None:
        assert SandboxOverrides(command="sh").is_empty() is False

    def test_envs_set_not_empty(self) -> None:
        assert SandboxOverrides(envs={"K": "V"}).is_empty() is False

    def test_resource_spec_set_not_empty(self) -> None:
        assert SandboxOverrides(resource_spec=ResourceSpecification(cpu=2, memory=4)).is_empty() is False

    def test_empty_command_string_not_empty(self) -> None:
        """An explicitly-set empty command string is still an override (explicit intent)."""
        assert SandboxOverrides(command="").is_empty() is False


# ---------------------------------------------------------------------------
# TestValidate
# ---------------------------------------------------------------------------


class TestValidate:
    """SandboxOverrides.validate() raises on invalid fields."""

    # -- image --

    def test_image_none_skips(self) -> None:
        SandboxOverrides(image=None).validate()  # no error

    def test_image_valid(self) -> None:
        SandboxOverrides(image="ubuntu:22.04").validate()

    def test_image_empty_string_fails(self) -> None:
        with pytest.raises(InvalidSandboxOverridesError, match="image"):
            SandboxOverrides(image="").validate()

    def test_image_whitespace_fails(self) -> None:
        with pytest.raises(InvalidSandboxOverridesError, match="image"):
            SandboxOverrides(image="   ").validate()

    # -- command --

    def test_command_none_skips(self) -> None:
        SandboxOverrides(command=None).validate()

    def test_command_valid_string(self) -> None:
        SandboxOverrides(command="python main.py").validate()

    def test_command_empty_string_fails(self) -> None:
        with pytest.raises(InvalidSandboxOverridesError, match="command"):
            SandboxOverrides(command="").validate()

    def test_command_whitespace_fails(self) -> None:
        with pytest.raises(InvalidSandboxOverridesError, match="command"):
            SandboxOverrides(command="   ").validate()

    # -- envs --

    def test_envs_empty_passes(self) -> None:
        SandboxOverrides(envs={}).validate()

    def test_envs_valid_passes(self) -> None:
        SandboxOverrides(envs={"FOO": "bar", "BAZ": "qux"}).validate()

    def test_envs_non_string_key_fails(self) -> None:
        with pytest.raises(InvalidSandboxOverridesError, match="envs"):
            # Bypass from_template_config coercion to test validate directly
            so = SandboxOverrides.__new__(SandboxOverrides)
            object.__setattr__(so, "image", None)
            object.__setattr__(so, "command", None)
            object.__setattr__(so, "envs", {42: "val"})  # type: ignore[dict-item]
            object.__setattr__(so, "resource_spec", None)
            so.validate()

    def test_envs_non_string_value_fails(self) -> None:
        with pytest.raises(InvalidSandboxOverridesError, match="envs"):
            so = SandboxOverrides.__new__(SandboxOverrides)
            object.__setattr__(so, "image", None)
            object.__setattr__(so, "command", None)
            object.__setattr__(so, "envs", {"KEY": 123})  # type: ignore[dict-item]
            object.__setattr__(so, "resource_spec", None)
            so.validate()

    # -- resource_spec --

    def test_resource_spec_none_skips(self) -> None:
        SandboxOverrides(resource_spec=None).validate()

    def test_resource_spec_valid(self) -> None:
        SandboxOverrides(resource_spec=ResourceSpecification(cpu=4, memory=8)).validate()

    def test_resource_spec_zero_cpu_fails(self) -> None:
        with pytest.raises(InvalidSandboxOverridesError, match="cpu"):
            SandboxOverrides(resource_spec=ResourceSpecification(cpu=0, memory=8)).validate()

    def test_resource_spec_negative_cpu_fails(self) -> None:
        with pytest.raises(InvalidSandboxOverridesError, match="cpu"):
            SandboxOverrides(resource_spec=ResourceSpecification(cpu=-1, memory=8)).validate()

    def test_resource_spec_zero_memory_fails(self) -> None:
        with pytest.raises(InvalidSandboxOverridesError, match="memory"):
            SandboxOverrides(resource_spec=ResourceSpecification(cpu=4, memory=0)).validate()

    def test_resource_spec_negative_memory_fails(self) -> None:
        with pytest.raises(InvalidSandboxOverridesError, match="memory"):
            SandboxOverrides(resource_spec=ResourceSpecification(cpu=4, memory=-2)).validate()

    def test_resource_spec_not_resource_spec_object_fails(self) -> None:
        with pytest.raises(InvalidSandboxOverridesError, match="resource_spec"):
            so = SandboxOverrides.__new__(SandboxOverrides)
            object.__setattr__(so, "image", None)
            object.__setattr__(so, "command", None)
            object.__setattr__(so, "envs", {})
            object.__setattr__(so, "resource_spec", "4C8G")  # type: ignore[assignment]
            so.validate()


# ---------------------------------------------------------------------------
# TestMergedEnvs
# ---------------------------------------------------------------------------


class TestMergedEnvs:
    """SandboxOverrides.merged_envs() merges user envs over base envs."""

    def test_empty_overrides_returns_base_copy(self) -> None:
        base = {"A": "1", "B": "2"}
        so = SandboxOverrides()
        result = so.merged_envs(base)
        assert result == {"A": "1", "B": "2"}
        assert result is not base  # must be a copy

    def test_no_overlap_merges(self) -> None:
        base = {"A": "1"}
        so = SandboxOverrides(envs={"B": "2"})
        assert so.merged_envs(base) == {"A": "1", "B": "2"}

    def test_user_overrides_base(self) -> None:
        base = {"A": "platform", "B": "default"}
        so = SandboxOverrides(envs={"B": "user"})
        assert so.merged_envs(base) == {"A": "platform", "B": "user"}

    def test_empty_user_envs_no_effect(self) -> None:
        base = {"X": "1"}
        so = SandboxOverrides(envs={})
        assert so.merged_envs(base) == {"X": "1"}

    def test_does_not_mutate_base(self) -> None:
        base = {"A": "1"}
        original = dict(base)
        so = SandboxOverrides(envs={"B": "2"})
        so.merged_envs(base)
        assert base == original


# ---------------------------------------------------------------------------
# TestToCreateKwargs
# ---------------------------------------------------------------------------


class TestToCreateKwargs:
    """SandboxOverrides.to_create_kwargs() returns only non-None fields."""

    def test_all_empty_returns_empty_dict(self) -> None:
        assert SandboxOverrides().to_create_kwargs() == {}

    def test_image_only(self) -> None:
        so = SandboxOverrides(image="img:v1")
        assert so.to_create_kwargs() == {"image": "img:v1"}

    def test_command_only(self) -> None:
        so = SandboxOverrides(command="python main.py")
        assert so.to_create_kwargs() == {"command": "python main.py"}

    def test_resource_spec_only(self) -> None:
        rs = ResourceSpecification(cpu=4, memory=8)
        so = SandboxOverrides(resource_spec=rs)
        kwargs = so.to_create_kwargs()
        assert kwargs == {"resource_spec": rs}
        assert isinstance(kwargs["resource_spec"], ResourceSpecification)

    def test_all_set(self) -> None:
        rs = ResourceSpecification(cpu=2, memory=4)
        so = SandboxOverrides(
            image="img:v1",
            command="python main.py",
            envs={"K": "V"},
            resource_spec=rs,
        )
        kwargs = so.to_create_kwargs()
        assert kwargs == {
            "image": "img:v1",
            "command": "python main.py",
            "resource_spec": rs,
        }

    def test_envs_not_in_kwargs(self) -> None:
        """envs are handled via merged_envs(), not to_create_kwargs()."""
        so = SandboxOverrides(envs={"KEY": "VAL"})
        assert so.to_create_kwargs() == {}
