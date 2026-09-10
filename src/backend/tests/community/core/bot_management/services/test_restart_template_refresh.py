from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AicodingProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.provisioning import (
    BotProvisioningContext,
)


def _service(stored_config):
    service = MagicMock()
    service.get_template_config.return_value = stored_config
    return service


def _ctx(active_engine="claude_code"):
    return BotProvisioningContext(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_type="personal",
        active_engine=active_engine,
        template_type="architect",
    )


def _refresh(stored_config, latest, *, active_engine="claude_code"):
    template_service = _service(stored_config)
    AicodingProvisioningStrategy(active_engine).apply_restart_extra_configs(
        _ctx(active_engine),
        {"template_config": latest} if latest is not None else None,
        template_service=template_service,
    )
    return template_service


def test_restart_persists_matching_newer_template_snapshot():
    latest = {
        "template_key": "architect",
        "template_uid": "aicoding_bot_template",
        "template_version_id": 101,
        "template_version": "V2",
        "bot_template_config": {"id": 101},
    }

    service = _refresh(
        {
            "template_key": "architect",
            "template_uid": "aicoding_bot_template",
            "template_version_id": 100,
        },
        latest,
    )

    service.create_or_update_template.assert_called_once_with(
        bot_id="bot-1",
        template_config=latest,
        template_type="architect",
        active_engine="claude_code",
    )


def test_restart_accepts_equal_or_lower_version_owned_by_frontend():
    for incoming_version in (99, 100):
        service = _refresh(
            {
                "template_key": "architect",
                "template_uid": "aicoding_bot_template",
                "template_version_id": 100,
            },
            {
                "template_key": "architect",
                "template_version_id": incoming_version,
            },
        )

        service.create_or_update_template.assert_called_once()


def test_restart_ignores_missing_or_invalid_template_snapshot():
    for latest in (None, {}, [], "invalid"):
        service = _refresh(
            {
                "template_key": "architect",
                "template_uid": "aicoding_bot_template",
                "template_version_id": 100,
            },
            latest,
        )

        service.create_or_update_template.assert_not_called()


def test_restart_uses_active_engine_without_template_type_matching():
    service = _refresh(
        {
            "template_key": "architect",
            "template_uid": "aicoding_bot_template",
            "template_version_id": 100,
        },
        {
            "template_key": "another-template",
            "template_version_id": 101,
        },
    )

    service.create_or_update_template.assert_called_once()


def test_non_template_engine_does_not_refresh():
    service = _refresh(
        {"template_version_id": 100},
        {"template_version_id": 101},
        active_engine="openclaw",
    )

    service.get_template_config.assert_not_called()
    service.create_or_update_template.assert_not_called()


def test_restart_persists_resync_marker_for_confirmed_template_update():
    latest = {
        "template_key": "architect",
        "template_uid": "aicoding_bot_template",
        "template_version_id": 101,
    }
    template_service = _service({"template_version_id": 100})

    AicodingProvisioningStrategy("claude_code").apply_restart_extra_configs(
        _ctx("claude_code"),
        {"template_config": latest, "confirmed_template_update": True},
        template_service=template_service,
    )

    persisted = template_service.create_or_update_template.call_args.kwargs[
        "template_config"
    ]
    assert persisted is not latest
    assert persisted["template_version_id"] == 101
    assert persisted["_aicoding_restart"] == {
        "resync_authorization": True,
        "template_version_id": 101,
        "source": "restart_template_update",
    }
    assert "_aicoding_restart" not in latest


def test_restart_accepts_unversioned_domain_policy_snapshot():
    service = _refresh(None, {"envs": {"DOMAIN_POLICY": ""}}, active_engine="aicoding")
    service.create_or_update_template.assert_called_once_with(
        bot_id="bot-1",
        template_config={"envs": {"DOMAIN_POLICY": ""}},
        template_type="architect",
        active_engine="aicoding",
    )


def test_restart_can_confirm_unversioned_snapshot():
    service = _service(None)
    AicodingProvisioningStrategy("aicoding").apply_restart_extra_configs(
        _ctx("aicoding"),
        {
            "template_config": {"envs": {"DOMAIN_POLICY": None}},
            "confirmed_template_update": True,
        },
        template_service=service,
    )
    saved = service.create_or_update_template.call_args.kwargs["template_config"]
    assert saved["_aicoding_restart"]["resync_authorization"] is True
    assert "template_version_id" not in saved["_aicoding_restart"]


