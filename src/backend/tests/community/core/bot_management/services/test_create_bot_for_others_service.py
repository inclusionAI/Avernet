from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


TARGET_USER = "172168"
AGENT_CODE = "agent-172168"


def _freeze_restart_wait_clock(monkeypatch):
    from agentclaw.community.core.bot_management.services import (
        create_bot_for_others_service as service_module,
    )

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return cls(2026, 7, 16, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(service_module, "datetime", FrozenDateTime)
    return service_module, FrozenDateTime


def _bot(*, status="ACTIVE", ext=None, gmt_modified=None):
    return {
        "bot_id": "default",
        "owner_id": TARGET_USER,
        "entity_id": TARGET_USER,
        "entity_type": "staff",
        "bot_name": "Default Bot",
        "bot_desc": "default vault",
        "status": status,
        "active_engine": "openclaw",
        "template_type": None,
        "ext": ext or {},
        "gmt_modified": gmt_modified,
    }


def _service(*, existing_bot=None):
    from agentclaw.community.core.bot_management.services.create_bot_for_others_service import (
        CreateBotForOthersService,
    )

    repository = MagicMock()
    repository.get_by_id_and_owner.return_value = existing_bot

    bot_service = MagicMock()
    bot_service.check_create_bot_preflight.return_value = None
    bot_service.create_bot.return_value = {
        "bot_id": "default",
        "status": "PENDING",
    }
    bot_service.restart_bot.return_value = {
        "bot_id": "default",
        "status": "PENDING",
    }

    passport = MagicMock()
    passport.apply_first_agent_passport.return_value = {
        "token": "applied-token",
        "agent_code": AGENT_CODE,
    }
    passport.query_agent_passport.return_value = {"agent_code": AGENT_CODE}
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_token.return_value = "verified-token"

    relationship = MagicMock()
    relationship.query_relationships.return_value = []
    relationship.create_relationship.return_value = {"auth_id": 42}

    skill_set_service = MagicMock()
    skill_set_service.get_bot_mcp_codes.return_value = ["mcp.one", "hitl"]
    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value = skill_set_service

    service = CreateBotForOthersService(
        repository=repository,
        bot_service=bot_service,
        passport_plugin=passport,
        auth_relationship_plugin=relationship,
        skill_set_factory=skill_set_factory,
    )
    return (
        service,
        repository,
        bot_service,
        passport,
        relationship,
        skill_set_factory,
    )


def _execute(service, *, bot_type=None):
    return service.execute(
        target_user_id=TARGET_USER,
        target_nick_name="Alice",
        bot_type=bot_type,
        operator_user_id="admin-1",
        operator_name="Admin One",
        cookie="session-cookie",
    )


def test_new_default_bot_gets_verified_passport_and_owner_before_create():
    (
        service,
        _,
        bot_service,
        passport,
        relationship,
        skill_set_factory,
    ) = _service()
    events = []

    passport.apply_first_agent_passport.side_effect = lambda **_kwargs: (
        events.append("passport.apply")
        or {"token": "applied-token", "agent_code": AGENT_CODE}
    )
    passport.query_agent_passport.side_effect = lambda **_kwargs: (
        events.append("passport.query") or {"agent_code": AGENT_CODE}
    )
    passport.query_auth_status.side_effect = lambda **_kwargs: {
        "status": "ISSUED"
    }
    passport.query_token.side_effect = lambda **_kwargs: (
        events.append("passport.token") or "verified-token"
    )
    relationship.create_relationship.side_effect = lambda **_kwargs: (
        events.append("relationship.create") or {"auth_id": 42}
    )
    bot_service.create_bot.side_effect = lambda **_kwargs: (
        events.append("bot.create")
        or {"bot_id": "default", "status": "PENDING"}
    )
    bot_service.check_create_bot_preflight.side_effect = lambda **_kwargs: (
        events.append("bot.preflight") or None
    )

    result = _execute(service, bot_type="personal")

    assert result["action"] == "created"
    assert result["passport"] == {
        "status": "ISSUED",
        "agent_code": AGENT_CODE,
        "token_present": True,
        "source": "applied",
    }
    assert "token" not in result["passport"]
    assert events.index("bot.preflight") < events.index("passport.apply")
    assert events.index("passport.apply") < events.index("passport.token")
    assert events.index("passport.token") < events.index("relationship.create")
    assert events.index("relationship.create") < events.index("bot.create")

    apply_kwargs = passport.apply_first_agent_passport.call_args.kwargs
    assert apply_kwargs["bot_id"] == "default"
    assert apply_kwargs["owner_workno"] == TARGET_USER
    assert apply_kwargs["mcp_codes"] == ["mcp.one"]
    assert apply_kwargs["engine_type"] == "openclaw"
    assert apply_kwargs["access_mode"] == "RESTRICTED"
    assert apply_kwargs["workspace_path"] == "/home/admin/.openclaw"

    factory_kwargs = skill_set_factory.create.call_args.kwargs
    assert factory_kwargs == {
        "user_id": TARGET_USER,
        "entity_id": TARGET_USER,
        "bot_id": "default",
        "entity_type": "staff",
        "engine_type": "openclaw",
    }

    relationship.create_relationship.assert_called_once_with(
        work_no=TARGET_USER,
        agent_code=AGENT_CODE,
        description="Bot owner default authorization",
        operator_work_no="admin-1",
        operator_name="Admin One",
    )
    create_kwargs = bot_service.create_bot.call_args.kwargs
    assert create_kwargs["user_id"] == TARGET_USER
    assert create_kwargs["bot_id"] == "default"
    assert create_kwargs["bot_type"] == "personal"
    assert create_kwargs["cookie"] == "session-cookie"
    assert create_kwargs["ext"] == {"passport": {"agent_code": AGENT_CODE}}


def test_new_bot_preflight_failure_does_not_apply_passport():
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotLimitExceededError,
    )

    service, _, bot_service, passport, relationship, _ = _service()
    bot_service.check_create_bot_preflight.side_effect = BotLimitExceededError(
        "limit"
    )

    with pytest.raises(BotLimitExceededError):
        _execute(service)

    passport.apply_first_agent_passport.assert_not_called()
    relationship.create_relationship.assert_not_called()
    bot_service.create_bot.assert_not_called()


