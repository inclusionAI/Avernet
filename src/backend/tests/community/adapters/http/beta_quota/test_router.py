"""Beta Quota HTTP router unit tests."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.beta_quota import router
from agentclaw.community.adapters.http.beta_quota.schemas import AdjustQuotaRequest


_USER = AuthenticatedUser(id="owner-1", staffId="owner-1", operatorName="owner-1")


def _run(coro):
    """Run async route handler from sync tests."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fixed_env(monkeypatch):
    """Keep router env parameter deterministic."""
    monkeypatch.setattr(router.env_utils, "get_current_env", lambda: "pre")


def test_get_quota_returns_total_and_remaining():
    service = MagicMock()
    service.get_quota.return_value = {"total": 100, "remaining": 42}

    resp = _run(router.get_quota(user=_USER, service=service))

    assert resp.success is True
    assert resp.message == "OK"
    assert resp.error_code == 200
    assert resp.data == {"total": 100, "remaining": 42}
    service.get_quota.assert_called_once_with("pre")


def test_get_quota_returns_40401_when_config_missing():
    service = MagicMock()
    service.get_quota.side_effect = ValueError("配置不存在")

    resp = _run(router.get_quota(user=_USER, service=service))

    assert resp.success is False
    assert resp.message == "配置不存在"
    assert resp.error_code == 40401
    assert resp.data is None


def test_adjust_quota_returns_new_remaining():
    service = MagicMock()
    service.adjust_quota.return_value = {"total": 100, "remaining": 41}

    resp = _run(
        router.adjust_quota(AdjustQuotaRequest(delta=-1), user=_USER, service=service)
    )

    assert resp.success is True
    assert resp.message == "OK"
    assert resp.error_code == 200
    assert resp.data == {"total": 100, "remaining": 41}
    service.adjust_quota.assert_called_once_with("pre", -1, "owner-1")


def test_adjust_quota_returns_40001_when_quota_insufficient():
    service = MagicMock()
    service.adjust_quota.side_effect = ValueError("名额不足")

    resp = _run(
        router.adjust_quota(AdjustQuotaRequest(delta=-5), user=_USER, service=service)
    )

    assert resp.success is False
    assert resp.message == "名额不足"
    assert resp.error_code == 40001
    assert resp.data is None
