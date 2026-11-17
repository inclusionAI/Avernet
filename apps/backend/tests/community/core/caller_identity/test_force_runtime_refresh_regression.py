"""Independent service-to-store coverage of Caller runtime recovery."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.caller_identity.credential import CallerCredentialError, CallerToken
from agentclaw.community.core.caller_identity.expiry_runtime_updater import ExpiryAwareCallerRuntimeUpdater
from agentclaw.community.core.caller_identity.service import CallerIdentityService
from agentclaw.community.core.caller_identity.session_validator import CallerSessionValidator
from agentclaw.community.core.expert_chat.services.expert_chat_instance_service import ExpertChatInstanceService
from tests.community.repository.chat.test_expert_chat_instance_unified import repo as repo


class RecordingInstaller:
    """An old-signature installer must not receive the internal recovery flag."""

    def __init__(self):
        self.installed = []
        self.failure = None

    def update_caller_identity(
        self, *, bot_id, owner_user_id, caller_user_id, caller_token,
        stage, publish_id, entity_id=None, binding_id=None,
        is_test_exchange=False, session_key=None,
    ):
        assert (bot_id, owner_user_id, caller_user_id, binding_id) == ("b1", "o1", "u1", 7)
        if self.failure is not None:
            raise self.failure
        self.installed.append(caller_token)


class Issuer:
    def __init__(self, token):
        self.token = token
        self.calls = []

    def exchange(self, *, auth_context, iam_token, bot_id, owner_user_id, task_metadata):
        assert (auth_context.user_id, bot_id, owner_user_id) == ("u1", "b1", "o1")
        self.calls.append(task_metadata)
        return self.token


def _chain(instances, *, provider="arca", engine="openclaw"):
    binding = SimpleNamespace(
        apply_reason="caller_instance:b1", applied_by="u1", status="ACTIVE", device_provider=provider,
    )
    bindings = Mock(get_by_id=Mock(return_value=binding))
    bots = Mock(get_by_id_and_entity=Mock(return_value={"active_engine": engine}))
    installer = RecordingInstaller()
    updater = ExpiryAwareCallerRuntimeUpdater(
        instances=instances, delegate=installer, bindings=bindings, bots=bots,
    )
    service = CallerIdentityService(
        session_validator=CallerSessionValidator(binding_repository=bindings, runtime_bindings=Mock()),
        bot_repository=bots, collaborator_repository=Mock(), lock_repository=Mock(),
        mcp_provider=Mock(), repository=Mock(), mcp_sync_service=Mock(),
        bcs_caller_sync=Mock(),
    )
    return service, updater, installer, binding


def _seed(repo, expiry):
    repo.upsert_instance("u1", "b1", "o1", status="success", ext={"binding_id": 7})
    with repo.caller_token_update(
        user_id="u1", bot_id="b1", owner_id="o1", binding_id=7, expires_at=expiry,
    ):
        pass


def _exchange(service, updater, end, *, force=False, binding_id=7, subject="u1"):
    token = CallerToken("test-token", subject, end, "fingerprint")
    issuer = Issuer(token)
    kwargs = {"force_runtime_refresh": True} if force else {}
    service.exchange_caller_identity(
        iam_token="test-iam", caller_user_id="u1", bot_id="b1",
        owner_user_id="o1", token_provider=issuer, runtime_updater=updater,
        stage="online", publish_id=3, entity_id="o1", binding_id=binding_id,
        session_key="business-session", **kwargs,
    )
    assert len(issuer.calls) == 1
    return token


@pytest.mark.parametrize("force,hours", [(True, 1), (True, 48), (True, 72), (False, 1), (False, 48), (False, 72)])
def test_real_chain_installs_and_records_the_actual_selected_token(repo, caplog, force, hours):
    caplog.set_level("INFO")
    now = datetime.now(UTC)
    old, issued = now + timedelta(hours=48), now + timedelta(hours=hours)
    _seed(repo, old)
    service, updater, installer, _ = _chain(repo)

    token = _exchange(service, updater, issued, force=force)

    should_install = force or hours > 48
    assert installer.installed == ([token] if should_install else [])
    expected = issued if should_install else old
    assert repo.get_instance("u1", "b1", "o1")["caller_token_expires_at"] == expected.isoformat()
    if force:
        assert "caller_token_force_install_requested" in caplog.text
        assert "caller_token_force_install_succeeded" in caplog.text
    assert "test-token" not in caplog.text
    assert "test-iam" not in caplog.text


def test_real_chain_failure_keeps_old_expiry_and_masks_the_failure_payload(repo, caplog):
    caplog.set_level("INFO")
    old = datetime.now(UTC) + timedelta(hours=48)
    _seed(repo, old)
    service, updater, installer, _ = _chain(repo)
    installer.failure = RuntimeError("test-token test-iam private-failure")

    with pytest.raises(RuntimeError):
        _exchange(service, updater, old - timedelta(hours=24), force=True)

    assert installer.installed == []
    assert repo.get_instance("u1", "b1", "o1")["caller_token_expires_at"] == old.isoformat()
    assert "caller_token_force_install_failed" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "private-failure" not in caplog.text
    assert "test-token" not in caplog.text
    assert "test-iam" not in caplog.text


@pytest.mark.parametrize("binding_id,subject,error", [(8, "u1", ValueError), (7, "someone-else", CallerCredentialError)])
def test_force_does_not_bypass_target_or_subject_checks(repo, binding_id, subject, error):
    old = datetime.now(UTC) + timedelta(hours=48)
    _seed(repo, old)
    service, updater, installer, _ = _chain(repo)
    with pytest.raises(error):
        _exchange(service, updater, old - timedelta(hours=24), force=True, binding_id=binding_id, subject=subject)
    assert installer.installed == []
    assert repo.get_instance("u1", "b1", "o1")["caller_token_expires_at"] == old.isoformat()


@pytest.mark.parametrize("provider,engine", [("teclaw", "openclaw"), ("arca", "teclaw")])
def test_force_keeps_teclaw_installation_independent_of_expiry(provider, engine):
    instances = Mock()
    instances.caller_token_update.side_effect = AssertionError("Teclaw must not access expiry coordination")
    instances.get_instance.side_effect = AssertionError("Teclaw must not read expiry")
    service, updater, installer, _ = _chain(instances, provider=provider, engine=engine)
    issued = _exchange(service, updater, datetime.now(UTC) + timedelta(hours=1), force=True)
    assert installer.installed == [issued]
    instances.caller_token_update.assert_not_called()
    instances.get_instance.assert_not_called()


@pytest.mark.asyncio
async def test_startup_failure_isolated_without_logging_raw_credentials(caplog):
    caplog.set_level("INFO")
    service = ExpertChatInstanceService.__new__(ExpertChatInstanceService)
    service._caller_identity = Mock()
    service._caller_identity.exchange_caller_identity.side_effect = RuntimeError("test-startup-iam test-startup-token")
    service._token_provider = Mock()
    service._runtime_updater = Mock()

    await service._exchange_caller_identity(
        iam_token="test-startup-iam", user_id="u1", bot_id="b1", owner_id="o1",
        publish_id=3, binding_id=7, force_runtime_refresh=True,
    )

    assert service._caller_identity.exchange_caller_identity.call_args.kwargs["force_runtime_refresh"] is True
    assert "Caller identity exchange requested" in caplog.text
    assert "Caller identity exchange failed" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "test-startup-iam" not in caplog.text
    assert "test-startup-token" not in caplog.text
