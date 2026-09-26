from threading import Event, Lock, Thread
from unittest.mock import MagicMock

import pytest

from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipError
from agentclaw.community.plugin_api.passport import PassportError


def _bot(*, ext=None):
    return {
        "bot_id": "default",
        "owner_id": "172168",
        "entity_id": "172168",
        "entity_type": "staff",
        "bot_name": "Default Bot",
        "bot_desc": "default vault",
        "active_engine": "openclaw",
        "template_type": None,
        "ext": ext or {"avatar_url": "https://example.test/avatar.png"},
    }


def _service(*, repository=None, passport=None, relationship=None):
    from agentclaw.community.core.bot_management.services.default_bot_passport_repair_service import (
        DefaultBotPassportRepairService,
    )

    repository = repository or MagicMock()
    passport = passport or MagicMock()
    relationship = relationship or MagicMock()
    skill_set_service = MagicMock()
    skill_set_service.get_bot_mcp_codes_for_env.return_value = ["mcp.one", "hitl"]
    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value = skill_set_service
    return (
        DefaultBotPassportRepairService(
            repository=repository,
            passport_plugin=passport,
            auth_relationship_plugin=relationship,
            skill_set_factory=skill_set_factory,
        ),
        repository,
        passport,
        relationship,
    )


def _repair(service):
    return service.repair(
        target_user_id="172168",
        target_env="prod",
        operator_user_id="admin-1",
        operator_name="Admin One",
    )


def test_repair_missing_passport_uses_first_apply_and_verifies_control_plane():
    from agentclaw.community.core.bot_management.services.default_bot_passport_repair_service import (
        DefaultBotPassportRepairService,
    )

    repository = MagicMock()
    before = _bot(
        ext={
            "avatar_url": "https://example.test/avatar.png",
            "passport": {"legacy": "preserved"},
        }
    )
    after = _bot(
        ext={
            "avatar_url": "https://example.test/avatar.png",
            "passport": {"legacy": "preserved", "agent_code": "agent-172168"},
        }
    )
    repository.get_live_by_id_owner_and_env.side_effect = [[before], [after]]
    repository.update_ext_by_id_owner_and_env.return_value = after

    passport = MagicMock()
    passport.query_agent_passport.side_effect = [
        None,
        {
            "agent_code": "agent-172168",
            "credential_id": "credential-172168",
        },
    ]
    passport.query_auth_status.side_effect = [None, {"status": "ISSUED"}]
    passport.query_token.side_effect = [None, "verified-token"]
    passport.apply_first_agent_passport.return_value = {
        "token": "applied-token",
        "agent_code": "agent-172168",
    }

    relationship = MagicMock()
    relationship.query_relationships_for_env.side_effect = [
        [],
        [{"authId": 42, "workNo": "172168", "agentCode": "agent-172168"}],
    ]
    relationship.create_relationship_for_env.return_value = {"auth_id": 42}

    skill_set_service = MagicMock()
    skill_set_service.get_bot_mcp_codes_for_env.return_value = ["mcp.one", "hitl"]
    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value = skill_set_service

    service = DefaultBotPassportRepairService(
        repository=repository,
        passport_plugin=passport,
        auth_relationship_plugin=relationship,
        skill_set_factory=skill_set_factory,
    )

    result = service.repair(
        target_user_id="172168",
        target_env="prod",
        operator_user_id="admin-1",
        operator_name="Admin One",
    )

    assert result["action"] == "repaired"
    assert result["target_user_id"] == "172168"
    assert result["bot_id"] == "default"
    assert result["target_env"] == "prod"
    assert result["passport"] == {
        "status": "ISSUED",
        "agent_code": "agent-172168",
        "credential_id": "credential-172168",
        "token_present": True,
        "source": "applied",
    }
    assert result["owner_relationship"] == {
        "verified": True,
        "created": True,
        "auth_id": 42,
    }
    assert result["database"] == {"ext_agent_code_verified": True}
    assert result["runtime"] == {
        "restart_required": True,
        "restart_environment": "prod",
    }

    passport.apply_first_agent_passport.assert_called_once()
    apply_kwargs = passport.apply_first_agent_passport.call_args.kwargs
    assert apply_kwargs["bot_id"] == "default"
    assert apply_kwargs["owner_workno"] == "172168"
    assert apply_kwargs["mcp_codes"] == ["mcp.one"]
    assert apply_kwargs["target_env"] == "prod"
    passport.apply_agent_passport.assert_not_called()

    expected_query = {
        "bot_id": "default",
        "owner_workno": "172168",
        "target_env": "prod",
    }
    assert [call.kwargs for call in passport.query_agent_passport.call_args_list] == [
        expected_query,
        expected_query,
    ]
    assert [call.kwargs for call in passport.query_auth_status.call_args_list] == [
        expected_query,
        expected_query,
    ]
    assert [call.kwargs for call in passport.query_token.call_args_list] == [
        expected_query,
        expected_query,
    ]

    update_kwargs = repository.update_ext_by_id_owner_and_env.call_args.kwargs
    assert update_kwargs["env"] == "prod"
    assert update_kwargs["bot_id"] == "default"
    assert update_kwargs["owner_id"] == "172168"
    assert update_kwargs["ext"]["avatar_url"] == "https://example.test/avatar.png"
    assert update_kwargs["ext"]["passport"] == {
        "legacy": "preserved",
        "agent_code": "agent-172168",
    }

    relationship.create_relationship_for_env.assert_called_once_with(
        target_env="prod",
        work_no="172168",
        agent_code="agent-172168",
        description="Bot owner default authorization",
        operator_work_no="admin-1",
        operator_name="Admin One",
    )


