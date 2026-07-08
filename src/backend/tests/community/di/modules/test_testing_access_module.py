"""TestingAccessModule -- pytest vs singlebox PolicyService 分流。

被测对象: ``_policy_service_protocol`` provider 内的 ``SERVER_ENV=singlebox``
分流逻辑。

为什么需要单测: pytest 默认进程不带 ``SERVER_ENV=singlebox``,所以日常单测只走
``LocalPolicyService`` 那条 fallback (返 prod_impl); singlebox 分支 (返
``LocalPolicyService``) 只有显式 monkeypatch 才能命中。本文件覆盖两条分支。

不通过 injector 整体 build,直接调 provider 方法验返回实例,保持单测窄、快。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.di.modules.testing_access_module import TestingAccessModule
from agentclaw.community.plugins.local.policy_service import LocalPolicyService


@pytest.fixture
def module() -> TestingAccessModule:
    return TestingAccessModule()


@pytest.fixture
def prod_impl() -> MagicMock:
    """假装的 prod PolicyService —— 单测只验 provider 是否选了 Local 还是透传 prod。"""
    return MagicMock(name="ProdPolicyService")


def test_singlebox_branch_returns_local(module, prod_impl, monkeypatch):
    """SERVER_ENV=singlebox → LocalPolicyService(); prod_impl 被忽略。"""
    monkeypatch.setenv("SERVER_ENV", "singlebox")
    result = module._policy_service_protocol(prod_impl)
    assert isinstance(result, LocalPolicyService)


def test_singlebox_branch_case_insensitive(module, prod_impl, monkeypatch):
    """SERVER_ENV 比较走 lower(),大写也命中。"""
    monkeypatch.setenv("SERVER_ENV", "SingleBox")
    result = module._policy_service_protocol(prod_impl)
    assert isinstance(result, LocalPolicyService)


def test_pytest_default_branch_returns_prod(module, prod_impl, monkeypatch):
    """SERVER_ENV 未设(pytest 默认) → 透传 prod_impl,本地 e2e 测试不受影响。"""
    monkeypatch.delenv("SERVER_ENV", raising=False)
    result = module._policy_service_protocol(prod_impl)
    assert result is prod_impl


def test_non_singlebox_env_returns_prod(module, prod_impl, monkeypatch):
    """SERVER_ENV=dev / prod 等其他值 → 透传 prod_impl。"""
    monkeypatch.setenv("SERVER_ENV", "dev")
    result = module._policy_service_protocol(prod_impl)
    assert result is prod_impl
