import pytest

from secbaas.plugins.crypto.stub import StubCryptoPlugin
from secbaas.spi.crypto import CryptoPlugin
from secbaas.spi.secret import DEV_SM4_KEY


class CryptoPluginContract:
    """Abstract conformance test contract for CryptoPlugin implementations.

    Every CryptoPlugin (Stub, Real) must pass these tests.
    """

    plugin: CryptoPlugin

    def test_sm4_roundtrip(self) -> None:
        plaintext = "hello world"
        ct = self.plugin.sm4_encrypt(plaintext)
        assert isinstance(ct, str)
        assert ct != plaintext
        decrypted = self.plugin.sm4_decrypt(ct)
        assert decrypted == plaintext

    def test_sm4_empty_string(self) -> None:
        ct = self.plugin.sm4_encrypt("")
        decrypted = self.plugin.sm4_decrypt(ct)
        assert decrypted == ""

    def test_sm4_decrypt_invalid_raises(self) -> None:
        with pytest.raises(Exception):
            self.plugin.sm4_decrypt("invalid_base64!!")

    def test_symmetric_roundtrip(self) -> None:
        plaintext = "sensitive data"
        key = "my-secret-encryption-key"
        ct = self.plugin.symmetric_encrypt(plaintext, key)
        assert isinstance(ct, str)
        assert ct != plaintext
        decrypted = self.plugin.symmetric_decrypt(ct, key)
        assert decrypted == plaintext

    def test_symmetric_wrong_key_raises(self) -> None:
        ct = self.plugin.symmetric_encrypt("data", "key-a")
        with pytest.raises(Exception):
            self.plugin.symmetric_decrypt(ct, "key-b")

    def test_jwt_generate_verify_roundtrip(self) -> None:
        token = self.plugin.generate_jwt("target", "secret", ttl_seconds=3600)
        assert isinstance(token, str)
        parts = token.split(".")
        assert len(parts) == 3

        is_valid, error, payload = self.plugin.verify_jwt(token, "secret")
        assert is_valid is True
        assert error is None
        assert payload["target"] == "target"

    def test_jwt_verify_rejects_wrong_secret(self) -> None:
        token = self.plugin.generate_jwt("target", "secret-a")
        is_valid, error, _ = self.plugin.verify_jwt(token, "secret-b")
        assert is_valid is False

    def test_jwt_verify_rejects_expired(self) -> None:
        token = self.plugin.generate_jwt("target", "secret", ttl_seconds=-1)
        is_valid, error, _ = self.plugin.verify_jwt(token, "secret")
        assert is_valid is False

    def test_jwt_verify_rejects_malformed(self) -> None:
        is_valid, error, _ = self.plugin.verify_jwt("not.a.jwt.token.extra", "secret")
        assert is_valid is False


class TestStubCryptoPlugin(CryptoPluginContract):
    def setup_method(self) -> None:
        self.plugin = StubCryptoPlugin()


class TestRealCryptoPluginConformance(CryptoPluginContract):
    @pytest.fixture(autouse=True)
    def _skip_without_gmssl(self) -> None:
        try:
            import gmssl  # noqa: F401
        except ImportError:
            pytest.skip("gmssl not installed")

    def setup_method(self) -> None:
        from secbaas.plugins.crypto.real import RealCryptoPlugin
        from secbaas.plugins.secret.stub import StubSecretStorePlugin

        secret_store = StubSecretStorePlugin(
            secrets={"other_manual_secbaas_common_symmetric": DEV_SM4_KEY}
        )
        self.plugin = RealCryptoPlugin(secret_store, env="local")
