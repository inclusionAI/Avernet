"""
Tests for main.py MIST integration functions.
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestLoadYamlConfig:
    """Tests for load_yaml_config function."""

    def test_load_valid_config(self, tmp_path):
        """Test loading a valid YAML config file."""
        from main import load_yaml_config

        config_file = tmp_path / "application.yaml"
        config_file.write_text("app_name: test\nversion: 1.0")

        result = load_yaml_config(str(tmp_path))
        assert result["app_name"] == "test"
        assert result["version"] == 1.0

    def test_load_missing_config(self, tmp_path):
        """Test loading when config file doesn't exist."""
        from main import load_yaml_config

        result = load_yaml_config(str(tmp_path))
        assert result == {}

    def test_load_empty_config(self, tmp_path):
        """Test loading an empty config file."""
        from main import load_yaml_config

        config_file = tmp_path / "application.yaml"
        config_file.write_text("")

        result = load_yaml_config(str(tmp_path))
        assert result == {}

    def test_load_config_with_mist_key(self, tmp_path):
        """Test loading config with MIST_KEY."""
        from main import load_yaml_config

        config_file = tmp_path / "application.yaml"
        config_file.write_text("MIST_KEY: test_mist_key\napp_name: test")

        result = load_yaml_config(str(tmp_path))
        assert result["MIST_KEY"] == "test_mist_key"


class TestGetMistCredentials:
    """Tests for get_mist_credentials function."""

    def test_use_environment_variables_priority(self):
        """Test that environment variables take priority over MIST."""
        from main import get_mist_credentials

        os.environ['LLM_AUTH_TOKEN'] = 'env_llm_token'
        os.environ['EMBEDDING_AUTH_TOKEN'] = 'env_embedding_token'

        try:
            result = get_mist_credentials('test_app', 'ALIPAY', 'dev', 'test_mist_key')

            assert result['LLM_AUTH_TOKEN'] == 'env_llm_token'
            assert result['EMBEDDING_AUTH_TOKEN'] == 'env_embedding_token'
        finally:
            os.environ.pop('LLM_AUTH_TOKEN', None)
            os.environ.pop('EMBEDDING_AUTH_TOKEN', None)

    def test_get_credentials_from_mist(self):
        """Test getting credentials from MIST when env vars not set."""
        os.environ.pop('LLM_AUTH_TOKEN', None)
        os.environ.pop('EMBEDDING_AUTH_TOKEN', None)

        mock_mist_client = MagicMock()
        mock_mist_client.getSecretInfo.return_value = {
            'dbUser': 'test_user',
            'password': 'test_token',
            'version': 1
        }

        mock_mist_module = MagicMock()
        mock_mist_module.MistClient.return_value = mock_mist_client

        with patch.dict('sys.modules', {'mist.mist_client': mock_mist_module}):
            import importlib
            import main
            importlib.reload(main)
            from main import get_mist_credentials

            result = get_mist_credentials('test_app', 'ALIPAY', 'dev', 'test_mist_key')

            assert result['LLM_AUTH_TOKEN'] == 'test_token'
            assert result['EMBEDDING_AUTH_TOKEN'] == 'test_token'

    def test_mist_returns_none(self):
        """Test handling when MIST returns None."""
        os.environ.pop('LLM_AUTH_TOKEN', None)
        os.environ.pop('EMBEDDING_AUTH_TOKEN', None)

        mock_mist_client = MagicMock()
        mock_mist_client.getSecretInfo.return_value = None

        mock_mist_module = MagicMock()
        mock_mist_module.MistClient.return_value = mock_mist_client

        with patch.dict('sys.modules', {'mist.mist_client': mock_mist_module}):
            import importlib
            import main
            importlib.reload(main)
            from main import get_mist_credentials

            result = get_mist_credentials('test_app', 'ALIPAY', 'dev', 'test_mist_key')

            assert result == {}

    def test_mist_not_installed(self):
        """Test handling when MIST SDK is not installed."""
        os.environ.pop('LLM_AUTH_TOKEN', None)
        os.environ.pop('EMBEDDING_AUTH_TOKEN', None)

        # Simulate ImportError when mist is not installed
        with patch.dict('sys.modules', {'mist.mist_client': None}):
            import importlib
            import main
            importlib.reload(main)
            from main import get_mist_credentials

            result = get_mist_credentials('test_app', 'ALIPAY', 'dev', 'test_mist_key')
            assert result == {}

    def test_mist_exception(self):
        """Test handling when MIST throws an exception."""
        os.environ.pop('LLM_AUTH_TOKEN', None)
        os.environ.pop('EMBEDDING_AUTH_TOKEN', None)

        mock_mist_module = MagicMock()
        mock_mist_module.MistClient.side_effect = Exception("MIST error")

        with patch.dict('sys.modules', {'mist.mist_client': mock_mist_module}):
            import importlib
            import main
            importlib.reload(main)
            from main import get_mist_credentials

            result = get_mist_credentials('test_app', 'ALIPAY', 'dev', 'test_mist_key')
            assert result == {}


class TestInjectSecretsToEnv:
    """Tests for inject_secrets_to_env function."""

    def test_inject_secrets_to_empty_env(self):
        """Test injecting secrets when env vars don't exist."""
        from main import inject_secrets_to_env

        secrets = {
            'LLM_AUTH_TOKEN': 'test_llm_token',
            'EMBEDDING_AUTH_TOKEN': 'test_embedding_token'
        }

        try:
            inject_secrets_to_env(secrets)

            assert os.environ.get('LLM_AUTH_TOKEN') == 'test_llm_token'
            assert os.environ.get('EMBEDDING_AUTH_TOKEN') == 'test_embedding_token'
        finally:
            os.environ.pop('LLM_AUTH_TOKEN', None)
            os.environ.pop('EMBEDDING_AUTH_TOKEN', None)

    def test_not_overwrite_existing_env(self):
        """Test that existing env vars are not overwritten."""
        from main import inject_secrets_to_env

        os.environ['LLM_AUTH_TOKEN'] = 'original_token'

        try:
            secrets = {
                'LLM_AUTH_TOKEN': 'new_token',
                'EMBEDDING_AUTH_TOKEN': 'test_embedding_token'
            }

            inject_secrets_to_env(secrets)

            assert os.environ.get('LLM_AUTH_TOKEN') == 'original_token'
            assert os.environ.get('EMBEDDING_AUTH_TOKEN') == 'test_embedding_token'
        finally:
            os.environ.pop('LLM_AUTH_TOKEN', None)
            os.environ.pop('EMBEDDING_AUTH_TOKEN', None)

    def test_skip_empty_values(self):
        """Test that empty values are not injected."""
        from main import inject_secrets_to_env

        secrets = {
            'LLM_AUTH_TOKEN': '',
            'EMBEDDING_AUTH_TOKEN': None
        }

        inject_secrets_to_env(secrets)

        assert 'LLM_AUTH_TOKEN' not in os.environ
        assert 'EMBEDDING_AUTH_TOKEN' not in os.environ

    def test_empty_secrets_dict(self):
        """Test handling empty secrets dict."""
        from main import inject_secrets_to_env

        inject_secrets_to_env({})

        assert 'LLM_AUTH_TOKEN' not in os.environ
        assert 'EMBEDDING_AUTH_TOKEN' not in os.environ