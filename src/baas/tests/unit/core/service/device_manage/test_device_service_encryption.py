from unittest.mock import MagicMock

from secbaas.community.spi.secret import DEV_SM4_KEY

"""Tests for device_service SM4 encrypt/decrypt with EncryptableHeaderRule."""


def _make_mock_secret_plugin(key=DEV_SM4_KEY):
    mock = MagicMock()
    mock.resolve_common_sm4_key.return_value = key
    return mock


from secbaas.community.api.device_manage import (
    EncryptableHeaderRule,
    EncryptableOutBoundRule,
)
from secbaas.community.core.service.device_manage import (
    _decrypt_header_rule_values,
    _encrypt_header_rule_values,
)
from secbaas.community.core.utils.secret_utils import (
    common_sm4_decrypt,
    common_sm4_encrypt,
)


class TestDeviceServiceEncryptionFlow:
    """Test end-to-end encryption flow in device_manage_service."""

    def test_encrypt_header_values_in_create_device(self):
        """Test create_device encrypts header values when encrypt_value=True."""
        rule = EncryptableHeaderRule(
            domains=["*.api.com"],
            action="SET_HEADER",
            header_name="Authorization",
            value="Bearer secret",
            encrypt_value=True,
        )

        # Simulate encryption logic from _encrypt_header_rule_values
        if rule.encrypt_value and rule.value:
            rule.value = common_sm4_encrypt(rule.value, DEV_SM4_KEY)

        # Value should now be encrypted
        assert rule.value != "Bearer secret"
        assert rule.encrypt_value is True  # Flag unchanged

        # Verify we can decrypt it back
        decrypted = common_sm4_decrypt(rule.value, DEV_SM4_KEY)
        assert decrypted == "Bearer secret"

    def test_decrypt_header_values_in_start_device(self):
        """Test start_device decrypts header values before SDK usage."""
        # Start with encrypted value
        encrypted = common_sm4_encrypt("Bearer secret", DEV_SM4_KEY)
        rule = EncryptableHeaderRule(
            domains=["*.api.com"],
            action="SET_HEADER",
            header_name="Authorization",
            value=encrypted,
            encrypt_value=True,
        )

        # Simulate start_device decryption logic
        value = rule.value
        if rule.encrypt_value and rule.value:
            try:
                value = common_sm4_decrypt(rule.value, DEV_SM4_KEY)
            except Exception:
                value = rule.value

        assert value == "Bearer secret"

    def test_skip_encryption_when_flag_false(self):
        """No encryption when encrypt_value=False."""
        rule = EncryptableHeaderRule(
            domains=["*.api.com"],
            action="SET_HEADER",
            header_name="Authorization",
            value="Bearer plaintext",
            encrypt_value=False,
        )

        # Encryption logic should skip
        if rule.encrypt_value and rule.value:
            rule.value = common_sm4_encrypt(rule.value, DEV_SM4_KEY)

        # Value unchanged
        assert rule.value == "Bearer plaintext"

    def test_skip_decryption_when_flag_false(self):
        """No decryption when encrypt_value=False."""
        rule = EncryptableHeaderRule(
            domains=["*.api.com"],
            action="SET_HEADER",
            header_name="Authorization",
            value="Bearer plaintext",
            encrypt_value=False,
        )

        # Decryption logic should skip
        value = rule.value
        if rule.encrypt_value and rule.value:
            value = common_sm4_decrypt(rule.value, DEV_SM4_KEY)

        assert value == "Bearer plaintext"


