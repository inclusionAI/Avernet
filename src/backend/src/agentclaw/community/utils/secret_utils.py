import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def generate_jwt_token(target: str, secret_key: str, ttl: int = 120) -> str:
    """生成 nginx 代理鉴权 JWT token。

    Args:
        target: 目标文本
        secret_key: 签名密钥
        ttl: token 有效期（秒），默认 360000

    Returns:
        JWT token 字符串
    """
    header = _b64url_encode(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    payload = _b64url_encode(json.dumps({
        'target': target,
        'exp': int(time.time()) + ttl,
    }).encode())
    signing_input = f'{header}.{payload}'
    signature = hmac.new(secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f'{header}.{payload}.{_b64url_encode(signature)}'


def _b64url_decode(data: str) -> bytes:
    """Base64 URL 安全解码。"""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += '=' * padding
    return base64.urlsafe_b64decode(data)


def verify_jwt_token(token: str, secret_key: str) -> tuple[bool, Optional[str], Optional[dict]]:
    """验证 nginx 代理鉴权 JWT token。

    Args:
        token: JWT token 字符串
        secret_key: 签名密钥

    Returns:
        tuple[bool, Optional[str], Optional[dict]]: (是否验证通过, 错误信息, payload字典)
    """
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return False, "Invalid token format", None

        header_b64, payload_b64, signature_b64 = parts
        signing_input = f'{header_b64}.{payload_b64}'

        # 验证签名
        expected_signature = hmac.new(
            secret_key.encode(), signing_input.encode(), hashlib.sha256
        ).digest()
        actual_signature = _b64url_decode(signature_b64)

        if not hmac.compare_digest(expected_signature, actual_signature):
            return False, "Invalid signature", None

        # 解析 payload
        payload = json.loads(_b64url_decode(payload_b64).decode())

        # 检查过期时间
        if 'exp' in payload and payload['exp'] > int(time.time()):
            return False, "Token expired", payload

        return True, None, payload

    except Exception as e:
        return False, f"Token verification failed: {e}", None


def symmetric_encrypt(plaintext: str, secret_key: str) -> str:
    """使用 AES-GCM 对称加密字符串。

    Args:
        plaintext: 待加密的明文字符串
        secret_key: 加密密钥（任意长度，会通过 SHA-256 派生为 256 位密钥）

    Returns:
        Base64 URL 安全编码的密文字符串，格式为: nonce(12字节) + ciphertext + tag(16字节)
    """
    # 从 secret_key 派生 256 位密钥
    key = hashlib.sha256(secret_key.encode()).digest()
    aesgcm = AESGCM(key)

    # 生成 12 字节的随机 nonce
    nonce = os.urandom(12)

    # 加密
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)

    # 返回 nonce + ciphertext (Base64 URL 安全编码)
    return _b64url_encode(nonce + ciphertext)


def symmetric_decrypt(ciphertext: str, secret_key: str) -> str:
    """使用 AES-GCM 对称解密字符串。

    Args:
        ciphertext: Base64 URL 安全编码的密文字符串
        secret_key: 解密密钥（需与加密时使用的密钥相同）

    Returns:
        解密后的明文字符串

    Raises:
        ValueError: 解密失败（密钥错误或数据损坏）
    """
    # 从 secret_key 派生 256 位密钥
    key = hashlib.sha256(secret_key.encode()).digest()
    aesgcm = AESGCM(key)

    # Base64 URL 安全解码（补齐 padding）
    padding = 4 - len(ciphertext) % 4
    if padding != 4:
        ciphertext += '=' * padding
    data = base64.urlsafe_b64decode(ciphertext)

    # 提取 nonce (前 12 字节) 和 ciphertext
    nonce = data[:12]
    ct = data[12:]

    # 解密
    try:
        plaintext = aesgcm.decrypt(nonce, ct, None)
        return plaintext.decode()
    except Exception as e:
        raise ValueError(f"解密失败: {e}")


# NOTE: get_secret_from_mist / get_kv_from_mist moved to
# ``plugins/prod/mist_secret.py`` (B11 Phase A) — they wrapped the corp Mist
# manager (``plugins.prod.layotto``) from this community-shared util, a core→prod
# back-edge. Community/test code reads secrets through the injected
# ``SecretResolver`` seam; corp code imports them from ``plugins.prod.mist_secret``.
