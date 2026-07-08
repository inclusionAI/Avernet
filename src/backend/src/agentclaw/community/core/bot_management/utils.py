"""Bot management 共享工具函数.

统一封装 agent_code 获取逻辑，支持从 bot.ext 本地提取 + passport API fallback。
"""

import json
from typing import TYPE_CHECKING, Any, Dict, List

import requests

from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:
    from agentclaw.community.plugin_api.passport import PassportPlugin

logger = get_logger()


def is_baas_publish_failure_message(message: Any) -> bool:
    """Return True for BaaS publish lifecycle failures written by AgentClaw."""
    return isinstance(message, str) and (
        message.startswith("BaaS publish FAILED:")
        or message.startswith("BaaS publish timeout")
    )


def clear_baas_publish_failure_ext(ext: Any) -> dict[str, Any]:
    """Remove stale BaaS publish failure marker while preserving other ext keys."""
    cleaned = dict(ext) if isinstance(ext, dict) else {}
    if (
        cleaned.get("start_status") == "FAILED"
        and is_baas_publish_failure_message(cleaned.get("start_message"))
    ):
        cleaned.pop("start_status", None)
        cleaned.pop("start_message", None)
    return cleaned


def extract_agent_code_from_ext(bot: dict[str, Any]) -> str | None:
    """从 bot dict 的 ext 字段提取 agent_code。

    Args:
        bot: Bot 信息字典，需包含 "ext" 字段

    Returns:
        agent_code 或 None
    """
    ext = bot.get("ext") or {}
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except json.JSONDecodeError:
            ext = {}
    agent_code = (ext.get("passport") or {}).get("agent_code")
    return agent_code if agent_code else None


def query_agent_code_from_passport(
    bot_id: str,
    owner_workno: str,
    passport_plugin: "PassportPlugin",
) -> str | None:
    """调用 passport 接口查询 agent_code。

    Args:
        bot_id: Bot ID
        owner_workno: 所有者工号
        passport_plugin: PassportPlugin injected by caller.

    Returns:
        agent_code 或 None
    """
    try:
        passport = passport_plugin.query_agent_passport(
            bot_id=bot_id,
            owner_workno=owner_workno,
        )
        if passport:
            agent_code = passport.get("agent_code")
            if agent_code:
                logger.info(
                    f"[query_agent_code_from_passport] Resolved from API: "
                    f"bot_id={bot_id}, agent_code={agent_code}"
                )
                return agent_code
    except Exception as e:
        logger.warning(
            f"[query_agent_code_from_passport] Failed: bot_id={bot_id}, "
            f"owner={owner_workno}, error={e}"
        )
    return None


def resolve_agent_code(
    *,
    bot: dict[str, Any] | None = None,
    bot_id: str = "",
    owner_id: str = "",
    passport_plugin: "PassportPlugin | None" = None,
) -> str:
    """统一获取 agent_code。

    优先级：
    1. 传入 bot dict -> 从 bot.ext.passport.agent_code 提取
    2. 以上取不到 -> fallback 调用 passport 接口 (使用 bot_id + owner_id)

    Args:
        bot: Bot 信息字典（可选）
        bot_id: Bot ID（fallback 接口用）
        owner_id: 所有者 ID（fallback 接口用）
        passport_plugin: PassportPlugin (required only for fallback path).

    Returns:
        agent_code，获取失败返回空字符串
    """
    # 1. 优先从 bot.ext 提取
    if bot is not None:
        agent_code = extract_agent_code_from_ext(bot)
        if agent_code:
            return agent_code
        # 若 bot dict 里有 bot_id/owner_id，优先用它的
        bot_id = bot.get("bot_id", "") or bot_id
        owner_id = bot.get("owner_id", "") or owner_id

    # 2. Fallback: 调 passport 接口
    if bot_id and owner_id and passport_plugin is not None:
        agent_code = query_agent_code_from_passport(bot_id, owner_id, passport_plugin)
        if agent_code:
            return agent_code

    return ""


# template_type -> BOT_TYPE 环境变量映射
_BOT_TYPE_ENV_MAP: Dict[str, str] = {
    "personalCoding": "personal",
    "applicationCoding": "application",
}


