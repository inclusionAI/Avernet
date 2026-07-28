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


# Backward-compatible wrapper for legacy callers.  The actual coding
# template/env rules live in ``engines/aicoding/strategy.py``.
def build_aix_extra_envs(
    template_config: Dict[str, Any] | None,
    template_type: str | None = None,
) -> Dict[str, str] | None:
    """构建 AIX coding bot 的额外环境变量。

    Compatibility wrapper around EngineProvisioningStrategy.  Kept so older
    callers/tests can continue importing ``build_aix_extra_envs`` while the
    single source of truth for coding templates and RELAY_DEFAULT_* envs lives
    in the aicoding provisioning strategy.
    """
    from agentclaw.community.core.bot_management.engines import resolve_provisioning

    # Legacy compat wrapper only knows template_type/template_config; pass empty
    # identity fields (required by BotProvisioningContext) — the aicoding strategy
    # only consults template_type/template_config anyway.
    ctx, strategy = resolve_provisioning(
        bot_id="",
        owner_id="",
        bot_type="",
        active_engine="aicoding",
        template_type=template_type,
        template_config=template_config,
    )
    return strategy.build_extra_envs(ctx)


YUQUE_KNOWLEDGE_KEYS = (
    "yuque_kb_repos",
    "wiki_knowledge_spaces",
    "business_wiki_spaces",
    "repo_wiki_spaces",
)

CODE_REPO_KEYS = (
    "backend_repo",
    "frontend_repo",
    "lib_repo",
    "repos",
    "init_repos",
    "application_repo_urls",
)


def _extract_item_url(item: Any, *, url_keys: tuple[str, ...]) -> str:
    """Extract a URL-like value from either a string item or a dict item."""
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    for key in url_keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_item_token(item: Any) -> str:
    """Extract the Yuque team token from known field names."""
    if not isinstance(item, dict):
        return ""
    for key in ("token", "teamToken", "team_token"):
        value = item.get(key)
        if isinstance(value, str):
            return value
    return ""


def _iter_template_list_items(
    template_config: Dict[str, Any], keys: tuple[str, ...]
) -> List[Any]:
    """Iterate list values for top-level template_config keys.

    AC resolved snapshots may expose the same AppCoding-compatible shape under
    different semantic keys.  The backend runtime keeps using the existing
    AppCoding consumers, so these helpers normalize aliases at the edge instead
    of adding new hard-coded product branches in callers.
    """
    if not isinstance(template_config, dict):
        return []
    result: List[Any] = []
    for key in keys:
        value = template_config.get(key)
        if isinstance(value, list):
            result.extend(value)
    return result

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

        # Extract code repo URLs from template_config.  New template-factory
        # bots reuse the AppCoding memory/knowledge pipeline but their resolved
        # snapshots may carry repo aliases such as repos/init_repos.
        code_repo_urls = _extract_code_repo_urls(template_config)
        if code_repo_urls:
            payload["codeRepoUrls"] = code_repo_urls

        # Extract Yuque/Wiki URLs from AppCoding-compatible and semantic keys.
        yuque_urls: List[Dict[str, str]] = [
            {"url": url, "teamToken": token}
            for url, token in _extract_yuque_pairs(template_config)
        ]
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
    """Extract (url, token) pairs from template_config knowledge fields.

    Historical AppCoding bots use ``yuque_kb_repos``.  New template-factory bots
    reuse the same memory/knowledge consumers but AC may resolve semantic keys
    such as ``wiki_knowledge_spaces``, ``business_wiki_spaces`` or
    ``repo_wiki_spaces``.  Treat all of them as AppCoding-compatible knowledge
    sources at the backend edge.  Empty URLs are skipped; missing token is the
    empty string so token changes still participate in diff detection.
    """
    if not isinstance(template_config, dict):
        return []
    pairs: List[tuple] = []
    for item in _iter_template_list_items(template_config, YUQUE_KNOWLEDGE_KEYS):
        # Preserve AppCoding compatibility: arbitrary non-dict list entries are
        # ignored.  A direct string is only accepted if it is clearly a URL.
        if isinstance(item, str) and not item.strip().startswith(("http://", "https://")):
            continue
        url = _extract_item_url(
            item,
            url_keys=("url", "wiki_url", "repo_wiki_url", "space_url", "link"),
        )
        if url:
            pairs.append((url, _extract_item_token(item)))
    return pairs


def _extract_code_repo_urls(template_config: Dict[str, Any]) -> List[str]:
    """Extract repository URLs from AppCoding and template-factory aliases."""
    if not isinstance(template_config, dict):
        return []
    urls: List[str] = []
    for item in _iter_template_list_items(template_config, CODE_REPO_KEYS):
        url = _extract_item_url(item, url_keys=("repo_url", "url", "git_url", "ssh_url"))
        if url:
            urls.append(url)
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
