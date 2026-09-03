"""
通用工具函数
"""
import base64
import re

_MANAGED_SESSION_KEY = re.compile(
    r"^(?:agent:([^:]+):)?(session:[^:]+:user:[^:]+)$",
    re.IGNORECASE,
)


def normalize_managed_session_lookup_key(session_key: str) -> str:
    """Normalize only the relative session key returned by OCB create."""
    match = _MANAGED_SESSION_KEY.fullmatch(session_key)
    if match is not None and match.group(1) is None:
        return session_key.lower()
    return session_key


def managed_session_keys_equal(candidate: str, requested: str) -> bool:
    """Match relative OCB keys with their agent-scoped OpenClaw form."""
    if candidate == requested:
        return True

    candidate_match = _MANAGED_SESSION_KEY.fullmatch(candidate)
    requested_match = _MANAGED_SESSION_KEY.fullmatch(requested)
    if candidate_match is None or requested_match is None:
        return False

    candidate_agent, candidate_relative = candidate_match.groups()
    requested_agent, requested_relative = requested_match.groups()
    if (candidate_agent is None) == (requested_agent is None):
        return False
    return candidate_relative.lower() == requested_relative.lower()


def encode_session_key(session_key: str) -> str:
    """
    URL-safe base64 编码 session key

    用于前端对 session key 进行编码，避免 URL 路径中的特殊字符问题。
    """
    return base64.urlsafe_b64encode(session_key.encode()).decode().rstrip('=')


def decode_session_key(session_id: str) -> str:
    """
    兼容处理 session key 解码

    - 如果 session_id 包含冒号（原始格式），直接返回
    - 否则尝试 base64 解码
    - 解码失败则返回原值
    """
    # 原始 session key 包含冒号，base64 编码后不包含
    if ':' in session_id:
        return session_id

    # 尝试 base64 解码
    original = session_id
    try:
        padding = 4 - len(session_id) % 4
        if padding != 4:
            session_id += '=' * padding
        return base64.urlsafe_b64decode(session_id).decode()
    except Exception:
        return original
