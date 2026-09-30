import base64
import hashlib
import hmac
import re
import secrets


class APIKeyGenerator:
    """
    Key 格式：
      API Key : {32 位 base62 随机}

    示例：
      xK9mP2nQ8rL4vT6wY1zA3bC
    """

    BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

    @classmethod
    def generate(cls) -> str:
        """
        生成单个 API Key
        返回 api_key 字符串
        """
        # 生成 api_key（32 位 base62）
        return f"{cls._random_base62(32)}"

    @classmethod
    def hash_key(cls, api_key: str) -> str:
        """
        存储用哈希：PBKDF2 + salt
        比纯 sha256 更安全，防暴力破解
        """
        salt = secrets.token_bytes(32)
        dk = hashlib.pbkdf2_hmac(
            hash_name="sha256", password=api_key.encode(), salt=salt, iterations=100_000
        )
        # 格式：base64(salt):base64(dk)
        return base64.b64encode(salt).decode() + ":" + base64.b64encode(dk).decode()

    @classmethod
    def verify_key(cls, api_key: str, stored_hash: str) -> bool:
        """验证 API Key（恒定时间比较，防时序攻击）"""
        try:
            salt_b64, dk_b64 = stored_hash.split(":")
            salt = base64.b64decode(salt_b64)
            dk = base64.b64decode(dk_b64)

            new_dk = hashlib.pbkdf2_hmac(
                hash_name="sha256",
                password=api_key.encode(),
                salt=salt,
                iterations=100_000,
            )
            # 恒定时间比较，防止时序攻击
            return hmac.compare_digest(dk, new_dk)
        except Exception:
            return False

    @classmethod
    def _random_base62(cls, length: int) -> str:
        """生成指定长度的 base62 随机字符串"""
        return "".join(secrets.choice(cls.BASE62) for _ in range(length))

    @staticmethod
    def validate_format(api_key: str) -> bool:
        """校验 Key 格式合法性"""
        pattern = r"^[0-9A-Za-z]{32}$"
        return bool(re.match(pattern, api_key))
