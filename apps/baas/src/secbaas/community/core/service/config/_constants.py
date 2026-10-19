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

    # A2A v1.0 client-side grey-release switch (1b migration)
    A2A_CLIENT_MODE = "bot_run.a2a_client_mode"
    """BaaS→engine adapter 传输灰度开关。

    Value: "off" (default) | "grey" | "on"
    - off:  整组件走 v3 WS——生产默认，行为与迁移前一致
    - grey: 引擎容器 Agent Card 声明 JSONRPC 1.x 时走 A2A，否则回退 v3
    - on:   强制 A2A，能力探测失败直接抛错（fail-fast，不静默降级）
    Grey boundary: 附件与 HITL resolve 流程尚在 1b 范围外（A2A 路径对
    附件显式报错、resolve 需 E4 引擎面），启用 grey 前须确认业务流不含两者。
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

    # ── 敏感日志过滤豁免开关 ────────────────────────────────────

    SENSITIVE_BYPASS_SESSION_KEYS = "bot_run.sensitive_bypass_session_keys"
    """敏感日志过滤豁免的 session_key 片段白名单。

    Value: comma- and/or newline-separated list of session_key 片段。
    Usage: AsyncChatClientPool 读取（TTL 缓存，默认 60s 刷新，
    配置变更无需重启），以 loader 函数注入 AsyncChatClient；
    兜底事件日志的 sessionKey 包含任一片段时，不做敏感字段
    过滤（全量打印 payload）。未配置或读取失败时默认仍做过滤。
    """
