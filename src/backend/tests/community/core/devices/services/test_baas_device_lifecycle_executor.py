"""Tests for the shared BaaS device lifecycle executor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.baas_device_lifecycle_executor import (
    BaasDeviceLifecycleError,
    BaasDeviceLifecycleExecutor,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError


def _baas_service() -> MagicMock:
    baas = MagicMock()
    baas._baas_api_base = "http://baas.local"
    baas._build_personal_bot_payload.return_value = {"name": "Bot", "config": {}}
    baas.create_bot.return_value = {
        "bot_uuid": "BAAS-BOT-1",
        "publish_id": 12345,
        "request_id": "req-create",
    }
    baas.post_bots_api.return_value = {
        "bot_uuid": "BAAS-BOT-1",
        "publish_id": 12345,
        "request_id": "req-create",
    }
    baas.destroy_bot.return_value = {
        "bot_uuid": "BAAS-BOT-1",
        "publish_id": 67890,
        "request_id": "req-destroy",
    }
    baas.approve_publish.return_value = {"status": "SUCCESS"}
    return baas


class TestCreateBotFromPayload:
    def test_posts_payload_to_baas_and_approves_publish(self):
        baas = _baas_service()
        executor = BaasDeviceLifecycleExecutor(baas)
        payload = {"name": "Bot", "config": {}}

        result = executor.create_bot_from_payload(
            payload=payload,
            owner_id="u001",
            request_id="req-create",
            action="baas_device_create",
            approve_comment="自动审批",
        )

        assert result.bot_uuid == "BAAS-BOT-1"
        assert result.publish_id == "12345"
        assert result.request_id == "req-create"
        assert result.raw_response["bot_uuid"] == "BAAS-BOT-1"
        baas._build_personal_bot_payload.assert_not_called()
        baas.post_bots_api.assert_called_once_with(
            path="/api/v1/bots",
            payload=payload,
            action="baas_device_create",
        )
        baas.approve_publish.assert_called_once_with(
            publish_id=12345,
            operator="u001",
            request_id="req-create",
            comment="自动审批",
        )

    def test_missing_bot_uuid_is_rejected(self):
        baas = _baas_service()
        baas.post_bots_api.return_value = {"publish_id": 12345}
        executor = BaasDeviceLifecycleExecutor(baas)

        with pytest.raises(BaasDeviceLifecycleError, match="bot_uuid"):
            executor.create_bot_from_payload(
                payload={"name": "Bot", "config": {}},
                owner_id="u001",
                request_id="req-create",
            )

        baas.approve_publish.assert_not_called()

    def test_post_failure_is_wrapped(self):
        baas = _baas_service()
        baas.post_bots_api.side_effect = BaasServiceError("secbaas down")
        executor = BaasDeviceLifecycleExecutor(baas)

        with pytest.raises(BaasDeviceLifecycleError, match="create failed"):
            executor.create_bot_from_payload(
                payload={"name": "Bot", "config": {}},
                owner_id="u001",
                request_id="req-create",
            )

        baas.approve_publish.assert_not_called()

    def test_approve_failure_is_wrapped(self):
        baas = _baas_service()
        baas.approve_publish.side_effect = BaasServiceError("approve failed")
        executor = BaasDeviceLifecycleExecutor(baas)

        with pytest.raises(BaasDeviceLifecycleError, match="approve"):
            executor.create_bot_from_payload(
                payload={"name": "Bot", "config": {}},
                owner_id="u001",
                request_id="req-create",
            )

    def test_can_skip_manual_approve_when_payload_auto_approves(self):
        baas = _baas_service()
        executor = BaasDeviceLifecycleExecutor(baas)

        result = executor.create_bot_from_payload(
            payload={
                "name": "Bot",
                "config": {"auto_approve_publish": True},
            },
            owner_id="u001",
            request_id="req-create",
            approve_publish=False,
        )

        assert result.bot_uuid == "BAAS-BOT-1"
        assert result.publish_id == "12345"
        baas.approve_publish.assert_not_called()


class TestDestroyBot:
    def test_destroy_approves_destroy_publish_when_returned(self):
        baas = _baas_service()
        executor = BaasDeviceLifecycleExecutor(baas)

        result = executor.destroy_bot(
            bot_uuid="BAAS-BOT-1",
            operator="u001",
            request_id="req-destroy",
        )

        assert result.bot_uuid == "BAAS-BOT-1"
        assert result.publish_id == "67890"
        assert result.request_id == "req-destroy"
        baas.destroy_bot.assert_called_once_with(
            bot_uuid="BAAS-BOT-1",
            operator="u001",
            request_id="req-destroy",
        )
        baas.approve_publish.assert_called_once_with(
            publish_id=67890,
            operator="u001",
            request_id="req-destroy",
            comment="自动审批销毁",
        )

    def test_destroy_without_publish_id_skips_approve(self):
        baas = _baas_service()
        baas.destroy_bot.return_value = {"bot_uuid": "BAAS-BOT-1"}
        executor = BaasDeviceLifecycleExecutor(baas)

        result = executor.destroy_bot(
            bot_uuid="BAAS-BOT-1",
            operator="u001",
            request_id="req-destroy",
        )

        assert result.publish_id == ""
        baas.approve_publish.assert_not_called()

    def test_destroy_failure_is_wrapped(self):
        baas = _baas_service()
        baas.destroy_bot.side_effect = BaasServiceError("destroy failed")
        executor = BaasDeviceLifecycleExecutor(baas)

        with pytest.raises(BaasDeviceLifecycleError, match="destroy"):
            executor.destroy_bot(
                bot_uuid="BAAS-BOT-1",
                operator="u001",
                request_id="req-destroy",
            )

    def test_destroy_approve_failure_is_non_blocking(self):
        baas = _baas_service()
        baas.approve_publish.side_effect = BaasServiceError("approve failed")
        executor = BaasDeviceLifecycleExecutor(baas)

        result = executor.destroy_bot(
            bot_uuid="BAAS-BOT-1",
            operator="u001",
            request_id="req-destroy",
        )

        assert result.publish_id == "67890"
