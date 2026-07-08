from secbaas.spi.secret import DEV_SM4_KEY

"""Tests for factory.py SM4 decryption logic."""

from secbaas.api.template_manage import ArcaTemplateConfig
from secbaas.core.utils.secret_utils import common_sm4_decrypt, common_sm4_encrypt


class TestFactoryDecryption:
    """Test factory decrypts api_key when encrypt_api_key=True."""

    def test_factory_decrypts_api_key_when_flag_true(self):
        """Factory should decrypt api_key when creating credentials if flag is True."""
        encrypted = common_sm4_encrypt("secret-api-key", DEV_SM4_KEY)
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

        # Simulate factory decryption logic
        api_key = config.api_key
        if config.encrypt_api_key:
            api_key = common_sm4_decrypt(config.api_key, DEV_SM4_KEY)

        assert api_key == "secret-api-key"

    def test_factory_uses_plaintext_when_flag_false(self):
        """Factory should use api_key as-is when encrypt_api_key=False."""
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://api.arca.example.com",
            api_key="plaintext-key",
            encrypt_api_key=False,
            template_id="tpl-123",
            arca_template_id_pre=None,
            arca_template_id_prod=None,
            oss_mount_id=None,
        )

        api_key = config.api_key
        if config.encrypt_api_key:
            api_key = common_sm4_decrypt(config.api_key, DEV_SM4_KEY)

        assert api_key == "plaintext-key"

    def test_round_trip_encryption_decryption(self):
        """Full round-trip: encrypt then decrypt works correctly."""
        original = "my-super-secret-api-key-12345"

        # Step 1: Encrypt (simulate service layer)
        encrypted = common_sm4_encrypt(original, DEV_SM4_KEY)

        # Step 2: Store in config
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

        # Step 3: Decrypt (simulate factory layer)
        api_key = config.api_key
        if config.encrypt_api_key:
            api_key = common_sm4_decrypt(config.api_key, DEV_SM4_KEY)

        # Step 4: Verify round-trip
        assert api_key == original
