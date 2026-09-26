//! Static configuration types: structs, enums, and their inherent impls.
//! The BcsConfig inherent impl lives in [`super::loading`] and [`super::validation`];
//! `default_*` helpers live in [`super::defaults`] and are re-exported here so that
//! the `#[serde(default = "<name>")]` attribute paths resolve at struct-definition scope.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use secrecy::Secret;
use serde::{Deserialize, Serialize};

#[allow(unused_imports)]
use bcs_config_api::{
    AuthChainConfig, AuthSdkConfig, BcsFuseConfig, CacheConfig, ChannelConfigSection,
    DatabaseConfig, DatabaseType, DingTalkAccountConfig, EventingConfig, FusionProviderConfig,
    HumanNotifyConfig, LeaderElectionConfig, LlmConfig, LoggingConfig, ManifestConfig,
    SecretConfig, SecurityConfig, UserDirectoryConfig, deserialize_optional_secret,
    serialize_optional_secret,
};

// Bring the default_* fns into this scope so `#[serde(default = "default_<x>")]`
// attributes (which resolve as paths at the struct's definition site) see them.
#[allow(unused_imports)]
use super::defaults::*;

#[allow(unused_imports)]
use bcs_config_api::{ApiAuthConfig, ApiConfig, GatewayApiAuthConfig};

// Helper used by impl ProviderHttpConfig::validate::is_api_route_namespace
// (kept in validation.rs alongside the broader path-policy helpers).
#[allow(unused_imports)]
use super::validation::is_api_route_namespace;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InviteConfig {
    #[serde(default)]
    pub token_secret: Option<String>,

    #[serde(default)]
    pub token_secret_secret: Option<String>,

    /// Whether the invite-code access gate is enabled.
    /// When false or unset, protected routes do not enforce invite-code binding.
    #[serde(default)]
    pub invite_code_gate_enabled: bool,

    /// Whether anonymous OpenAPI callers may claim a newly generated invite code.
    #[serde(default)]
    pub public_claim_enabled: bool,

    /// Maximum number of invite codes that anonymous OpenAPI callers may claim.
    #[serde(default = "default_public_claim_max_count")]
    pub public_claim_max_count: u64,

    #[serde(default = "default_invite_ttl_seconds")]
    pub default_ttl_seconds: u64,

    #[serde(default)]
    pub base_url: Option<String>,

    #[serde(default)]
    pub group_link_url: Option<String>,

    #[serde(default)]
    pub session_link_url: Option<String>,
}

impl Default for InviteConfig {
    fn default() -> Self {
        Self {
            token_secret: None,
            token_secret_secret: None,
            invite_code_gate_enabled: false,
            public_claim_enabled: false,
            public_claim_max_count: default_public_claim_max_count(),
            default_ttl_seconds: default_invite_ttl_seconds(),
            base_url: None,
            group_link_url: None,
            session_link_url: None,
        }
    }
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


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionFilesShareConfig {
    /// HMAC secret for share-token mint/consume. If unset, bootstrap logs a
    /// warning and generates a random 32-byte secret that does NOT survive
    /// restart — production deployments must set this explicitly.
    #[serde(default)]
    pub token_secret: Option<String>,

    #[serde(default)]
    pub token_secret_secret: Option<String>,

    /// Default share-token TTL in seconds. Clamped to `[60, 604800]` at mint.
    #[serde(default = "default_session_files_share_ttl")]
    pub default_ttl_seconds: u64,

    /// Public base URL used to construct share links. When None, falls back
    /// to `bcs_endpoint` or `http://{bind}:{port}` in the service layer.
    #[serde(default)]
    pub share_base_url: Option<String>,

    /// TTL (seconds) for share URLs minted at history-read time for image
    /// echo. Clamped to [60, 604800]. Independent from `default_ttl_seconds`
    /// (the share-API default) so history echo can be tuned separately.
    #[serde(default = "default_history_attachment_ttl")]
    pub history_attachment_ttl_seconds: u64,
}

impl Default for SessionFilesShareConfig {
    fn default() -> Self {
        Self {
            token_secret: None,
            token_secret_secret: None,
            default_ttl_seconds: default_session_files_share_ttl(),
            share_base_url: None,
            history_attachment_ttl_seconds: default_history_attachment_ttl(),
        }
    }
}


#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct ProviderHttpConfig {
    /// Select HTTP delivery identity reads; writes always maintain gateway bindings.
    #[serde(default)]
    pub downlink_detection_source: bcs_domain::bot_provider::DownlinkDetectionSource,
    /// Inbound HTTP header names that BCS may forward to HTTP provider webhooks.
    /// Empty by default; matching is case-insensitive.
    #[serde(default)]
    pub bypass_headers: Vec<String>,
    /// Non-sensitive routing headers explicitly approved for durable queues.
    #[serde(default)]
    pub queue_persistable_headers: Vec<String>,
}

