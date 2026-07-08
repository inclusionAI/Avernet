"""
测试 session key 编解码工具函数
"""
import pytest

from engine.community.shared.utils import decode_session_key, encode_session_key


class TestEncodeSessionKey:
    """测试 encode_session_key 函数"""

    def test_encode_simple_session_key(self) -> None:
        """测试简单 session key 编码"""
        session_key = "session:abc123"
        encoded = encode_session_key(session_key)
        assert ":" not in encoded
        assert len(encoded) > 0

    def test_encode_session_key_with_slash(self) -> None:
        """测试包含斜杠的 session key 编码"""
        session_key = "session:abc/123/def"
        encoded = encode_session_key(session_key)
        assert "/" not in encoded
        assert ":" not in encoded

    def test_encode_session_key_with_special_chars(self) -> None:
        """测试包含特殊字符的 session key 编码"""
        session_key = "agent:main:session:test-session-id"
        encoded = encode_session_key(session_key)
        assert ":" not in encoded
        # 验证是有效的 base64 字符串
        import base64
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += "=" * padding
        decoded = base64.urlsafe_b64decode(encoded).decode()
        assert decoded == session_key

    def test_encode_empty_string(self) -> None:
        """测试空字符串编码"""
        assert encode_session_key("") == ""


class TestDecodeSessionKey:
    """测试 decode_session_key 函数"""

    def test_decode_unencoded_key_with_colon(self) -> None:
        """测试未编码的原始 key（包含冒号）直接返回"""
        session_key = "session:abc123:user:456"
        assert decode_session_key(session_key) == session_key

    def test_decode_encoded_key(self) -> None:
        """测试编码后的 key 正确解码"""
        original = "session:abc123"
        encoded = encode_session_key(original)
        decoded = decode_session_key(encoded)
        assert decoded == original

    def test_decode_encoded_key_with_slash(self) -> None:
        """测试编码后包含斜杠原始内容的 key 解码"""
        original = "session:abc/123/def"
        encoded = encode_session_key(original)
        decoded = decode_session_key(encoded)
        assert decoded == original

    def test_decode_invalid_base64_returns_original(self) -> None:
        """测试无效 base64 字符串返回原值"""
        invalid = "not-valid-base64!!!"
        decoded = decode_session_key(invalid)
        assert decoded == invalid

    def test_decode_empty_string(self) -> None:
        """测试空字符串解码"""
        assert decode_session_key("") == ""

    def test_decode_already_has_padding(self) -> None:
        """测试已有 padding 的编码字符串"""
        import base64
        original = "session:abc"
        # 手动添加 padding
        encoded_with_padding = base64.urlsafe_b64encode(original.encode()).decode()
        decoded = decode_session_key(encoded_with_padding)
        assert decoded == original


class TestEncodeDecodeRoundtrip:
    """测试编码-解码往返"""

    @pytest.mark.parametrize("session_key", [
        "session:abc123",
        "session:abc/123/def",
        "agent:main:session:test-session-id",
        "session:uuid:user:123456",
        "a:b:c:d:e:f",
    ])
    def test_roundtrip(self, session_key: str) -> None:
        """测试各种 session key 编码后能正确解码还原"""
        encoded = encode_session_key(session_key)
        decoded = decode_session_key(encoded)
        assert decoded == session_key
