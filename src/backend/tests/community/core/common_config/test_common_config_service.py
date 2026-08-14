from __future__ import annotations

import pytest

from agentclaw.community.core.common_config.models import CommonConfigRecord
from agentclaw.community.core.common_config.service import CommonConfigService


class FakeCommonConfigRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], CommonConfigRecord] = {}
        self.next_id = 1

    def get_by_id(self, *, config_id: int):
        for row in self.rows.values():
            if row.id == config_id:
                return row
        return None

    def get_by_biz_param(self, *, business_code: str, param_code: str, env: str):
        return self.rows.get((business_code, param_code, env))

    def list_configs(self, *, env, business_code=None, enable=None, keyword=None, offset=0, limit=100):
        rows = [r for r in self.rows.values() if r.env == env]
        if business_code is not None:
            rows = [r for r in rows if r.business_code == business_code]
        if enable is not None:
            rows = [r for r in rows if r.enable == enable]
        if keyword is not None:
            rows = [
                r
                for r in rows
                if keyword in (r.business_name or "")
                or keyword in r.param_code
                or keyword in r.param_name
            ]
        return rows[offset : offset + limit]

    def count_configs(self, *, env, business_code=None, enable=None, keyword=None):
        return len(
            self.list_configs(
                env=env,
                business_code=business_code,
                enable=enable,
                keyword=keyword,
                offset=0,
                limit=10_000,
            )
        )

    def create_config(self, **kwargs):
        row = CommonConfigRecord(id=self.next_id, gmt_create=None, gmt_modified=None, **kwargs)
        self.next_id += 1
        self.rows[(row.business_code, row.param_code, row.env)] = row
        return row.id

    def update_config(self, *, config_id: int, updates: dict):
        row = self.get_by_id(config_id=config_id)
        if row is None:
            return False
        for key, value in updates.items():
            setattr(row, key, value)
        return True

    def upsert_config(self, **kwargs):
        key = (kwargs["business_code"], kwargs["param_code"], kwargs["env"])
        existing = self.rows.get(key)
        if existing is None:
            return self.create_config(**kwargs)
        for field in ("business_name", "param_name", "param_value", "enable", "ext_info"):
            setattr(existing, field, kwargs[field])
        return existing.id

    def delete_config(self, *, config_id: int):
        row = self.get_by_id(config_id=config_id)
        if row is None:
            return False
        del self.rows[(row.business_code, row.param_code, row.env)]
        return True

    def delete_by_biz_param(self, *, business_code: str, param_code: str, env: str):
        return self.rows.pop((business_code, param_code, env), None) is not None


def test_skills_pool_rollout_rejects_generic_config_writes() -> None:
    repo = FakeCommonConfigRepository()
    service = CommonConfigService(repo)

    with pytest.raises(ValueError, match="operator API"):
        service.upsert_config(
            business_code="skills_pool",
            business_name="Skills Pool",
            param_code="layout_rollout",
            param_name="Rollout",
            param_value={},
            env="pre",
        )

    config_id = repo.create_config(
        business_code="skills_pool",
        business_name="Skills Pool",
        param_code="layout_rollout",
        param_name="Rollout",
        param_value="{}",
        enable="0",
        ext_info=None,
        env="pre",
    )
    with pytest.raises(ValueError, match="operator API"):
        service.update_config(
            config_id=config_id,
            updates={"enable": "1"},
        )


def test_generic_update_cannot_rename_config_into_rollout_key() -> None:
    repo = FakeCommonConfigRepository()
    service = CommonConfigService(repo)
    config_id = service.create_config(
        business_code="other",
        business_name="Other",
        param_code="safe",
        param_name="Safe",
        param_value={},
        env="pre",
    )

    with pytest.raises(ValueError, match="operator API"):
        service.update_config(
            config_id=config_id,
            updates={
                "business_code": "skills_pool",
                "param_code": "layout_rollout",
            },
        )


