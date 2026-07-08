from __future__ import annotations

import pytest

from agentclaw.community.core.common_config.beta_quota_service import (
    BUSINESS_CODE,
    PARAM_CODE,
    BetaQuotaService,
)


class FakeCommonConfigService:
    def __init__(
        self, config: dict | None = None, call_log: list | None = None
    ) -> None:
        self.config = config
        self.updates: list[dict] = []
        self._call_log = call_log

    def get_config(
        self, *, business_code: str, param_code: str, env: str, only_enabled: bool = False
    ):
        assert business_code == BUSINESS_CODE
        assert param_code == PARAM_CODE
        return self.config

    def update_config(self, *, config_id: int, updates: dict) -> bool:
        if self._call_log is not None:
            self._call_log.append("update_config")
        self.updates.append({"config_id": config_id, "updates": updates})
        if self.config is not None:
            self.config["param_value"] = updates["param_value"]
        return True


class FakePolicyService:
    """Records allow() calls."""

    def __init__(self, call_log: list | None = None) -> None:
        self.allow_calls: list[dict] = []
        self._call_log = call_log

    def allow(self, *, entity_id: str, entity_type: str) -> None:
        if self._call_log is not None:
            self._call_log.append("allow")
        self.allow_calls.append({"entity_id": entity_id, "entity_type": entity_type})


def _service(
    config: dict | None,
) -> tuple[BetaQuotaService, FakeCommonConfigService, FakePolicyService]:
    fake_config = FakeCommonConfigService(config)
    fake_policy = FakePolicyService()
    return BetaQuotaService(fake_config, fake_policy), fake_config, fake_policy


def _config(
    total: int, remaining: int, config_id: int = 7, enable: str = "1"
) -> dict:
    return {
        "id": config_id,
        "param_value": {"total": total, "remaining": remaining},
        "enable": enable,
    }


def test_get_quota_returns_total_and_remaining():
    service, _, _ = _service(_config(100, 30))

    assert service.get_quota("pre") == {"total": 100, "remaining": 30}


def test_get_quota_raises_when_config_missing():
    service, _, _ = _service(None)

    with pytest.raises(ValueError, match="内测名额配置不存在"):
        service.get_quota("pre")


def test_adjust_quota_increase_keeps_total():
    call_log: list = []
    fake = FakeCommonConfigService(_config(100, 30), call_log=call_log)
    policy = FakePolicyService(call_log=call_log)
    service = BetaQuotaService(fake, policy)

    assert service.adjust_quota("pre", 5, "1001") == {"total": 100, "remaining": 35}
    assert fake.updates[0]["updates"]["param_value"] == {"total": 100, "remaining": 35}
    # 先加白后扣名额：allow 必须发生在 update_config 之前
    assert call_log == ["allow", "update_config"]


def test_adjust_quota_decrease():
    service, _, _ = _service(_config(100, 30))

    assert service.adjust_quota("pre", -1, "1001") == {"total": 100, "remaining": 29}


def test_adjust_quota_rejects_negative_remaining():
    service, fake, policy = _service(_config(100, 0))

    with pytest.raises(ValueError, match="名额不足"):
        service.adjust_quota("pre", -1, "1001")
    assert fake.updates == []
    assert policy.allow_calls == []


def test_adjust_quota_raises_when_config_missing():
    service, _, _ = _service(None)

    with pytest.raises(ValueError, match="内测名额配置不存在"):
        service.adjust_quota("pre", -1, "1001")


def test_get_quota_raises_when_config_disabled():
    service, _, _ = _service(_config(100, 30, enable="0"))

    with pytest.raises(ValueError, match="内测未开放"):
        service.get_quota("pre")


def test_adjust_quota_raises_when_config_disabled():
    service, fake, policy = _service(_config(100, 30, enable="0"))

    with pytest.raises(ValueError, match="内测未开放"):
        service.adjust_quota("pre", -1, "1001")
    assert fake.updates == []
    assert policy.allow_calls == []


def test_get_quota_works_when_config_enabled():
    service, _, _ = _service(_config(100, 30, enable="1"))

    assert service.get_quota("pre") == {"total": 100, "remaining": 30}


def test_adjust_quota_whitelists_caller_when_no_record():
    # 无 policy 记录 → allow 被调用（merge upsert 插入 policy=on）
    service, _, policy = _service(_config(100, 30))

    service.adjust_quota("pre", -1, "1001")

    assert policy.allow_calls == [{"entity_id": "1001", "entity_type": "staff"}]


def test_adjust_quota_whitelists_caller_when_policy_off():
    # 已有 policy=off → allow 仍被调用（新语义核心：off 必须翻成 on）
    service, _, policy = _service(_config(100, 30))

    service.adjust_quota("pre", 1, "1001")

    assert policy.allow_calls == [{"entity_id": "1001", "entity_type": "staff"}]


def test_adjust_quota_whitelists_caller_when_policy_on():
    # 已有 policy=on → allow 仍被调用（幂等）
    service, _, policy = _service(_config(100, 30))

    service.adjust_quota("pre", 1, "1001")

    assert policy.allow_calls == [{"entity_id": "1001", "entity_type": "staff"}]


def test_adjust_quota_raises_when_whitelist_fails():
    fake_config = FakeCommonConfigService(_config(100, 30))

    class _RaisingPolicy(FakePolicyService):
        def allow(self, *, entity_id: str, entity_type: str) -> None:
            raise RuntimeError("boom")

    service = BetaQuotaService(fake_config, _RaisingPolicy())

    # 加白失败 → 整体失败，对外固定 message，名额不写入
    with pytest.raises(ValueError, match="申请试用白名单失败"):
        service.adjust_quota("pre", -1, "1001")
    assert fake_config.updates == []
