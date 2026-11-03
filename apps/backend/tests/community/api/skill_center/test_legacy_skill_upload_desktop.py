
import json





from unittest.mock import MagicMock


















from tests.community.api.skill_center._skills_api_fixtures import mock_ctx as mock_ctx, _upload_skill_di_app
class TestUploadSkillDesktopLiveStatus:
    """Desktop bots' DB status lags BaaS; the upload gate consults BaaS live
    status before rejecting. Cloud bots (personal/service) always trust DB."""

    def _post_upload(self, client):
        return client.post(
            "/api/skills/upload",
            files=[
                (
                    "files",
                    ("SKILL.md", b"---\nname: a\ndescription: a\n---", "text/markdown"),
                )
            ],
            data={"file_paths": json.dumps(["SKILL.md"])},
        )

    def test_desktop_db_offline_but_baas_active_passes(self, mock_ctx):
        # The fix target: DB still OFFLINE (lag) but BaaS says ACTIVE → upload.
        mock_bot_service = MagicMock()
        mock_bot_service.resolve_desktop_live_status = MagicMock(return_value="ACTIVE")
        with _upload_skill_di_app(
            mock_ctx,
            bot_status="OFFLINE",
            bot_type="desktop",
            device_id="dev-1",
            bot_service=mock_bot_service,
        ) as (client, mock_svc, _, _):
            response = self._post_upload(client)
            body = response.json()
            assert body["success"] is True
            assert mock_svc.upload_skill.await_count == 1
            mock_bot_service.resolve_desktop_live_status.assert_called_once()

    def test_desktop_baas_failure_falls_back_to_db_and_rejects(self, mock_ctx):
        # BaaS unreachable → resolver returns None → DB OFFLINE → reject.
        mock_bot_service = MagicMock()
        mock_bot_service.resolve_desktop_live_status = MagicMock(return_value=None)
        with _upload_skill_di_app(
            mock_ctx,
            bot_status="OFFLINE",
            bot_type="desktop",
            device_id="dev-1",
            bot_service=mock_bot_service,
        ) as (client, mock_svc, _, _):
            response = self._post_upload(client)
            body = response.json()
            assert body["success"] is False
            assert "expected ACTIVE" in body["message"]
            mock_svc.upload_skill.assert_not_called()

    def test_desktop_baas_confirms_offline_still_rejects(self, mock_ctx):
        # BaaS itself says OFFLINE → keep rejecting (don't falsely pass).
        mock_bot_service = MagicMock()
        mock_bot_service.resolve_desktop_live_status = MagicMock(return_value="OFFLINE")
        with _upload_skill_di_app(
            mock_ctx,
            bot_status="OFFLINE",
            bot_type="desktop",
            device_id="dev-1",
            bot_service=mock_bot_service,
        ) as (client, mock_svc, _, _):
            response = self._post_upload(client)
            body = response.json()
            assert body["success"] is False
            assert "OFFLINE" in body["message"]
            mock_svc.upload_skill.assert_not_called()

    def test_desktop_pending_does_not_consult_baas(self, mock_ctx):
        # Process state: BaaS is unreliable, gate must trust DB and NOT query.
        mock_bot_service = MagicMock()
        mock_bot_service.resolve_desktop_live_status = MagicMock(return_value=None)
        with _upload_skill_di_app(
            mock_ctx,
            bot_status="PENDING",
            bot_type="desktop",
            device_id="dev-1",
            bot_service=mock_bot_service,
        ) as (client, mock_svc, _, _):
            response = self._post_upload(client)
            body = response.json()
            assert body["success"] is False
            assert "PENDING" in body["message"]
            mock_svc.upload_skill.assert_not_called()

    def test_cloud_bot_never_consults_baas(self, mock_ctx):
        # personal/service: gate uses DB directly, resolve_desktop_live_status
        # is never called (the if-branch is skipped entirely).
        mock_bot_service = MagicMock()
        mock_bot_service.resolve_desktop_live_status = MagicMock(return_value="ACTIVE")
        with _upload_skill_di_app(
            mock_ctx,
            bot_status="ACTIVE",
            bot_type="personal",
            bot_service=mock_bot_service,
        ) as (client, mock_svc, _, _):
            response = self._post_upload(client)
            body = response.json()
            assert body["success"] is True
            mock_bot_service.resolve_desktop_live_status.assert_not_called()

