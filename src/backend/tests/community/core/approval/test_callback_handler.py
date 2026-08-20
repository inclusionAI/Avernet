"""Tests for the antprocess approval callback handler — public_scope routing.

The handler normalizes the platform's form-encoded callback and delegates to
``BotPublicService.handle_public_approval_callback``. ``public_scope`` (a string,
e.g. ``"user"``/``"agent"``) is forwarded verbatim: non-empty => new-version
publish (BCS-delegated), empty/absent => legacy ac_bots publish. The branch
itself lives in the service; these tests pin the routing.
"""
from unittest.mock import MagicMock

from agentclaw.community.core.approval.callback_handler import handle_approval_callback


def _call(callback_data):
    svc = MagicMock()
    svc.handle_public_approval_callback.return_value = {"success": True}
    result = handle_approval_callback(callback_data, svc)
    return result, svc


class TestCallbackHandlerBcsPub:
    def test_public_scope_user_forwarded_as_new_version(self):
        _, svc = _call({
            "global_unique_id": "puid1", "last_operate": "agree",
            "owner_id": "o1", "bot_id": "b1", "public_scope": "user",
        })
        svc.handle_public_approval_callback.assert_called_once_with(
            bot_id="b1", owner_id="o1", puid="puid1",
            last_operate="AGREE", public_scope="user",
        )

    def test_public_scope_absent_forwarded_as_empty_legacy(self):
        _, svc = _call({
            "global_unique_id": "puid1", "last_operate": "agree",
            "owner_id": "o1", "bot_id": "b1",
        })
        svc.handle_public_approval_callback.assert_called_once_with(
            bot_id="b1", owner_id="o1", puid="puid1",
            last_operate="AGREE", public_scope="",
        )

    def test_empty_global_unique_id_short_circuits(self):
        result, svc = _call({"global_unique_id": "", "last_operate": "agree"})
        assert result["success"] is False
        svc.handle_public_approval_callback.assert_not_called()
