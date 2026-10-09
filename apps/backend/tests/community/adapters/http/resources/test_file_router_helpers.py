"""Unit tests for ``file_router`` module-level helpers.

``_content_headers`` is the single caller-path for ``download_file``'s response
headers. It must always emit ``attachment`` so the browser saves the file
instead of rendering text/json inline in the tab (the ``inline`` branch for
browsable types was left over from when ``preview_file`` shared this helper;
preview now returns JSON via ``PreviewResponse`` and does not land here).
"""
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.resources.file_router import (
    _authorize_preview_bot,
    _content_headers,
    _validate_preview_publish,
)

pytestmark = pytest.mark.unit


class TestContentHeadersAlwaysAttaches:
    def test_txt_is_attachment_not_inline(self):
        # the bug: .txt landed in _INLINE_TYPES → inline → no browser download
        media, disp = _content_headers("dir/random_text 2.txt")
        assert media == "text/plain"
        assert disp.startswith("attachment;"), disp
        assert "inline" not in disp

    def test_inline_types_now_attach(self):
        # every former _INLINE_TYPES member must attach on the download path
        for ext in ("txt", "md", "json", "jsonl", "xml", "html", "pdf", "png", "jpg", "mp4"):
            _, disp = _content_headers(f"a/file.{ext}")
            assert disp.startswith("attachment;"), (ext, disp)

    def test_unknown_ext_is_attachment(self):
        _, disp = _content_headers("poem.sh")
        assert disp.startswith("attachment;"), disp

    def test_filename_is_url_encoded(self):
        _, disp = _content_headers("random_text 2.txt")
        # quote() encodes the space so the filename parameter stays valid
        assert "random_text%202.txt" in disp, disp

    def test_no_extension_is_attachment(self):
        _, disp = _content_headers("README")
        assert disp.startswith("attachment;"), disp


class TestAuthorizePreviewDefaultBot:
    def test_missing_default_bot_row_uses_authenticated_user(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None

        owner_id = _authorize_preview_bot(
            bot_id="default",
            requested_owner_id=None,
            user_id="current-user",
            bot_repo=bot_repo,
            collaborator_svc=MagicMock(),
        )

        assert owner_id == "current-user"
        bot_repo.get_by_id_and_owner.assert_not_called()
        bot_repo.get_by_id.assert_not_called()

    def test_missing_default_bot_row_rejects_different_requested_owner(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            _authorize_preview_bot(
                bot_id="default",
                requested_owner_id="another-user",
                user_id="current-user",
                bot_repo=bot_repo,
                collaborator_svc=MagicMock(),
            )

        assert exc_info.value.status_code == 403
        bot_repo.get_by_id_and_owner.assert_not_called()


class TestAuthorizePreviewBot:
    def test_owner_is_authorized(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot-1",
            "owner_id": "owner",
        }
        collaborator_svc = MagicMock()

        owner_id = _authorize_preview_bot(
            bot_id="bot-1",
            requested_owner_id="owner",
            user_id="owner",
            bot_repo=bot_repo,
            collaborator_svc=collaborator_svc,
        )

        assert owner_id == "owner"
        collaborator_svc.check_collaborator_permission.assert_not_called()

    def test_unknown_bot_returns_404(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        bot_repo.get_by_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            _authorize_preview_bot(
                bot_id="missing",
                requested_owner_id=None,
                user_id="current-user",
                bot_repo=bot_repo,
                collaborator_svc=MagicMock(),
            )

        assert exc_info.value.status_code == 404

    def test_authorized_collaborator_uses_authoritative_owner(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        bot_repo.get_by_id.return_value = {
            "bot_id": "bot-1",
            "owner_id": "owner",
        }
        collaborator_svc = MagicMock()
        collaborator_svc.check_collaborator_permission.return_value = {
            "has_permission": True,
        }

        owner_id = _authorize_preview_bot(
            bot_id="bot-1",
            requested_owner_id=None,
            user_id="collaborator",
            bot_repo=bot_repo,
            collaborator_svc=collaborator_svc,
        )

        assert owner_id == "owner"

    def test_unauthorized_collaborator_returns_403(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        bot_repo.get_by_id.return_value = {"owner_id": "owner"}
        collaborator_svc = MagicMock()
        collaborator_svc.check_collaborator_permission.return_value = {
            "has_permission": False,
        }

        with pytest.raises(HTTPException) as exc_info:
            _authorize_preview_bot(
                bot_id="bot-1",
                requested_owner_id=None,
                user_id="attacker",
                bot_repo=bot_repo,
                collaborator_svc=collaborator_svc,
            )

        assert exc_info.value.status_code == 403

    def test_permission_check_error_fails_closed(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        bot_repo.get_by_id.return_value = {"owner_id": "owner"}
        collaborator_svc = MagicMock()
        collaborator_svc.check_collaborator_permission.side_effect = RuntimeError("down")

        with pytest.raises(HTTPException) as exc_info:
            _authorize_preview_bot(
                bot_id="bot-1",
                requested_owner_id=None,
                user_id="attacker",
                bot_repo=bot_repo,
                collaborator_svc=collaborator_svc,
            )

        assert exc_info.value.status_code == 403


class TestValidatePreviewPublish:
    def test_missing_publish_id_needs_no_lookup(self):
        publish_repo = MagicMock()

        _validate_preview_publish(
            publish_id=None,
            bot_id="bot-1",
            publish_repo=publish_repo,
        )

        publish_repo.get_by_id.assert_not_called()

    def test_matching_publish_is_allowed(self):
        publish_repo = MagicMock()
        publish_repo.get_by_id.return_value.source_bot_id = "bot-1"

        _validate_preview_publish(
            publish_id="42",
            bot_id="bot-1",
            publish_repo=publish_repo,
        )

        publish_repo.get_by_id.assert_called_once_with(42)

    @pytest.mark.parametrize("publish_id", ["not-an-id", "42"])
    def test_invalid_or_foreign_publish_returns_400(self, publish_id):
        publish_repo = MagicMock()
        if publish_id == "42":
            publish_repo.get_by_id.return_value.source_bot_id = "other-bot"

        with pytest.raises(HTTPException) as exc_info:
            _validate_preview_publish(
                publish_id=publish_id,
                bot_id="bot-1",
                publish_repo=publish_repo,
            )

        assert exc_info.value.status_code == 400
