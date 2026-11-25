"""Service tests for ``task_config`` value encode/validate + match (spec 2026-11-02).

Covers ReviewFocus #1 (set member absent -> False), #2 (unknown name -> False),
#4 (env isolation), #5 (no silent str/bool coercion), plus type/shape rejection.
The service is constructed directly with a fake repo (bypassing injector).
"""

from types import SimpleNamespace

import pytest

from agentclaw.community.core.errors import CallbackCorrelationError
from agentclaw.community.core.task.task_config.service import TaskConfigService


class _FakeRepo:
    """In-memory TaskConfigRepositoryProtocol: stores the JSON text the service
    hands it (it does not interpret value semantics — same contract as the real
    repository, which persists pre-serialized JSON text)."""

    def __init__(self):
        self.rows: dict[tuple[str, str], SimpleNamespace] = {}

    def upsert(self, *, config_name, config_type, config_value, env, operator=None):
        key = (env, config_name)
        existing = self.rows.get(key)
        creator = existing.creator if existing else operator
        row = SimpleNamespace(
            id=existing.id if existing else len(self.rows) + 1,
            config_name=config_name,
            config_type=config_type,
            config_value=config_value,
            env=env,
            creator=creator,
            modifier=operator,
        )
        self.rows[key] = row
        return row.id

    def get_by_name(self, *, env, config_name):
        return self.rows.get((env, config_name))


def _svc() -> TaskConfigService:
    return TaskConfigService(repo=_FakeRepo())


def test_match_set_member_present_and_absent():
    s = _svc()
    s.set_config(
        config_name="skill_set",
        config_type="set",
        config_value=["a", "b"],
        env="pre",
        operator="u",
    )
    assert s.match(config_name="skill_set", value="a", env="pre") is True
    assert s.match(config_name="skill_set", value="z", env="pre") is False  # RF #1


def test_match_unknown_name_returns_false():
    assert _svc().match(config_name="nope", value="a", env="pre") is False  # RF #2


def test_match_single_string_and_bool():
    s = _svc()
    s.set_config(config_name="ss", config_type="single_string", config_value="hello", env="pre")
    assert s.match(config_name="ss", value="hello", env="pre") is True
    assert s.match(config_name="ss", value="world", env="pre") is False
    s.set_config(config_name="fl", config_type="bool", config_value=True, env="pre")
    assert s.match(config_name="fl", value=True, env="pre") is True
    assert s.match(config_name="fl", value=False, env="pre") is False


def test_set_invalid_type_raises():
    with pytest.raises(CallbackCorrelationError):
        _svc().set_config(config_name="x", config_type="bogus", config_value="y", env="pre")


def test_set_value_shape_mismatch_raises():
    s = _svc()
    with pytest.raises(CallbackCorrelationError):
        s.set_config(config_name="x", config_type="set", config_value="not_a_list", env="pre")
    with pytest.raises(CallbackCorrelationError):
        s.set_config(config_name="y", config_type="bool", config_value="not_bool", env="pre")


def test_match_rejects_value_type_mismatch():
    s = _svc()
    s.set_config(config_name="fl", config_type="bool", config_value=True, env="pre")
    with pytest.raises(CallbackCorrelationError):
        s.match(config_name="fl", value="true", env="pre")  # RF #5: no silent coercion


def test_match_isolated_across_envs():
    s = _svc()
    s.set_config(config_name="x", config_type="single_string", config_value="pre_v", env="pre")
    assert s.match(config_name="x", value="pre_v", env="prod") is False  # RF #4
