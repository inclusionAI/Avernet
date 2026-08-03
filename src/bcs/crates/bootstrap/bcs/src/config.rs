//! Configuration for the Bot Coordination Service.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use secrecy::Secret;
use serde::{Deserialize, Serialize};

// Re-export storage config contract types for convenience.
pub use bcs_config_api::{
    BcsFuseConfig, CacheConfig, DatabaseConfig, DatabaseType, MistConfig, RedisCacheConfig,
};

// Re-export config contract types from bcs-config-api.
// These types were split out of this module in C2 and now live in a pure
// data-contract crate so downstream modules can depend on them without
// pulling in the rest of the `bcs` binary.
pub use bcs_config_api::{
    AuthChainConfig, AuthSdkConfig, ChannelConfigSection, DingTalkAccountConfig,
    FusionProviderConfig, LlmConfig, LlmProviderType, LogOutputConfig, LogOutputFormat,
    LoggingConfig, ManifestConfig, LeaderElectionConfig, StructuredOutputMode, SecurityConfig,
    UserDirectoryConfig, UserDirectoryProviderConfig,
    deserialize_optional_secret, serialize_optional_secret,
};
#[allow(unused_imports)]
pub use bcs_config_api::{DmPolicy, RedisAuthMode};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct InviteConfig {
    #[serde(default)]
    pub token_secret: Option<String>,

    #[serde(default = "default_invite_ttl_seconds")]
    pub default_ttl_seconds: u64,

    #[serde(default)]
    pub base_url: Option<String>,

    #[serde(default)]
    pub group_link_url: Option<String>,

    #[serde(default)]
    pub session_link_url: Option<String>,
}

fn default_invite_ttl_seconds() -> u64 {
    86400
}

/// Session file workspace configuration (Task 11 bootstrap wiring).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionFilesConfig {
    /// Storage backend tag. `"local"` selects `bcs_storage_local::LocalStoragePlugin`.
    /// Other backends are linked into the binary by future plugin registration.
    #[serde(default = "default_session_files_storage_backend")]
    pub storage_backend: String,

    /// Object size at or above which uploads switch from single-PUT to multipart.
    #[serde(default = "default_session_files_multipart_threshold")]
    pub multipart_threshold: u64,

    /// Hard cap on a single object's size in bytes; intersected with the
    /// backend's `capabilities().max_object_size` at service construction.
    #[serde(default = "default_session_files_max_file_size")]
    pub max_file_size: u64,

    /// In-session + share download share-link TTL (baas expire_seconds), seconds.
    #[serde(default = "default_session_files_share_link_ttl")]
    pub share_link_ttl: u64,

    /// Share-token configuration — independent of `invite.token_secret`
    /// so rotating one does not invalidate the other's outstanding tokens.
    #[serde(default)]
    pub share: SessionFilesShareConfig,

    /// Backend-specific config pass-through (local: data_dir; baas: endpoint/tenant/...).
    #[serde(default)]
    pub backend: toml::Table,
}

impl Default for SessionFilesConfig {
    fn default() -> Self {
        Self {
            storage_backend: default_session_files_storage_backend(),
            multipart_threshold: default_session_files_multipart_threshold(),
            max_file_size: default_session_files_max_file_size(),
            share_link_ttl: default_session_files_share_link_ttl(),
            share: SessionFilesShareConfig::default(),
            backend: toml::Table::new(),
        }
    }
}

fn default_session_files_storage_backend() -> String {
    "local".to_string()
}

fn default_session_files_multipart_threshold() -> u64 {
    104_857_600
}

fn default_session_files_max_file_size() -> u64 {
    5_368_709_120
}