def test_new_bot_is_not_created_when_first_apply_returns_no_token():
    from agentclaw.community.core.bot_management.errors import (
        CreateBotForOthersError,
    )

    service, _, bot_service, passport, relationship, _ = _service()
    passport.apply_first_agent_passport.return_value = {
        "token": "",
        "agent_code": AGENT_CODE,
        "iframe_url": "https://authorization.invalid/iframe",
    }

    with pytest.raises(CreateBotForOthersError) as exc_info:
        _execute(service)

    assert exc_info.value.error_code == 5401
    assert "authorization.invalid" not in str(exc_info.value)
    passport.query_token.assert_not_called()
    relationship.create_relationship.assert_not_called()
    bot_service.create_bot.assert_not_called()


def test_new_bot_is_not_created_when_post_apply_token_query_is_empty():
    from agentclaw.community.core.bot_management.errors import (
        CreateBotForOthersError,
    )

    service, _, bot_service, passport, relationship, _ = _service()
    passport.query_token.return_value = None

    with pytest.raises(CreateBotForOthersError) as exc_info:
        _execute(service)

    assert exc_info.value.error_code == 5400
    relationship.create_relationship.assert_not_called()
    bot_service.create_bot.assert_not_called()


def test_new_bot_is_not_created_when_passport_apply_raises():
    from agentclaw.community.core.bot_management.errors import (
        CreateBotForOthersError,
    )

    service, _, bot_service, passport, relationship, _ = _service()
    passport.apply_first_agent_passport.side_effect = RuntimeError(
        "upstream unavailable"
    )

    with pytest.raises(CreateBotForOthersError) as exc_info:
        _execute(service)

    assert exc_info.value.error_code == 5400
    relationship.create_relationship.assert_not_called()
    bot_service.create_bot.assert_not_called()


def test_new_bot_is_not_created_when_apply_returns_no_agent_code():
    from agentclaw.community.core.bot_management.errors import (
        CreateBotForOthersError,
    )

    service, _, bot_service, passport, relationship, _ = _service()
    passport.apply_first_agent_passport.return_value = {"token": "applied-token"}

    with pytest.raises(CreateBotForOthersError) as exc_info:
        _execute(service)

    assert exc_info.value.error_code == 5400
    relationship.create_relationship.assert_not_called()
    bot_service.create_bot.assert_not_called()