def test_repair_rejects_invalid_target_env_before_reading_data():
    from agentclaw.community.core.bot_management.errors import (
        DefaultBotPassportRepairError,
    )

    service, repository, passport, relationship = _service()

    with pytest.raises(DefaultBotPassportRepairError) as exc_info:
        service.repair(
            target_user_id="172168",
            target_env="staging",
            operator_user_id="admin-1",
            operator_name="Admin One",
        )

    assert exc_info.value.error_code == 400
    repository.get_live_by_id_owner_and_env.assert_not_called()
    passport.apply_first_agent_passport.assert_not_called()
    relationship.create_relationship_for_env.assert_not_called()


@pytest.mark.parametrize(
    ("matches", "expected_error_code"),
    [([], 404), ([_bot(), _bot()], 409)],
)
def test_repair_requires_exactly_one_live_default_bot(matches, expected_error_code):
    from agentclaw.community.core.bot_management.errors import (
        DefaultBotPassportRepairError,
    )

    repository = MagicMock()
    repository.get_live_by_id_owner_and_env.return_value = matches
    service, _, passport, relationship = _service(repository=repository)

    with pytest.raises(DefaultBotPassportRepairError) as exc_info:
        _repair(service)

    assert exc_info.value.error_code == expected_error_code
    passport.query_agent_passport.assert_not_called()
    passport.apply_first_agent_passport.assert_not_called()
    relationship.create_relationship_for_env.assert_not_called()


def test_repair_rejects_authorization_page_without_token():
    from agentclaw.community.core.bot_management.errors import (
        DefaultBotPassportRepairError,
    )

    repository = MagicMock()
    repository.get_live_by_id_owner_and_env.return_value = [_bot()]
    passport = MagicMock()
    passport.query_agent_passport.return_value = None
    passport.query_auth_status.return_value = None
    passport.query_token.return_value = None
    passport.apply_first_agent_passport.return_value = {
        "token": "",
        "iframe_url": "https://authorization.invalid/iframe",
    }
    service, _, _, relationship = _service(repository=repository, passport=passport)

    with pytest.raises(DefaultBotPassportRepairError) as exc_info:
        _repair(service)

    assert exc_info.value.error_code == 5401
    assert "authorization.invalid" not in str(exc_info.value)
    repository.update_ext_by_id_owner_and_env.assert_not_called()
    relationship.create_relationship_for_env.assert_not_called()
    passport.apply_agent_passport.assert_not_called()