class _TemplateRepository:
    """In-memory record store; existence is independent from ext contents."""

    def __init__(self, record):
        self.record = deepcopy(record)
        self.inserts = 0
        self.updates = 0

    def exists_by_bot_id(self, bot_id):
        return self.record is not None

    def insert(self, data):
        assert self.record is None
        self.inserts += 1
        self.record = {"id": 1, **deepcopy(data)}
        return deepcopy(self.record)

    def update_by_bot_id(self, bot_id, data):
        assert self.record is not None
        self.updates += 1
        self.record.update(deepcopy(data))
        return deepcopy(self.record)


@pytest.mark.parametrize("engine", ["aicoding", "claude_code"])
@pytest.mark.parametrize("template_type", ["personalCoding", "applicationCoding"])
@pytest.mark.parametrize(
    "record", [None, {"id": 1}, {"id": 1, "ext": None}, {"id": 1, "ext": {}}]
)
def test_restart_strategy_reuses_existing_service_for_missing_or_empty_records(
    engine, template_type, record
):
    from agentclaw.community.core.bot_management.services.template_service import (
        TemplateService,
    )
    from agentclaw.community.core.bot_management.token_vault import (
        TokenVault,
        CIPHER_PREFIX,
    )

    repo = _TemplateRepository(record)
    vault = TokenVault(master_key="restart-test-key")
    service = TemplateService(repository=repo, vault=vault)
    ctx = BotProvisioningContext(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_type="personal",
        active_engine=engine,
        template_type=template_type,
    )
    candidate = {
        "template_key": template_type,
        "template_uid": "test-template",
        "template_version_id": 101,
        "token": "test-token",
        "envs": {"DOMAIN_POLICY": ""},
        "backend_repo": [],
    }
    original = deepcopy(candidate)
    strategy = AicodingProvisioningStrategy(engine)
    strategy.apply_restart_extra_configs(
        ctx, {"template_config": candidate}, template_service=service
    )
    saved = repo.record["ext"]
    assert saved["token"].startswith(CIPHER_PREFIX)
    assert vault.decrypt_or_passthrough(saved["token"]) == candidate["token"]
    assert saved["backend_repo"] == []
    assert saved["envs"] == {"DOMAIN_POLICY": ""}
    assert repo.inserts == int(record is None)
    assert repo.updates == int(record is not None)
    assert candidate == original

    # A later same-version policy refresh updates the same record, preserves
    # ciphertext, and does not repeat first-time creation.
    refreshed = {**saved, "envs": {"DOMAIN_POLICY": None}}
    strategy.apply_restart_extra_configs(
        ctx, {"template_config": refreshed}, template_service=service
    )
    assert repo.inserts == int(record is None)
    assert repo.updates == int(record is not None) + 1
    assert repo.record["ext"]["token"] == saved["token"]
    assert repo.record["ext"]["envs"]["DOMAIN_POLICY"] is None


@pytest.mark.parametrize("engine", ["openclaw", "teclaw", "moltis", "desktop", ""])
def test_restart_strategy_does_not_touch_template_service_for_other_engines(engine):
    service = _refresh(None, {"envs": {"DOMAIN_POLICY": ""}}, active_engine=engine)
    assert service.mock_calls == []


def test_restart_strategy_reports_empty_save_result():
    service = _service(None)
    service.create_or_update_template.return_value = None
    with pytest.raises(RuntimeError, match="Failed to persist"):
        AicodingProvisioningStrategy("aicoding").apply_restart_extra_configs(
            _ctx("aicoding"),
            {"template_config": {"envs": {"DOMAIN_POLICY": ""}}},
            template_service=service,
        )


def test_restart_strategy_propagates_save_failure_to_existing_hook():
    service = _service(None)
    error = RuntimeError("mock storage failure")
    service.create_or_update_template.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        AicodingProvisioningStrategy("aicoding").apply_restart_extra_configs(
            _ctx("aicoding"),
            {"template_config": {"envs": {"DOMAIN_POLICY": ""}}},
            template_service=service,
        )
    assert caught.value is error
