"""
通用工具函数
"""
import base64


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
