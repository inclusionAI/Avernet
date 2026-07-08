from __future__ import annotations

import pytest

from agentclaw.community.core.bot_management.token_vault import (
    TokenVault,
    CIPHER_PREFIX,
)


class TestEncryptDecryptRoundTrip:
    def test_round_trip_with_key(self):
        vault = TokenVault(master_key="real-master-key-123")
        cipher = vault.encrypt("abcdef0123456789")
        assert cipher.startswith(CIPHER_PREFIX)
        assert vault.decrypt_or_passthrough(cipher) == "abcdef0123456789"

    def test_ciphertext_is_not_plaintext(self):
        vault = TokenVault(master_key="real-master-key-123")
        cipher = vault.encrypt("abcdef0123456789")
        assert "abcdef0123456789" not in cipher


class TestPassthroughPlaintext:
    def test_no_prefix_returns_as_is(self):
        """存量明文 token（无 enc:v1: 前缀）原样返回。"""
        vault = TokenVault(master_key="real-master-key-123")
        assert vault.decrypt_or_passthrough("abcdef0123456789") == "abcdef0123456789"

    def test_empty_string_passthrough(self):
        vault = TokenVault(master_key="real-master-key-123")
        assert vault.decrypt_or_passthrough("") == ""


class TestEncryptIdempotent:
    def test_encrypt_then_encrypt_again_yields_different_ciphertext(self):
        """加密不幂等（每次 nonce 随机）——调用方靠前缀判断是否已加密，而非重复加密。"""
        vault = TokenVault(master_key="real-master-key-123")
        c1 = vault.encrypt("abcdef0123456789")
        c2 = vault.encrypt("abcdef0123456789")
        assert c1 != c2  # 随机 nonce，但二者都能解密回同一明文
        assert vault.decrypt_or_passthrough(c1) == "abcdef0123456789"
        assert vault.decrypt_or_passthrough(c2) == "abcdef0123456789"


class TestEmptyKeyDegradation:
    """singlebox/CI: LocalSecretResolver 返 None → master_key 空 → encrypt 跳过（明文落库）。"""

    def test_empty_key_encrypt_returns_plaintext_without_prefix(self):
        vault = TokenVault(master_key="")
        result = vault.encrypt("abcdef0123456789")
        assert result == "abcdef0123456789"  # 不加密，原样返回，无前缀
        assert not result.startswith(CIPHER_PREFIX)

    def test_empty_key_decrypt_passthrough_works(self):
        vault = TokenVault(master_key="")
        assert vault.decrypt_or_passthrough("abcdef0123456789") == "abcdef0123456789"


class TestDecryptFailureContract:
    """前缀存在但解密失败时应抛 ValueError ——调用方据此决定异常处理策略。"""

    def test_wrong_key_raises_value_error(self):
        encrypted_with_other_key = (
            TokenVault(master_key="other-key-456")
            .encrypt("abcdef0123456789")
        )
        vault = TokenVault(master_key="real-master-key-123")
        with pytest.raises(ValueError):
            vault.decrypt_or_passthrough(encrypted_with_other_key)

    def test_corrupted_ciphertext_raises_value_error(self):
        vault = TokenVault(master_key="real-master-key-123")
        with pytest.raises(ValueError):
            vault.decrypt_or_passthrough(CIPHER_PREFIX + "not-valid-base64-or-ciphertext!!")


class TestEmptyPlaintextRoundTrip:
    """encrypt('') + decrypt_or_passthrough 还原空串，与 decrypt_or_passthrough('') passthrough 对称覆盖。"""

    def test_empty_plaintext_round_trip(self):
        vault = TokenVault(master_key="real-master-key-123")
        cipher = vault.encrypt("")
        assert cipher.startswith(CIPHER_PREFIX)
        assert vault.decrypt_or_passthrough(cipher) == ""


class TestPrefixConstant:
    def test_prefix_value(self):
        assert CIPHER_PREFIX == "enc:v1:"
