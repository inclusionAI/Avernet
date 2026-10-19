"""Coding Arca orchestration owns ordering, fencing and allocation lock handoff."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_arca import RestartBackupTargetChanged
from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import AicodingRestartBackupMixin
from agentclaw.community.core.bot_management.engines.aicoding.restart_state import TASK_TYPE, journal, task_key
from agentclaw.community.core.task_queue.types import Fail, Reschedule
from tests.community.core.bot_management.aicoding.test_restart_service import lifecycle  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_type", ["personal", "service"])
@pytest.mark.parametrize("outcome", ["success", "busy", "target_changed", "stop_error", "start_error", "release_warning"])
async def test_arca_lock_ownership_and_no_mutation_replay(request, outcome, bot_type):
    s = request.getfixturevalue("lifecycle")
    s.repo.bot["bot_type"] = bot_type
    s.service._mark_service_bot_default_image = Mock(
        side_effect=lambda bot: {**bot, "ext": {**bot.get("ext", {}), "sbot_use_default_image": True}},
    )
    capture = s.handler._capture_completion = Mock(wraps=s.handler._capture_completion)
    s.binding.update(device_provider="arca", device_props={"sandbox_id": "old-sandbox"})
    s.service.restart_bot = Mock(side_effect=AssertionError("generic restart must not run"))
    s.service._resolve_restart_target_provider = Mock(return_value="arca")
    events = []
    lock = SimpleNamespace(lock_token="owned-token")
    s.service._try_acquire_restart_lock = Mock(
        side_effect=lambda *args: events.append("lock") or (None if outcome == "busy" else lock),
    )
    release = s.service._restart_lock_repo.release = Mock()

    def prepare(*args, **kwargs):
        events.append("backup")

        def verify():
            events.append("verify")
            if outcome == "target_changed":
                raise RestartBackupTargetChanged("target changed")
        return verify

    def stop(**kwargs):
        assert journal(s.repo.bot)["phase"] == "RESTARTING"
        events.append("stop")
        if outcome == "stop_error":
            raise RuntimeError("private stop error")
        s.repo.bot["binding_id"] = None
        return outcome != "release_warning"

    def start(**kwargs):
        events.append("start")
        assert kwargs["restart_lock_key"][-1] == "owned-token"
        assert kwargs["device_provider"] == "arca"
        if bot_type == "service":
            assert kwargs["bot_ext_override"]["sbot_use_default_image"] is True
        else:
            assert kwargs["bot_ext_override"] is None
        if outcome == "start_error":
            raise RuntimeError("private allocation error")
        s.repo.bot.update(binding_id=8, status="PENDING")
        return deepcopy(s.repo.bot)

    s.service.stop_bot = Mock(side_effect=stop)
    s.service.start_bot = Mock(side_effect=start)
    await s.service.restart_bot_async(bot_id="b", user_id="o")
    assert events == []
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    with patch.object(AicodingRestartBackupMixin, "_prepare_restart", side_effect=prepare):
        result = s.handler.handle(task.payload)
    assert isinstance(result, Fail if outcome == "target_changed" else Reschedule)
    assert events[:2] == ["backup", "lock"]
    if outcome == "busy":
        assert events == ["backup", "lock"]
        assert journal(s.repo.bot)["phase"] == "BACKING_UP"
        release.assert_not_called()
        return
    if outcome in {"target_changed", "stop_error", "start_error"}:
        release.assert_called_once()
        assert release.call_args.args[-1] == "owned-token"
    else:
        # Successful allocation now owns releasing this exact token.
        release.assert_not_called()
    expected = ["backup", "lock", "verify"]
    if outcome != "target_changed":
        expected.append("stop")
    if outcome not in {"target_changed", "stop_error"}:
        expected.append("start")
    assert events == expected
    if outcome == "release_warning":
        assert "restart_warning" in capture.call_args.kwargs["result"]
    s.handler.handle(task.payload)
    assert events == expected  # Never replay an ambiguous mutation.
    s.service.restart_bot.assert_not_called()