fn default_session_files_share_link_ttl() -> u64 {
    3600
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SessionFilesShareConfig {
    /// HMAC secret for share-token mint/consume. If unset, bootstrap logs a
    /// warning and generates a random 32-byte secret that does NOT survive
    /// restart — production deployments must set this explicitly.
    #[serde(default)]
    pub token_secret: Option<String>,

    /// Default share-token TTL in seconds. Clamped to `[60, 604800]` at mint.
    #[serde(default = "default_session_files_share_ttl")]
    pub default_ttl_seconds: u64,

    /// Public base URL used to construct share links. When None, falls back
    /// to `bcs_endpoint` or `http://{bind}:{port}` in the service layer.
    #[serde(default)]
    pub share_base_url: Option<String>,
}

fn default_session_files_share_ttl() -> u64 {
    86400
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct CorsConfig {
    #[serde(default)]
    pub allowed_origins: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TelemetryConfig {
    #[serde(default = "default_telemetry_enabled")]
    pub enabled: bool,

    #[serde(default = "default_telemetry_service_name")]
    pub service_name: String,

    #[serde(default)]
    pub otlp_traces_endpoint: Option<String>,

    #[serde(default)]
    pub extra_headers: BTreeMap<String, String>,
}

impl Default for TelemetryConfig {
    fn default() -> Self {
        Self {
            enabled: default_telemetry_enabled(),
            service_name: default_telemetry_service_name(),
            otlp_traces_endpoint: None,
            extra_headers: BTreeMap::new(),
        }
    }
}

impl TelemetryConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.service_name.trim().is_empty() {
            return Err("telemetry.service_name must not be empty".to_string());
        }
        if let Some(endpoint) = self.otlp_traces_endpoint.as_deref() {
            let endpoint = reqwest::Url::parse(endpoint)
                .map_err(|error| format!("telemetry.otlp_traces_endpoint is invalid: {error}"))?;
            if !matches!(endpoint.scheme(), "http" | "https") {
                return Err(
                    "telemetry.otlp_traces_endpoint must use http or https".to_string(),
                );
            }
        }
        for (name, value) in &self.extra_headers {
            axum::http::HeaderName::try_from(name.as_str()).map_err(|_| {
                format!("telemetry.extra_headers contains invalid header name '{name}'")
            })?;
            axum::http::HeaderValue::try_from(value.as_str()).map_err(|_| {
                format!("telemetry.extra_headers contains an invalid value for '{name}'")
            })?;
        }
        Ok(())
    }
}

fn default_telemetry_enabled() -> bool {
    true
}

fn default_telemetry_service_name() -> String {
    "bcn".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CollaborationConfig {
    #[serde(default)]
    pub templates: CollaborationTemplatesConfig,
}

impl Default for CollaborationConfig {
    fn default() -> Self {
        Self {
            templates: CollaborationTemplatesConfig::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CollaborationTemplatesConfig {
    #[serde(default)]
    pub storage_type: CollaborationTemplateStorageKind,

    #[serde(default = "default_collaboration_templates_base_dir")]
    pub base_dir: PathBuf,

    #[serde(default = "default_collaboration_templates_default_language")]
    pub default_language: String,
}

impl Default for CollaborationTemplatesConfig {
    fn default() -> Self {
        Self {
            storage_type: CollaborationTemplateStorageKind::default(),
            base_dir: default_collaboration_templates_base_dir(),
            default_language: default_collaboration_templates_default_language(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CollaborationTemplateStorageKind {
    File,
    Mysql,
}

impl Default for CollaborationTemplateStorageKind {
    fn default() -> Self {
        Self::File
    }
}

fn default_collaboration_templates_base_dir() -> PathBuf {
    PathBuf::from("/data/bcs/collaboration-templates")
}

fn default_collaboration_templates_default_language() -> String {
    "zh-CN".to_string()
}

/// Gateway Principal verification trust and signing-key lookup configuration.
///
/// The signing key itself is intentionally not configuration: bootstrap resolves
/// it from `signing_key_env` at process startup.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GatewayPrincipalConfig {
    #[serde(default = "default_gateway_principal_issuer")]
    pub issuer: String,

    #[serde(default = "default_gateway_principal_audience")]
    pub audience: String,

    #[serde(default = "default_gateway_principal_key_id")]
    pub key_id: String,

    #[serde(default = "default_gateway_principal_signing_key_env")]
    pub signing_key_env: String,
}

impl Default for GatewayPrincipalConfig {
    fn default() -> Self {
        Self {
            issuer: default_gateway_principal_issuer(),
            audience: default_gateway_principal_audience(),
            key_id: default_gateway_principal_key_id(),
            signing_key_env: default_gateway_principal_signing_key_env(),
        }
    }
}

impl GatewayPrincipalConfig {
    pub fn validate(&self) -> Result<(), String> {
        for (field, value) in [
            ("issuer", &self.issuer),
            ("audience", &self.audience),
            ("key_id", &self.key_id),
            ("signing_key_env", &self.signing_key_env),
        ] {
            if value.trim().is_empty() {
                return Err(format!("gateway_principal.{field} must not be blank"));
            }
        }
        Ok(())
    }
}

fn default_gateway_principal_issuer() -> String {
    "gateway".to_string()
}

fn default_gateway_principal_audience() -> String {
    "bcs".to_string()
}

fn default_gateway_principal_key_id() -> String {
    "bare".to_string()
}

fn default_gateway_principal_signing_key_env() -> String {
    "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE".to_string()
}

/// BCS configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BcsConfig {
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

    /// Mist (secret management) configuration. Disabled by default; when
    /// enabled the BCS process talks to the configured local secret sidecar to
    /// fetch secrets via the SecretService/SecretAccessPort stack. Use the
    /// `GET /admin/secret/:name` route for end-to-end verification on dev
    /// machines.
    #[serde(default)]
    pub mist: MistConfig,

    /// Channel(IM bridge) configuration.
    #[serde(default)]
    pub channels: ChannelConfigSection,

    /// Structured collaboration authoring-template configuration.
    #[serde(default)]
    pub collaboration: CollaborationConfig,

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
    /// Used to generate confirm URLs in group proposals.
    /// Falls back to http://{bind}:{port} if not set.
    #[serde(default)]
    pub bcs_endpoint: Option<String>,

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
    #[serde(default = "default_async_chat_run_max_entries")]
    pub async_chat_run_max_entries: usize,

    /// AI安全网关配置。
    /// 用于Bot间消息的安全检查和拦截。
    #[serde(default)]
    pub security_gateway: SecurityGatewayConfig,

    /// Runtime security policy configuration.
    #[serde(default)]
    pub security: SecurityConfig,

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

    /// Session file workspace configuration (uploads, downloads, share tokens).
    #[serde(default)]
    pub session_files: SessionFilesConfig,

    /// Provider IDs allowed to call the switch-bot-delivery endpoint.
    /// Empty list means no provider can switch bot delivery.
    #[serde(default)]
    pub allowed_switch_provider_ids: Vec<String>,

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

fn default_metrics_endpoint_path() -> String {
    "/metrics".to_string()
}

fn default_metrics_mode() -> MetricsMode {
    MetricsMode::Pull
}

fn default_metrics_enabled() -> bool {
    false
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MetricsConfig {
    #[serde(default = "default_metrics_enabled")]
    pub enabled: bool,

    #[serde(default = "default_metrics_mode")]
    pub mode: MetricsMode,

    #[serde(default = "default_metrics_endpoint_path")]
    pub endpoint_path: String,
}

impl Default for MetricsConfig {
    fn default() -> Self {
        Self {
            enabled: default_metrics_enabled(),
            mode: MetricsMode::Pull,
            endpoint_path: default_metrics_endpoint_path(),
        }
    }
}

impl MetricsConfig {
    pub fn validate(&self) -> Result<(), String> {
        if !self.endpoint_path.starts_with('/') {
            return Err("metrics.endpoint_path must start with '/'".to_string());
        }
        if self.endpoint_path.contains('{') || self.endpoint_path.contains('}') {
            return Err("metrics.endpoint_path must not contain route parameters".to_string());
        }
        if self.endpoint_path == "/health"
            || self.endpoint_path == "/ws"
            || self.endpoint_path == "/ws/bot"
            || self.endpoint_path.starts_with("/ws/")
        {
            return Err(format!(
                "metrics.endpoint_path conflicts with reserved path '{}'",
                self.endpoint_path
            ));
        }
        if is_api_route_namespace(&self.endpoint_path) {
            return Err(format!(
                "metrics.endpoint_path conflicts with existing API route '{}'",
                self.endpoint_path
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MessageHistoryConfig {
    /// Groups created at or after this timestamp (ms) use the new message store path.
    /// Groups created before this timestamp fall back to the legacy history path.
    /// Default: 0 (all groups use new path).
    #[serde(default = "default_message_history_cutoff")]
    pub cutoff_timestamp: u64,

    /// ManagerWorker sessions created at or after this timestamp (ms) use the
    /// new message store path. Default disables ManagerWorker DB reads until a
    /// rollout timestamp is explicitly configured.
    #[serde(default = "default_manager_worker_message_history_cutoff")]
    pub manager_worker_cutoff_timestamp: u64,

    /// Max visible history messages for a newly joined participant.
    /// Default: 100.
    #[serde(default = "default_new_participant_visible_limit")]
    pub new_participant_visible_limit: u64,

    /// Default page size when the caller does not specify a limit.
    /// Default: 50.
    #[serde(default = "default_message_page_limit")]
    pub default_page_limit: u32,

    /// Hard cap on page size to prevent excessive queries.
    /// Default: 100.
    #[serde(default = "default_message_max_page_limit")]
    pub max_page_limit: u32,
}

impl Default for MessageHistoryConfig {
    fn default() -> Self {
        Self {
            cutoff_timestamp: default_message_history_cutoff(),
            manager_worker_cutoff_timestamp: default_manager_worker_message_history_cutoff(),
            new_participant_visible_limit: default_new_participant_visible_limit(),
            default_page_limit: default_message_page_limit(),
            max_page_limit: default_message_max_page_limit(),
        }
    }
}

fn default_message_history_cutoff() -> u64 {
    0
}

fn default_manager_worker_message_history_cutoff() -> u64 {
    u64::MAX
}

fn default_new_participant_visible_limit() -> u64 {
    100
}

fn default_message_page_limit() -> u32 {
    50
}

fn default_message_max_page_limit() -> u32 {
    100
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MetricsMode {
    Pull,
}

fn is_api_route_namespace(path: &str) -> bool {
    // NOTE: This blocks known API route namespaces, not a complete Axum route
    // inventory. Exact route collisions still surface during router
    // construction; update this list when adding new top-level API roots.
    const API_ROOTS: &[&str] = &[
        "/me",
        "/bots",
        "/admin",
        "/actors",
        "/friends",
        "/groups",
        "/collaboration",
        "/chat",
        "/onboard",
        "/manifest",
    ];

    API_ROOTS.iter().any(|root| path_is_under_root(path, root))
}

fn path_is_under_root(path: &str, root: &str) -> bool {
    path == root
        || path
            .strip_prefix(root)
            .is_some_and(|suffix| suffix.starts_with('/'))
}

impl Default for BcsConfig {
    fn default() -> Self {
        Self {
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
            leader_election: None,
            cache: CacheConfig::default(),
            database: DatabaseConfig::default(),
            mist: MistConfig::default(),
            channels: ChannelConfigSection::default(),
            collaboration: CollaborationConfig::default(),
            max_groups_as_driver: default_max_groups_as_driver(),
            max_group_members: default_max_group_members(),
            max_groups_as_member: default_max_groups_as_member(),
            group_chat_delay_min_ms: default_group_chat_delay_min_ms(),
            group_chat_delay_max_ms: default_group_chat_delay_max_ms(),
            max_group_messages: default_max_group_messages(),
            strict_container_validation: true,
            bcs_endpoint: None,
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
            async_chat_run_timeout_ms: default_async_chat_run_timeout_ms(),
            async_chat_run_retention_ms: default_async_chat_run_retention_ms(),
            async_chat_poll_wait_max_ms: default_async_chat_poll_wait_max_ms(),
            async_chat_run_max_entries: default_async_chat_run_max_entries(),
            security_gateway: SecurityGatewayConfig::default(),
            security: SecurityConfig::default(),
            message_history: MessageHistoryConfig::default(),
            api_keys: Vec::new(),
            metrics: MetricsConfig::default(),
            invite: InviteConfig::default(),
            session_files: SessionFilesConfig::default(),
            allowed_switch_provider_ids: Vec::new(),
            provider_stream_gray_enabled: default_provider_stream_gray_enabled(),
            provider_stream_gray_created_by: Vec::new(),
        }
    }
}

pub type SecurityGatewayProviderConfig = BTreeMap<String, serde_json::Value>;

/// AI安全网关配置。
///
/// `provider` 选择安全网关实现并在启动阶段注入对应 `SecurityGatewayPort`。
/// 开源版内置 `"noop"`（永远放行）；其他 provider 由链接进二进制的插件注册。
///
/// `dry_run` 是投递策略：true 仅观测（拦截只打日志），false 真正阻断。
/// provider 私有配置放在 `providers.<provider>` map 中，由具体插件解析。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SecurityGatewayConfig {
    #[serde(default = "default_security_provider")]
    pub provider: String,

    #[serde(default = "default_security_dry_run")]
    pub dry_run: bool,

    #[serde(default)]
    pub providers: BTreeMap<String, SecurityGatewayProviderConfig>,
}

impl Default for SecurityGatewayConfig {
    fn default() -> Self {
        Self {
            provider: default_security_provider(),
            dry_run: default_security_dry_run(),
            providers: BTreeMap::new(),
        }
    }
}

fn default_security_provider() -> String {
    "noop".to_string()
}

fn default_security_dry_run() -> bool {
    true
}

fn standalone_env_config_path(config_dir: &Path) -> Option<PathBuf> {
    let suffix = crate::config_loader::Environment::resolve().config_suffix();
    for ext in ["toml", "json"] {
        let path = config_dir.join(format!("bcs-config{}.{}", suffix, ext));
        if path.exists() {
            return Some(path);
        }
    }
    None
}

fn local_path_base_dir_for_config_dir(config_dir: &Path) -> PathBuf {
    let base_dir = if config_dir
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name == "configs")
    {
        config_dir.parent().unwrap_or(config_dir)
    } else {
        config_dir
    };

    base_dir
        .canonicalize()
        .unwrap_or_else(|_| base_dir.to_path_buf())
}

fn local_path_base_dir_for_config_file(path: &Path) -> PathBuf {
    let config_dir = path.parent().unwrap_or_else(|| Path::new("."));
    local_path_base_dir_for_config_dir(config_dir)
}

fn normalize_local_path(path: &mut PathBuf, base_dir: &Path) {
    if path.is_relative() {
        *path = base_dir.join(path.as_path());
    }
}

fn normalize_local_string_path(path: &mut String, base_dir: &Path) {
    let value = PathBuf::from(path.as_str());
    if value.is_relative() {
        *path = base_dir.join(value).display().to_string();
    }
}

fn normalize_local_paths(config: &mut BcsConfig, base_dir: &Path) {
    normalize_local_path(&mut config.collaboration.templates.base_dir, base_dir);

    for bundle in &mut config.manifest.bundles {
        let Some(file) = bundle.file.as_mut() else {
            continue;
        };
        normalize_local_string_path(file, base_dir);
    }
}

impl BcsConfig {
    pub fn validate_metrics(&self) -> Result<(), String> {
        self.metrics.validate()?;
        if self.metrics.enabled && !cfg!(feature = "prometheus-metrics") {
            return Err(
                "metrics.enabled=true requires the bcs prometheus-metrics Cargo feature"
                    .to_string(),
            );
        }
        Ok(())
    }

    /// Load configuration with multi-environment support.
    ///
    /// This is the recommended way to load config for multi-environment deployment.
    ///
    /// Priority order for config directory:
    /// 1. Explicit directory (from CLI `--config-dir`)
    /// 2. Default: `configs/` directory
    ///
    /// Environment detection (priority):
    /// 1. `SERVER_ENV`
    /// 2. `REAL_SERVER_ENV`
    /// 3. `ALIPAY_APP_ENV`
    /// 4. Fallback to `local` environment
    ///
    /// Config files:
    /// - Base config: `bcs-config.toml` (or `.json`)
    /// - Environment-specific: `bcs-config-{env}.toml` (or `.json`)
    ///
    /// The environment-specific config is deep-merged with the base config.
    pub fn try_load_with_env(config_dir: Option<&PathBuf>) -> std::result::Result<Self, String> {
        let config_dir = config_dir
            .cloned()
            .unwrap_or_else(|| PathBuf::from("configs"));

        if config_dir.is_file() {
            return Self::from_file(&config_dir).map_err(|err| err.to_string());
        }

        let loader = crate::config_loader::ConfigLoader::new(config_dir);
        let result = match loader.load_with_info::<Self>() {
            Ok(result) => result,
            Err(crate::config_loader::ConfigLoadError::BaseConfigNotFound(_)) => {
                let Some(path) = standalone_env_config_path(loader.config_dir()) else {
                    return Err(format!(
                        "Base config file not found in {}",
                        loader.config_dir().display()
                    ));
                };
                return Self::from_file(&path).map_err(|err| err.to_string());
            }
            Err(err) => return Err(err.to_string()),
        };
        let mut config = result.config;
        let local_path_base_dir = local_path_base_dir_for_config_dir(&result.config_dir);
        normalize_local_paths(&mut config, &local_path_base_dir);
        validate_loaded_config(&config).map_err(|err| err.to_string())?;
        Ok(config)
    }

    /// The environment-specific config is deep-merged with the base config.
    pub fn load_with_env(config_dir: Option<&PathBuf>) -> Self {
        // Determine config directory
        let config_dir = config_dir
            .cloned()
            .unwrap_or_else(|| PathBuf::from("configs"));

        if config_dir.is_file() {
            return Self::load(Some(&config_dir));
        }

        let loader = crate::config_loader::ConfigLoader::new(config_dir.clone());

        match loader.load_with_info::<Self>() {
            Ok(result) => {
                // Log loaded config files
                if let Some(ref env_path) = result.env_config_path {
                    tracing::info!(
                        environment = %result.environment,
                        base_config = %result.base_config_path.display(),
                        env_config = %env_path.display(),
                        "Loaded configuration files"
                    );
                } else {
                    tracing::info!(
                        environment = %result.environment,
                        base_config = %result.base_config_path.display(),
                        "Loaded configuration file (no environment override)"
                    );
                }

                // Log final merged configuration (pretty-printed JSON)
                let redacted_value =
                    crate::config_loader::redact_sensitive_values(&result.merged_value);
                if let Ok(config_json) = serde_json::to_string_pretty(&redacted_value) {
                    tracing::info!("Final merged configuration:\n{}", config_json);
                }

                let mut config = result.config;
                let local_path_base_dir = local_path_base_dir_for_config_dir(&result.config_dir);
                normalize_local_paths(&mut config, &local_path_base_dir);

                if let Err(e) = validate_loaded_config(&config) {
                    panic!("Invalid BCS configuration: {}", e);
                }
                config
            }
            Err(e) => {
                if matches!(
                    &e,
                    crate::config_loader::ConfigLoadError::BaseConfigNotFound(_)
                ) && let Some(path) = standalone_env_config_path(&config_dir)
                {
                    tracing::info!(
                        config_path = %path.display(),
                        "Loading BCS config from standalone environment file"
                    );
                    return Self::load(Some(&path));
                }

                // Fallback to legacy single-file loading for backward compatibility
                tracing::warn!(
                    error = %e,
                    config_dir = %config_dir.display(),
                    "Multi-env config loading failed, falling back to single-file loading"
                );
                Self::load(None)
            }
        }
    }

    /// Get default config file paths (relative to project root)
    fn default_config_paths() -> Vec<PathBuf> {
        vec![
            // Current working directory: ./configs/bcs-config.toml (TOML support)
            PathBuf::from("configs/bcs-config.toml"),
            // Current working directory: ./configs/bcs-config.json
            PathBuf::from("configs/bcs-config.json"),
            // Parent directory (when running from crates/bcs): ../configs/bcs-config.toml
            PathBuf::from("../configs/bcs-config.toml"),
            // Parent directory (when running from crates/bcs): ../configs/bcs-config.json
            PathBuf::from("../configs/bcs-config.json"),
            // Two levels up (when running from crates/bcs/src): ../../configs/bcs-config.toml
            PathBuf::from("../../configs/bcs-config.toml"),
            // Two levels up (when running from crates/bcs/src): ../../configs/bcs-config.json
            PathBuf::from("../../configs/bcs-config.json"),
        ]
    }

    /// Load configuration with explicit path or default paths.
    ///
    /// Priority order:
    /// 1. Explicit single-file path (legacy fallback)
    /// 2. Default config paths (configs/bcs-config.json, configs/bcs-config.toml, etc.)
    ///
    /// Panics if no config file is found.
    ///
    /// New entry points should prefer `load_with_env` or `try_load_with_env`.
    pub fn load(explicit_path: Option<&PathBuf>) -> Self {
        // 1. Try explicit single-file path first.
        if let Some(path) = explicit_path {
            let path_str = path.display().to_string();
            tracing::info!(config_path = %path_str, "Loading BCS config from explicit path");
            match Self::from_file(path) {
                Ok(config) => {
                    tracing::info!(config_path = %path_str, "Successfully loaded BCS config");
                    return config;
                }
                Err(e) => {
                    tracing::error!(config_path = %path_str, error = %e, "Failed to load config file");
                    panic!("Failed to load config file '{}': {}", path_str, e);
                }
            }
        }

        // 2. Try default config paths
        for default_path in Self::default_config_paths() {
            let path_str = default_path.display().to_string();
            if default_path.exists() {
                tracing::info!(config_path = %path_str, "Loading BCS config from default path");
                match Self::from_file(&default_path) {
                    Ok(config) => {
                        tracing::info!(config_path = %path_str, "Successfully loaded BCS config");
                        return config;
                    }
                    Err(e) => {
                        tracing::warn!(config_path = %path_str, error = %e, "Failed to load default config file, trying next");
                    }
                }
            }
        }

        // No config file found - show error and exit
        tracing::error!(
            "No config file found. Please provide one via:\n\
             \x20  -c, --config-dir <DIR> specify config directory\n\
             \x20  BCS_CONFIG_DIR env      set config directory via environment variable\n\
             \x20  Default paths: configs/bcs-config.json, configs/bcs-config.toml"
        );
        panic!(
            "No config file found. Use -c <config-dir> or set BCS_CONFIG_DIR environment variable."
        );
    }

    /// Load config from a JSON or TOML file.
    ///
    /// File format is detected by extension:
    /// - `.json` → JSON format
    /// - `.toml` → TOML format
    /// - Other/missing → Try JSON first, then TOML
    pub fn from_file(path: &PathBuf) -> Result<Self, Box<dyn std::error::Error>> {
        let content = std::fs::read_to_string(path)?;
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

        match ext.to_lowercase().as_str() {
            "json" => {
                let mut config: Self = serde_json::from_str(&content)?;
                let local_path_base_dir = local_path_base_dir_for_config_file(path);
                normalize_local_paths(&mut config, &local_path_base_dir);
                validate_loaded_config(&config)?;
                Ok(config)
            }
            "toml" => {
                let mut config: Self = toml::from_str(&content)?;
                let local_path_base_dir = local_path_base_dir_for_config_file(path);
                normalize_local_paths(&mut config, &local_path_base_dir);
                validate_loaded_config(&config)?;
                Ok(config)
            }
            _ => {
                // Try JSON first, then TOML
                if let Ok(mut config) = serde_json::from_str::<Self>(&content) {
                    let local_path_base_dir = local_path_base_dir_for_config_file(path);
                    normalize_local_paths(&mut config, &local_path_base_dir);
                    validate_loaded_config(&config)?;
                    return Ok(config);
                }
                let mut config: Self = toml::from_str(&content)?;
                let local_path_base_dir = local_path_base_dir_for_config_file(path);
                normalize_local_paths(&mut config, &local_path_base_dir);
                validate_loaded_config(&config)?;
                Ok(config)
            }
        }
    }

    /// Validate `api_keys` (Part B Task 3, spec §9.6.2):
    ///
    /// - every `sha256` must be 64 lowercase hex chars
    /// - sha256 values must be globally unique
    /// - names must be globally unique
    ///
    /// Returns a static-string error suitable for surfacing at startup.
    pub fn validate_api_keys(&self) -> Result<(), String> {
        let mut seen_sha = std::collections::HashSet::new();
        let mut seen_name = std::collections::HashSet::new();
        for k in &self.api_keys {
            if k.sha256.len() != 64
                || !k
                    .sha256
                    .chars()
                    .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
            {
                return Err(format!(
                    "api_key {} has invalid sha256 (must be 64 lowercase hex chars)",
                    k.name
                ));
            }
            if !seen_sha.insert(k.sha256.clone()) {
                return Err(format!("duplicate api_key sha256 (name: {})", k.name));
            }
            if !seen_name.insert(k.name.clone()) {
                return Err(format!("duplicate api_key name: {}", k.name));
            }
        }
        Ok(())
    }
}

fn validate_loaded_config(config: &BcsConfig) -> Result<(), Box<dyn std::error::Error>> {
    config.gateway_principal.validate().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    config.telemetry.validate().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    config.validate_metrics().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    config.validate_api_keys().map_err(|e| {
        Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
            as Box<dyn std::error::Error>
    })?;
    if let Some(oauth) = config.auth.oauth.as_ref() {
        oauth.validate().map_err(|e| {
            Box::new(std::io::Error::new(std::io::ErrorKind::InvalidInput, e))
                as Box<dyn std::error::Error>
        })?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use secrecy::ExposeSecret;

    #[allow(unsafe_code)]
    fn safe_set_var(key: &str, value: impl AsRef<std::ffi::OsStr>) {
        unsafe {
            std::env::set_var(key, value);
        }
    }

    #[allow(unsafe_code)]
    fn safe_remove_var(key: &str) {
        unsafe {
            std::env::remove_var(key);
        }
    }

    #[test]
    fn test_default_config() {
        let config = BcsConfig::default();

        assert_eq!(config.bind, "127.0.0.1");
        assert_eq!(config.port, 21000);
        assert_eq!(config.bots_base_dir, PathBuf::from("/bots"));
        assert!(config.fusion_provider.is_none());
        assert_eq!(config.max_history_per_session, 1000);
        assert_eq!(config.async_chat_run_timeout_ms, 7_500_000);
        assert!(config.security.outbound_url.block_private_networks);
        assert!(!config.security.outbound_url.allow_loopback);
    }

    #[test]
    fn test_provider_stream_gray_config_defaults_to_full_rollout() {
        let config = BcsConfig::default();

        assert!(!config.provider_stream_gray_enabled);
    }

    #[test]
    fn test_provider_stream_gray_config_can_enable_gray_mode() {
        let toml = r#"
            bots_base_dir = "/bots"
            provider_stream_gray_enabled = true
            provider_stream_gray_created_by = ["197262"]
        "#;

        let config: BcsConfig = toml::from_str(toml).expect("parse provider stream gray config");

        assert!(config.provider_stream_gray_enabled);
        assert_eq!(config.provider_stream_gray_created_by, vec!["197262".to_string()]);
    }

    #[test]
    fn test_config_security_outbound_url_section_parses() {
        let toml = r#"
            bots_base_dir = "/bots"
            [security.outbound_url]
            block_private_networks = false
            allow_loopback = true
        "#;
        let config: BcsConfig = toml::from_str(toml).expect("parse [security.outbound_url]");
        assert!(!config.security.outbound_url.block_private_networks);
        assert!(config.security.outbound_url.allow_loopback);
    }

    #[test]
    fn test_message_history_manager_worker_cutoff_defaults_disabled() {
        let config = MessageHistoryConfig::default();

        assert_eq!(config.cutoff_timestamp, 0);
        assert_eq!(config.manager_worker_cutoff_timestamp, u64::MAX);
    }

    #[test]
    fn test_config_auth_chain_section_parses() {
        let toml = r#"
            bots_base_dir = "/bots"
            [auth]
            chain = ["agentpass", "session"]
            require_authentication = true
            mock_user_id = "12345"
            allow_mock_headers = true
        "#;
        let config: BcsConfig = toml::from_str(toml).expect("parse [auth]");
        assert_eq!(config.auth.chain, vec!["agentpass", "session"]);
        assert!(config.auth.require_authentication);
        assert!(config.auth.allow_mock_headers);

        // resolve honors a non-empty chain verbatim.
        let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
        assert_eq!(resolved.chain, vec!["agentpass", "session"]);
        assert!(resolved.require_authentication);
        assert_eq!(resolved.local.mock_user_id.as_deref(), Some("12345"));
        assert!(resolved.local.allow_mock_headers);
    }

    #[test]
    fn test_config_cors_section_parses() {
        let toml = r#"
            bots_base_dir = "/bots"
            [cors]
            allowed_origins = ["https://botchat.example.com", "http://localhost:8000"]
        "#;
        let config: BcsConfig = toml::from_str(toml).expect("parse [cors]");
        assert_eq!(
            config.cors.allowed_origins,
            vec![
                "https://botchat.example.com".to_string(),
                "http://localhost:8000".to_string(),
            ]
        );
    }

    #[test]
    fn test_telemetry_config_defaults_without_section() {
        let config: BcsConfig = toml::from_str(r#"bots_base_dir = "/bots""#).unwrap();

        assert!(config.telemetry.enabled);
        assert_eq!(config.telemetry.service_name, "bcn");
        assert_eq!(config.telemetry.otlp_traces_endpoint, None);
        assert!(config.telemetry.extra_headers.is_empty());
    }

    #[test]
    fn test_telemetry_config_parses_endpoint_and_extra_headers() {
        let config: BcsConfig = toml::from_str(
            r#"
bots_base_dir = "/bots"

[telemetry]
enabled = true
service_name = "bcn-prod"
otlp_traces_endpoint = "https://collector.example.com/v1/traces"

[telemetry.extra_headers]
x-collector-route = "collector-local"
"#,
        )
        .unwrap();

        assert_eq!(config.telemetry.service_name, "bcn-prod");
        assert_eq!(
            config.telemetry.otlp_traces_endpoint.as_deref(),
            Some("https://collector.example.com/v1/traces")
        );
        assert_eq!(
            config
                .telemetry
                .extra_headers
                .get("x-collector-route")
                .map(String::as_str),
            Some("collector-local")
        );
    }

    #[test]
    fn test_telemetry_config_validation_rejects_invalid_endpoint_and_headers() {
        let invalid_endpoint = TelemetryConfig {
            otlp_traces_endpoint: Some("not-an-http-endpoint".to_string()),
            ..TelemetryConfig::default()
        };
        assert!(invalid_endpoint.validate().is_err());

        let invalid_header = TelemetryConfig {
            extra_headers: BTreeMap::from([(
                "invalid header".to_string(),
                "value".to_string(),
            )]),
            ..TelemetryConfig::default()
        };
        assert!(invalid_header.validate().is_err());
    }

    #[test]
    #[serial_test::serial]
    fn test_config_auth_mock_user_id_env_override() {
        // Save and clear env vars so the test is hermetic.
        let saved_user_id = std::env::var("BCS_MOCK_USER_ID").ok();
        let saved_nick_name = std::env::var("BCS_MOCK_USER_NICK_NAME").ok();
        let saved_auth_mock = std::env::var("BCS_AUTH_MOCK").ok();
        safe_remove_var("BCS_MOCK_USER_ID");
        safe_remove_var("BCS_MOCK_USER_NICK_NAME");
        safe_remove_var("BCS_AUTH_MOCK");

        let config = BcsConfig::default();
        // Without env var, mock_user_id is None.
        let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
        assert!(resolved.local.mock_user_id.is_none());
        assert!(!resolved.local.allow_mock_headers);

        // With env var, it fills in when config is absent.
        safe_set_var("BCS_MOCK_USER_ID", "99999");
        safe_set_var("BCS_MOCK_USER_NICK_NAME", "EnvUser");
        let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
        assert_eq!(resolved.local.mock_user_id.as_deref(), Some("99999"));
        assert_eq!(resolved.local.mock_user_name.as_deref(), Some("EnvUser"));
        safe_remove_var("BCS_MOCK_USER_ID");
        safe_remove_var("BCS_MOCK_USER_NICK_NAME");

        safe_set_var("BCS_AUTH_MOCK", "1");
        let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
        assert!(resolved.local.allow_mock_headers);
        safe_remove_var("BCS_AUTH_MOCK");

        // Config value takes priority over env var.
        let toml = r#"
            bots_base_dir = "/bots"
            [auth]
            mock_user_id = "from_config"
        "#;
        let config: BcsConfig = toml::from_str(toml).unwrap();
        safe_set_var("BCS_MOCK_USER_ID", "from_env");
        let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
        assert_eq!(resolved.local.mock_user_id.as_deref(), Some("from_config"));
        safe_remove_var("BCS_MOCK_USER_ID");

        // Restore original env vars.
        if let Some(v) = saved_user_id {
            safe_set_var("BCS_MOCK_USER_ID", v);
        }
        if let Some(v) = saved_nick_name {
            safe_set_var("BCS_MOCK_USER_NICK_NAME", v);
        }
        if let Some(v) = saved_auth_mock {
            safe_set_var("BCS_AUTH_MOCK", v);
        }
    }

    #[test]
    fn test_config_auth_chain_absent_falls_back_to_default() {
        let toml = r#"bots_base_dir = "/bots""#;
        let config: BcsConfig = toml::from_str(toml).expect("parse without [auth]");
        assert!(config.auth.chain.is_empty());

        // Empty chain → build-profile default applied in the composition root.
        let resolved = crate::auth_wiring::resolve_auth_config(&config.auth, "test");
        let expected = if cfg!(debug_assertions) {
            vec!["local".to_string()]
        } else {
            vec![
                "agentpass".to_string(),
                "cookie".to_string(),
                "session".to_string(),
            ]
        };
        assert_eq!(resolved.chain, expected);
        // The contract crate's default is now neutral (empty), not build-profile.
        assert!(bcs_auth_api::AuthConfig::default().chain.is_empty());
    }

    #[test]
    fn test_build_oauth_provider_known_kinds() {
        use bcs_config_api::ProviderSettings;

        let google = ProviderSettings {
            kind: None, // defaults to the instance name "google"
            client_id: "gid".to_string(),
            client_secret: None,
            private_key: None,
            alipay_public_key: None,
        };
        // Arc<dyn OAuthProvider> is not Debug, so match instead of .expect().
        match crate::auth_wiring::build_oauth_provider("google", &google) {
            Ok(p) => assert_eq!(p.name(), "google"),
            Err(e) => panic!("google provider should build: {e}"),
        }

        // Explicit kind decoupled from the instance name (multi-instance case).
        let gh = ProviderSettings {
            kind: Some("github".to_string()),
            client_id: "ghid".to_string(),
            client_secret: None,
            private_key: None,
            alipay_public_key: None,
        };
        match crate::auth_wiring::build_oauth_provider("github-partner", &gh) {
            Ok(p) => assert_eq!(p.name(), "github"),
            Err(e) => panic!("github provider should build: {e}"),
        }
    }

    #[test]
    fn test_build_oauth_provider_unknown_kind_errors() {
        use bcs_config_api::ProviderSettings;

        let cfg = ProviderSettings {
            kind: Some("facebook".to_string()),
            client_id: "id".to_string(),
            client_secret: None,
            private_key: None,
            alipay_public_key: None,
        };
        let err = match crate::auth_wiring::build_oauth_provider("fb", &cfg) {
            Ok(_) => panic!("unknown kind must error, not silently drop"),
            Err(e) => e,
        };
        assert!(err.contains("unknown provider kind"), "got: {err}");
        assert!(err.contains("facebook"), "got: {err}");
    }

    #[test]
    fn test_build_oauth_provider_empty_client_id_errors() {
        use bcs_config_api::ProviderSettings;

        let cfg = ProviderSettings {
            kind: None,
            client_id: "  ".to_string(),
            client_secret: None,
            private_key: None,
            alipay_public_key: None,
        };
        let err = match crate::auth_wiring::build_oauth_provider("google", &cfg) {
            Ok(_) => panic!("empty client_id must error"),
            Err(e) => e,
        };
        assert!(err.contains("client_id"), "got: {err}");
    }

    #[test]
    fn test_collaboration_template_storage_config_parses() {
        let toml = r#"
            bots_base_dir = "/bots"

            [collaboration.templates]
            storage_type = "mysql"
            base_dir = "seeds/collaboration-templates"
            default_language = "zh-CN"
        "#;

        let config: BcsConfig = toml::from_str(toml).expect("parse collaboration templates");

        assert_eq!(
            config.collaboration.templates.storage_type,
            CollaborationTemplateStorageKind::Mysql
        );
        assert_eq!(
            config.collaboration.templates.base_dir,
            PathBuf::from("seeds/collaboration-templates")
        );
        assert_eq!(config.collaboration.templates.default_language, "zh-CN");
    }

    #[test]
    fn test_config_serde() {
        let json = r#"{
            "bind": "0.0.0.0",
            "port": 22000,
            "bots_base_dir": "/custom/bots",
            "max_history_per_session": 500
        }"#;

        let config: BcsConfig = serde_json::from_str(json).unwrap();

        assert_eq!(config.bind, "0.0.0.0");
        assert_eq!(config.port, 22000);
        assert_eq!(config.bots_base_dir, PathBuf::from("/custom/bots"));
        assert_eq!(config.max_history_per_session, 500);
    }

    #[test]
    fn test_config_rejects_unknown_key() {
        let json = r#"{
            "bind": "0.0.0.0",
            "port": 22000,
            "bots_base_dir": "/custom/bots",
            "unknown_key": true
        }"#;

        let err = serde_json::from_str::<BcsConfig>(json).expect_err("unknown key rejected");
        assert!(err.to_string().contains("unknown field"));
    }

    #[test]
    fn test_config_accepts_external_database_type() {
        let json = r#"{
            "bind": "0.0.0.0",
            "port": 22000,
            "bots_base_dir": "/custom/bots",
            "database": {
                "type": "postgres"
            }
        }"#;

        let config: BcsConfig = serde_json::from_str(json).expect("external db type parses");
        assert_eq!(
            config.database.database_type,
            DatabaseType::Other("postgres".to_string())
        );
    }

    #[test]
    fn test_config_with_fusion_provider() {
        let json = r#"{
            "bind": "127.0.0.1",
            "port": 21000,
            "bots_base_dir": "/bots",
            "fusion_provider": {
                "provider": "anthropic",
                "model": "claude-3-opus",
                "api_key": "test-key",
                "base_url": "https://api.anthropic.com"
            }
        }"#;

        let config: BcsConfig = serde_json::from_str(json).unwrap();

        let fusion = config.fusion_provider.unwrap();
        assert_eq!(fusion.provider, "anthropic");
        assert_eq!(fusion.model, "claude-3-opus");
        assert_eq!(fusion.api_key, Some("test-key".to_string()));
        assert_eq!(
            fusion.base_url,
            Some("https://api.anthropic.com".to_string())
        );
    }

    #[test]
    fn test_config_with_manifest_bundle_array_of_tables() {
        let toml = r#"
bots_base_dir = "/bots"

[[manifest.bundles]]
name = "bcsPanel"
url = "https://cdn.example.com/bcs-panel/1.0.0/index.js"
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();

        assert_eq!(config.manifest.schema_version, 1);
        assert_eq!(config.manifest.bundles.len(), 1);
        assert_eq!(config.manifest.bundles[0].name, "bcsPanel");
        assert_eq!(
            config.manifest.bundles[0].url.as_deref(),
            Some("https://cdn.example.com/bcs-panel/1.0.0/index.js")
        );
    }

    #[test]
    fn test_config_with_manifest_bundle_file() {
        let toml = r#"
bots_base_dir = "/bots"

[[manifest.bundles]]
name = "bcsPanel"
type = "file"
file = "assets/panel/dist/index.umd.js"
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();

        assert_eq!(config.manifest.schema_version, 1);
        assert_eq!(config.manifest.bundles.len(), 1);
        assert_eq!(config.manifest.bundles[0].name, "bcsPanel");
        assert_eq!(config.manifest.bundles[0].url, None);
        assert_eq!(
            config.manifest.bundles[0].file.as_deref(),
            Some("assets/panel/dist/index.umd.js")
        );
    }

    #[test]
    fn test_config_loader_resolves_local_paths_relative_to_config_root() {
        let dir = tempfile::tempdir().unwrap();
        let config_dir = dir.path().join("configs");
        std::fs::create_dir_all(&config_dir).unwrap();
        std::fs::write(config_dir.join("bcs-config.toml"), r#"
bots_base_dir = "/bots"

[collaboration.templates]
base_dir = "seeds/collaboration-templates"

[[manifest.bundles]]
name = "bcsPanel"
type = "file"
file = "assets/panel/dist/index.umd.js"
"#).unwrap();

        let config = BcsConfig::try_load_with_env(Some(&config_dir)).unwrap();
        let expected_root = std::fs::canonicalize(dir.path()).unwrap();
        let expected_manifest_file = expected_root
            .join("assets/panel/dist/index.umd.js")
            .display()
            .to_string();
        let expected_template_dir = expected_root.join("seeds/collaboration-templates");

        assert_eq!(
            config.manifest.bundles[0].file.as_deref(),
            Some(expected_manifest_file.as_str())
        );
        assert_eq!(
            config.collaboration.templates.base_dir,
            expected_template_dir
        );
    }

    #[test]
    fn test_config_loader_resolves_local_paths_relative_to_custom_config_dir() {
        let dir = tempfile::tempdir().unwrap();
        let config_dir = dir.path().join("bcs-config");
        std::fs::create_dir_all(&config_dir).unwrap();
        std::fs::write(config_dir.join("bcs-config.toml"), r#"
bots_base_dir = "/bots"

[collaboration.templates]
base_dir = "seeds/collaboration-templates"

[[manifest.bundles]]
name = "bcsPanel"
type = "file"
file = "assets/panel/dist/index.umd.js"
"#).unwrap();

        let config = BcsConfig::try_load_with_env(Some(&config_dir)).unwrap();
        let expected_root = std::fs::canonicalize(&config_dir).unwrap();
        let expected_manifest_file = expected_root
            .join("assets/panel/dist/index.umd.js")
            .display()
            .to_string();
        let expected_template_dir = expected_root.join("seeds/collaboration-templates");

        assert_eq!(
            config.manifest.bundles[0].file.as_deref(),
            Some(expected_manifest_file.as_str())
        );
        assert_eq!(
            config.collaboration.templates.base_dir,
            expected_template_dir
        );
    }

    #[test]
    fn test_config_defaults_on_partial_json() {
        // Only provide some fields, others should use defaults
        let json = r#"{
            "bots_base_dir": "/my/bots"
        }"#;

        let config: BcsConfig = serde_json::from_str(json).unwrap();

        assert_eq!(config.bind, "127.0.0.1"); // default
        assert_eq!(config.port, 21000); // default
        assert_eq!(config.bots_base_dir, PathBuf::from("/my/bots"));
        assert_eq!(config.max_history_per_session, 1000); // default
    }

    #[test]
    fn test_fusion_provider_config() {
        let fusion = FusionProviderConfig {
            provider: "openai".to_string(),
            model: "gpt-4".to_string(),
            api_key: None,
            base_url: None,
        };

        let json = serde_json::to_string(&fusion).unwrap();
        let parsed: FusionProviderConfig = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.provider, "openai");
        assert_eq!(parsed.model, "gpt-4");
        assert!(parsed.api_key.is_none());
        assert!(parsed.base_url.is_none());
    }

    #[test]
    fn test_config_with_dingtalk() {
        let json = r#"{
            "bind": "0.0.0.0",
            "port": 21000,
            "bots_base_dir": "/bots",
            "dingtalk_accounts": [
                {
                    "account_id": "test-account",
                    "client_id": "test-client-id",
                    "client_secret": "test-client-secret",
                    "gateway_mode": false,
                    "enable_scene_group": true,
                    "dm_policy": "open",
                    "allowlist": ["*"]
                }
            ]
        }"#;

        let config: BcsConfig = serde_json::from_str(json).unwrap();
        assert_eq!(config.dingtalk_accounts.len(), 1);
        assert_eq!(config.dingtalk_accounts[0].account_id, "test-account");
        assert_eq!(
            config.dingtalk_accounts[0].client_id,
            Some("test-client-id".to_string())
        );
        assert!(config.dingtalk_accounts[0].client_secret.is_some());
        assert!(config.dingtalk_accounts[0].enable_scene_group);
    }

    #[test]
    fn test_config_with_cache_section() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[cache]
type = "redis"

[cache.redis.connection]
type = "direct"
host = "127.0.0.1"
port = 6379
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();
        assert_eq!(config.cache.cache_type, "redis");
        assert_eq!(config.cache.redis.connection.connection_type, "direct");
        assert_eq!(config.cache.redis.connection.port, Some(6379));
    }

    #[test]
    fn test_config_with_mysql() {
        let json = r#"{
            "bind": "0.0.0.0",
            "port": 21000,
            "bots_base_dir": "/bots",
            "database": {
                "type": "mysql",
                "mysql": {
                    "database": "bcs",
                    "connection": {
                        "type": "direct",
                        "user": "bcs_user",
                        "password": "secret",
                        "host": "10.0.0.2",
                        "port": 11306
                    }
                }
            }
        }"#;

        let config: BcsConfig = serde_json::from_str(json).unwrap();
        assert_eq!(config.database.database_type, DatabaseType::Mysql);
        let mysql = config.database.mysql;
        assert_eq!(mysql.database, "bcs");
        assert_eq!(mysql.connection.user.as_deref(), Some("bcs_user"));
    }

    #[test]
    fn test_config_with_sqlite_database() {
        let json = r#"{
            "bind": "0.0.0.0",
            "port": 21000,
            "bots_base_dir": "/bots",
            "database": {
                "type": "sqlite",
                "sqlite": {
                    "path": "custom-bcs.db"
                }
            }
        }"#;

        let config: BcsConfig = serde_json::from_str(json).unwrap();
        assert_eq!(config.database.database_type, DatabaseType::Sqlite);
        assert_eq!(config.database.sqlite.path, "custom-bcs.db");
    }

    #[test]
    fn session_files_config_parses_share_link_ttl_and_backend() {
        let toml_str = r#"
storage_backend = "baas"
multipart_threshold = 104857600
max_file_size = 5368709120
share_link_ttl = 3600

[share]
token_secret = "s3cret"
default_ttl_seconds = 86400

[backend]
endpoint = "http://baas:8080"
tenant = "teamclaw"
"#;
        let cfg: SessionFilesConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(cfg.storage_backend, "baas");
        assert_eq!(cfg.share_link_ttl, 3600);
        assert_eq!(cfg.backend["endpoint"], toml::Value::String("http://baas:8080".into()));
        assert_eq!(cfg.backend["tenant"], toml::Value::String("teamclaw".into()));
    }

    #[test]
    fn test_config_rejects_legacy_group_storage() {
        let json = r#"{
            "bind": "0.0.0.0",
            "port": 21000,
            "bots_base_dir": "/bots",
            "group_storage": {
                "storage_type": "sqlite"
            }
        }"#;

        let err = serde_json::from_str::<BcsConfig>(json).expect_err("legacy group_storage rejected");
        assert!(err.to_string().contains("unknown field"));
    }

    #[test]
    fn test_config_with_leader_election() {
        let json = r#"{
            "bind": "0.0.0.0",
            "port": 21000,
            "bots_base_dir": "/bots",
            "leader_election": {
                "enabled": true,
                "provider": "distributed",
                "lease": {
                    "ttl_secs": 30,
                    "renewal_interval_secs": 10
                },
                "providers": {
                    "distributed": {
                        "zone": "default",
                        "lock_prefix": "bcs"
                    }
                }
            }
        }"#;

        let config: BcsConfig = serde_json::from_str(json).unwrap();
        assert!(config.leader_election.is_some());
        let election = config.leader_election.unwrap();
        assert!(election.enabled);
        assert_eq!(election.provider.as_deref(), Some("distributed"));
        assert_eq!(election.lease.ttl_secs, 30);
        assert_eq!(election.lease.renewal_interval_secs, 10);
        let provider = election
            .providers
            .get("distributed")
            .expect("distributed provider options");
        assert_eq!(provider.get("zone").and_then(|value| value.as_str()), Some("default"));
        assert_eq!(provider.get("lock_prefix").and_then(|value| value.as_str()), Some("bcs"));
    }

    #[test]
    fn test_config_rejects_legacy_leader_election_fields() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[leader_election]
enabled = true
provider = "distributed"
lock_ttl_secs = 30
renewal_interval_secs = 10
"#;

        let err = toml::from_str::<BcsConfig>(toml)
            .expect_err("legacy leader_election fields must be rejected");
        assert!(err.to_string().contains("unknown field"));
    }

    #[test]
    fn test_config_toml_format() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[cache]
type = "redis"

[cache.redis.connection]
type = "direct"
host = "127.0.0.1"
port = 6379

[leader_election]
enabled = true
provider = "distributed"

[leader_election.lease]
ttl_secs = 30
renewal_interval_secs = 10

[leader_election.providers.distributed]
zone = "default"
lock_prefix = "bcs"
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();
        assert_eq!(config.bind, "0.0.0.0");
        assert_eq!(config.port, 21000);
        assert_eq!(config.cache.cache_type, "redis");
        assert!(config.leader_election.is_some());
        let election = config.leader_election.unwrap();
        assert!(election.enabled);
        assert_eq!(election.provider.as_deref(), Some("distributed"));
        assert_eq!(election.lease.ttl_secs, 30);
        let provider = election
            .providers
            .get("distributed")
            .expect("distributed provider options");
        assert_eq!(provider.get("zone").and_then(|value| value.as_str()), Some("default"));
        assert_eq!(provider.get("lock_prefix").and_then(|value| value.as_str()), Some("bcs"));
    }

    #[test]
    fn test_metrics_config_defaults() {
        let config = BcsConfig::default();
        assert!(!config.metrics.enabled);
        assert_eq!(config.metrics.mode, MetricsMode::Pull);
        assert_eq!(config.metrics.endpoint_path, "/metrics");
        assert!(config.metrics.validate().is_ok());
    }

    #[test]
    fn test_metrics_config_toml() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[metrics]
enabled = true
mode = "pull"
endpoint_path = "/metrics"
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();
        assert!(config.metrics.enabled);
        assert_eq!(config.metrics.mode, MetricsMode::Pull);
        assert_eq!(config.metrics.endpoint_path, "/metrics");
    }

    #[test]
    fn test_metrics_config_rejects_invalid_mode() {
        let json = r#"{
            "bots_base_dir": "/bots",
            "metrics": {
                "mode": "push"
            }
        }"#;

        let err = serde_json::from_str::<BcsConfig>(json).expect_err("invalid mode rejected");
        assert!(err.to_string().contains("unknown variant"));
    }

    #[test]
    fn test_metrics_config_validates_endpoint_path() {
        let mut config = MetricsConfig::default();
        config.endpoint_path = "metrics".to_string();
        assert!(config.validate().is_err());

        config.endpoint_path = "/groups/{id}".to_string();
        assert!(config.validate().is_err());

        config.endpoint_path = "/ws/bot".to_string();
        assert!(config.validate().is_err());

        config.endpoint_path = "/groups".to_string();
        assert!(config.validate().is_err());

        config.endpoint_path = "/groups/metrics".to_string();
        assert!(config.validate().is_err());

        config.endpoint_path = "/bots/metrics".to_string();
        assert!(config.validate().is_err());

        config.endpoint_path = "/actors/metrics".to_string();
        assert!(config.validate().is_err());

        config.endpoint_path = "/chat/runs/metrics".to_string();
        assert!(config.validate().is_err());

        config.endpoint_path = "/metrics".to_string();
        assert!(config.validate().is_ok());

        config.endpoint_path = "/botmetrics".to_string();
        assert!(config.validate().is_ok());
    }

    #[test]
    fn test_config_cache_toml_with_redis_auth() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[cache]
type = "redis"

[cache.redis.connection]
type = "direct"
host = "redis.example.com"
port = 6379
auth_mode = "redis"
username = "bcs"
password = "redis-pass"
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();
        let connection = &config.cache.redis.connection;

        assert_eq!(connection.auth_mode, RedisAuthMode::Redis);
        assert_eq!(connection.username.as_deref(), Some("bcs"));
        assert_eq!(
            connection.password.as_ref().map(|password| password.expose_secret().as_str()),
            Some("redis-pass")
        );
    }

    #[test]
    fn test_config_rejects_top_level_redis_section() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[redis]
host = "127.0.0.1"
port = 6379
skip_auth = true
"#;

        let err =
            toml::from_str::<BcsConfig>(toml).expect_err("top-level redis should be rejected");
        assert!(err.to_string().contains("redis"));
    }

    #[test]
    fn test_security_gateway_provider_options_parse_as_map() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[security_gateway]