def build_aix_extra_envs(
    template_config: Dict[str, Any] | None,
    template_type: str | None = None,
) -> Dict[str, str] | None:
    """构建 AIX coding bot 的额外环境变量。

    根据传入的 template_type 与 template_config 按需写入：
    - BOT_TYPE: 由 template_type 映射而来
      (personalCoding -> personal, applicationCoding -> application)
    - AIX_DEVFLOW_INFO: devflow_workflow 字段值（有值才写入）
    - GIT_ADDRESSES: backend_repo + frontend_repo + lib_repo 中所有 repo_url 的合集
      （非空才写入，序列化为 JSON 字符串）

    Args:
        template_config: Template 配置字典，可能为空
        template_type: 模板类型，例如 "applicationCoding"、"personalCoding"

    Returns:
        extra_envs 字典；若三者都未写入则返回 None。
        典型场景:
        - applicationCoding: {"BOT_TYPE": "application", "AIX_DEVFLOW_INFO": ..., "GIT_ADDRESSES": ...}
        - personalCoding: {"BOT_TYPE": "personal"}（通常没有 devflow / repos）
    """
    envs: Dict[str, str] = {}

    # BOT_TYPE：由 template_type 决定，与 template_config 无关
    bot_type = _BOT_TYPE_ENV_MAP.get(template_type or "")
    if bot_type:
        envs["BOT_TYPE"] = bot_type

    if template_config:
        # AIX_DEVFLOW_INFO：从 devflow_workflow 中提取 path 字段（支持字符串或字典格式）
        devflow_workflow = template_config.get("devflow_workflow", "")
        if isinstance(devflow_workflow, dict):
            aix_devflow_info = devflow_workflow.get("path", "")
        elif isinstance(devflow_workflow, str):
            aix_devflow_info = devflow_workflow
        else:
            aix_devflow_info = ""
        if aix_devflow_info:
            envs["AIX_DEVFLOW_INFO"] = aix_devflow_info

        # GIT_ADDRESSES：收集所有 repo_url
        repo_list: List[str] = []
        for repo_key in ("backend_repo", "frontend_repo", "lib_repo"):
            for repo in template_config.get(repo_key, []) or []:
                repo_url = repo.get("repo_url")
                if repo_url:
                    repo_list.append(repo_url)
        if repo_list:
            envs["GIT_ADDRESSES"] = json.dumps(repo_list, ensure_ascii=False)

        # RELAY_DEFAULT_MODEL / RELAY_DEFAULT_RUNTIME：仅 applicationCoding 下发 model / runtime，
        # 作为容器内 relay 的默认配置（覆盖容器写死默认）。显式判 template_type，避免
        # personalCoding 等被误注入；有值才写。create / restart 都经本函数，env 经 extra_envs
        # 透传到 arca/baas 两条分支。
        if (template_type or "") == "applicationCoding":
            model = template_config.get("model")
            if isinstance(model, str) and model.strip():
                envs["RELAY_DEFAULT_MODEL"] = model.strip()
            runtime = template_config.get("runtime")
            if isinstance(runtime, str) and runtime.strip():
                envs["RELAY_DEFAULT_RUNTIME"] = runtime.strip()

    return envs or None


