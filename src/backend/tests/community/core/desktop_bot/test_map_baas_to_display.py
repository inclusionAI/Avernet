"""Tests for map_baas_to_display — the by-owner list's direct BaaS consumer.

Unlike map_baas_to_local (the scan's write-decision, which has PENDING-transition
protection and orphan soft-delete), this is a pure read mapping for the list:
BaaS device/bot status → the status string to SHOW, or None to leave the DB
value untouched. No transition protection — the list shows BaaS live state.
"""
from agentclaw.community.core.desktop_bot.status_mapping import map_baas_to_display


class TestMapBaasToDisplay:
    def test_all_online_is_active(self):
        assert map_baas_to_display(
            {"bot_status": "ACTIVE", "device_status": "ALL_ONLINE"}
        ) == "ACTIVE"

    def test_partial_online_is_active(self):
        assert map_baas_to_display(
            {"bot_status": "ACTIVE", "device_status": "PARTIAL_ONLINE"}
        ) == "ACTIVE"

    def test_all_offline_is_offline(self):
        # The key A-plan behavior: live ALL_OFFLINE shows OFFLINE directly,
        # NO PENDING-transition protection (the scan owns that, not the list).
        assert map_baas_to_display(
            {"bot_status": "ACTIVE", "device_status": "ALL_OFFLINE"}
        ) == "OFFLINE"

    def test_released_is_released(self):
        assert map_baas_to_display(
            {"bot_status": "RELEASED", "device_status": "ALL_OFFLINE"}
        ) == "RELEASED"

    def test_failed_is_failed(self):
        assert map_baas_to_display(
            {"bot_status": "FAILED", "device_status": "ALL_OFFLINE"}
        ) == "FAILED"

    def test_baas_pending_does_not_override(self):
        # BaaS still provisioning → no authoritative live state to show.
        assert map_baas_to_display(
            {"bot_status": "PENDING", "device_status": "ALL_OFFLINE"}
        ) is None

    def test_unknown_device_status_does_not_override(self):
        assert map_baas_to_display(
            {"bot_status": "ACTIVE", "device_status": "WEIRD"}
        ) is None

    def test_empty_or_none_response_does_not_override(self):
        assert map_baas_to_display(None) is None
        assert map_baas_to_display({}) is None

    def test_case_insensitive(self):
        assert map_baas_to_display(
            {"bot_status": "active", "device_status": "all_online"}
        ) == "ACTIVE"
