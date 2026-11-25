from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_dormant.ops_service import DormantOpsService
from agentclaw.community.core.bot_dormant.recycle_service import RecycleBotService
from agentclaw.community.core.bot_dormant.service import DormantBotService
from agentclaw.community.plugin_api.passport import PassportPlugin


def test_unfreeze_passport_one_only_calls_passport() -> None:
    dormant_service = MagicMock(spec=DormantBotService)
    passport = MagicMock(spec=PassportPlugin)
    service = DormantOpsService(
        dormant_service, passport, MagicMock(spec=RecycleBotService)
    )

    result = service.unfreeze_passport_one(
        bot_id="default",
        owner_id="37565",
        reason="recover license",
    )

    assert result == {
        "bot_id": "default",
        "owner_id": "37565",
        "status": "passport_online",
    }
    passport.unfreeze_agent_passport.assert_called_once_with(
        bot_id="default",
        owner_workno="37565",
        reason="recover license",
    )
    assert dormant_service.mock_calls == []


def test_unfreeze_passport_one_propagates_passport_error() -> None:
    passport = MagicMock(spec=PassportPlugin)
    passport.unfreeze_agent_passport.side_effect = RuntimeError(
        "passport unavailable"
    )
    service = DormantOpsService(
        MagicMock(spec=DormantBotService),
        passport,
        MagicMock(spec=RecycleBotService),
    )

    with pytest.raises(RuntimeError, match="passport unavailable"):
        service.unfreeze_passport_one(
            bot_id="default",
            owner_id="37565",
            reason="recover license",
        )


def test_real_recycle_submits_shared_task_without_early_success():
    dormant = MagicMock(spec=DormantBotService)
    recycle = MagicMock(spec=RecycleBotService)
    recycle.submit.return_value = {"bot_id": "b1", "owner_id": "u1", "status": "ACTIVE",
                                   "changed": True, "operation_id": "op-1", "phase": "QUEUED"}
    service = DormantOpsService(dormant, MagicMock(spec=PassportPlugin), recycle)
    result = service.recycle_one(bot_id="b1", owner_id="u1", dry_run=False, reason="manual test")
    assert result["run_id"] == result["operation_id"] == "op-1"
    assert result["phase"] == "QUEUED" and result["status"] == "ACTIVE"
    assert result["dry_run"] is False
    kwargs = recycle.submit.call_args.kwargs
    assert kwargs["source"] == "manual_ops" and kwargs["reason"] == "manual test"
    recycle.recycle.assert_not_called()
    dormant._enqueue_recycle.assert_not_called()
    dormant._write_audit.assert_not_called()


def test_recycle_default_preserves_preview_without_submitting_task():
    from datetime import UTC, datetime
    dormant = MagicMock(spec=DormantBotService)
    dormant._db = MagicMock()
    bot = MagicMock(status="ACTIVE", bot_type="personal", bot_id="b1", owner_id="u1",
                    entity_id="u1", bot_name="Preview", gmt_create=datetime.now(UTC).replace(tzinfo=None))
    session = dormant._db.orm_session.return_value.__enter__.return_value
    session.query.return_value.filter.return_value.order_by.return_value.first.return_value = bot
    recycle = MagicMock(spec=RecycleBotService)
    service = DormantOpsService(dormant, MagicMock(spec=PassportPlugin), recycle)
    result = service.recycle_one(bot_id="b1", owner_id="u1")
    assert result["dry_run"] is True and result["status"] == "dry_run_recycled"
    assert result["run_id"].startswith("manual-recycle-")
    recycle.submit.assert_not_called()
    recycle.recycle.assert_not_called()
    dormant._enqueue_recycle.assert_called_once()
    assert dormant._write_audit.call_args.kwargs["dry_run"] is True
