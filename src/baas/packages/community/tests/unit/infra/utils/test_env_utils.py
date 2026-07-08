"""Unit tests for env_utils module."""

import socket
from unittest.mock import patch

from secbaas.core.utils import env_utils


class TestIsEmptyEnv:
    """Tests for is_empty_env function."""

    def test_is_empty_env_true_when_no_env_vars(self, monkeypatch):
        """WHEN no SERVER_ENV, REAL_SERVER_ENV, or ALIPAY_APP_ENV environment variables are set,
        THEN is_empty_env() returns True."""
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.is_empty_env() is True

    def test_is_empty_env_false_when_env_set(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'prod', THEN is_empty_env() returns False."""
        monkeypatch.setenv("SERVER_ENV", "prod")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.is_empty_env() is False

    def test_is_empty_env_false_when_real_server_env_set(self, monkeypatch):
        """WHEN REAL_SERVER_ENV is set, THEN is_empty_env() returns False."""
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.setenv("REAL_SERVER_ENV", "pre")
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.is_empty_env() is False

    def test_is_empty_env_false_when_alipay_app_env_set(self, monkeypatch):
        """WHEN ALIPAY_APP_ENV is set, THEN is_empty_env() returns False."""
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.setenv("ALIPAY_APP_ENV", "gray")

        assert env_utils.is_empty_env() is False


class TestIsDev:
    """Tests for is_dev function."""

    def test_is_dev_true_for_stable(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'stable', THEN is_dev() returns True."""
        monkeypatch.setenv("SERVER_ENV", "stable")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.is_dev() is True

    def test_is_dev_true_for_dev(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'dev', THEN is_dev() returns True."""
        monkeypatch.setenv("SERVER_ENV", "dev")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.is_dev() is True

    def test_is_dev_true_for_empty_environment(self, monkeypatch):
        """WHEN no environment variables are set, THEN is_dev() returns True."""
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.is_dev() is True

    def test_is_dev_false_for_prod(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'prod', THEN is_dev() returns False."""
        monkeypatch.setenv("SERVER_ENV", "prod")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.is_dev() is False

    def test_is_dev_false_for_pre(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'pre', THEN is_dev() returns False."""
        monkeypatch.setenv("SERVER_ENV", "pre")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.is_dev() is False

    def test_is_dev_false_for_gray(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'gray', THEN is_dev() returns False."""
        monkeypatch.setenv("SERVER_ENV", "gray")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.is_dev() is False


class TestGetCurrentEnv:
    """Tests for get_current_env function."""

    def test_get_current_env_returns_prod_for_prod(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'prod', THEN get_current_env() returns 'prod'."""
        monkeypatch.setenv("SERVER_ENV", "prod")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.get_current_env() == "prod"

    def test_get_current_env_returns_prod_for_gray(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'gray', THEN get_current_env() returns 'prod'."""
        monkeypatch.setenv("SERVER_ENV", "gray")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.get_current_env() == "prod"

    def test_get_current_env_returns_pre_for_pre(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'pre', THEN get_current_env() returns 'pre'."""
        monkeypatch.setenv("SERVER_ENV", "pre")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.get_current_env() == "pre"

    def test_get_current_env_returns_pre_for_prepub(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'prepub', THEN get_current_env() returns 'pre'."""
        monkeypatch.setenv("SERVER_ENV", "prepub")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.get_current_env() == "pre"

    def test_get_current_env_returns_dev_for_dev(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'dev', THEN get_current_env() returns 'dev'."""
        monkeypatch.setenv("SERVER_ENV", "dev")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.get_current_env() == "dev"

    def test_get_current_env_returns_dev_for_empty(self, monkeypatch):
        """WHEN no environment variables are set, THEN get_current_env() returns 'dev'."""
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.get_current_env() == "dev"

    def test_get_current_env_case_insensitive(self, monkeypatch):
        """WHEN SERVER_ENV has uppercase letters, THEN it's handled case-insensitively."""
        monkeypatch.setenv("SERVER_ENV", "PROD")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.get_current_env() == "prod"


class TestGetCurrentEnvWithGray:
    """Tests for get_current_env_with_gray function."""

    def test_get_current_env_with_gray_distinguishes_gray(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'gray', THEN get_current_env_with_gray() returns 'gray'."""
        monkeypatch.setenv("SERVER_ENV", "gray")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.get_current_env_with_gray() == "gray"

    def test_get_current_env_with_gray_returns_prod_for_prod(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'prod', THEN get_current_env_with_gray() returns 'prod'."""
        monkeypatch.setenv("SERVER_ENV", "prod")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.get_current_env_with_gray() == "prod"

    def test_get_current_env_with_gray_returns_pre_for_pre(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'pre', THEN get_current_env_with_gray() returns 'pre'."""
        monkeypatch.setenv("SERVER_ENV", "pre")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.get_current_env_with_gray() == "pre"

    def test_get_current_env_with_gray_returns_dev_for_dev(self, monkeypatch):
        """WHEN SERVER_ENV is set to 'dev', THEN get_current_env_with_gray() returns 'dev'."""
        monkeypatch.setenv("SERVER_ENV", "dev")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)

        assert env_utils.get_current_env_with_gray() == "dev"


class TestGetLocalIp:
    """Tests for get_local_ip function."""

    def test_get_local_ip_returns_valid_ip_address(self):
        """WHEN get_local_ip() is called, THEN returns a valid IP address string."""
        ip = env_utils.get_local_ip()

        # Should be a valid IP address format
        parts = ip.split(".")
        assert len(parts) == 4
        for part in parts:
            assert 0 <= int(part) <= 255

    def test_get_local_ip_handles_socket_errors_gracefully(self):
        """WHEN socket connection fails, THEN returns '127.0.0.1' as fallback."""
        with patch.object(
            socket.socket, "connect", side_effect=OSError("Network unreachable")
        ):
            ip = env_utils.get_local_ip()
            assert ip == "127.0.0.1"

    def test_get_local_ip_returns_correct_ip_on_success(self):
        """WHEN socket connection succeeds, THEN returns the actual IP address."""
        mock_ip = "192.168.1.100"

        with patch.object(socket.socket, "getsockname", return_value=(mock_ip, 12345)):
            ip = env_utils.get_local_ip()
            assert ip == mock_ip
