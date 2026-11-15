"""Request-body validation for the legacy Bot update route."""

from unittest.mock import MagicMock, patch

import pytest

from tests.community.api.bot_management._bot_router_test_support import (
    client as client,
    mock_bot_service as mock_bot_service,
    mock_passport as mock_passport,
)


class TestUpdateBotInvalidBody:
    @pytest.mark.parametrize(
        "content",
        [b"", b"{secret-marker", b"\xff", b"null", b"[]", b'"secret-marker"', b"12", b"true"],
    )
    def test_invalid_body_is_client_error_without_update_or_leak(self, client, caplog, content):
        caplog.set_level("INFO")
        tc, svc, passport = client
        response = tc.put(
            "/api/bots/default", content=content, headers={"content-type": "application/json"}
        )
        assert response.status_code == 200
        assert response.json()["success"] is False
        assert response.json()["error_code"] == 400
        assert response.json()["data"] is None
        assert "secret-marker" not in response.text
        assert "secret-marker" not in caplog.text
        svc.update_bot.assert_not_called()
        passport.update_passport.assert_not_called()

    @pytest.mark.parametrize("has_collaborators,expected", [(False, 403), (True, 423)])
    def test_permission_and_lock_precede_invalid_body(self, client, has_collaborators, expected):
        from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError

        tc, svc, _ = client
        lock_service = MagicMock()
        lock_service.get_lock_info.return_value = MagicMock(
            has_collaborators=has_collaborators, lock=None
        )
        interceptor = "agentclaw.community.core.bot_collaborator.interceptor.collaborator.CollaboratorPermissionInterceptor"
        with (
            patch(f"{interceptor}._get_lock_service", return_value=lock_service),
            patch(f"{interceptor}._space_granted_permission", return_value=None),
            patch(f"{interceptor}._is_coding_app", return_value=False),
            patch(
                "agentclaw.community.core.bot_management.addressing.BotAddressResolver.resolve",
                side_effect=BotAddressNotFoundError,
            ),
        ):
            response = tc.put("/api/bots/default?owner_id=other", content=b"{")
        assert response.json()["error_code"] == expected
        svc.update_bot.assert_not_called()

    def test_unknown_bot_invalid_body_is_rejected_before_update(self, client):
        from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError

        tc, svc, _ = client
        with (
            patch(
                "agentclaw.community.core.bot_management.addressing.BotAddressResolver.resolve",
                side_effect=BotAddressNotFoundError,
            ) as resolve,
            patch(
                "agentclaw.community.core.bot_collaborator.interceptor.collaborator.CollaboratorPermissionInterceptor._resolve_owner_id",
                return_value=None,
            ) as resolve_owner,
        ):
            response = tc.put("/api/bots/missing", content=b"{")
        resolve.assert_called_once()
        resolve_owner.assert_called_once()
        assert response.status_code == 200
        assert response.json()["error_code"] == 400
        assert response.json()["message"] == "请求体不是有效的 JSON"
        svc.update_bot.assert_not_called()
