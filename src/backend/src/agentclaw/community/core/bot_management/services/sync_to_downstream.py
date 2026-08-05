"""
sync_to_downstream — 下游同步模块

将 Bot 数据初始化结果同步到 ECB（知识图谱）和 BCS Fuse（向量索引）。
由 DataInitService 直接调用，不再作为独立脚本运行。
"""
from __future__ import annotations

import json
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from agentclaw.community.log import get_logger

logger = get_logger()


# 下游服务地址（ECB / BCSFuse）由调用方从 EcbConfig / BcsFuseConfig 传入
# （社区构建默认空字符串，此时下游同步会因空 URL 请求失败而降级 —
# sync_to_* 已包裹重试/兜底）。


# HTTP 超时（秒）— 真实 BCSFuse 处理较慢，需要足够的超时时间
HTTP_TIMEOUT_SECONDS = 15
MAX_RETRIES = 3
# Base of the exponential backoff between retries: attempt N waits
# ``RETRY_BACKOFF_BASE_SECONDS * 2 ** (N - 1)``. Named rather than inlined so a
# test asserting retry *counts* can collapse the waits without patching
# ``time.sleep`` process-wide.
RETRY_BACKOFF_BASE_SECONDS = 1.0

# link_type → MCP server code 映射
_LINK_TYPE_TO_MCP_CODE = {
    "yuque": "skylarkmcpserver",
    "dima": "dimamcpserver",
    "antcode": "antcodemcpserver",
}


# link_type 规范化（预留，当前无需转换）
_LINK_TYPE_NORMALIZE: dict[str, str] = {}


