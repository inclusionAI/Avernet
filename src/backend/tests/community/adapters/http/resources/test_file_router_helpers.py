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
