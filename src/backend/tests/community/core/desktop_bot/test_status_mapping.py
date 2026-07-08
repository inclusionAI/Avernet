"""Unit tests for status_mapping pure functions."""
import pytest

from agentclaw.community.core.desktop_bot.status_mapping import StatusDecision, map_baas_to_local


class TestOrphanPaths:
    def test_orphan_pending_to_failed(self):
        decision = map_baas_to_local(
            baas_response=None, current_local_status="PENDING", confirmed_orphan=True,
        )
        assert decision.target_status == "FAILED"
        assert decision.release_reason == "baas_orphan_404"
        assert decision.soft_delete is False

    def test_orphan_active_to_released(self):
        decision = map_baas_to_local(
            baas_response=None, current_local_status="ACTIVE", confirmed_orphan=True,
        )
        assert decision.target_status == "RELEASED"
        assert decision.release_reason == "baas_orphan_404"
        assert decision.soft_delete is True

    def test_orphan_offline_to_released(self):
        decision = map_baas_to_local(
            baas_response=None, current_local_status="OFFLINE", confirmed_orphan=True,
        )
        assert decision.target_status == "RELEASED"
        assert decision.soft_delete is True

    def test_orphan_releasing_to_released(self):
        decision = map_baas_to_local(
            baas_response=None, current_local_status="RELEASING", confirmed_orphan=True,
        )
        assert decision.target_status == "RELEASED"
        assert decision.soft_delete is True

    def test_orphan_failed_to_released(self):
        decision = map_baas_to_local(
            baas_response=None, current_local_status="FAILED", confirmed_orphan=True,
        )
        assert decision.target_status == "RELEASED"
        assert decision.soft_delete is True


class TestBaasStatePaths:
    def test_baas_released_terminal(self):
        decision = map_baas_to_local(
            baas_response={"bot_status": "RELEASED", "device_status": ""},
            current_local_status="ACTIVE", confirmed_orphan=False,
        )
        assert decision.target_status == "RELEASED"
        assert decision.soft_delete is True

    def test_baas_failed_terminal(self):
        decision = map_baas_to_local(
            baas_response={"bot_status": "FAILED", "device_status": ""},
            current_local_status="ACTIVE", confirmed_orphan=False,
        )
        assert decision.target_status == "FAILED"
        assert decision.soft_delete is False

    def test_baas_destroying_to_releasing(self):
        decision = map_baas_to_local(
            baas_response={"bot_status": "DESTROYING", "device_status": ""},
            current_local_status="ACTIVE", confirmed_orphan=False,
        )
        assert decision.target_status == "RELEASING"
        assert decision.soft_delete is False

    def test_active_all_online(self):
        decision = map_baas_to_local(
            baas_response={"bot_status": "ACTIVE", "device_status": "ALL_ONLINE"},
            current_local_status="OFFLINE", confirmed_orphan=False,
        )
        assert decision.target_status == "ACTIVE"

    def test_active_partial_online(self):
        decision = map_baas_to_local(
            baas_response={"bot_status": "ACTIVE", "device_status": "PARTIAL_ONLINE"},
            current_local_status="OFFLINE", confirmed_orphan=False,
        )
        assert decision.target_status == "ACTIVE"
        assert decision.log_context.get("partial_online") == "true"

    def test_active_all_offline(self):
        decision = map_baas_to_local(
            baas_response={"bot_status": "ACTIVE", "device_status": "ALL_OFFLINE"},
            current_local_status="ACTIVE", confirmed_orphan=False,
        )
        assert decision.target_status == "OFFLINE"

    def test_pending_all_offline_stays_pending(self):
        """重启过渡态保护:本地 PENDING + BaaS ACTIVE + 设备 ALL_OFFLINE
        是重启刚发起、设备还没连回来的正常过渡,不应被对账成 OFFLINE。
        """
        decision = map_baas_to_local(
            baas_response={"bot_status": "ACTIVE", "device_status": "ALL_OFFLINE"},
            current_local_status="PENDING", confirmed_orphan=False,
        )
        assert decision.target_status is None
        assert decision.log_context.get("pending_transition") == "true"

    def test_pending_all_online_becomes_active(self):
        """PENDING + 设备 ALL_ONLINE = 重启完成,正常转 ACTIVE(不受过渡保护影响)。"""
        decision = map_baas_to_local(
            baas_response={"bot_status": "ACTIVE", "device_status": "ALL_ONLINE"},
            current_local_status="PENDING", confirmed_orphan=False,
        )
        assert decision.target_status == "ACTIVE"

    def test_baas_pending_no_change(self):
        decision = map_baas_to_local(
            baas_response={"bot_status": "PENDING", "device_status": ""},
            current_local_status="PENDING", confirmed_orphan=False,
        )
        assert decision.target_status is None

    def test_baas_pending_after_local_active_warns(self):
        decision = map_baas_to_local(
            baas_response={"bot_status": "PENDING", "device_status": ""},
            current_local_status="ACTIVE", confirmed_orphan=False,
        )
        assert decision.target_status is None
        assert decision.log_context.get("unexpected_baas_pending") == "true"

    def test_unrecognized_combo(self):
        decision = map_baas_to_local(
            baas_response={"bot_status": "UNKNOWN_STATE", "device_status": "WEIRD"},
            current_local_status="ACTIVE", confirmed_orphan=False,
        )
        assert decision.target_status is None
        assert "unrecognized" in str(decision.log_context)


class TestEdgeCases:
    def test_none_baas_response_not_orphan(self):
        """baas_response=None but not orphan → warning, no change."""
        decision = map_baas_to_local(
            baas_response=None, current_local_status="ACTIVE", confirmed_orphan=False,
        )
        assert decision.target_status is None
        assert "warning" in decision.log_context

    def test_case_insensitive_bot_status(self):
        decision = map_baas_to_local(
            baas_response={"bot_status": "failed", "device_status": ""},
            current_local_status="ACTIVE", confirmed_orphan=False,
        )
        assert decision.target_status == "FAILED"

    def test_active_unrecognized_device_status(self):
        decision = map_baas_to_local(
            baas_response={"bot_status": "ACTIVE", "device_status": "SOME_NEW_STATE"},
            current_local_status="ACTIVE", confirmed_orphan=False,
        )
        assert decision.target_status is None
        assert "unrecognized" in str(decision.log_context)