impl ProviderHttpConfig {
    pub fn validate(&self) -> Result<(), String> {
        bcs_config_api::queued_provider_headers::validate_config(&self.bypass_headers, &self.queue_persistable_headers)?;
        for raw_name in &self.bypass_headers {
            let name = raw_name.trim();
            if name.is_empty() {
                return Err(
                    "provider_http.bypass_headers must not contain empty header names".to_string(),
                );
            }
            axum::http::HeaderName::try_from(name).map_err(|_| {
                format!("provider_http.bypass_headers contains invalid header name '{raw_name}'")
            })?;
            if is_reserved_provider_bypass_header(name) {
                return Err(format!(
                    "provider_http.bypass_headers contains reserved header name '{raw_name}'"
                ));
            }
        }
        Ok(())
    }
}

fn is_reserved_provider_bypass_header(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    matches!(
        lower.as_str(),
        "authorization"
            | "cookie"
            | "host"
            | "content-length"
            | "content-type"
            | "x-bcs-bot-token"
            | "x-bcs-service-key"
    ) || lower == "bcn"
        || lower.starts_with("bcn-")
        || lower.starts_with("x-bcn-")
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
                return Err("telemetry.otlp_traces_endpoint must use http or https".to_string());
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


#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CollaborationConfig {
    #[serde(default)]
    pub templates: CollaborationTemplatesConfig,
    #[serde(default)]
    pub fixed_loop_limits: bcs_config_api::FixedLoopLimits,
    /// Enable Loop execution and recovery.
    /// Disabled by default; deployments must opt in explicitly.
    #[serde(default)]
    pub loop_execution_enabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OpenApiV1Config {
    /// Explicitly approved Providers accepting registration by any authenticated
    /// Human. Empty means only the Provider creator/owners may issue tokens.
    #[serde(default)]
    pub registration_self_service_provider_ids: Vec<String>,
    #[serde(default = "default_openapi_v1_public_collaboration_base_url")]
    pub public_collaboration_base_url: String,
    /// Base URL for internal-collaboration endpoints that live under a
    /// different gateway path than the public openapi prefix (e.g. the no-auth
    /// shared-file download at `/api/v1/collaboration/sessions/shared-file/content`).
    /// Defaults to `public_collaboration_base_url` when unset.
    #[serde(default)]
    pub internal_collaboration_base_url: Option<String>,
}

impl Default for OpenApiV1Config {
    fn default() -> Self {
        Self {
            registration_self_service_provider_ids: Vec::new(),
            public_collaboration_base_url: default_openapi_v1_public_collaboration_base_url(),
            internal_collaboration_base_url: None,
        }
    }
}

impl OpenApiV1Config {
    pub fn validated_public_collaboration_base_url(&self) -> Result<String, String> {
        let raw = self.public_collaboration_base_url.trim();
        if raw.is_empty() {
            return Err("openapi_v1.public_collaboration_base_url must not be blank".to_string());
        }
        let mut url = url::Url::parse(raw).map_err(|_| {
            "openapi_v1.public_collaboration_base_url must be an absolute HTTP(S) URL".to_string()
        })?;
        if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
            return Err(
                "openapi_v1.public_collaboration_base_url must be an absolute HTTP(S) URL"
                    .to_string(),
            );
        }
        if !url.username().is_empty() || url.password().is_some() {
            return Err(
                "openapi_v1.public_collaboration_base_url must not contain userinfo".to_string(),
            );
        }
        if url.query().is_some() || url.fragment().is_some() {
            return Err(
                "openapi_v1.public_collaboration_base_url must not contain query or fragment"
                    .to_string(),
            );
        }
        let normalized_path = url.path().trim_end_matches('/').to_string();
        url.set_path(&normalized_path);
        Ok(url.to_string().trim_end_matches('/').to_string())
    }

    /// Returns the validated internal-collaboration base URL, falling back to
    /// `public_collaboration_base_url` when `internal_collaboration_base_url`
    /// is not set.
    pub fn validated_internal_collaboration_base_url(&self) -> Result<String, String> {
        let raw = self
            .internal_collaboration_base_url
            .as_deref()
            .map(|v| v.trim())
            .unwrap_or("");
        if raw.is_empty() {
            return self.validated_public_collaboration_base_url();
        }
        let mut url = url::Url::parse(raw).map_err(|_| {
            "openapi_v1.internal_collaboration_base_url must be an absolute HTTP(S) URL"
                .to_string()
        })?;
        if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
            return Err(
                "openapi_v1.internal_collaboration_base_url must be an absolute HTTP(S) URL"
                    .to_string(),
            );
        }
        if !url.username().is_empty() || url.password().is_some() {
            return Err(
                "openapi_v1.internal_collaboration_base_url must not contain userinfo".to_string(),
            );
        }
        if url.query().is_some() || url.fragment().is_some() {
            return Err(
                "openapi_v1.internal_collaboration_base_url must not contain query or fragment"
                    .to_string(),
            );
        }
        let normalized_path = url.path().trim_end_matches('/').to_string();
        url.set_path(&normalized_path);
        Ok(url.to_string().trim_end_matches('/').to_string())
    }
}


