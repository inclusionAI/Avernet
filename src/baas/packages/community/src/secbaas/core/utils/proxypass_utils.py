"""Platform-independent proxypass URL and JWT utilities.

These functions are used by multiple PaaS platforms (ARCA, TeClaw, Poolab, LOCAL)
to construct agentclawproxy URLs and HS256 JWT tokens.  They have zero dependency
on the DI container so are safe to import from ``core.utils``.
"""

from secbaas.core.utils import secret_utils
from secbaas.core.utils.env_utils import get_current_env


def build_proxypass_url(target: str, path: str, scheme: str = "wss") -> str:
    """构造 proxypass 代理 URL。

    平台无关的工具函数，根据当前环境返回对应的 agentclawproxy URL。
    TeClaw、Poolab、ARCA、LOCAL 平台均可使用。

    Args:
        target: 目标标识，格式为 {TYPE}_{device_id}@{template_id}:{port}
            或 {TYPE}_{device_id}:{port}（当 template_id 不可用时）
        path: 请求路径，如 /ws 或 /api/v1/invoke
        scheme: 协议方案，默认为 "wss"（WebSocket）；
            对于 HTTP 调用使用 "https"

    Returns:
        完整的 proxypass URL，格式为
        {scheme}://agentclawproxy-{env}.alipay.com/proxypass/{target}{path}

    Example:
        >>> build_proxypass_url("TECLAW_bot123@42:20003", "/ws")
        'wss://agentclawproxy-dev.alipay.com/proxypass/TECLAW_bot123@42:20003/ws'
    """
    env = get_current_env()
    if env == "pre":
        host = "agentclawproxy-pre.alipay.com"
    elif env == "prod":
        host = "agentclawproxy-prod.alipay.com"
    else:
        host = "agentclawproxy-dev.alipay.com"
    return f"{scheme}://{host}/proxypass/{target}{path}"


def generate_proxypass_jwt(target: str, key: str, ttl: int = 300) -> str:
    """生成 proxypass JWT token。

    平台无关的工具函数，使用给定的密钥对 target 进行 HS256 签名。
    TeClaw、Poolab、ARCA、LOCAL 平台均可使用。

    Args:
        target: 目标标识，格式为 {TYPE}_{device_id}@{template_id}:{port}
            或 {TYPE}_{device_id}:{port}（当 template_id 不可用时）
        key: HS256 签名密钥。
        ttl: token 有效期（秒），默认 300。调用方应根据自身场景
            选择合适的 TTL（如 WS relay 使用 120s）。

    Returns:
        HS256 签名的 JWT token 字符串，包含 target claim 和 exp claim。

    Example:
        >>> generate_proxypass_jwt("TECLAW_bot123@42:20003", "my-secret-key")
        'eyJhbGciOiJIUzI1NiIs...'
    """
    return secret_utils.generate_jwt_token(target, key, ttl=ttl)
