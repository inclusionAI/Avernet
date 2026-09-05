from agentclaw.community.di.modules.infrastructure.community import task_runner_integration as module


def test_merchant_task_bot_bindings_malformed_json_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(module, "_user_config", lambda: {"merchant_task_bot_bindings": "not-json"})

    config = module.TaskRunnerIntegrationModule().merchant_task_bot_bindings()

    assert config.bot_id_by_role == {}


def test_merchant_task_bot_bindings_structured_json_is_decoded(monkeypatch):
    monkeypatch.setattr(module, "_user_config", lambda: {"merchant_task_bot_bindings": '{"store_owner_bot_id": "bot-1"}'})

    config = module.TaskRunnerIntegrationModule().merchant_task_bot_bindings()

    assert config.bot_id_by_role == {"store_owner_bot_id": "bot-1"}


def test_merchant_task_bot_bindings_dict_is_passed_through(monkeypatch):
    """``merchant_task_bot_bindings`` 为 dict 时直通,不经 JSON 解析。"""
    monkeypatch.setattr(
        module, "_user_config", lambda: {"merchant_task_bot_bindings": {"store_owner_bot_id": "bot-1"}}
    )

    config = module.TaskRunnerIntegrationModule().merchant_task_bot_bindings()

    assert config.bot_id_by_role == {"store_owner_bot_id": "bot-1"}
