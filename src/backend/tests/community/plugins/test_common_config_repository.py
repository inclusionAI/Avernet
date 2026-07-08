from __future__ import annotations

from agentclaw.community.core.common_config.repository import CommonConfigRepository


def test_common_config_repository_crud_and_upsert(test_injector):
    from agentclaw.community.plugin_api.database import DatabasePlugin

    db = test_injector.get(DatabasePlugin)
    import asyncio

    asyncio.run(db.bootstrap())
    repo = CommonConfigRepository(db)

    config_id = repo.create_config(
        business_code="engine_mount",
        business_name="引擎目录挂载",
        param_code="user_whitelist",
        param_name="用户白名单",
        param_value='["10001"]',
        enable="1",
        ext_info='{"scene":"engine_dir_mount"}',
        env="pre",
    )

    row = repo.get_by_biz_param(
        business_code="engine_mount", param_code="user_whitelist", env="pre"
    )
    assert row is not None
    assert row.id == config_id
    assert row.param_value == '["10001"]'

    assert repo.update_config(config_id=config_id, updates={"param_value": '["10002"]'}) is True
    assert repo.get_by_id(config_id=config_id).param_value == '["10002"]'

    upsert_id = repo.upsert_config(
        business_code="engine_mount",
        business_name="引擎目录挂载",
        param_code="user_whitelist",
        param_name="用户白名单",
        param_value='["10003"]',
        enable="0",
        ext_info=None,
        env="pre",
    )
    assert upsert_id == config_id
    assert repo.get_by_id(config_id=config_id).enable == "0"

    page = repo.list_configs(env="pre", business_code="engine_mount", enable="0", keyword="白名单")
    assert [r.id for r in page] == [config_id]
    assert repo.count_configs(env="pre", business_code="engine_mount", enable="0") == 1

    assert repo.delete_by_biz_param(
        business_code="engine_mount", param_code="user_whitelist", env="pre"
    ) is True
    assert repo.get_by_id(config_id=config_id) is None


def test_common_config_repository_empty_update_and_delete_miss(test_injector):
    from agentclaw.community.plugin_api.database import DatabasePlugin

    db = test_injector.get(DatabasePlugin)
    import asyncio

    asyncio.run(db.bootstrap())
    repo = CommonConfigRepository(db)

    assert repo.update_config(config_id=404, updates={"id": 1, "env": "prod"}) is False
    assert repo.update_config(config_id=404, updates={"param_value": "missing"}) is False
    assert repo.delete_config(config_id=404) is False
    assert repo.delete_by_biz_param(
        business_code="missing", param_code="missing", env="pre"
    ) is False

    config_id = repo.create_config(
        business_code="delete_case",
        business_name=None,
        param_code="target",
        param_name="目标配置",
        param_value=None,
        enable="1",
        ext_info=None,
        env="pre",
    )
    assert repo.delete_config(config_id=config_id) is True
    assert repo.get_by_id(config_id=config_id) is None


def test_common_config_repository_to_record_none():
    assert CommonConfigRepository._to_record(None) is None


def test_common_config_repository_upsert_uses_mysql_lastrowid_path():
    class FakeExecuteResult:
        lastrowid = 12345

    class FakeDialect:
        name = "mysql"

    class FakeBind:
        dialect = FakeDialect()

    class FakeSession:
        def __init__(self):
            self.executed_stmt = None

        def get_bind(self):
            return FakeBind()

        def execute(self, stmt):
            self.executed_stmt = stmt
            return FakeExecuteResult()

    class FakeSessionContext:
        def __init__(self, session):
            self.session = session

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeDatabase:
        def __init__(self):
            self.session = FakeSession()

        def orm_session(self):
            return FakeSessionContext(self.session)

    fake_db = FakeDatabase()
    repo = CommonConfigRepository(fake_db)

    config_id = repo.upsert_config(
        business_code="engine_mount",
        business_name="引擎目录挂载",
        param_code="user_whitelist",
        param_name="用户白名单",
        param_value='["10003"]',
        enable="0",
        ext_info=None,
        env="pre",
    )

    assert config_id == 12345
    assert fake_db.session.executed_stmt is not None
