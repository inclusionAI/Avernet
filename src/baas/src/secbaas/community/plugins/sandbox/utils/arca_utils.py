"""Arca 相关工具函数。"""

from typing import Any, cast

import httpx

from secbaas.community.config import ConfigPath, get_config, get_config_by_path
from secbaas.community.core.utils import secret_utils
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

logger = get_logger(__name__)

_ARCA_PROXY_HOST_MAP: dict[str, ConfigPath] = {
    "dev": ConfigPath.AGENTCLAW_PROXY_HOST_DEV,
    "pre": ConfigPath.AGENTCLAW_PROXY_HOST_PRE,
    "prod": ConfigPath.AGENTCLAW_PROXY_HOST_PROD,
}

# Bolt 服务端口
BOLT_PORT = 20003

# Mist 密钥名称
PROXYPASS_SECRET_NAME = "other_manual_agentclawproxy_proxypass_secret"

# Arca 设备上的 OSS 挂载路径（本地技能目录）
ARCA_SKILLS_LOCAL_DIR = "/home/admin/.extra-skills/skills-local"


# ── Internal Helpers ──────────────────────────────────────────────────────────


def _get_arca_proxy_base_url() -> str:
    """获取 Arca 代理服务基础 URL。

    根据当前环境从配置读取对应的基础 URL。

    Returns:
        基础 URL 字符串
    """
    env = get_current_env()
    path = _ARCA_PROXY_HOST_MAP.get(env, ConfigPath.AGENTCLAW_PROXY_HOST_DEV)
    host = get_config_by_path(get_config(), path)
    return f"https://{host}"


def _get_arca_target(
    sandbox_id: str, port: int | None = None, template_id: int | None = None
) -> str:
    """获取 Arca target 标识。

    Args:
        sandbox_id: Arca sandbox ID
        port: 目标端口号，默认为 BOLT_PORT (20003)
        template_id: 可选的模板 ID。提供时，target 包含 @template_id 后缀，
            格式为 ARCA_{sandbox_id}@{template_id}:{port}，
            供 agentclawproxy 在多租户场景中正确解析 Arca API key。

    Returns:
        target 字符串，格式为 ARCA_{sandbox_id}:{port}
        或 ARCA_{sandbox_id}@{template_id}:{port}（当提供 template_id 时）
    """
    effective_port = port if port is not None else BOLT_PORT
    if template_id is not None:
        return f"ARCA_{sandbox_id}@{template_id}:{effective_port}"
    return f"ARCA_{sandbox_id}:{effective_port}"


def _get_proxypass_token(
    sandbox_id: str,
    port: int | None = None,
    template_id: int | None = None,
    ttl: int = 300,
) -> str:
    """获取代理通行 token。

    Args:
        sandbox_id: Arca sandbox ID
        port: 目标端口号，默认为 BOLT_PORT (20003)
            当通过 resolve_ws_conn_info 或 resolve_invoke_http_info 获取
            特定端口的代理 token 时，应传入与 URL target 一致的 port，
            避免 token target 与 URL target 不匹配导致 agentclawproxy 拒绝请求。
        template_id: 可选的模板 ID。提供时，JWT token 的 target claim 会包含
            @template_id 后缀，供 agentclawproxy 在多租户场景中正确解析
            Arca API key。
        ttl: token 有效期（秒），默认 300。WS relay 场景应传 120s
            以与 expires_at 保持一致。

    Returns:
        JWT token 字符串
    """
    from secbaas.community.bootstrap import get_container

    target = _get_arca_target(sandbox_id, port=port, template_id=template_id)
    key = get_container().plugins.secret_plugin().get_secret(PROXYPASS_SECRET_NAME)
    return secret_utils.generate_jwt_token(target, key, ttl=ttl)


# Re-exported from core.utils for backward compatibility.
# New code should import directly from secbaas.community.core.utils.proxypass_utils.
from secbaas.community.core.utils.proxypass_utils import (  # noqa: E402, F401
    build_proxypass_url as _build_proxypass_url,
)


def _generate_proxypass_jwt(target: str, ttl: int = 300) -> str:
    """生成 proxypass JWT token（从 DI 容器获取密钥的便捷封装）。

    平台无关的工具函数，使用 MIST 中的 proxypass 密钥对 target
    进行 HS256 签名。TeClaw、Poolab、ARCA 平台均可使用。

    Args:
        target: 目标标识，格式为 {TYPE}_{device_id}@{template_id}:{port}
            或 {TYPE}_{device_id}:{port}（当 template_id 不可用时）
        ttl: token 有效期（秒），默认 300。调用方应根据自身场景
            选择合适的 TTL（如 WS relay 使用 120s）。

    Returns:
        HS256 签名的 JWT token 字符串，包含 target claim 和 exp claim。
    """
    from secbaas.community.bootstrap import get_container

    key = get_container().plugins.secret_plugin().get_secret(PROXYPASS_SECRET_NAME)
    return secret_utils.generate_jwt_token(target, key, ttl=ttl)


def _get_proxypass_headers(sandbox_id: str) -> dict[str, str]:
    """获取代理通行请求头。

    Args:
        sandbox_id: Arca sandbox ID

    Returns:
        包含 x-proxypass-token 的请求头字典
    """
    return {"x-proxypass-token": _get_proxypass_token(sandbox_id)}


def _get_bolt_url(sandbox_id: str, api_path: str) -> str:
    """构建 Bolt API 完整 URL。

    Args:
        sandbox_id: Arca sandbox ID
        api_path: API 路径，如 /api/file/read

    Returns:
        完整的 Bolt API URL
    """
    target = _get_arca_target(sandbox_id)
    base_url = _get_arca_proxy_base_url()
    return f"{base_url}/proxypass/{target}{api_path}"


# ── File API ──────────────────────────────────────────────────────────────────


async def upload_to_arca(
    content: bytes,
    file_path: str,
    sandbox_id: str,
) -> dict[str, Any]:
    """上传文件内容到 Arca (Bolt) 服务。

    通过 HTTP API 将文件上传到 Arca 服务的 Bolt 组件。

    Args:
        content: 文件内容的字节流
        file_path: 目标文件路径（在 Bolt 服务中的路径）
        sandbox_id: Arca sandbox ID

    Returns:
        包含上传结果的字典

    Raises:
        ValueError: sandbox_id 为空时
        Exception: 上传失败时

    Example:
        result = await upload_to_arca(
            content=b"file content",
            file_path="/data/test.txt",
            sandbox_id="xxx"
        )
    """
    if sandbox_id is None:
        raise ValueError("Sandbox ID is required")

    bolt_url = _get_bolt_url(sandbox_id, "/api/file/upload")
    logger.info(f"[arca_utils.upload_to_arca] bolt_url: {bolt_url}")

    # Prepare file and data
    files = {"file": (file_path, content)}
    data = {"target_path": file_path}
    headers = _get_proxypass_headers(sandbox_id)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                bolt_url, files=files, data=data, headers=headers
            )
            response.raise_for_status()
            result = response.json()
            logger.info(
                f"[arca_utils.upload_to_arca] Success: uploaded to {file_path}, result {result}"
            )
            return cast(dict[str, Any], result)
    except Exception as e:
        logger.error(f"[arca_utils.upload_to_arca] Failed: {e}")
        raise
