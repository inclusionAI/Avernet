from secbaas.community.spi.secret import DEV_SM4_KEY

"""Tests for device template models including SM4 encryption flag support."""

from secbaas.community.api.template_manage import ArcaTemplateConfig


class TestArcaTemplateConfigEncryptionFlag:
    """Test encrypt_api_key flag support in ArcaTemplateConfig (SM4-REQ-01)."""

    def test_encrypt_api_key_flag_default_false(self):
        """encrypt_api_key defaults to False (backward compatible)."""
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://api.arca.example.com",
            api_key="sk-plaintext123",
            template_id="tpl-123",
            arca_template_id_pre=None,
            arca_template_id_prod=None,
            oss_mount_id=None,
        )
        assert config.encrypt_api_key is False
        assert config.api_key == "sk-plaintext123"

    def test_encrypt_api_key_flag_explicit_true(self):
        """encrypt_api_key can be set to True (to be encrypted by service layer)."""
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://api.arca.example.com",
            api_key="sk-secret456",
            encrypt_api_key=True,  # Service layer will encrypt this
            template_id="tpl-123",
            arca_template_id_pre=None,
            arca_template_id_prod=None,
            oss_mount_id=None,
        )
        assert config.encrypt_api_key is True
        # Before service layer processing, value is still plaintext
        assert config.api_key == "sk-secret456"

    def test_api_key_not_auto_decrypted_in_model(self):
        """Model layer does NOT auto-transform (service layer handles both encrypt/decrypt)."""
        from secbaas.community.core.utils.secret_utils import common_sm4_encrypt

        # Simulating already-encrypted value (after service layer encryption)
        encrypted = common_sm4_encrypt("original-api-key", DEV_SM4_KEY)
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://api.arca.example.com",
            api_key=encrypted,
            encrypt_api_key=True,
            template_id="tpl-123",
            arca_template_id_pre=None,
            arca_template_id_prod=None,
            oss_mount_id=None,
        )
        # Model layer keeps value as-is (no auto-decrypt)
        assert config.api_key == encrypted
        assert "original-api-key" not in config.api_key