def test_new_bot_rejects_post_apply_agent_code_mismatch():
    from agentclaw.community.core.bot_management.errors import (
        CreateBotForOthersError,
    )

    service, _, bot_service, passport, relationship, _ = _service()
    passport.query_agent_passport.return_value = {
        "agent_code": "different-agent"
    }

    with pytest.raises(CreateBotForOthersError) as exc_info:
        _execute(service)

    assert exc_info.value.error_code == 5400
    relationship.create_relationship.assert_not_called()
    bot_service.create_bot.assert_not_called()


def test_new_bot_uses_applied_agent_code_when_local_query_omits_it():
    service, _, bot_service, passport, _, _ = _service()
    passport.query_agent_passport.return_value = {"agent_code": None}

    result = _execute(service)

    assert result["passport"]["agent_code"] == AGENT_CODE
    assert bot_service.create_bot.call_args.kwargs["ext"] == {
        "passport": {"agent_code": AGENT_CODE}
    }


def test_new_bot_is_not_created_when_passport_verification_query_raises():
    from agentclaw.community.core.bot_management.errors import (
        CreateBotForOthersError,
    )

    service, _, bot_service, passport, relationship, _ = _service()
    passport.query_agent_passport.side_effect = RuntimeError(
        "query unavailable"
    )

    with pytest.raises(CreateBotForOthersError) as exc_info:
        _execute(service)

    assert exc_info.value.error_code == 5400
    relationship.create_relationship.assert_not_called()
    bot_service.create_bot.assert_not_called()


def test_new_bot_is_not_created_when_owner_relationship_creation_fails():
    from agentclaw.community.core.bot_management.errors import (
        CreateBotForOthersError,
    )

    service, _, bot_service, _, relationship, _ = _service()
    relationship.create_relationship.return_value = None

    with pytest.raises(CreateBotForOthersError) as exc_info:
        _execute(service)

    assert exc_info.value.error_code == 5402
    bot_service.create_bot.assert_not_called()


def test_new_bot_is_not_created_when_owner_relationship_query_raises():
    from agentclaw.community.core.bot_management.errors import (
        CreateBotForOthersError,
    )

    service, _, bot_service, _, relationship, _ = _service()
    relationship.query_relationships.side_effect = RuntimeError(
        "relationship unavailable"
    )

    with pytest.raises(CreateBotForOthersError) as exc_info:
        _execute(service)

    assert exc_info.value.error_code == 5402
    bot_service.create_bot.assert_not_called()


def test_active_bot_with_complete_identity_is_verified_without_restart():
    stored = _bot(
        ext={
            "avatar_url": "https://example.test/avatar.png",
            "passport": {"legacy": "kept", "agent_code": AGENT_CODE},
        }
    )
    service, repository, bot_service, passport, relationship, _ = _service(
        existing_bot=stored
    )
    relationship.query_relationships.return_value = [
        {
            "auth_id": 42,
            "work_no": TARGET_USER,
            "agent_code": AGENT_CODE,
        }
    ]

    result = _execute(service)

    assert result["action"] == "skipped"
    assert result["runtime"]["restart_required"] is False
    passport.apply_first_agent_passport.assert_not_called()
    relationship.create_relationship.assert_not_called()
    repository.update_by_owner.assert_not_called()
    bot_service.create_bot.assert_not_called()
    bot_service.restart_bot.assert_not_called()


def test_active_bot_with_missing_identity_is_repaired_but_not_restarted():
    stored = _bot(
        ext={
            "avatar_url": "https://example.test/avatar.png",
            "passport": {"legacy": "kept"},
        }
    )
    service, repository, bot_service, passport, _, _ = _service(
        existing_bot=stored
    )
    passport.query_agent_passport.side_effect = [None, {"agent_code": AGENT_CODE}]
    passport.query_auth_status.side_effect = [None, {"status": "ISSUED"}]
    passport.query_token.side_effect = [None, "verified-token"]
    repository.update_by_owner.return_value = _bot(
        ext={
            "avatar_url": "https://example.test/avatar.png",
            "passport": {"legacy": "kept", "agent_code": AGENT_CODE},
        }
    )

    result = _execute(service)

    assert result["action"] == "repaired"
    assert result["runtime"]["restart_required"] is True
    passport.apply_first_agent_passport.assert_called_once()
    update_kwargs = repository.update_by_owner.call_args.kwargs
    assert update_kwargs == {
        "bot_id": "default",
        "owner_id": TARGET_USER,
        "update_data": {
            "ext": {
                "avatar_url": "https://example.test/avatar.png",
                "passport": {"legacy": "kept", "agent_code": AGENT_CODE},
            }
        },
    }
    bot_service.restart_bot.assert_not_called()