class TestEncryptHeaderRuleValues:
    """Test _encrypt_header_rule_values helper function."""

    def test_encrypts_encryptable_outbound_rule(self):
        """Encrypt values when EncryptableOutBoundRule with encrypt_value=True."""
        outbound_rule = EncryptableOutBoundRule(
            header_operation_rules=[
                EncryptableHeaderRule(
                    domains=["*.api.com"],
                    action="SET_HEADER",
                    header_name="X-Token",
                    value="secret-token",
                    encrypt_value=True,
                )
            ]
        )

        # Create a mock extra_config-like structure
        class MockDeployConfig:
            def __init__(self, outbound_rule):
                self.outbound_operation_rule = outbound_rule

        class MockDeviceConfig:
            def __init__(self, deploy_config):
                self.deploy_config = deploy_config

        extra_config = MockDeviceConfig(MockDeployConfig(outbound_rule))

        _encrypt_header_rule_values(extra_config, _make_mock_secret_plugin())

        rule = (
            extra_config.deploy_config.outbound_operation_rule.header_operation_rules[0]
        )
        assert rule.value != "secret-token"
        assert rule.encrypt_value is True
        assert common_sm4_decrypt(rule.value, DEV_SM4_KEY) == "secret-token"

    def test_skips_plaintext_values(self):
        """Skip encryption when encrypt_value=False."""
        outbound_rule = EncryptableOutBoundRule(
            header_operation_rules=[
                EncryptableHeaderRule(
                    domains=["*.api.com"],
                    action="SET_HEADER",
                    header_name="X-Plain",
                    value="plaintext",
                    encrypt_value=False,
                )
            ]
        )

        class MockDeployConfig:
            def __init__(self, outbound_rule):
                self.outbound_operation_rule = outbound_rule

        class MockDeviceConfig:
            def __init__(self, deploy_config):
                self.deploy_config = deploy_config

        extra_config = MockDeviceConfig(MockDeployConfig(outbound_rule))

        _encrypt_header_rule_values(extra_config, _make_mock_secret_plugin())

        rule = (
            extra_config.deploy_config.outbound_operation_rule.header_operation_rules[0]
        )
        assert rule.value == "plaintext"

    def test_skips_none_config(self):
        """Handle None config gracefully."""
        _encrypt_header_rule_values(None, _make_mock_secret_plugin())

    def test_skips_sdk_outbound_rule(self):
        """Skip SDK OutBoundOperationRule (not EncryptableOutBoundRule)."""
        from secbaas.community.api.device_manage import (
            HeaderOperationRule,
            OutBoundOperationRule,
        )

        sdk_rule = OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["*.api.com"],
                    action="SET_HEADER",
                    header_name="X-SDK",
                    value="sdk-value",
                )
            ]
        )

        class MockDeployConfig:
            def __init__(self, outbound_rule):
                self.outbound_operation_rule = outbound_rule

        class MockDeviceConfig:
            def __init__(self, deploy_config):
                self.deploy_config = deploy_config

        extra_config = MockDeviceConfig(MockDeployConfig(sdk_rule))

        # Should not raise exception
        _encrypt_header_rule_values(extra_config, _make_mock_secret_plugin())

        # Value unchanged
        rule = (
            extra_config.deploy_config.outbound_operation_rule.header_operation_rules[0]
        )
        assert rule.value == "sdk-value"


class TestDecryptHeaderRuleValues:
    """Test _decrypt_header_rule_values helper function."""

    def test_decrypts_and_creates_sdk_rule(self):
        """Decrypt values and create SDK-compatible OutBoundOperationRule."""
        encrypted = common_sm4_encrypt("my-secret", DEV_SM4_KEY)
        encryptable_rule = EncryptableOutBoundRule(
            header_operation_rules=[
                EncryptableHeaderRule(
                    domains=["*.api.com"],
                    action="SET_HEADER",
                    header_name="Authorization",
                    value=encrypted,
                    encrypt_value=True,
                )
            ]
        )

        result = _decrypt_header_rule_values(
            encryptable_rule, _make_mock_secret_plugin()
        )

        from secbaas.community.api.device_manage import OutBoundOperationRule

        assert isinstance(result, OutBoundOperationRule)
        assert result.header_operation_rules[0].value == "my-secret"

    def test_passthrough_sdk_rule(self):
        """Return SDK OutBoundOperationRule as-is."""
        from secbaas.community.api.device_manage import (
            HeaderOperationRule,
            OutBoundOperationRule,
        )

        sdk_rule = OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["*.api.com"],
                    action="SET_HEADER",
                    header_name="X-Plain",
                    value="plaintext",
                )
            ]
        )

        result = _decrypt_header_rule_values(sdk_rule, _make_mock_secret_plugin())

        assert result is sdk_rule
        assert result.header_operation_rules[0].value == "plaintext"

    def test_handles_none(self):
        """Return None when input is None."""
        result = _decrypt_header_rule_values(None, _make_mock_secret_plugin())
        assert result is None