def trigger_memory_initialization(
    bot_id: str,
    bot_name: str,
    user_id: str,
    template_config: Dict[str, Any],
    cookie: str,
    aixcore_base_url: str = "",
    aixcore_base_url_pre: str = "",
) -> None:
    """Trigger memory initialization for applicationCoding bot.

    The aixcore endpoint is deployment config (``WorkspaceHostingConfig``,
    passed by BotService). ``pre`` env uses ``aixcore_base_url_pre``, else
    ``aixcore_base_url``. Empty ⇒ memoryOS init is skipped (feature-off).

    Calls the AIX memoryos init API to initialize bot memory with
    code repositories and knowledge base information.

    Args:
        bot_id: Bot ID
        bot_name: Bot name
        user_id: User ID (staff_id)
        template_config: Template configuration containing repos and yuque URLs
        cookie: User's session cookie for authentication
    """
    logger = get_logger()
    logger.info(f"[trigger_memory_initialization] Starting: bot_id={bot_id}, bot_name={bot_name}, user_id={user_id}")

    try:
        # Build the API request payload
        payload: Dict[str, Any] = {
            "botId": bot_id,
            "botKind": "application",
            "keyword": bot_name,
            "userId": user_id,
        }

        # Extract code repo URLs from template_config
        code_repo_urls: List[str] = []
        for repo_key in ["backend_repo", "frontend_repo", "lib_repo"]:
            repos = template_config.get(repo_key, [])
            if repos:
                for repo in repos:
                    repo_url = repo.get("repo_url")
                    if repo_url:
                        code_repo_urls.append(repo_url)

        if code_repo_urls:
            payload["codeRepoUrls"] = code_repo_urls

        # Extract yuque URLs from template_config (yuque_kb_repos field)
        # 每一项形如 {"url": ..., "teamToken": ...}，teamToken 取自 template_config 中的 token 字段
        yuque_urls: List[Dict[str, str]] = []
        yuque_kb_repos = template_config.get("yuque_kb_repos", [])
        if yuque_kb_repos:
            for item in yuque_kb_repos:
                if not isinstance(item, dict):
                    continue
                url = item.get("url")
                if not url:
                    continue
                yuque_urls.append(
                    {"url": url, "teamToken": item.get("token") or ""}
                )
            if yuque_urls:
                payload["yuqueUrls"] = yuque_urls

        # Call the memoryos init API with cookie (environment-dependent)
        current_env = get_current_env()
        if current_env == "dev":
            logger.info(f"[trigger_memory_initialization] Dev environment, skipping memoryos init for bot {bot_id}")
            return

        # memoryOS init endpoint is deployment config (WorkspaceHostingConfig,
        # passed in). Select by env; empty ⇒ feature-off (skip init).
        aixcore = aixcore_base_url_pre if current_env == "pre" else aixcore_base_url
        if not aixcore:
            logger.info(
                "[trigger_memory_initialization] aixcore base url not configured; "
                "skipping memoryos init for bot %s",
                bot_id,
            )
            return
        api_url = f"{aixcore}/api/memoryos/init/full"

        headers = {
            "Content-Type": "application/json",
        }

        # Add cookie from upstream caller (required for authentication)
        if not cookie:
            logger.warning(f"[trigger_memory_initialization] No cookie provided, skipping for bot {bot_id}")
            return

        headers["Cookie"] = cookie

        logger.info(
            f"[trigger_memory_initialization] Calling memoryos init API for bot {bot_id}: "
            f"codeRepoUrls={len(code_repo_urls)}, yuqueUrls={len(yuque_urls)}"
        )

        response = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=30,
        )

        if response.status_code == 200:
            logger.info(
                f"[trigger_memory_initialization] Memoryos init API call succeeded for bot {bot_id}"
            )
        else:
            logger.warning(
                f"[trigger_memory_initialization] Memoryos init API call failed for bot {bot_id}: "
                f"status={response.status_code}, response={response.text}"
            )

    except Exception as e:
        # Log error but don't fail bot creation if memory init fails
        logger.error(
            f"[trigger_memory_initialization] Failed to trigger memory initialization for bot {bot_id}: {e}"
        )


def _extract_yuque_pairs(template_config: Dict[str, Any]) -> List[tuple]:
    """从 template_config.yuque_kb_repos 中提取 (url, token) 列表（已过滤空 url）。

    token 缺省视为空字符串。用于变更检测，能同时捕获 url 与 teamToken 的变化。
    """
    if not isinstance(template_config, dict):
        return []
    items = template_config.get("yuque_kb_repos") or []
    if not isinstance(items, list):
        return []
    pairs: List[tuple] = []
    for item in items:
        if isinstance(item, dict):
            url = item.get("url")
            if url:
                pairs.append((url, item.get("token") or ""))
    return pairs


def _extract_code_repo_urls(template_config: Dict[str, Any]) -> List[str]:
    """从 template_config 的 backend_repo/frontend_repo/lib_repo 中提取 repo_url 列表。"""
    if not isinstance(template_config, dict):
        return []
    urls: List[str] = []
    for repo_key in ["backend_repo", "frontend_repo", "lib_repo"]:
        repos = template_config.get(repo_key) or []
        if not isinstance(repos, list):
            continue
        for repo in repos:
            if isinstance(repo, dict):
                repo_url = repo.get("repo_url")
                if repo_url:
                    urls.append(repo_url)
    return urls


def memory_sources_changed(
    old_template_config: Dict[str, Any],
    new_template_config: Dict[str, Any],
) -> bool:
    """对比新旧 template_config 中的语雀知识库（URL + teamToken）或代码仓库 URL 是否发生变化。

    任一变化即返回 True。顺序不敏感，重复不敏感（按集合比较）。语雀对 (url, token) 二元组比较，
    teamToken 变化也会触发重新初始化。
    """
    if set(_extract_yuque_pairs(old_template_config)) != set(_extract_yuque_pairs(new_template_config)):
        return True
    if set(_extract_code_repo_urls(old_template_config)) != set(_extract_code_repo_urls(new_template_config)):
        return True
    return False


# 保留旧名称作为别名，避免外部调用方报错
yuque_kb_repos_changed = memory_sources_changed


def extract_workflow_name(template_config: Dict[str, Any]) -> str:
    """从 template_config 的 devflow_workflow 字段提取工作流名称。

    支持 dict（取 name 字段）和 str 两种格式。
    """
    if not isinstance(template_config, dict):
        return ""
    devflow_workflow = template_config.get("devflow_workflow", "")
    if isinstance(devflow_workflow, dict):
        return devflow_workflow.get("name", "")
    if isinstance(devflow_workflow, str):
        return devflow_workflow
    return ""
