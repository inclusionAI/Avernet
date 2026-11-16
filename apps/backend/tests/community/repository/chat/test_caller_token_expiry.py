"""Installed Caller expiry and interactive/ASC coordination with a real store."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.community.repository.chat.test_expert_chat_instance_unified import repo as repo


def updater_bindings():
    return Mock(get_by_id=Mock(return_value=SimpleNamespace(
        apply_reason="caller_instance:b1", applied_by="u1", status="ACTIVE", device_provider="arca",
    )))


def seed(repo):
    return repo.upsert_instance(
        "u1", "b1", "o1", status="success",
        ext={"bot_uuid": "uuid-1", "binding_id": 7, "baas_publish_id": 3},
    )


def scope(repo, expiry, binding_id=7):
    return repo.caller_token_update(
        user_id="u1", bot_id="b1", owner_id="o1",
        binding_id=binding_id, expires_at=expiry,
    )


def expiry(repo):
    return repo.get_instance("u1", "b1", "o1")["caller_token_expires_at"]


def test_expiry_written_only_after_success(repo):
    seed(repo)
    end = datetime.now(UTC) + timedelta(days=2)
    with pytest.raises(RuntimeError):
        with scope(repo, end) as install:
            assert install is True
            raise RuntimeError("outbound failed")
    assert expiry(repo) is None
    with scope(repo, end) as install:
        assert install is True
    assert expiry(repo) == end.isoformat()


@pytest.mark.parametrize("hours", [1, 48, 72])
def test_runtime_updater_preserves_longer_token(repo, hours, caplog):
    from agentclaw.community.core.caller_identity.credential import CallerToken
    from agentclaw.community.core.caller_identity.expiry_runtime_updater import ExpiryAwareCallerRuntimeUpdater

    caplog.set_level("INFO")
    seed(repo)
    now = datetime.now(UTC)
    with scope(repo, now + timedelta(hours=48)):
        pass
    delegate = Mock()
    updater = ExpiryAwareCallerRuntimeUpdater(bots=Mock(get_by_id_and_entity=Mock(return_value={"active_engine": "openclaw"}), get_unique_by_id=Mock(return_value={"active_engine": "openclaw"})), instances=repo, delegate=delegate, bindings=updater_bindings())
    issued_expiry = now + timedelta(hours=hours)
    token = CallerToken("test-token", "u1", issued_expiry, "fingerprint")
    updater.update_caller_identity(
        bot_id="b1", owner_user_id="o1", caller_user_id="u1", caller_token=token,
        stage="online", publish_id=None, binding_id=7,
    )
    assert delegate.update_caller_identity.call_count == int(hours > 48)
    assert expiry(repo) == (now + timedelta(hours=max(hours, 48))).isoformat()
    assert "test-token" not in caplog.text
    event = "caller_token_expiry_preserved" if hours <= 48 else "caller_token_expiry_recorded"
    assert event in caplog.text


@pytest.mark.parametrize("change", ["binding_id", "bot_uuid", "baas_publish_id", "release", "init"])
def test_replacement_invalidates_expiry(repo, change):
    row = seed(repo)
    with scope(repo, datetime.now(UTC) + timedelta(days=2)):
        pass
    if change in {"release", "init"}:
        repo.update_instance("u1", "b1", "o1", status=change)
    else:
        ext = dict(row["ext"])
        ext[change] = 99 if change != "bot_uuid" else "uuid-2"
        repo.update_instance("u1", "b1", "o1", ext=ext)
    assert expiry(repo) is None


def test_progress_and_same_binding_upsert_preserve_expiry(repo):
    row = seed(repo)
    end = datetime.now(UTC) + timedelta(days=2)
    with scope(repo, end):
        pass
    ext = dict(row["ext"], baas_publish={"status": "SUCCESS"})
    repo.update_instance("u1", "b1", "o1", ext=ext, status="success")
    repo.upsert_instance("u1", "b1", "o1", ext=ext, status="success")
    assert expiry(repo) == end.isoformat()
    repo.upsert_instance("u1", "b1", "o1", ext=dict(ext, binding_id=8), status="success")
    assert expiry(repo) is None


def test_missing_stale_or_released_instance_rejected(repo):
    end = datetime.now(UTC) + timedelta(days=2)
    with pytest.raises(ValueError):
        with scope(repo, end):
            pytest.fail("missing instance must not install")
    seed(repo)
    with pytest.raises(ValueError):
        with scope(repo, end, binding_id=8):
            pytest.fail("wrong binding must not install")
    repo.update_instance("u1", "b1", "o1", status="release")
    with pytest.raises(ValueError):
        with scope(repo, end):
            pytest.fail("released instance must not install")


def test_parallel_installs_keep_runtime_and_ledger_consistent(repo):
    seed(repo)
    long_end = datetime.now(UTC) + timedelta(days=2)
    short_end = long_end - timedelta(days=1)
    entered, release, competing = Event(), Event(), Event()
    installed = []

    def refresh(end, hold=False):
        if not hold:
            competing.set()
        with scope(repo, end) as install:
            if install:
                if hold:
                    entered.set()
                    assert release.wait(5)
                installed.append(end)

    with ThreadPoolExecutor(max_workers=2) as pool:
        longer = pool.submit(refresh, long_end, True)
        assert entered.wait(5)
        shorter = pool.submit(refresh, short_end)
        assert competing.wait(5)
        release.set()
        longer.result(timeout=5)
        shorter.result(timeout=5)
    assert installed == [long_end]
    assert expiry(repo) == long_end.isoformat()


def test_updater_install_failure_does_not_advance_expiry(repo):
    from agentclaw.community.core.caller_identity.credential import CallerToken
    from agentclaw.community.core.caller_identity.expiry_runtime_updater import ExpiryAwareCallerRuntimeUpdater

    seed(repo)
    old = datetime.now(UTC) + timedelta(hours=1)
    with scope(repo, old):
        pass
    delegate = Mock()
    delegate.update_caller_identity.side_effect = RuntimeError("private-failure")
    updater = ExpiryAwareCallerRuntimeUpdater(bots=Mock(get_by_id_and_entity=Mock(return_value={"active_engine": "openclaw"}), get_unique_by_id=Mock(return_value={"active_engine": "openclaw"})), instances=repo, delegate=delegate, bindings=updater_bindings())
    issued_expiry = old + timedelta(days=2)
    with pytest.raises(RuntimeError):
        updater.update_caller_identity(
            bot_id="b1", owner_user_id="o1", caller_user_id="u1",
            caller_token=CallerToken("test-token", "u1", issued_expiry, "fp"),
            stage="online", publish_id=None, binding_id=7,
        )
    assert expiry(repo) == old.isoformat()


@pytest.mark.parametrize("test_exchange,binding_id", [(False, 8), (False, None), (True, 7)])
def test_service_or_test_targets_keep_existing_update_behavior(repo, test_exchange, binding_id):
    from agentclaw.community.core.caller_identity.credential import CallerToken
    from agentclaw.community.core.caller_identity.expiry_runtime_updater import ExpiryAwareCallerRuntimeUpdater

    seed(repo)
    delegate = Mock()
    bindings = updater_bindings()
    bindings.get_by_id.return_value.apply_reason = "service"
    updater = ExpiryAwareCallerRuntimeUpdater(bots=Mock(get_by_id_and_entity=Mock(return_value={"active_engine": "openclaw"}), get_unique_by_id=Mock(return_value={"active_engine": "openclaw"})), instances=repo, delegate=delegate, bindings=bindings)
    issued_expiry = datetime.now(UTC) + timedelta(hours=1)
    updater.update_caller_identity(
        bot_id="b1", owner_user_id="o1", caller_user_id="u1",
        caller_token=CallerToken("test-token", "u1", issued_expiry, "fp"),
        stage="online", publish_id=None, binding_id=binding_id,
        is_test_exchange=test_exchange, session_key="business-session",
    )
    delegate.update_caller_identity.assert_called_once()
    assert delegate.update_caller_identity.call_args.kwargs["session_key"] == "business-session"
    assert expiry(repo) is None


def test_other_environment_does_not_reuse_expiry(repo, monkeypatch):
    from agentclaw.community.core.repository.implementations.chat import expert_chat_instance as module

    seed(repo)
    end = datetime.now(UTC) + timedelta(days=2)
    with scope(repo, end):
        pass
    monkeypatch.setattr(module, "get_current_env", lambda: "other")
    with pytest.raises(ValueError):
        with scope(repo, end):
            pytest.fail("another environment must not reuse this instance")


@pytest.mark.parametrize("expiry_value", [datetime(2000, 1, 1, tzinfo=UTC), datetime(2099, 1, 1)])
def test_invalid_expiry_cannot_install(repo, expiry_value):
    seed(repo)
    with pytest.raises(ValueError):
        with scope(repo, expiry_value):
            pytest.fail("invalid expiry must be rejected before outbound")


def test_upsert_without_ext_clears_old_expiry(repo):
    seed(repo)
    with scope(repo, datetime.now(UTC) + timedelta(days=2)):
        pass
    repo.upsert_instance("u1", "b1", "o1", status="success")
    assert expiry(repo) is None


def test_mysql_updates_evaluate_expiry_before_replacing_target(repo):
    from sqlalchemy.dialects import mysql
    from unittest.mock import MagicMock, patch

    session = MagicMock()
    session.__enter__.return_value = session
    session.query.return_value.filter.return_value.limit.return_value.all.return_value = []
    session.get_bind.return_value.dialect.name = "mysql"
    session.execute.return_value.lastrowid = 1
    session.execute.return_value.rowcount = 1
    with patch.object(repo._db, "orm_session", return_value=session):
        repo.update_instance("u1", "b1", "o1", ext={"binding_id": 8}, status="success")
        statement = session.execute.call_args.args[0]
        assignments = str(statement.compile(dialect=mysql.dialect())).split(" SET ")[1]
        assert assignments.split("=", 1)[0].strip() == "caller_token_expires_at"
        assert "JSON_EXTRACT" in assignments.upper()
        repo.upsert_instance("u1", "b1", "o1", ext={"binding_id": 8}, status="success")
        statement = session.execute.call_args.args[0]
        assignments = str(statement.compile(dialect=mysql.dialect())).split("ON DUPLICATE KEY UPDATE ")[1]
        assert assignments.split("=", 1)[0].strip() == "caller_token_expires_at"


@pytest.mark.asyncio
async def test_iam_success_keeps_original_response_when_longer_token_installed(repo):
    from agentclaw.community.core.caller_identity.credential import CallerToken
    from agentclaw.community.core.caller_identity.contracts import CallerIdentityStage
    from agentclaw.community.core.caller_identity.expiry_runtime_updater import ExpiryAwareCallerRuntimeUpdater
    from agentclaw.community.core.runtime_binding.errors import RuntimeBindingResolutionError
    from agentclaw.community.core.runtime_binding.models import ResolvedRuntimeBinding, RuntimeBindingSource
    from tests.community.core.caller_identity.test_iam_token_service import _service, _request, _context

    seed(repo)
    end = datetime.now(UTC) + timedelta(days=2)
    with scope(repo, end):
        pass
    delegate = Mock()
    updater = ExpiryAwareCallerRuntimeUpdater(bots=Mock(get_by_id_and_entity=Mock(return_value={"active_engine": "openclaw"}), get_unique_by_id=Mock(return_value={"active_engine": "openclaw"})), instances=repo, delegate=delegate, bindings=updater_bindings())
    service, identity, auth, *_ = _service(
        context=_context(owner_id="o1"),
        service_target=RuntimeBindingResolutionError("no shared target"),
        caller_target=ResolvedRuntimeBinding(7, RuntimeBindingSource.CALLER_INSTANCE),
        lock_holder="u1",
    )
    auth.resolve_user_from_request.return_value = SimpleNamespace(staffId="u1")
    identity.exchange_caller_token.return_value = CallerToken("short-secret", "u1", end - timedelta(days=1), "fp")

    def exchange(**kwargs):
        updater.update_caller_identity(**{key: kwargs[key] for key in (
            "bot_id", "owner_user_id", "caller_user_id", "caller_token", "stage", "publish_id", "binding_id",
        )})

    identity.exchange_caller_identity.side_effect = exchange
    result = await service.get_iam_token(
        iam_token="original-iam", auth_request=_request(), bot_id="b1", stage=CallerIdentityStage.ONLINE,
        publish_id=None, entity_id=None, is_test_exchange=False,
    )
    assert result.error is None
    assert result.iam_token == "original-iam"
    delegate.update_caller_identity.assert_not_called()
    assert expiry(repo) == end.isoformat()


def test_initial_iam_install_records_expiry(repo):
    from agentclaw.community.core.caller_identity.credential import CallerToken
    from agentclaw.community.core.caller_identity.expiry_runtime_updater import ExpiryAwareCallerRuntimeUpdater

    seed(repo)
    delegate = Mock()
    updater = ExpiryAwareCallerRuntimeUpdater(bots=Mock(get_by_id_and_entity=Mock(return_value={"active_engine": "openclaw"}), get_unique_by_id=Mock(return_value={"active_engine": "openclaw"})), instances=repo, delegate=delegate, bindings=updater_bindings())
    end = datetime.now(UTC) + timedelta(hours=1)
    updater.update_caller_identity(
        bot_id="b1", owner_user_id="o1", caller_user_id="u1", caller_token=CallerToken("secret", "u1", end, "fp"),
        stage="online", publish_id=None, binding_id=7,
    )
    delegate.update_caller_identity.assert_called_once()
    assert expiry(repo) == end.isoformat()


@pytest.mark.parametrize("change", ["subject", "binding_caller", "binding_status", "stale_ledger"])
def test_preserving_expiry_cannot_bypass_target_checks(repo, change):
    from agentclaw.community.core.caller_identity.credential import CallerToken, CallerCredentialError
    from agentclaw.community.core.caller_identity.expiry_runtime_updater import ExpiryAwareCallerRuntimeUpdater

    seed(repo)
    end = datetime.now(UTC) + timedelta(days=2)
    with scope(repo, end):
        pass
    bindings = updater_bindings()
    if change == "binding_caller":
        bindings.get_by_id.return_value.applied_by = "other"
    elif change == "binding_status":
        bindings.get_by_id.return_value.status = "RELEASED"
    elif change == "stale_ledger":
        repo.update_instance("u1", "b1", "o1", ext={"binding_id": 8})
    delegate = Mock()
    updater = ExpiryAwareCallerRuntimeUpdater(bots=Mock(get_by_id_and_entity=Mock(return_value={"active_engine": "openclaw"}), get_unique_by_id=Mock(return_value={"active_engine": "openclaw"})), instances=repo, delegate=delegate, bindings=bindings)
    issued_subject = "other" if change == "subject" else "u1"
    issued_expiry = end - timedelta(days=1)
    with pytest.raises((ValueError, CallerCredentialError)):
        updater.update_caller_identity(
            bot_id="b1", owner_user_id="o1", caller_user_id="u1",
            caller_token=CallerToken("test-token", issued_subject, issued_expiry, "fp"),
            stage="online", publish_id=None, binding_id=7,
        )
    delegate.update_caller_identity.assert_not_called()


@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
@pytest.mark.parametrize("provider,engine", [("teclaw", "openclaw"), ("baas", "teclaw")])
def test_teclaw_stages_keep_legacy_install_without_expiry_ledger(repo, stage, provider, engine, caplog):
    from agentclaw.community.core.caller_identity.credential import CallerToken
    from agentclaw.community.core.caller_identity.expiry_runtime_updater import ExpiryAwareCallerRuntimeUpdater

    seed(repo)
    old = datetime.now(UTC) + timedelta(days=2)
    with scope(repo, old):
        pass
    bindings = updater_bindings()
    bindings.get_by_id.return_value.device_provider = provider
    bots = Mock(get_unique_by_id=Mock(return_value={"active_engine": engine}))
    instances = Mock(wraps=repo)
    delegate = Mock()
    updater = ExpiryAwareCallerRuntimeUpdater(instances=instances, delegate=delegate, bindings=bindings, bots=bots)
    # All stages target the same shared binding despite an older issued token.
    issued_expiry = old - timedelta(days=1)
    token = CallerToken("test-token", "u1", issued_expiry, "fingerprint")
    with caplog.at_level("INFO"):
        updater.update_caller_identity(bot_id="b1", owner_user_id="o1", caller_user_id="u1",
                                       caller_token=token, stage=stage, publish_id=None, binding_id=7)
    delegate.update_caller_identity.assert_called_once()
    assert delegate.update_caller_identity.call_args.kwargs["binding_id"] == 7
    instances.caller_token_update.assert_not_called()
    assert expiry(repo) == old.isoformat()
    assert "reason=teclaw_shared_runtime" in caplog.text
    assert "test-token" not in caplog.text
    if provider == "teclaw":
        bots.get_unique_by_id.assert_not_called()


def test_engine_lookup_failure_cannot_bypass_expiry_guard():
    from agentclaw.community.core.caller_identity.credential import CallerToken
    from agentclaw.community.core.caller_identity.expiry_runtime_updater import ExpiryAwareCallerRuntimeUpdater

    instances, delegate = Mock(), Mock()
    bots = Mock(get_unique_by_id=Mock(side_effect=RuntimeError("bot lookup unavailable")))
    updater = ExpiryAwareCallerRuntimeUpdater(instances=instances, delegate=delegate,
                                              bindings=updater_bindings(), bots=bots)
    issued_expiry = datetime.now(UTC) + timedelta(hours=1)
    token = CallerToken("test-token", "u1", issued_expiry, "fingerprint")
    with pytest.raises(RuntimeError, match="bot lookup unavailable"):
        updater.update_caller_identity(bot_id="b1", owner_user_id="o1", caller_user_id="u1",
                                       caller_token=token, stage="online", publish_id=None, binding_id=7)
    delegate.update_caller_identity.assert_not_called()
    instances.caller_token_update.assert_not_called()


def refresh_scope(repo):
    return repo.caller_token_refresh(user_id="u1", bot_id="b1", owner_id="o1", binding_id=7)


def test_refresh_scope_leaves_expiry_unchanged_without_install(repo, caplog):
    seed(repo)
    with caplog.at_level("INFO"), refresh_scope(repo) as state:
        assert state.instance["caller_token_expires_at"] is None
        assert state.instance["ext"]["binding_id"] == 7
    assert expiry(repo) is None
    assert "caller_token_refresh_locked" in caplog.text
    assert "caller_token_expiry_recorded" not in caplog.text


def test_refresh_scope_records_only_successful_install(repo):
    seed(repo)
    end = datetime.now(UTC) + timedelta(days=2)
    with pytest.raises(RuntimeError), refresh_scope(repo) as state:
        state.expires_at = end
        raise RuntimeError("installation failed")
    assert expiry(repo) is None
    with refresh_scope(repo) as state:
        state.expires_at = end
    assert expiry(repo) == end.isoformat()


@pytest.mark.parametrize("delta", [-1, 0])
def test_refresh_scope_rejects_nonadvancing_expiry(repo, delta):
    seed(repo)
    end = datetime.now(UTC) + timedelta(days=2)
    with scope(repo, end):
        pass
    with pytest.raises(ValueError, match="caller_token_expiry_not_advanced"):
        with refresh_scope(repo) as state:
            state.expires_at = end + timedelta(hours=delta)
    assert expiry(repo) == end.isoformat()


@pytest.mark.parametrize("end", [datetime(2000, 1, 1, tzinfo=UTC), datetime(2099, 1, 1)])
def test_refresh_scope_rejects_invalid_expiry(repo, end):
    seed(repo)
    with pytest.raises(ValueError, match="caller_token_expiry_invalid"):
        with refresh_scope(repo) as state:
            state.expires_at = end
    assert expiry(repo) is None


def test_refresh_scope_waiter_observes_iam_committed_expiry(repo):
    seed(repo)
    end = datetime.now(UTC) + timedelta(days=3)
    entered, release, competing = Event(), Event(), Event()
    observed = []

    def iam():
        with scope(repo, end) as install:
            assert install
            entered.set()
            assert release.wait(5)

    def scheduled():
        competing.set()
        with refresh_scope(repo) as state:
            observed.append(state.instance["caller_token_expires_at"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(iam)
        assert entered.wait(5)
        second = pool.submit(scheduled)
        assert competing.wait(5)
        release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert observed == [end.isoformat()]
