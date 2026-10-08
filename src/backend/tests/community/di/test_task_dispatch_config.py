from __future__ import annotations

import pytest

from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule


def test_task_dispatch_sample_count_defaults_to_one(monkeypatch):
    monkeypatch.setattr(config_module, "_block", lambda _name: {})
    assert ConfigModule().task_dispatch().sample_count == 1


@pytest.mark.parametrize("value", [2, 5, "3"])
def test_task_dispatch_accepts_bounded_sample_count(monkeypatch, value):
    monkeypatch.setattr(
        config_module, "_block", lambda _name: {"sample_count": value}
    )
    assert ConfigModule().task_dispatch().sample_count == int(value)


@pytest.mark.parametrize("value", [0, -1, 6, True, "bad"])
def test_task_dispatch_invalid_sample_count_falls_back_to_one(monkeypatch, value):
    monkeypatch.setattr(
        config_module, "_block", lambda _name: {"sample_count": value}
    )
    assert ConfigModule().task_dispatch().sample_count == 1
