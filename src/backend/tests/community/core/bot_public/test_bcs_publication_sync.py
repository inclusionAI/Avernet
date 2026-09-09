"""BCS publication must sync only committed user visibility to BCSFuse."""

from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest

from agentclaw.community.core.bot_management.services.bcn_service import BcnServiceError
from agentclaw.community.plugin_api.approval_workflow import NO_WORKFLOW_MARKER
from tests.community.core.bot_public.test_bot_public_service import (
    _make_bot,
    _make_operator,
    _make_service,
)

MODULE = "agentclaw.community.core.bot_public.services.bot_public_service"
BOT_UUID = "20260902_s1kxfp9l:334018"


@pytest.fixture
def publication(monkeypatch):
    bcn = MagicMock()
    bcn.patch_attributes.return_value = {}
    bcn.get_attributes.return_value = {
        "friend_ext": {
            "public_user_approval": {"visibility": "public", "status": "PROCESSING"},
            "public_agent_approval": {"status": "PROCESSING"},
        },
        "friend_check_in_strategy": "OPEN",
    }
    config = MagicMock(base_url="http://bcsfuse.test", base_url_pre="", worker_id_with_owner=True)
    service = _make_service(bcn_service=bcn, bcsfuse_config=config)
    http = MagicMock()
    http.return_value.__enter__.return_value.status = 200
    monkeypatch.setattr(f"{MODULE}.urlopen", http)
    monkeypatch.setattr(f"{MODULE}.get_current_env", lambda: "prod")
    return service, bcn, http


@pytest.mark.parametrize("qualified", [False, True])
def test_private_user_syncs_exact_bcs_uuid_after_persistence(publication, qualified):
    service, bcn, http = publication
    service._bcsfuse_config.worker_id_with_owner = qualified
    bcn.patch_attributes.side_effect = lambda **kwargs: assert_no_http_yet(http)
    result = service.public_bcs_bot(BOT_UUID, "334018", "user", _make_operator(), visibility="private")
    assert result["state"] == "COMPLETED"
    bcn.patch_attributes.assert_called_once_with(bot_uuid=BOT_UUID, body={"user_visibility": "private"})
    http.assert_called_once()
    request = http.call_args.args[0]
    assert request.get_method() == "PUT"
    assert request.full_url == f"http://bcsfuse.test/v1/workers/{BOT_UUID}/offline"


def assert_no_http_yet(http):
    http.assert_not_called()
    return {}


@pytest.mark.parametrize("visibility, state", [("public", "online"), ("protected", "online"), ("private", "offline")])
def test_agreed_user_callback_syncs_persisted_visibility(publication, visibility, state):
    service, bcn, http = publication
    bcn.get_attributes.return_value["friend_ext"]["public_user_approval"]["visibility"] = visibility
    bcn.patch_attributes.side_effect = lambda **kwargs: assert_no_http_yet(http)
    result = service.handle_public_approval_callback(BOT_UUID, "334018", "p1", "AGREE", public_scope="user")
    assert result["success"]
    assert bcn.patch_attributes.call_args.kwargs["body"]["user_visibility"] == visibility
    http.assert_called_once()
    assert http.call_args.args[0].full_url == f"http://bcsfuse.test/v1/workers/{BOT_UUID}/{state}"


@pytest.mark.parametrize("scope, decision", [("agent", "AGREE"), ("user", "DISAGREE"), ("user", "CANCEL")])
def test_other_scope_or_rejection_does_not_change_runtime(publication, scope, decision):
    service, _, http = publication
    service.handle_public_approval_callback(BOT_UUID, "334018", "p1", decision, public_scope=scope)
    http.assert_not_called()


def test_private_agent_does_not_change_user_runtime(publication):
    service, bcn, http = publication
    service.public_bcs_bot(BOT_UUID, "334018", "agent", _make_operator(), visibility="private")
    bcn.patch_attributes.assert_called_once_with(bot_uuid=BOT_UUID, body={"visibility": "private"})
    http.assert_not_called()


@pytest.mark.parametrize("callback", [False, True])
def test_skipped_bcs_patch_does_not_sync(publication, callback):
    service, bcn, http = publication
    bcn.patch_attributes.return_value = {"skipped": True}
    if callback:
        service.handle_public_approval_callback(BOT_UUID, "334018", "p1", "AGREE", public_scope="user")
    else:
        result = service.public_bcs_bot(BOT_UUID, "334018", "user", _make_operator(), visibility="private")
        assert result["state"] == "SKIPPED"
    http.assert_not_called()


def test_failed_bcs_patch_does_not_sync(publication):
    service, bcn, http = publication
    bcn.patch_attributes.side_effect = BcnServiceError("write failed")
    with pytest.raises(BcnServiceError):
        service.public_bcs_bot(BOT_UUID, "334018", "user", _make_operator(), visibility="private")
    http.assert_not_called()


def test_pending_approval_does_not_sync(publication):
    service, _, http = publication
    service._process_service.start_approval.return_value = {"success": True, "state": "PROCESSING", "puid": "p1"}
    service.public_bcs_bot(BOT_UUID, "334018", "user", _make_operator(), visibility="public")
    http.assert_not_called()


@pytest.mark.parametrize("approval", [
    {"success": True, "state": "COMPLETED", "lastOperate": "AGREE", "puid": "p1"},
    {"success": False, "error_msg": f"{NO_WORKFLOW_MARKER} unavailable"},
])
def test_inline_approval_and_community_fallback_sync_user_publication(publication, approval):
    service, _, http = publication
    service._bot_repository.get_by_id_and_owner.return_value = _make_bot()
    service._process_service.start_approval.return_value = approval
    result = service.public_bcs_bot(BOT_UUID, "334018", "user", _make_operator(), visibility="public")
    assert result["success"]
    http.assert_called_once()
    assert http.call_args.args[0].full_url == f"http://bcsfuse.test/v1/workers/{BOT_UUID}/online"


def test_bcsfuse_failure_is_logged_without_reverting_bcs(publication, caplog):
    service, bcn, http = publication
    http.side_effect = HTTPError("http://bcsfuse.test", 404, "Worker not found", {}, None)
    result = service.public_bcs_bot(BOT_UUID, "334018", "user", _make_operator(), visibility="private")
    assert result["state"] == "COMPLETED"
    bcn.patch_attributes.assert_called_once()
    assert "[_sync_bcsfuse_runtime_state]" in caplog.text
    assert f"worker_id={BOT_UUID}" in caplog.text
    assert "status=404" in caplog.text
