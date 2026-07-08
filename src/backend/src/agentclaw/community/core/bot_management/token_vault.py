"""Token 字段落库对称加解密（AES-GCM）—— 供应商无关。

token 字段持久化时的对称加解密。加密复用既有 ``secret_utils.symmetric_encrypt/
decrypt``（AES-GCM，SHA-256 派生 key），不引新依赖。（主要使用方是把外部平台的
token 落库前加密，但本类本身与具体平台无关。）

密文带 ``enc:v1:`` 前缀，使读端可区分「新密文」与「存量明文」（passthrough），
实现零迁移兼容。

MasterKey 由 token-vault provider 经 ``SecretResolver`` 从密钥库解析。
singlebox/CI 下 ``LocalSecretResolver`` 返 None → master_key 空 → encrypt 跳过
（明文落库无前缀），与 ``outbound_rules`` 单 box aeskey 空跳过同形。
"""
from __future__ import annotations

from agentclaw.community.utils.secret_utils import symmetric_decrypt, symmetric_encrypt

CIPHER_PREFIX = "enc:v1:"


class TokenVault:
    """Token 字段对称加解密器（供应商无关）。

    master_key 为空时（singlebox/CI），encrypt 原样返回明文（不加前缀），
    decrypt_or_passthrough 对无前缀串原样返回 —— 保证本地联调不依赖 密钥库。
    """

    def __init__(self, master_key: str) -> None:
        self._master_key = master_key

    def encrypt(self, plaintext_token: str) -> str:
        """加密 token，返回 ``enc:v1:<密文>``；master_key 空则原样返回明文。"""
        if not self._master_key:
            return plaintext_token
        return CIPHER_PREFIX + symmetric_encrypt(plaintext_token, self._master_key)

    def decrypt_or_passthrough(self, stored: str) -> str:
        """有 ``enc:v1:`` 前缀则解密；否则原样返回（存量明文 / 空值兼容）。

        Raises:
            ValueError: 前缀存在但解密失败（密钥错误 / 数据损坏）——由调用方承接。
        """
        if not stored or not stored.startswith(CIPHER_PREFIX):
            return stored
        ciphertext = stored[len(CIPHER_PREFIX):]
        return symmetric_decrypt(ciphertext, self._master_key)