def test_active_bot_rejects_agent_code_persistence_readback_mismatch():
    from agentclaw.community.core.bot_management.errors import (
        CreateBotForOthersError,
    )

    stored = _bot(ext={"passport": {"legacy": "kept"}})
    service, repository, bot_service, passport, _, _ = _service(
        existing_bot=stored
    )
    passport.query_agent_passport.side_effect = [None, {"agent_code": AGENT_CODE}]
    passport.query_auth_status.side_effect = [None, {"status": "ISSUED"}]
    passport.query_token.side_effect = [None, "verified-token"]
    repository.update_by_owner.return_value = _bot(
        ext={"passport": {"agent_code": "stale-agent"}}
    )

    with pytest.raises(CreateBotForOthersError) as exc_info:
        _execute(service)

    assert exc_info.value.error_code == 500
    bot_service.restart_bot.assert_not_called()


def test_failed_bot_is_repaired_before_restart_after_wait_period():
    stored = _bot(
        status="FAILED",
        ext={"passport": {"agent_code": AGENT_CODE}},
        gmt_modified=datetime.now(timezone.utc) - timedelta(minutes=31),
    )
    service, _, bot_service, passport, relationship, _ = _service(
        existing_bot=stored
    )
    events = []
    passport.query_token.side_effect = lambda **_kwargs: (
        events.append("passport.token") or "verified-token"
    )
    relationship.query_relationships.side_effect = lambda **_kwargs: (
        events.append("relationship.query")
        or [
            {
                "auth_id": 42,
                "work_no": TARGET_USER,
                "agent_code": AGENT_CODE,
            }
        ]
    )
    bot_service.restart_bot.side_effect = lambda **_kwargs: (
        events.append("bot.restart")
        or {"bot_id": "default", "status": "PENDING"}
    )

    result = _execute(service)

    assert result["action"] == "restarted"
    assert events.index("passport.token") < events.index("relationship.query")
    assert events.index("relationship.query") < events.index("bot.restart")
    bot_service.restart_bot.assert_called_once_with(
        bot_id="default",
        user_id=TARGET_USER,
        nick_name="Alice",
    )


def test_recent_failed_bot_is_repaired_but_still_observes_restart_wait():
    stored = _bot(
        status="FAILED",
        ext={"passport": {"legacy": "kept"}},
        gmt_modified=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    service, repository, bot_service, passport, _, _ = _service(
        existing_bot=stored
    )
    passport.query_agent_passport.side_effect = [None, {"agent_code": AGENT_CODE}]
    passport.query_auth_status.side_effect = [None, {"status": "ISSUED"}]
    passport.query_token.side_effect = [None, "verified-token"]
    repository.update_by_owner.return_value = _bot(
        status="FAILED",
        ext={"passport": {"legacy": "kept", "agent_code": AGENT_CODE}},
        gmt_modified=stored["gmt_modified"],
    )

    result = _execute(service)

    assert result["action"] == "skipped_wait"
    assert result["passport"]["source"] == "applied"
    assert 19 <= result["minutes_remaining"] <= 20
    bot_service.restart_bot.assert_not_called()


def test_restart_wait_treats_naive_datetime_as_utc(monkeypatch):
    service_module, frozen_datetime = _freeze_restart_wait_clock(monkeypatch)

    result = service_module.CreateBotForOthersService._restart_wait(
        frozen_datetime(2026, 7, 16, 11, 50)
    )

    assert result == (10, 20)


def test_restart_wait_treats_naive_iso_string_as_utc(monkeypatch):
    service_module, _ = _freeze_restart_wait_clock(monkeypatch)

    result = service_module.CreateBotForOthersService._restart_wait(
        "2026-07-16T11:50:00"
    )

    assert result == (10, 20)
