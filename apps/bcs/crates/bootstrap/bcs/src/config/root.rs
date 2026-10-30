//! BCS root configuration: the `BcsConfig` struct, its serde defaults,
//! and its `Default` implementation.

use std::path::PathBuf;

use secrecy::Secret;
use serde::{Deserialize, Serialize};

use bcs_config_api::{
    AuthChainConfig, AuthSdkConfig, BcsFuseConfig, CacheConfig, ChannelConfigSection,
    DatabaseConfig, DingTalkAccountConfig, EventingConfig, FusionProviderConfig,
    HumanNotifyConfig, LeaderElectionConfig, LlmConfig, LoggingConfig, ManifestConfig,
    SecretConfig, SecurityConfig, UserDirectoryConfig,
    deserialize_optional_secret, serialize_optional_secret,
};

use super::{
    collaboration::{CollaborationConfig, OpenApiV1Config},
    http::{CorsConfig, ProviderHttpConfig},
    invite::InviteConfig,
    message_history::MessageHistoryConfig,
    metrics::MetricsConfig,
    principal::{GatewayPrincipalConfig, GroupSessionWsConfig},
    security::SecurityGatewayConfig,
    session_files::SessionFilesConfig,
    telemetry::TelemetryConfig,
};

/// BCS configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BcsConfig {
    /// Drafts are disabled unless explicitly enabled; revision availability lives in the database.
    #[serde(default)]
    pub collaboration_drafts: Option<crate::collaboration_drafts::CollaborationDraftConfig>,
    #[serde(default)]
    pub state_machine_history: bcs_config_api::StateMachineHistoryConfig,
    /// Optional caller identity service section (spec §10.13.7): absent =
    /// no caller capability assembled and the anti-downgrade guard stays
    /// closed; present = full validation at startup, fail closed.
    #[serde(default)]
    pub caller_identity: Option<bcs_config_api::CallerIdentityServiceConfig>,
    /// Speaking-lease idle window (spec §10.13.9, §15.99): defaults to the
    /// frozen 60s constant; absent section changes nothing.
    #[serde(default)]
    pub speaking_lease: bcs_config_api::SpeakingLeaseConfig,
    /// Address to bind to.
    #[serde(default = "default_bind")]
    pub bind: String,

    /// Port to listen on.
    #[serde(default = "default_port")]
    pub port: u16,

    /// Base directory containing all bot folders.
    /// Each subdirectory represents a bot with IDENTITY.md, SOUL.md, etc.
    pub bots_base_dir: PathBuf,

    /// LLM provider for context fusion.
    /// Uses the same config format as moltis gateway.
    #[serde(default)]
    pub fusion_provider: Option<FusionProviderConfig>,

    /// LLM provider for state-machine judge calls.
    #[serde(default)]
    pub llm: LlmConfig,

    /// Maximum message history to keep per session.
    #[serde(default = "default_max_history")]
    pub max_history_per_session: usize,

    /// Whether to store messages in Group.
    /// When disabled (default), BCS does not store messages to reduce memory usage.
    /// Messages should be stored and managed by bots themselves.
    #[serde(default)]
    pub store_messages: bool,

    /// DingTalk account configurations.
    #[serde(default)]
    pub dingtalk_accounts: Vec<DingTalkAccountConfig>,

    /// Authentication token for client WebSocket connections (/ws endpoint).
    /// If not set, all client WebSocket connections are allowed (development mode).
    #[serde(
        default,
        serialize_with = "serialize_optional_secret",
        deserialize_with = "deserialize_optional_secret"
    )]
    pub auth_token: Option<Secret<String>>,

    /// Gateway-signed Principal verification trust configuration.
    #[serde(default)]
    pub gateway_principal: GatewayPrincipalConfig,

    /// Group-session WebSocket JWT signing-key lookup configuration.
    #[serde(default)]
    pub group_session_ws: GroupSessionWsConfig,

    /// Rate limit for bot WebSocket upgrades (`/ws/bot`).
    #[serde(default)]
    pub bot_ws_admission: crate::bot_ws_admission_config::BotWsAdmissionConfig,

    /// Leader election configuration for distributed deployment.
    /// When enabled, uses a configured election provider to elect one leader per environment.
    #[serde(default)]
    pub leader_election: Option<LeaderElectionConfig>,

    /// Capability-local cache selector.
    #[serde(default)]
    pub cache: CacheConfig,

    /// Unified database backend for DB-backed stores.
    #[serde(default)]
    pub database: DatabaseConfig,

    /// Provider-neutral secret backend selector.
    ///
    /// Defaults to `noop` for public/local builds. Product binaries can select
    /// additional providers registered by linked crates.
    #[serde(default)]
    pub secret: SecretConfig,

    /// Channel(IM bridge) configuration.
    #[serde(default)]
    pub channels: ChannelConfigSection,

    /// Human mention notification configuration.
    #[serde(default)]
    pub human_notify: HumanNotifyConfig,

    /// Managed message admission; all business flows default to disabled.
    #[serde(default)]
    pub message_delivery: bcs_config_api::message_delivery::MessageDeliveryConfig,

    /// HTTP provider webhook adapter configuration.
    #[serde(default)]
    pub provider_http: ProviderHttpConfig,

    /// Structured collaboration authoring-template configuration.
    #[serde(default)]
    pub collaboration: CollaborationConfig,

    /// Public base used by delivery adapters to project OpenAPI V1 URLs.
    #[serde(default)]
    pub openapi_v1: OpenApiV1Config,

    /// Whether to enable channel binding during bot onboard.
    /// When false (default), channel binding is handled at connect time for "default:{staff_id}" bots.
    /// When true, onboard request can include binding_channels for explicit binding.
    #[serde(default)]
    pub onboard_binding_enabled: bool,

    /// Maximum number of active groups a single bot can drive simultaneously (as driver role).
    #[serde(default = "default_max_groups_as_driver")]
    pub max_groups_as_driver: usize,

    /// Maximum number of participants allowed in a single group.
    #[serde(default = "default_max_group_members")]
    pub max_group_members: usize,

    /// Maximum number of groups a single bot can be a member of (any role, any status).
    #[serde(default = "default_max_groups_as_member")]
    pub max_groups_as_member: usize,

    /// Minimum random delay (ms) before delivering a group chat message to each bot.
    /// Defaults to 3000 (3 seconds).
    #[serde(default = "default_group_chat_delay_min_ms")]
    pub group_chat_delay_min_ms: u64,

    /// Maximum random delay (ms) before delivering a group chat message to each bot.
    /// Defaults to 8000 (8 seconds). Set both min and max to 0 to disable delay.
    #[serde(default = "default_group_chat_delay_max_ms")]
    pub group_chat_delay_max_ms: u64,

    /// Maximum number of messages allowed in a single group.
    /// > 0: enforce limit; <= 0: no limit.
    #[serde(default = "default_max_group_messages")]
    pub max_group_messages: i64,

    /// Strict validation of x-agentclaw-bolt-id header.
    /// When true, mismatched container bot ID will reject the request.
    /// When false, only warn log but allow the request.
    #[serde(default)]
    pub strict_container_validation: bool,

    /// External endpoint URL for BCS (e.g. "https://bcs.example.com").
    /// Used as the public base URL for share links and storage plugins.
    /// Falls back to http://{bind}:{port} if not set.
    #[serde(default)]
    pub bcs_endpoint: Option<String>,

    /// Backend work-order service base URL used for friend-connect notifications.
    /// When unset, BCS keeps the legacy no-op notification port.
    #[serde(default)]
    pub friend_work_order_base_url: Option<String>,

    /// Botchat frontend URL (e.g. "https://botchat.example.com").
    /// Used to generate frontend URLs: onboard registration, chat pages, etc.
    #[serde(default)]
    pub botchat_url: Option<String>,

    /// Registration page path on the botchat frontend (default: "/bcn/register").
    #[serde(default = "default_register_path")]
    pub register_path: String,

    /// Default visibility for newly onboarded bots.
    /// Valid values: "public", "protected", or "private".
    /// Falls back to "private" if not configured.
    #[serde(default)]
    pub default_visibility: Option<String>,

    /// Public manifest exposed by GET /manifest.
    #[serde(default, alias = "manifests")]
    pub manifest: ManifestConfig,

    /// Logging configuration.
    #[serde(default)]
    pub logging: LoggingConfig,

    /// OpenTelemetry trace export configuration.
    #[serde(default)]
    pub telemetry: TelemetryConfig,

    /// bcsfuse integration configuration.
    /// When enabled, fusion is delegated to bcsfuse (Python service) via HTTP.
    #[serde(default)]
    pub bcsfuse: BcsFuseConfig,

    /// User identity SDK configuration.
    /// When configured, enables user identity extraction from Cookie/Bearer token
    /// and bot ownership verification.
    #[serde(default)]
    pub auth_sdk: AuthSdkConfig,

    /// External user directory used to resolve stable user ids to display
    /// metadata. Disabled by default; providers are supplied by linked plugins.
    #[serde(default)]
    pub user_directory: UserDirectoryConfig,

    /// Auth plugin chain configuration (`[auth]` section). Selects which auth
    /// plugins are enabled and in what order. Empty/omitted → build-profile
    /// default (`bcs_auth_api::AuthConfig::default`).
    #[serde(default)]
    pub auth: AuthChainConfig,

    /// CORS configuration for browser clients.
    #[serde(default)]
    pub cors: CorsConfig,

    /// DingTalk group message logger configuration.
    /// When present and enabled = true, BCS spawns a background task that
    /// listens to the specified groups and writes each message as a JSONL log line.
    #[serde(default)]
    pub group_logger: Option<ding_logger::GroupLoggerConfig>,

    /// Default execution deadline for HTTP Provider `chat.send` runs that do
    /// not provide an explicit `timeout_ms`. Applied at process startup.
    #[serde(default = "default_provider_chat_run_timeout_ms")]
    pub provider_chat_run_timeout_ms: u64,

    /// Async chat run (bcs-cli chat-async) — max wall-clock a single run may
    /// be pending/running before the cleanup task marks it failed("timeout").
    /// Default 2 h 5 min, configurable up to 24 h.
    #[serde(default = "default_async_chat_run_timeout_ms")]
    pub async_chat_run_timeout_ms: u64,

    /// Async chat run — how long a terminal (completed/failed/cancelled) run
    /// is retained so slow pollers can still fetch the result. Default 120 s.
    #[serde(default = "default_async_chat_run_retention_ms")]
    pub async_chat_run_retention_ms: u64,

    /// Async chat run — server-side cap on the GET long-poll `wait_ms`
    /// parameter. Default 30 s.
    #[serde(default = "default_async_chat_poll_wait_max_ms")]
    pub async_chat_poll_wait_max_ms: u64,

    /// Async chat run — hard cap on stored records. New submissions are
    /// rejected with 503 when full. Default 100_000.
    ///
    /// Memory-mode only: bounds the in-process HashMap. In `persistent` mode
    /// this cap is **not** enforced as a total-rows limit (that would reject
    /// new runs once long-retained audit rows crossed the cap); persistent
    /// growth is bounded by `expires_at_ms` (active runs, timeout sweep) and
    /// the MySQL platform's audit-retention pruning (terminal rows, spec §11.2).
    #[serde(default = "default_async_chat_run_max_entries")]
    pub async_chat_run_max_entries: usize,

    /// Direct Chat async run store backend: `"memory"` (in-process,
    /// pre-#1546 behavior; not restart/replica-safe) or `"persistent"`
    /// (MySQL-authoritative + Redis hot cache). Default `"memory"` — opt-in
    /// for the governed store. See #1546.
    #[serde(default = "default_async_chat_run_store")]
    pub async_chat_run_store: String,

    /// Provider downlink run context backend: `"memory"` or `"redis"`.
    /// Default `"memory"`. The `"redis"` option requires a configured
    /// `[cache.redis]` and a Redis-backed `BotRunContextPort` implementation.
    #[serde(default = "default_bot_run_context_store")]
    pub bot_run_context_store: String,

    /// AI安全网关配置。
    /// 用于Bot间消息的安全检查和拦截。
    #[serde(default)]
    pub security_gateway: SecurityGatewayConfig,

    /// Runtime security policy configuration.
    #[serde(default)]
    pub security: SecurityConfig,

    /// Public Event recording and delivery configuration.
    #[serde(default)]
    pub eventing: EventingConfig,

    /// Message history configuration.
    #[serde(default)]
    pub message_history: MessageHistoryConfig,

    /// Prometheus metrics export configuration.
    #[serde(default)]
    pub metrics: MetricsConfig,

    /// Service-invocation API keys for /services/* endpoint auth (Part B Task 3, spec §9.6.2).
    ///
    /// Each entry contains a sha256 hash of the raw key (never the raw key itself),
    /// plus an optional `bound_groups` list that restricts which service groups the
    /// key can access. Keys with an empty `bound_groups` list are admin keys that
    /// can access any service group.
    ///
    /// Validated at startup by `BcsConfig::validate_api_keys`.
    #[serde(default)]
    pub api_keys: Vec<bcs_http::service_key::ApiKeyEntry>,

    /// Invite link configuration for Human actor self-join.
    #[serde(default)]
    pub invite: InviteConfig,

    /// Trusted-App authority scope allow rules
    /// (empty by default: every trusted app denied).
    #[serde(default)]
    pub bot_authority: super::bot_authority::BotAuthorityConfig,

    /// Session file workspace configuration (uploads, downloads, share tokens).
    #[serde(default)]
    pub session_files: SessionFilesConfig,

    /// Provider IDs allowed to use backend-only Provider Bot operations,
    /// including switch-bot-delivery and Bot attribute management.
    /// Empty list means no Provider has either capability.
    #[serde(default)]
    pub allowed_switch_provider_ids: Vec<String>,

    /// Deployment-wide, default-closed authorization for built-in WS V3 MCP profiles.
    #[serde(default)]
    pub uplink: bcs_config_api::UplinkConfig,

    /// Switch for Provider 2.0 SSE gray-list mode.
    /// When false, Provider downlink rolls out to SSE for all Provider 2.0 bots.
    #[serde(default = "default_provider_stream_gray_enabled")]
    pub provider_stream_gray_enabled: bool,

    /// created_by staff IDs whose Provider 2.0 bots may receive chat.send with SSE transport
    /// when gray-list mode is enabled.
    #[serde(default)]
    pub provider_stream_gray_created_by: Vec<String>,
}

