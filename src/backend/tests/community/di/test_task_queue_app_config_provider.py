"""Unit tests for ``ConfigModule.task_queue`` — the owning-app config boundary.

``app`` decides which rows of the shared ``ac_task_queue`` table this deployment
enqueues into and claims from. Both sides read this one value, so the risk is not
the two disagreeing within a process; it is a value the column cannot carry
faithfully, which makes the *stored* name differ from the one the claim filter
looks for. That is silent: work is enqueued and simply never runs.

Hence the provider rejects rather than falls back. Falling back is the specific
accident worth preventing — the default is the *other* deployment's name as often
as not, so substituting it turns a typo into one backend claiming another's work.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task_queue.types import DEFAULT_APP, MAX_APP_LEN
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule


@pytest.fixture
def stub_user_config(monkeypatch):
    def _set(user_config: dict) -> None:
        monkeypatch.setattr(config_module, "_user_config", lambda: dict(user_config))

    return _set


def test_absent_block_keeps_the_column_default(stub_user_config):
    """A deployment that never heard of this block keeps owning exactly the rows
    it already owned — the dataclass default is the table's column default."""
    stub_user_config({})
    assert ConfigModule().task_queue().app == DEFAULT_APP


def test_configured_app_is_used_verbatim(stub_user_config):
    stub_user_config({"task_queue": {"app": "teclaw"}})
    assert ConfigModule().task_queue().app == "teclaw"


def test_app_at_the_length_limit_is_accepted(stub_user_config):
    """The bound is inclusive — the boundary value must not be rejected."""
    name = "a" * MAX_APP_LEN
    stub_user_config({"task_queue": {"app": name}})
    assert ConfigModule().task_queue().app == name


@pytest.mark.parametrize("blank", ["", "   ", "\t", None])
def test_blank_app_is_rejected(stub_user_config, blank):
    stub_user_config({"task_queue": {"app": blank}})
    with pytest.raises(ValueError, match="task_queue.app must name"):
        ConfigModule().task_queue()


@pytest.mark.parametrize("padded", ["claw ", " claw", "claw\t"])
def test_padded_app_is_rejected(stub_user_config, padded):
    """Not tidiness: MySQL/OceanBase compare with a PAD SPACE collation, so
    ``"claw "`` and ``"claw"`` are one app there and two on SQLite — a
    divergence no test running on SQLite could observe."""
    stub_user_config({"task_queue": {"app": padded}})
    with pytest.raises(ValueError, match="leading or trailing whitespace"):
        ConfigModule().task_queue()


def test_over_length_app_is_rejected(stub_user_config):
    """A non-strict server truncates it, so rows are filed under a name the
    claim filter never matches and none of this deployment's work ever runs."""
    stub_user_config({"task_queue": {"app": "a" * (MAX_APP_LEN + 1)}})
    with pytest.raises(ValueError, match="exceeds"):
        ConfigModule().task_queue()


def test_rejection_is_not_a_silent_fallback_to_the_default(stub_user_config):
    """The behaviour this whole boundary exists for. Were a bad value to fall
    back, a deployment named ``teclaw`` with a typo would boot as ``agentclaw``
    and start claiming the other backend's tasks."""
    stub_user_config({"task_queue": {"app": "teclaw " * 20}})
    with pytest.raises(ValueError):
        ConfigModule().task_queue()
