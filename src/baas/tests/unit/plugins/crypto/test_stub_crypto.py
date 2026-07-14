import pytest

from secbaas.community.plugins.crypto.stub import StubCryptoPlugin


@pytest.fixture
def plugin() -> StubCryptoPlugin:
    return StubCryptoPlugin()


def test_import(plugin: StubCryptoPlugin) -> None:
    assert isinstance(plugin, StubCryptoPlugin)


def test_sm4_encrypt_decrypt_roundtrip(plugin: StubCryptoPlugin) -> None:
    plaintext = "hello world"
    ciphertext = plugin.sm4_encrypt(plaintext)
    assert isinstance(ciphertext, str)
    assert len(ciphertext) > 0

    decrypted = plugin.sm4_decrypt(ciphertext)
    assert decrypted == plaintext


def test_sm4_decrypt_invalid_raises(plugin: StubCryptoPlugin) -> None:
    with pytest.raises(ValueError):
        plugin.sm4_decrypt("too_short")


def test_symmetric_encrypt_decrypt_roundtrip(plugin: StubCryptoPlugin) -> None:
    plaintext = "sensitive data"
    key = "test_key_32_bytes_xxxxxxxxxxxx"
    ciphertext = plugin.symmetric_encrypt(plaintext, key)
    assert isinstance(ciphertext, str)
    assert len(ciphertext) > 0

    decrypted = plugin.symmetric_decrypt(ciphertext, key)
    assert decrypted == plaintext


def test_symmetric_decrypt_wrong_key_raises(plugin: StubCryptoPlugin) -> None:
    ciphertext = plugin.symmetric_encrypt("data", "key-a")
    with pytest.raises(Exception):
        plugin.symmetric_decrypt(ciphertext, "key-b")


def test_jwt_generate_verify_roundtrip(plugin: StubCryptoPlugin) -> None:
    token = plugin.generate_jwt("target_id", "secret_key", ttl_seconds=120)
    assert isinstance(token, str)
    parts = token.split(".")
    assert len(parts) == 3

    is_valid, error, payload = plugin.verify_jwt(token, "secret_key")
    assert is_valid is True
    assert error is None
    assert payload is not None
    assert payload["target"] == "target_id"


def test_jwt_verify_rejects_wrong_secret(plugin: StubCryptoPlugin) -> None:
    token = plugin.generate_jwt("target_id", "secret_key")
    is_valid, error, payload = plugin.verify_jwt(token, "wrong_key")
    assert is_valid is False
    assert error is not None


def test_jwt_verify_rejects_expired(plugin: StubCryptoPlugin) -> None:
    token = plugin.generate_jwt("target_id", "secret_key", ttl_seconds=-1)
    is_valid, error, payload = plugin.verify_jwt(token, "secret_key")
    assert is_valid is False
    assert error == "Token expired"
