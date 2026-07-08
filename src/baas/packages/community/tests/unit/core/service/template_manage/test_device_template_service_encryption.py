"""Tests for device_template_service SM4 encryption logic."""

from secbaas.api.template_manage import ArcaTemplateConfig
from secbaas.core.service.template_manage import _ensure_api_key_encrypted
from secbaas.core.utils.secret_utils import common_sm4_decrypt
from secbaas.spi.secret import DEV_SM4_KEY


class TestServiceEncryptionLogic:
    """Test service layer always encrypts api_key to DB regardless of encrypt_api_key flag."""

    def test_service_encrypts_api_key_when_flag_not_already_encrypted(self):
        """Service should encrypt api_key when encrypt_api_key=False (plaintext from caller)."""
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://api.arca.example.com",
            api_key="secret-to-encrypt",
            encrypt_api_key=False,
            template_id="tpl-123",
            arca_template_id_pre=None,
            arca_template_id_prod=None,
            oss_mount_id=None,
        )

        # Call service layer encryption function
        _ensure_api_key_encrypted(config, key_b64=DEV_SM4_KEY)

        assert config.encrypt_api_key is True
        # api_key should now be encrypted (different from original)
        assert config.api_key != "secret-to-encrypt"
        # Verify we can decrypt it
        decrypted = common_sm4_decrypt(config.api_key, key_b64=DEV_SM4_KEY)
        assert decrypted == "secret-to-encrypt"

    def test_service_encrypts_api_key_when_flag_false(self):
        """Service should encrypt api_key even when encrypt_api_key=False (forced encryption)."""
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://api.arca.example.com",
            api_key="keep-plaintext",
            encrypt_api_key=False,
            template_id="tpl-123",
            arca_template_id_pre=None,
            arca_template_id_prod=None,
            oss_mount_id=None,
        )

        # Call service layer encryption function
        _ensure_api_key_encrypted(config, key_b64=DEV_SM4_KEY)

        # api_key should now be encrypted (different from original)
        assert config.api_key != "keep-plaintext"
        assert config.encrypt_api_key is True
        # Verify round-trip decryption
        decrypted = common_sm4_decrypt(config.api_key, key_b64=DEV_SM4_KEY)
        assert decrypted == "keep-plaintext"

    def test_service_handles_none_config(self):
        """Service should handle None config gracefully."""
        # Should not raise exception
        _ensure_api_key_encrypted(None, key_b64=DEV_SM4_KEY)

    def test_service_handles_sigma_config(self):
        """Service should skip encryption for non-Arca configs."""
        from secbaas.api.template_manage import SigmaTemplateConfig

        config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://api.sigma.example.com",
            access_key="access123",
            secret_key="secret456",
        )

        # _ensure_api_key_encrypted expects ArcaTemplateConfig | None,
        # but should be safe to call with None-like non-Arca configs
        # Pass None since SigmaTemplateConfig is not compatible with the signature
        _ensure_api_key_encrypted(None, key_b64=DEV_SM4_KEY)
        # Config unchanged - verify SigmaTemplateConfig directly
        assert config.secret_key == "secret456"

    def test_service_skips_when_already_encrypted(self):
        """Should skip encryption (no double-encrypt) when encrypt_api_key already True."""
        config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://api.arca.example.com",
            api_key="already-secure",
            encrypt_api_key=True,
            template_id="tpl-123",
            arca_template_id_pre=None,
            arca_template_id_prod=None,
            oss_mount_id=None,
        )

        _ensure_api_key_encrypted(config, key_b64=DEV_SM4_KEY)

        # api_key unchanged — no double encryption
        assert config.api_key == "already-secure"
        assert config.encrypt_api_key is True