provider = "agentpass"
dry_run = false

[security_gateway.providers.agentpass]
domain = "security-gateway.example.com"
endpoint = "/api/agentpass/zero_check.json"
timeout_ms = 300
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();
        let provider = config
            .security_gateway
            .providers
            .get("agentpass")
            .expect("agentpass provider config");

        assert_eq!(config.security_gateway.provider, "agentpass");
        assert!(!config.security_gateway.dry_run);
        assert_eq!(
            provider.get("endpoint").and_then(|value| value.as_str()),
            Some("/api/agentpass/zero_check.json")
        );
        assert_eq!(
            provider.get("timeout_ms").and_then(|value| value.as_u64()),
            Some(300)
        );
    }

    #[test]
    fn test_security_gateway_rejects_legacy_top_level_provider_options() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[security_gateway]
provider = "agentpass"
dry_run = false
endpoint = "/api/agentpass/zero_check.json"
"#;

        let err = toml::from_str::<BcsConfig>(toml)
            .expect_err("legacy top-level provider options should be rejected");
        assert!(err.to_string().contains("endpoint"));
    }

    #[test]
    fn test_user_directory_provider_options_parse_as_map() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[user_directory]
enabled = true
provider = "ldap"

[user_directory.providers.ldap]
base_url = "https://directory.example.com"
timeout_ms = 300
"#;

        let config: BcsConfig = toml::from_str(toml).unwrap();
        let provider = config
            .user_directory
            .providers
            .get("ldap")
            .expect("ldap provider config");

        assert!(config.user_directory.enabled);
        assert_eq!(config.user_directory.provider.as_deref(), Some("ldap"));
        assert_eq!(
            provider.get("base_url").and_then(|value| value.as_str()),
            Some("https://directory.example.com")
        );
        assert_eq!(
            provider.get("timeout_ms").and_then(|value| value.as_u64()),
            Some(300)
        );
    }

    #[test]
    fn test_user_directory_rejects_legacy_top_level_provider_options() {
        let toml = r#"
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/bots"

[user_directory]
enabled = true
provider = "ldap"
base_url = "https://directory.example.com"
"#;

        let err = toml::from_str::<BcsConfig>(toml)
            .expect_err("legacy top-level provider options should be rejected");
        assert!(err.to_string().contains("base_url"));
    }

}