def test_repair_reuses_complete_passport_and_existing_relationship():
    repository = MagicMock()
    stored = _bot(ext={"passport": {"agent_code": "agent-172168"}})
    repository.get_live_by_id_owner_and_env.side_effect = [[stored], [stored]]
    repository.update_ext_by_id_owner_and_env.return_value = stored
    passport = MagicMock()
    passport.query_agent_passport.return_value = {
        "agent_code": "agent-172168",
        "credential_id": "credential-172168",
    }
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_token.return_value = "verified-token"
    relationship = MagicMock()
    relationship.query_relationships_for_env.return_value = [
        {"auth_id": 42, "work_no": "172168", "agent_code": "agent-172168"}
    ]
    service, _, _, _ = _service(
        repository=repository, passport=passport, relationship=relationship
    )

    result = _repair(service)

    assert result["action"] == "verified"
    assert result["passport"]["source"] == "existing"
    assert result["owner_relationship"]["created"] is False
    passport.apply_first_agent_passport.assert_not_called()
    passport.apply_agent_passport.assert_not_called()
    relationship.create_relationship_for_env.assert_not_called()
    repository.update_ext_by_id_owner_and_env.assert_not_called()


def test_repair_rejects_post_apply_agent_code_mismatch():
    from agentclaw.community.core.bot_management.errors import (
        DefaultBotPassportRepairError,
    )

    repository = MagicMock()
    repository.get_live_by_id_owner_and_env.return_value = [_bot()]
    passport = MagicMock()
    passport.query_agent_passport.side_effect = [
        None,
        {"agent_code": "different-agent", "credential_id": "credential-172168"},
    ]
    passport.query_auth_status.side_effect = [None, {"status": "ISSUED"}]
    passport.query_token.side_effect = [None, "verified-token"]
    passport.apply_first_agent_passport.return_value = {
        "token": "applied-token",
        "agent_code": "agent-172168",
    }
    service, _, _, relationship = _service(repository=repository, passport=passport)

    with pytest.raises(DefaultBotPassportRepairError) as exc_info:
        _repair(service)

    assert exc_info.value.error_code == 5400
    repository.update_ext_by_id_owner_and_env.assert_not_called()
    relationship.create_relationship_for_env.assert_not_called()


def test_repair_rejects_database_readback_mismatch():
    from agentclaw.community.core.bot_management.errors import (
        DefaultBotPassportRepairError,
    )

    repository = MagicMock()
    repository.get_live_by_id_owner_and_env.side_effect = [
        [_bot()],
        [_bot(ext={"passport": {"agent_code": "stale-agent"}})],
    ]
    passport = MagicMock()
    passport.query_agent_passport.return_value = {
        "agent_code": "agent-172168",
        "credential_id": "credential-172168",
    }
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_token.return_value = "verified-token"
    service, _, _, relationship = _service(repository=repository, passport=passport)

    with pytest.raises(DefaultBotPassportRepairError) as exc_info:
        _repair(service)

    assert exc_info.value.error_code == 500
    relationship.create_relationship_for_env.assert_not_called()


def test_repair_requires_owner_relationship_requery_to_match():
    from agentclaw.community.core.bot_management.errors import (
        DefaultBotPassportRepairError,
    )

    repository = MagicMock()
    stored = _bot(ext={"passport": {"agent_code": "agent-172168"}})
    repository.get_live_by_id_owner_and_env.side_effect = [[stored], [stored]]
    passport = MagicMock()
    passport.query_agent_passport.return_value = {
        "agent_code": "agent-172168",
        "credential_id": "credential-172168",
    }
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_token.return_value = "verified-token"
    relationship = MagicMock()
    relationship.query_relationships_for_env.side_effect = [[], []]
    relationship.create_relationship_for_env.return_value = {"auth_id": 42}
    service, _, _, _ = _service(
        repository=repository, passport=passport, relationship=relationship
    )

    with pytest.raises(DefaultBotPassportRepairError) as exc_info:
        _repair(service)

    assert exc_info.value.error_code == 5402


def test_repair_maps_passport_failures_to_identity_error():
    from agentclaw.community.core.bot_management.errors import (
        DefaultBotPassportRepairError,
    )

    repository = MagicMock()
    repository.get_live_by_id_owner_and_env.return_value = [_bot()]
    passport = MagicMock()
    passport.query_agent_passport.side_effect = PassportError("upstream unavailable")
    service, _, _, relationship = _service(repository=repository, passport=passport)

    with pytest.raises(DefaultBotPassportRepairError) as exc_info:
        _repair(service)

    assert exc_info.value.error_code == 5400
    repository.update_ext_by_id_owner_and_env.assert_not_called()
    relationship.create_relationship_for_env.assert_not_called()


