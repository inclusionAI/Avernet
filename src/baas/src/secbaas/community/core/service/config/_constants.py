"""System configuration key constants.

Centralized definition of system configuration keys used throughout
the application to avoid magic strings and ensure consistency.

Usage:
    from secbaas.community.core.service.config._constants import SystemConfigKey

    config = SystemConfigService.get_config(SystemConfigKey.ARCA_DFT_TENANT)
"""

from enum import StrEnum


class SystemConfigKey(StrEnum):
    """System configuration key constants.

    These keys are stored in the system_config table and represent
    important system-wide configuration values.

    Naming convention:
        {module}.{purpose} - e.g., "arca.dft_tenant"
    """

    # Arca PaaS platform configuration
    ARCA_DFT_TENANT = "arca.dft_tenant"
    """Default tenant ID for Arca PaaS platform (per environment).

    Value: template_id (int) as string
    Usage: Loaded via ArcaPaasService to get default Arca credentials
    """

    # Callback timeout configuration
    CALLBACK_TIMEOUT_SECONDS = "publish.callback_timeout_seconds"
    """System-level callback timeout in seconds.

    Value: integer string (e.g., "1800")
    Usage: Override for bot callback timeout at system level,
    applied when no user-specified value exists.
    Falls back to DEFAULT_CALLBACK_TIMEOUT_SECONDS (1800).
    """

    # SessionKey matching configuration
    SESSION_KEY_IGNORE_CASE = "bot_run.session_key_ignore_case"
    """Whether sessionKey fuzzy matching should ignore case.

    Value: "true" or "false" (default: "false")
    Usage: Read once at AsyncChatClient initialization; when enabled,
    SessionKeyMatcher performs case-insensitive contains matching.
    """

    INTERACTION_PROCESS = "bot_run.interaction_process"
    """Whether engine interaction events are persisted and forwarded to SSE.

    Value: "true" or "false" (default: "false")
    Usage: Read when a new pooled AsyncChatClient connection is created.
    Existing pooled connections keep the value selected at creation time.
    """

    # Add more system config keys here as needed

    # Chunk cleanup configuration
    CHUNK_CLEANUP_ENABLED = "bot_run.chunk_cleanup_enabled"
    """Whether to clean up chunk records after stream ends.

    Value: "true" or "false" (default: "true")
    Usage: Read per environment by appending ".{env}" suffix to the key.
    """

    DISPATCHER_ROUTE = "bot_run.dispatcher_route"

    BCN_QUEUE_DISPATCHER_ENABLED = "bot_run.bcn_queue_dispatcher_enabled"
    """Whether BCN requests default to QueueTaskMessageDispatcher.

    Value: "true" or "false" (default: "false")
    Usage: When enabled, BCN requests (metadata.bot_options.from_bcn == "true")
    use QueueTaskMessageDispatcher instead of TaskMessageDispatcher, unless
    overridden by a more specific dispatcher_route config.
    """

    # ── 评测环境开关配置 ──────────────────────────────────────────────────

    EVAL_ENV_ENABLED = "bot_run.eval_env_enabled"
    """评测环境路由开关，关闭时降级走 online 生产路由。

    Value: "true" or "false" (default: "false")
    Usage: 当 Default/Eval 区服务 Bot 出现部署失败、服务不可用等异常时，
    关闭此开关使 eval 生命周期阶段的请求降级走 online 生产路由，
    避免 eval 路由指向不可用的评测容器。
    """

    # ── Expired sandbox whitelist configuration ─────────────────────────────

    EXPIRE_SANDBOX_WHITELIST_BOT_UUIDS = "expire_sandbox.whitelist_bot_uuids"
    """Whitelist for the expired ACK pod sweep (ExpireSandboxTimer).

    Value: comma- and/or newline-separated list of bot_uuid (plain text).
    Usage: read at the start of each sweep (env-scoped); a matching bot skips
    the stop-bot / pod-destroy, exempting long-running demos, canaries, and key
    accounts that must not be reclaimed by expiry.
    """