def test_common_config_service_serializes_value_and_hides_disabled_by_default():
    repo = FakeCommonConfigRepository()
    service = CommonConfigService(repo)

    config_id = service.upsert_config(
        business_code="engine_mount",
        business_name="引擎目录挂载",
        param_code="user_whitelist",
        param_name="用户白名单",
        param_value=["10001", "10002"],
        ext_info={"scene": "engine_dir_mount"},
        enable="1",
        env="pre",
    )

    assert service.get_value(
        business_code="engine_mount",
        param_code="user_whitelist",
        env="pre",
    ) == ["10001", "10002"]

    assert service.get_config(
        business_code="engine_mount",
        param_code="user_whitelist",
        env="pre",
    )["ext_info"] == {"scene": "engine_dir_mount"}

    service.disable_config(config_id=config_id)
    assert service.get_value(
        business_code="engine_mount",
        param_code="user_whitelist",
        env="pre",
        default=[],
    ) == []
    assert service.get_value(
        business_code="engine_mount",
        param_code="user_whitelist",
        env="pre",
        default=[],
        only_enabled=False,
    ) == ["10001", "10002"]


@pytest.mark.parametrize("param_value", [None, "null"], ids=["sql_null", "json_null"])
def test_common_config_service_preserves_enabled_top_level_null(param_value):
    repo = FakeCommonConfigRepository()
    service = CommonConfigService(repo)
    sentinel = object()

    service.create_config(
        business_code="bot_dormant",
        business_name="沉寂 bot",
        param_code="protected_owner_ids",
        param_name="受保护 owner",
        param_value=param_value,
        enable="1",
        env="prod",
    )

    assert service.get_value(
        business_code="bot_dormant",
        param_code="protected_owner_ids",
        env="prod",
        default=sentinel,
    ) is None


def test_common_config_service_returns_default_for_missing_and_disabled_config():
    repo = FakeCommonConfigRepository()
    service = CommonConfigService(repo)
    missing_sentinel = object()
    disabled_sentinel = object()

    assert service.get_value(
        business_code="bot_dormant",
        param_code="protected_owner_ids",
        env="prod",
        default=missing_sentinel,
    ) is missing_sentinel

    config_id = service.create_config(
        business_code="bot_dormant",
        business_name="沉寂 bot",
        param_code="protected_owner_ids",
        param_name="受保护 owner",
        param_value=["100001"],
        enable="1",
        env="prod",
    )
    service.disable_config(config_id=config_id)

    assert service.get_value(
        business_code="bot_dormant",
        param_code="protected_owner_ids",
        env="prod",
        default=disabled_sentinel,
    ) is disabled_sentinel


def test_common_config_service_lists_with_pagination_and_delete_by_key():
    repo = FakeCommonConfigRepository()
    service = CommonConfigService(repo)

    service.create_config(
        business_code="biz_a",
        business_name="业务A",
        param_code="p1",
        param_name="参数1",
        param_value="v1",
        enable="1",
        env="prod",
    )
    service.create_config(
        business_code="biz_a",
        business_name="业务A",
        param_code="p2",
        param_name="名单参数",
        param_value="v2",
        enable="0",
        env="prod",
    )

    page = service.list_configs(
        env="prod",
        business_code="biz_a",
        keyword="名单",
        page_num=1,
        page_size=10,
    )
    assert page["total"] == 1
    assert page["items"][0]["param_code"] == "p2"

    assert service.delete_config(business_code="biz_a", param_code="p2", env="prod") is True
    assert service.delete_config(business_code="biz_a", param_code="missing", env="prod") is False


def test_common_config_service_delete_by_id_and_requires_identifier():
    repo = FakeCommonConfigRepository()
    service = CommonConfigService(repo)

    config_id = service.create_config(
        business_code="biz_delete",
        business_name="删除业务",
        param_code="p1",
        param_name="参数1",
        param_value={"enabled": True},
        enable="1",
        env="prod",
    )

    assert service.delete_config(config_id=config_id) is True
    assert (
        service.get_config(
            business_code="biz_delete",
            param_code="p1",
            env="prod",
        )
        is None
    )
    assert service.delete_config(config_id=config_id) is False

    with pytest.raises(ValueError, match="删除配置需要"):
        service.delete_config()
