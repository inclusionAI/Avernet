"""The coding Arca restart must not destroy an instance a publish build is reading."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_arca_lifecycle import (
    BUILD_IN_PROGRESS_MESSAGE,
    AicodingArcaRestart,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_baas import (
    RestartSubmissionRejected,
)


def _bot():
    return {
        "entity_id": "ent_1",
        "bot_type": "personal",
        "status": "ACTIVE",
        "binding_id": 7,
        "device_binding": {"device_provider": "arca"},
        "ext": {},
    }


def _service(repo):
    lock = SimpleNamespace(lock_token="owned-token")
    return SimpleNamespace(
        _build_active_repo=repo,
        _restart_lock_repo=SimpleNamespace(release=Mock()),
        _try_acquire_restart_lock=Mock(return_value=lock),
        _resolve_restart_target_provider=Mock(return_value="arca"),
        _device_service_provider=Mock(),
        _restart_backup_dependencies=Mock(),
        _repository=Mock(),
        _mark_service_bot_default_image=Mock(side_effect=lambda bot: bot),
        stop_bot=Mock(return_value=True),
        start_bot=Mock(return_value={"status": "PENDING"}),
    )


def _execution():
    state = SimpleNamespace(bot_id="bot_1", owner_id="owner_1", read=Mock(return_value=_bot()))
    return SimpleNamespace(state=state, payload={}, check_target=Mock())


def _execute(service, verify):
    """Run the strategy with an explicit mutation-fence verifier.

    ``verify`` is the fence: it flips the journal to RESTARTING and the Bot to
    PENDING before ``stop_bot``. A guard rejection must happen before it,
    otherwise a still-running Bot would be marked FAILED.
    """
    strategy = SimpleNamespace(prepare_restart=Mock(return_value=verify))
    with patch(
        "agentclaw.community.core.bot_management.engines.registry.resolve_restart_strategy",
        return_value=(Mock(), strategy),
    ):
        return AicodingArcaRestart(service).execute(_bot(), _execution())


def _run(service):
    """Run with a fresh verifier and return (result, verify)."""
    verify = Mock()
    return _execute(service, verify), verify


def test_build_in_progress_rejects_restart_before_mutation_fence():
    repo = SimpleNamespace(is_active=Mock(return_value=True))
    service = _service(repo)
    verify = Mock()

    with pytest.raises(RestartSubmissionRejected) as excinfo:
        _execute(service, verify)

    assert str(excinfo.value) == BUILD_IN_PROGRESS_MESSAGE
    repo.is_active.assert_called_once()
    # F1 regression guard: verify() is the fence; a rejection after it would
    # write PENDING/FAILED for a Bot that was never touched.
    verify.assert_not_called()
    service.stop_bot.assert_not_called()
    service.start_bot.assert_not_called()
    # The restart lock acquired for this attempt is released on rejection.
    release = service._restart_lock_repo.release
    release.assert_called_once()
    assert release.call_args.args[-1] == "owned-token"


def test_idle_build_marker_allows_restart():
    repo = SimpleNamespace(is_active=Mock(return_value=False))
    service = _service(repo)

    result, verify = _run(service)

    repo.is_active.assert_called_once()
    verify.assert_called_once()
    service.stop_bot.assert_called_once()
    service.start_bot.assert_called_once()
    service._restart_lock_repo.release.assert_not_called()
    assert result == {"status": "PENDING"}


def test_guard_lookup_failure_keeps_pre_guard_behavior():
    repo = SimpleNamespace(is_active=Mock(side_effect=RuntimeError("db down")))
    service = _service(repo)

    result, verify = _run(service)

    verify.assert_called_once()
    service.stop_bot.assert_called_once()
    assert result == {"status": "PENDING"}


def test_unwired_guard_keeps_pre_guard_behavior():
    service = _service(None)

    result, verify = _run(service)

    verify.assert_called_once()
    service.stop_bot.assert_called_once()
    assert result == {"status": "PENDING"}