fn default_bind() -> String {
    "127.0.0.1".to_string()
}

fn default_port() -> u16 {
    21000
}

fn default_provider_stream_gray_enabled() -> bool {
    false
}

fn default_max_history() -> usize {
    1000
}

fn default_max_groups_as_driver() -> usize {
    3
}

fn default_max_group_members() -> usize {
    5
}

fn default_max_groups_as_member() -> usize {
    10
}

fn default_group_chat_delay_min_ms() -> u64 {
    3000
}

fn default_group_chat_delay_max_ms() -> u64 {
    8000
}

fn default_max_group_messages() -> i64 {
    100
}

fn default_register_path() -> String {
    "/bcn/register".to_string()
}

fn default_provider_chat_run_timeout_ms() -> u64 {
    bcs_service_api::DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS
}

fn default_async_chat_run_timeout_ms() -> u64 {
    (2 * 60 + 5) * 60 * 1_000
}

fn default_async_chat_run_retention_ms() -> u64 {
    120 * 1_000
}

fn default_async_chat_poll_wait_max_ms() -> u64 {
    30_000
}

fn default_async_chat_run_max_entries() -> usize {
    100_000
}

fn default_async_chat_run_store() -> String {
    "memory".to_string()
}

fn default_bot_run_context_store() -> String {
    "memory".to_string()
}