impl Default for CollaborationConfig {
    fn default() -> Self {
        Self {
            templates: CollaborationTemplatesConfig::default(),
            fixed_loop_limits: bcs_config_api::FixedLoopLimits::default(),
            loop_execution_enabled: false,
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


/// Gateway Principal verification trust and signing-key lookup configuration.
///
/// The signing key itself is intentionally not configuration: bootstrap resolves
/// it from `signing_key_secret` through the configured SecretAccessPort, or
/// falls back to `signing_key_env` at process startup for compatibility.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GatewayPrincipalConfig {
    /// Accepted JWT issuers. A token whose `iss` claim matches any entry is
    /// accepted. Defaults to both `gateway` and `backend` so tokens minted by
    /// either issuer are trusted out of the box.
    #[serde(default = "default_gateway_principal_issuers")]
    pub issuers: Vec<String>,

    #[serde(default = "default_gateway_principal_audience")]
    pub audience: String,

    #[serde(default = "default_gateway_principal_key_id")]
    pub key_id: String,

    #[serde(default = "default_gateway_principal_signing_key_env")]
    pub signing_key_env: String,

    #[serde(default)]
    pub signing_key_secret: Option<String>,
}

impl Default for GatewayPrincipalConfig {
    fn default() -> Self {
        Self {
            issuers: default_gateway_principal_issuers(),
            audience: default_gateway_principal_audience(),
            key_id: default_gateway_principal_key_id(),
            signing_key_env: default_gateway_principal_signing_key_env(),
            signing_key_secret: None,
        }
    }
}

impl GatewayPrincipalConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.issuers.is_empty() {
            return Err("gateway_principal.issuers must not be empty".to_string());
        }
        let mut seen: Vec<&str> = Vec::with_capacity(self.issuers.len());
        for issuer in &self.issuers {
            if issuer.trim().is_empty() {
                return Err("gateway_principal.issuers must not contain blank entries".to_string());
            }
            if seen.iter().any(|prior| *prior == issuer) {
                return Err("gateway_principal.issuers must not contain duplicates".to_string());
            }
            seen.push(issuer);
        }
        for (field, value) in [
            ("audience", &self.audience),
            ("key_id", &self.key_id),
            ("signing_key_env", &self.signing_key_env),
        ] {
            if value.trim().is_empty() {
                return Err(format!("gateway_principal.{field} must not be blank"));
            }
        }
        if self
            .signing_key_secret
            .as_deref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err("gateway_principal.signing_key_secret must not be blank".to_string());
        }
        Ok(())
    }
}


/// Group-session WebSocket JWT signing-key lookup configuration.
///
/// The signing key material is resolved through the configured SecretAccessPort
/// using `signing_key_secret` as the logical secret name.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GroupSessionWsConfig {
    #[serde(default = "default_group_session_ws_signing_key_secret")]
    pub signing_key_secret: String,
}

impl Default for GroupSessionWsConfig {
    fn default() -> Self {
        Self {
            signing_key_secret: default_group_session_ws_signing_key_secret(),
        }
    }
}

impl GroupSessionWsConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.signing_key_secret.trim().is_empty() {
            return Err("group_session_ws.signing_key_secret must not be blank".to_string());
        }
        Ok(())
    }
}


/// BCS configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BcsConfig {
    #[serde(default)]
    pub state_machine_history: bcs_config_api::StateMachineHistoryConfig,
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

    /// V1 API auth plugin chain configuration (`[api.auth]`). Absent →
    /// compat mode (preserve current Gateway-only protected routes and the
    /// existing `[gateway_principal]` key source; see spec §4.3 of the
    /// v1-api-auth-plugin-chain design). Present → `chain` is required and
    /// the bootstrap `api_auth_registry::validate_api_auth` runs against the
    /// registered production inventory at post-load validation time. The
    /// `[api]` section may carry other V1 API settings in future tasks; for
    /// Task 6 it only holds `auth`.
    #[serde(default)]
    pub api: Option<ApiConfig>,

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

    /// StateMachine history uses messages when persistence is enabled and the
    /// Session's original created_at is >= this UTC epoch-millisecond cutoff.
    /// Default: 0 (all Sessions use messages when persistence is enabled).
    #[serde(default)]
    pub state_machine_cutoff_timestamp: u64,

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
            state_machine_cutoff_timestamp: 0,
            new_participant_visible_limit: default_new_participant_visible_limit(),
            default_page_limit: default_message_page_limit(),
            max_page_limit: default_message_max_page_limit(),
        }
    }
}


#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MetricsMode {
    Pull,
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
