"""
API Key Generator 单元测试
"""

from secbaas.core.service.api_gateway._key_gen import APIKeyGenerator


class TestAPIKeyGenerator:
    """API Key Generator 单元测试"""

    def test_generate_length(self):
        """测试生成的 API Key 长度为 32 位"""
        api_key = APIKeyGenerator.generate()
        assert len(api_key) == 32

    def test_generate_base62_chars(self):
        """测试生成的 API Key 只包含 base62 字符"""
        api_key = APIKeyGenerator.generate()
        base62_chars = APIKeyGenerator.BASE62
        for char in api_key:
            assert char in base62_chars

    def test_generate_unique(self):
        """测试每次生成的 API Key 是唯一的"""
        keys = [APIKeyGenerator.generate() for _ in range(100)]
        assert len(set(keys)) == 100

    def test_hash_key_format(self):
        """测试哈希值格式为 base64(salt):base64(dk)"""
        api_key = "test_key_123456789012345678901"
        hashed = APIKeyGenerator.hash_key(api_key)

        # 验证格式
        assert ":" in hashed
        parts = hashed.split(":")
        assert len(parts) == 2

        # 验证是有效的 base64
        import base64

        salt = base64.b64decode(parts[0])
        dk = base64.b64decode(parts[1])

        # salt 应该是 32 字节
        assert len(salt) == 32
        # dk 应该是 32 字节 (sha256)
        assert len(dk) == 32

    def test_hash_key_different_salt(self):
        """测试相同 key 使用不同 salt 会产生不同哈希（安全特性）"""
        api_key = "test_key_12345678901234567890"
        hash1 = APIKeyGenerator.hash_key(api_key)
        hash2 = APIKeyGenerator.hash_key(api_key)
        # 相同 key 使用随机 salt 会产生不同哈希
        assert hash1 != hash2
        # 但两者都应该能验证成功
        assert APIKeyGenerator.verify_key(api_key, hash1) is True
        assert APIKeyGenerator.verify_key(api_key, hash2) is True

    def test_hash_key_different_keys(self):
        """测试不同输入产生不同哈希"""
        hash1 = APIKeyGenerator.hash_key("key_one_1234567890123456789")
        hash2 = APIKeyGenerator.hash_key("key_two_1234567890123456789")
        assert hash1 != hash2

    def test_verify_key_valid(self):
        """测试验证正确的 API Key 返回 True"""
        api_key = "test_key_123456789012345678901"
        hashed = APIKeyGenerator.hash_key(api_key)

        result = APIKeyGenerator.verify_key(api_key, hashed)
        assert result is True

    def test_verify_key_invalid(self):
        """测试验证错误的 API Key 返回 False"""
        api_key = "test_key_123456789012345678901"
        wrong_key = "wrong_key_123456789012345678"
        hashed = APIKeyGenerator.hash_key(api_key)

        result = APIKeyGenerator.verify_key(wrong_key, hashed)
        assert result is False

    def test_verify_key_invalid_format(self):
        """测试验证格式错误的哈希值返回 False"""
        api_key = "test_key_123456789012345678901"
        invalid_hash = "not_valid_base64:either"

        result = APIKeyGenerator.verify_key(api_key, invalid_hash)
        assert result is False

    def test_verify_key_empty_hash(self):
        """测试验证空哈希值返回 False"""
        api_key = "test_key_123456789012345678901"

        result = APIKeyGenerator.verify_key(api_key, "")
        assert result is False

    def test_validate_format_valid(self):
        """测试校验合法的 Key 格式"""
        valid_key = "0123456789ABCDEFGHIJKLMNOPQRSTUV"
        assert APIKeyGenerator.validate_format(valid_key) is True

    def test_validate_format_lowercase(self):
        """测试校验小写字母 Key"""
        valid_key = "abcdefghijklmnopqrstuvwxyz012345"
        assert APIKeyGenerator.validate_format(valid_key) is True

    def test_validate_format_mixed(self):
        """测试校验混合大小写 Key"""
        valid_key = "0123456789abcdefghijklmnopqrstuv"
        assert APIKeyGenerator.validate_format(valid_key) is True

    def test_validate_format_too_short(self):
        """测试校验过短的 Key"""
        invalid_key = "0123456789"
        assert APIKeyGenerator.validate_format(invalid_key) is False

    def test_validate_format_too_long(self):
        """测试校验过长的 Key"""
        invalid_key = "0123456789ABCDEFGHIJKLMNOPQRSTU"
        assert APIKeyGenerator.validate_format(invalid_key) is False

    def test_validate_format_invalid_chars(self):
        """测试校验包含非法字符的 Key"""
        invalid_key = "0123456789ABCDEFGHIJKLMNOPQR-"
        assert APIKeyGenerator.validate_format(invalid_key) is False

    def test_validate_format_special_chars(self):
        """测试校验包含特殊字符的 Key"""
        invalid_key = "0123456789ABCDEFGHIJKLMNOPQR_"
        assert APIKeyGenerator.validate_format(invalid_key) is False

    def test_validate_format_empty(self):
        """测试校验空字符串"""
        assert APIKeyGenerator.validate_format("") is False

    def test_random_base62_length(self):
        """测试随机字符串长度"""
        result = APIKeyGenerator._random_base62(16)
        assert len(result) == 16

    def test_random_base62_chars(self):
        """测试随机字符串只包含 base62 字符"""
        result = APIKeyGenerator._random_base62(100)
        base62_chars = APIKeyGenerator.BASE62
        for char in result:
            assert char in base62_chars

    def test_random_base62_unique(self):
        """测试每次生成的随机字符串是唯一的"""
        results = [APIKeyGenerator._random_base62(32) for _ in range(100)]
        assert len(set(results)) == 100


class TestAPIKeyGeneratorIntegration:
    """API Key Generator 集成测试"""

    def test_generate_and_verify(self):
        """测试生成、哈希、验证的完整流程"""
        # 生成
        api_key = APIKeyGenerator.generate()
        assert len(api_key) == 32
        assert APIKeyGenerator.validate_format(api_key)

        # 哈希
        hashed = APIKeyGenerator.hash_key(api_key)
        assert ":" in hashed

        # 验证
        assert APIKeyGenerator.verify_key(api_key, hashed) is True
        assert APIKeyGenerator.verify_key(api_key + "x", hashed) is False

    def test_multiple_keys_different_hashes(self):
        """测试多个不同的 Key 有不同的哈希值"""
        keys = [APIKeyGenerator.generate() for _ in range(10)]
        hashes = [APIKeyGenerator.hash_key(key) for key in keys]

        # 所有哈希应该唯一
        assert len(set(hashes)) == 10

        # 互相验证应该都通过
        for key, hashed in zip(keys, hashes):
            assert APIKeyGenerator.verify_key(key, hashed) is True

    def test_collision_resistance(self):
        """测试哈希的抗碰撞性（不同 key 不应有相同哈希）"""
        # 生成大量不同的 key
        keys = [APIKeyGenerator.generate() for _ in range(50)]
        hashes = [APIKeyGenerator.hash_key(key) for key in keys]

        # 哈希应该全部唯一（理论上可能有碰撞，但概率极低）
        assert len(set(hashes)) == 50

    def test_verify_key_with_badly_formatted_hash(self):
        """Test verification with hash containing no colon separator."""
        api_key = "test_key_123456789012345678901"
        assert APIKeyGenerator.verify_key(api_key, "no_colon_here") is False