def test_repair_maps_database_write_failure_to_persistence_error():
    from agentclaw.community.core.bot_management.errors import (
        DefaultBotPassportRepairError,
    )

    repository = MagicMock()
    repository.get_live_by_id_owner_and_env.return_value = [_bot()]
    repository.update_ext_by_id_owner_and_env.side_effect = RuntimeError("write failed")
    passport = MagicMock()
    passport.query_agent_passport.return_value = {
        "agent_code": "agent-172168",
        "credential_id": "credential-172168",
    }
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_token.return_value = "verified-token"
    service, _, _, relationship = _service(repository=repository, passport=passport)

    with pytest.raises(DefaultBotPassportRepairError) as exc_info:
        _repair(service)

    assert exc_info.value.error_code == 500
    relationship.query_relationships_for_env.assert_not_called()


def test_repair_maps_relationship_failure_to_authorization_error():
    from agentclaw.community.core.bot_management.errors import (
        DefaultBotPassportRepairError,
    )

    repository = MagicMock()
    stored = _bot(ext={"passport": {"agent_code": "agent-172168"}})
    repository.get_live_by_id_owner_and_env.side_effect = [[stored], [stored]]
    passport = MagicMock()
    passport.query_agent_passport.return_value = {
        "agent_code": "agent-172168",
        "credential_id": "credential-172168",
    }
    passport.query_auth_status.return_value = {"status": "ISSUED"}
    passport.query_token.return_value = "verified-token"
    relationship = MagicMock()
    relationship.query_relationships_for_env.side_effect = AuthRelationshipError(
        "aceagent unavailable"
    )
    service, _, _, _ = _service(
        repository=repository, passport=passport, relationship=relationship
    )

    with pytest.raises(DefaultBotPassportRepairError) as exc_info:
        _repair(service)

    assert exc_info.value.error_code == 5402


def test_repair_serializes_same_target_to_avoid_duplicate_first_apply():
    class Repository:
        def __init__(self):
            self._lock = Lock()
            self._bot = _bot()

        def get_live_by_id_owner_and_env(self, **_kwargs):
            with self._lock:
                return [dict(self._bot)]

        def update_ext_by_id_owner_and_env(self, *, ext, **_kwargs):
            with self._lock:
                self._bot = {**self._bot, "ext": ext}

    class Passport:
        def __init__(self):
            self.complete = False
            self.apply_count = 0
            self.apply_started = Event()
            self.release_apply = Event()

        def query_agent_passport(self, **_kwargs):
            if not self.complete:
                return None
            return {
                "agent_code": "agent-172168",
                "credential_id": "credential-172168",
            }

        def query_auth_status(self, **_kwargs):
            return {"status": "ISSUED"} if self.complete else None

        def query_token(self, **_kwargs):
            return "verified-token" if self.complete else None

        def apply_first_agent_passport(self, **_kwargs):
            self.apply_count += 1
            self.apply_started.set()
            assert self.release_apply.wait(timeout=2)
            self.complete = True
            return {"token": "applied-token", "agent_code": "agent-172168"}

    repository = Repository()
    passport = Passport()
    relationship = MagicMock()
    relationship.query_relationships_for_env.return_value = [
        {"auth_id": 42, "work_no": "172168", "agent_code": "agent-172168"}
    ]
    service, _, _, _ = _service(
        repository=repository, passport=passport, relationship=relationship
    )
    results = []
    failures = []

    def run_repair():
        try:
            results.append(_repair(service))
        except Exception as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    first = Thread(target=run_repair)
    second = Thread(target=run_repair)
    first.start()
    assert passport.apply_started.wait(timeout=2)
    second.start()
    second.join(timeout=0.1)

    assert second.is_alive()
    assert passport.apply_count == 1

    passport.release_apply.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not failures
    assert len(results) == 2
    assert passport.apply_count == 1
    assert service._target_locks == {}
