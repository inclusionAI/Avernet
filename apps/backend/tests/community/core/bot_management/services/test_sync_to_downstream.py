"""Tests for core.bot_management.services.sync_to_downstream."""
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services import sync_to_downstream
from agentclaw.community.core.bot_management.services.sync_to_downstream import (
    _LINK_TYPE_TO_MCP_CODE,
    sync_to_ecb,
    sync_to_bcsfuse,
    sync_all,
)


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch):
    """Neutralise the exponential ``time.sleep`` between sync retries.

    ``sync_to_ecb`` / ``sync_to_bcsfuse`` sleep 1s then 2s before giving up after
    ``MAX_RETRIES``. The exhaustion tests assert the *call count*, never the
    spacing, so the sleeps were 6s of dead wall-clock.
    """
    monkeypatch.setattr(sync_to_downstream, "RETRY_BACKOFF_BASE_SECONDS", 0.0)


class TestLinkTypeMappings:
    """link_type 映射常量测试。"""

    def test_yuque_maps_to_skylark(self):
        assert _LINK_TYPE_TO_MCP_CODE["yuque"] == "skylarkmcpserver"

    def test_dima_maps_to_dima(self):
        assert _LINK_TYPE_TO_MCP_CODE["dima"] == "dimamcpserver"

    def test_antcode_maps_to_antcode(self):
        assert _LINK_TYPE_TO_MCP_CODE["antcode"] == "antcodemcpserver"


class TestSyncToEcb:
    """ECB 同步测试。"""

    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream._get_link_resources")
    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream._post_json")
    def test_success_with_links(self, mock_post, mock_get_links):
        mock_get_links.return_value = [
            {"name": "知识库", "url": "https://yuque.antfin.com/test", "link_type": "yuque", "id": "1"},
        ]
        mock_post.return_value = {"success": True}

        result = sync_to_ecb("bot1", "437240", {"capability": "test"}, resource_repo=None)

        assert result["success"] is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        payload = call_args[0][1]
        assert payload["bot_id"] == "bot1"
        assert payload["staff_no"] == "437240"
        assert len(payload["mcps"]) == 1
        assert payload["mcps"][0]["mcp_name"] == "skylarkmcpserver"

    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream._get_link_resources")
    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream._post_json")
    def test_empty_links_sends_empty_mcps(self, mock_post, mock_get_links):
        mock_get_links.return_value = []
        mock_post.return_value = {"success": True}

        result = sync_to_ecb("bot1", "437240", {"capability": "test"}, resource_repo=None)

        assert result["success"] is True
        payload = mock_post.call_args[0][1]
        assert payload["mcps"] == []

    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream._get_link_resources")
    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream._post_json")
    def test_http_error_retries_and_fails(self, mock_post, mock_get_links):
        mock_get_links.return_value = []
        mock_post.side_effect = Exception("Connection refused")

        result = sync_to_ecb("bot1", "437240", {}, resource_repo=None)

        assert result["success"] is False
        assert "Connection refused" in result["error"]
        assert mock_post.call_count == 3  # MAX_RETRIES


class TestSyncToBcsfuse:
    """BCS Fuse 同步测试。"""

    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream._get_bot_info")
    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream._post_json")
    def test_success(self, mock_post, mock_get_bot_info):
        mock_get_bot_info.return_value = {"bot_name": "TestBot", "skill_sets": []}
        mock_post.return_value = {"worker_id": "w123"}

        result = sync_to_bcsfuse("bot1", "437240", {"role": "测试角色"}, MagicMock())

        assert result["success"] is True
        payload = mock_post.call_args[0][1]
        assert payload["name"] == "TestBot"
        assert payload["description"] == "测试角色"

    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream._get_bot_info")
    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream._post_json")
    def test_http_error_retries_and_fails(self, mock_post, mock_get_bot_info):
        mock_get_bot_info.return_value = {"bot_name": "TestBot", "skill_sets": []}
        mock_post.side_effect = Exception("Timeout")

        result = sync_to_bcsfuse("bot1", "437240", {}, MagicMock())

        assert result["success"] is False
        assert mock_post.call_count == 3


class TestSyncAll:
    """sync_all 集成测试。"""

    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream.sync_to_bcsfuse")
    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream.sync_to_ecb")
    def test_both_success(self, mock_ecb, mock_bcsfuse):
        mock_ecb.return_value = {"success": True}
        mock_bcsfuse.return_value = {"success": True}

        result = sync_all("bot1", "437240", {"capability": "test"}, resource_repo=None, bot_service=MagicMock())

        assert result["overall_success"] is True
        assert result["ecb"]["success"] is True
        assert result["bcsfuse"]["success"] is True

    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream.sync_to_bcsfuse")
    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream.sync_to_ecb")
    def test_one_fails_still_overall_success(self, mock_ecb, mock_bcsfuse):
        mock_ecb.return_value = {"success": False, "error": "ECB down"}
        mock_bcsfuse.return_value = {"success": True}

        result = sync_all("bot1", "437240", {}, resource_repo=None, bot_service=MagicMock())

        assert result["overall_success"] is True

    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream.sync_to_bcsfuse")
    @patch("agentclaw.community.core.bot_management.services.sync_to_downstream.sync_to_ecb")
    def test_both_fail(self, mock_ecb, mock_bcsfuse):
        mock_ecb.return_value = {"success": False}
        mock_bcsfuse.return_value = {"success": False}

        result = sync_all("bot1", "437240", {}, resource_repo=None, bot_service=MagicMock())

        assert result["overall_success"] is False