impl Default for BcsConfig {
    fn default() -> Self {
        Self {
            collaboration_drafts: None,
            caller_identity: None,
            speaking_lease: Default::default(),
            state_machine_history: Default::default(),
            bind: default_bind(),
            port: default_port(),
            bots_base_dir: PathBuf::from("/bots"),
            fusion_provider: None,
            llm: LlmConfig::default(),
            max_history_per_session: default_max_history(),
            store_messages: false,
            dingtalk_accounts: Vec::new(),
            auth_token: None,
            gateway_principal: GatewayPrincipalConfig::default(),
            group_session_ws: GroupSessionWsConfig::default(),
            bot_ws_admission: Default::default(),
            leader_election: None,
            cache: CacheConfig::default(),
            database: DatabaseConfig::default(),
            secret: SecretConfig::default(),
            channels: ChannelConfigSection::default(),
            human_notify: HumanNotifyConfig::default(),
            message_delivery: bcs_config_api::message_delivery::MessageDeliveryConfig::default(),
            provider_http: ProviderHttpConfig::default(),
            collaboration: CollaborationConfig::default(),
            openapi_v1: OpenApiV1Config::default(),
            max_groups_as_driver: default_max_groups_as_driver(),
            max_group_members: default_max_group_members(),
            max_groups_as_member: default_max_groups_as_member(),
            group_chat_delay_min_ms: default_group_chat_delay_min_ms(),
            group_chat_delay_max_ms: default_group_chat_delay_max_ms(),
            max_group_messages: default_max_group_messages(),
            strict_container_validation: true,
            bcs_endpoint: None,
            friend_work_order_base_url: None,
            botchat_url: None,
            register_path: default_register_path(),
            default_visibility: None,
            manifest: ManifestConfig::default(),
            onboard_binding_enabled: false,
            logging: LoggingConfig::default(),
            telemetry: TelemetryConfig::default(),
            bcsfuse: BcsFuseConfig::default(),
            auth_sdk: AuthSdkConfig::default(),
            user_directory: UserDirectoryConfig::default(),
            auth: AuthChainConfig::default(),
            cors: CorsConfig::default(),
            group_logger: None,
            provider_chat_run_timeout_ms: default_provider_chat_run_timeout_ms(),
            async_chat_run_timeout_ms: default_async_chat_run_timeout_ms(),
            async_chat_run_retention_ms: default_async_chat_run_retention_ms(),
            async_chat_poll_wait_max_ms: default_async_chat_poll_wait_max_ms(),
            async_chat_run_max_entries: default_async_chat_run_max_entries(),
            async_chat_run_store: default_async_chat_run_store(),
            bot_run_context_store: default_bot_run_context_store(),
            security_gateway: SecurityGatewayConfig::default(),
            security: SecurityConfig::default(),
            eventing: EventingConfig::default(),
            message_history: MessageHistoryConfig::default(),
            api_keys: Vec::new(),
            metrics: MetricsConfig::default(),
            invite: InviteConfig::default(),
            bot_authority:
                super::bot_authority::BotAuthorityConfig::default(),
            session_files: SessionFilesConfig::default(),
            allowed_switch_provider_ids: Vec::new(),
            uplink: Default::default(),
            provider_stream_gray_enabled: default_provider_stream_gray_enabled(),
            provider_stream_gray_created_by: Vec::new(),
        }
    }
}