def _post_json(url: str, payload: dict, timeout: int = HTTP_TIMEOUT_SECONDS) -> dict:
    """发送 JSON POST 请求。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")

    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise Exception(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise Exception(f"URL Error: {exc.reason}") from exc


def _get_bot_info(bot_id: str, owner_id: str, bot_service) -> dict:
    """通过管理接口获取 Bot 信息（bot_name、skill_sets）。"""
    try:
        bot = bot_service.get_bot(bot_id, owner_id)

        ext = bot.get("ext") or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except json.JSONDecodeError:
                ext = {}

        skill_sets = []
        raw_skill_sets = bot.get("skill_sets") or ext.get("skill_sets") or []
        if isinstance(raw_skill_sets, list):
            for skill_set in raw_skill_sets:
                if isinstance(skill_set, dict) and skill_set.get("name"):
                    skill_sets.append(skill_set)
                elif isinstance(skill_set, str):
                    skill_sets.append({"name": skill_set})

        return {
            "bot_name": bot.get("bot_name", ""),
            "skill_sets": skill_sets,
        }
    except Exception as exc:
        logger.warning(f"[_get_bot_info] Failed to get bot info: {exc}")
        return {"bot_name": bot_id, "skill_sets": []}


def _get_link_resources(bot_id: str, repository) -> list[dict]:
    """通过 ResourceService 获取 Bot 的 LINK 资源。

    Args:
        bot_id: Bot ID
        repository: ResourceRepositoryProtocol instance, supplied by caller (DI'd).
    """
    try:
        from agentclaw.community.core.resources.service import ResourceService

        service = ResourceService(bot_id=bot_id, repository=repository)
        resources = service.list_resources(resource_type=None)

        return [
            {
                "name": r.name,
                "url": r.attributes.get("url", ""),
                "link_type": r.attributes.get("link_type", ""),
                "id": str(r.id),
            }
            for r in resources
            if r.is_link
        ]
    except Exception as exc:
        logger.warning(f"[_get_link_resources] Failed to get link resources: {exc}")
        return []


def sync_to_ecb(
    bot_id: str, owner_id: str, summary: dict, resource_repo, base_url: str = ""
) -> dict:
    """同步到 ECB 知识图谱。

    POST /api/v1/bindings — BotBindingRequest 格式。
    自动获取 link_resources 构建 MCP bindings。

    ``resource_repo``: ResourceRepositoryProtocol instance, supplied by caller.
    ``base_url``: ECB host from EcbConfig (corp env overlays). Empty ⇒ the ECB
    request fails on the empty URL and degrades (retry/fallback wrap).
    """
    try:
        url = f"{base_url}/api/v1/bindings"

        link_resources = _get_link_resources(bot_id, resource_repo)

        # 按 link_type 分组构建 MCP binding
        links_by_type: dict[str, list[dict]] = {}
        for link in link_resources:
            link_type = link.get("link_type", "")
            normalized = _LINK_TYPE_NORMALIZE.get(link_type, link_type)
            if link_type:
                link_with_type = {**link, "link_type": normalized}
                links_by_type.setdefault(normalized, []).append(link_with_type)

        mcp_bindings = []
        for link_type, links in links_by_type.items():
            mcp_code = _LINK_TYPE_TO_MCP_CODE.get(link_type, "")
            if not mcp_code:
                continue

            link_names = [lk.get("name", "") for lk in links if lk.get("name")]
            filter_obj = {
                "link_type": link_type,
                "links": [
                    {"name": lk.get("name", ""), "url": lk.get("url", "")}
                    for lk in links
                ],
            }
            mcp_bindings.append({
                "mcp_name": mcp_code,
                "mcp_description": f"{link_type} 数据源: {', '.join(link_names)}",
                "mcp_filter": json.dumps(filter_obj, ensure_ascii=False),
                "executable_resources": link_names,
            })

        ecb_payload = {
            "bot_id": bot_id,
            "bot_summary": summary.get("capability", "") or summary.get("role", ""),
            "staff_no": owner_id,
            "mcps": mcp_bindings,
        }

        logger.info(f"[sync_to_ecb] POST {url} with {len(mcp_bindings)} MCP bindings ({len(link_resources)} links)")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = _post_json(url, ecb_payload)
                success = response.get("success", False)
                logger.info(f"[sync_to_ecb] Response (attempt {attempt}): success={success}")
                return {"success": success, "response": response}
            except Exception as exc:
                logger.warning(f"[sync_to_ecb] Attempt {attempt}/{MAX_RETRIES} failed: {exc}")
                if attempt == MAX_RETRIES:
                    return {"success": False, "error": str(exc)}
                delay = RETRY_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
                logger.info(f"[sync_to_ecb] Retrying in {delay}s...")
                time.sleep(delay)
    except Exception as exc:
        logger.error(f"[sync_to_ecb] Unexpected error: {exc}", exc_info=True)
        return {"success": False, "error": str(exc)}

    return {"success": False, "error": "Unexpected code path"}


def sync_to_bcsfuse(
    bot_id: str, owner_id: str, summary: dict, bot_service, base_url: str = ""
) -> dict:
    """同步到 BCS Fuse 向量索引。

    POST /v1/workers/{bot_uuid}/sync
    自动获取 bot_name 和 skill_sets。

    ``base_url``: BCSFuse host from BcsFuseConfig (corp env overlays). Empty ⇒
    the sync request fails on the empty URL and degrades (retry/fallback wrap).
    """
    try:
        url = f"{base_url}/v1/workers/{bot_id}/sync"

        bot_info = _get_bot_info(bot_id, owner_id, bot_service)
        bot_name = bot_info.get("bot_name", "") or bot_id
        bot_description = summary.get("role", "") or summary.get("capability", "")

        skill_sets = []
        for skill_set in bot_info.get("skill_sets", []):
            entry = {"name": skill_set.get("name", "")}
            description = skill_set.get("description")
            if description:
                entry["description"] = description
            skill_sets.append(entry)

        bcsfuse_payload = {
            "type": "bot",
            "name": bot_name,
            "description": bot_description,
            "runtime_state": "offline",
            "profile": {
                "display_name": bot_name,
                "skill_sets": skill_sets,
                "summary": {
                    "capability": summary.get("capability", ""),
                    "role": summary.get("role", ""),
                },
                "activate": True,
            },
        }

        logger.info(f"[sync_to_bcsfuse] POST {url}")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = _post_json(url, bcsfuse_payload)
                success = bool(response.get("worker_id") or response.get("success") or response.get("ok"))
                logger.info(f"[sync_to_bcsfuse] Response (attempt {attempt}): success={success}")
                return {"success": success, "response": response}
            except Exception as exc:
                logger.warning(f"[sync_to_bcsfuse] Attempt {attempt}/{MAX_RETRIES} failed: {exc}")
                if attempt == MAX_RETRIES:
                    return {"success": False, "error": str(exc)}
                delay = RETRY_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
                logger.info(f"[sync_to_bcsfuse] Retrying in {delay}s...")
                time.sleep(delay)
    except Exception as exc:
        logger.error(f"[sync_to_bcsfuse] Unexpected error: {exc}", exc_info=True)
        return {"success": False, "error": str(exc)}

    return {"success": False, "error": "Unexpected code path"}


def sync_all(
    bot_id: str,
    owner_id: str,
    summary: dict,
    resource_repo,
    bot_service,
    bcsfuse_base_url: str = "",
    ecb_base_url: str = "",
) -> dict:
    """同步到所有下游服务（ECB + BCS Fuse）。

    Args:
        bot_id: Bot ID
        owner_id: Bot owner 的 user ID
        summary: LLM 生成的 summary（包含 capability 和 role）
        resource_repo: ResourceRepositoryProtocol, supplied by caller (DI'd).
        bot_service: BotService, supplied by caller (DI'd).
        bcsfuse_base_url: BCSFuse host from BcsFuseConfig (corp env overlays).
        ecb_base_url: ECB host from EcbConfig (corp env overlays).

    Returns:
        同步结果 dict，包含 overall_success、ecb、bcsfuse
    """
    ecb_result = sync_to_ecb(bot_id, owner_id, summary, resource_repo, ecb_base_url)
    bcsfuse_result = sync_to_bcsfuse(
        bot_id, owner_id, summary, bot_service, bcsfuse_base_url
    )

    return {
        "overall_success": ecb_result.get("success", False) or bcsfuse_result.get("success", False),
        "ecb": ecb_result,
        "bcsfuse": bcsfuse_result,
    }
