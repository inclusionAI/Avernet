"""Tests for infra/local/utils.py — get_instance_id."""

from unittest.mock import patch

from secbaas.core.service.paas.desktop._utils import get_instance_id


class TestGetInstanceId:
    def test_returns_requested_ip_when_env_set(self, monkeypatch):
        monkeypatch.setenv("RequestedIP", "10.0.0.1")
        assert get_instance_id() == "10.0.0.1"

    def test_returns_local_ip_when_env_absent(self, monkeypatch):
        monkeypatch.delenv("RequestedIP", raising=False)
        with patch(
            "secbaas.core.service.paas.desktop._utils.get_local_ip",
            return_value="192.168.1.1",
        ):
            assert get_instance_id() == "192.168.1.1"

    def test_returns_local_ip_when_env_empty(self, monkeypatch):
        monkeypatch.setenv("RequestedIP", "")
        with patch(
            "secbaas.core.service.paas.desktop._utils.get_local_ip",
            return_value="192.168.1.1",
        ):
            assert get_instance_id() == "192.168.1.1"
