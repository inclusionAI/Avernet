//! Configuration for the Bot Coordination Service.
//!
//! Split by responsibility:
//! - `root` — the `BcsConfig` root struct, its serde defaults, and `Default`.
//! - `bot_authority` — trusted-app authority scope rules.
//! - `invite` — Human invite-code configuration.
//! - `session_files` — session file workspace and share-token configuration.
//! - `http` — provider HTTP webhook and CORS configuration.
//! - `telemetry` — OpenTelemetry export configuration.
//! - `collaboration` — collaboration authoring templates and OpenAPI V1 URLs.
//! - `principal` — gateway principal and group-session JWT signing lookup.
//! - `metrics` — Prometheus metrics export configuration.
//! - `message_history` — message history rollout cutoffs and page limits.
//! - `security` — AI security gateway configuration.
//! - `loading` — config loading, base-dir path normalization.
//! - `validation` — loaded-config validation rules.
//! - `tests` — configuration tests (colocated at module scope).

mod bot_authority;
mod a2a_input;
mod collaboration;
mod http;
mod invite;
mod loading;
mod message_history;
mod metrics;
mod principal;
mod root;
mod security;
mod session_files;
mod telemetry;
mod validation;

#[cfg(test)]
mod tests;

// Re-export storage config contract types for convenience.
// Names consumed only by `lib.rs` re-exports or direct submodule imports
// still stay listed so `crate::config::` paths remain stable.
#[allow(unused_imports)]
pub use bcs_config_api::{
    BcsFuseConfig, CacheConfig, DatabaseConfig, DatabaseType, RedisCacheConfig,
};

#[allow(unused_imports)]
// Re-export config contract types from bcs-config-api.
// These types were split out of this module in C2 and now live in a pure
// data-contract crate so downstream modules can depend on them without
// pulling in the rest of the `bcs` binary.
pub use bcs_config_api::{
    AuthChainConfig, AuthSdkConfig,
    ChannelConfigSection, DingTalkAccountConfig, EventingConfig, FusionProviderConfig,
    HumanNotifyConfig, LeaderElectionConfig, LlmConfig, LlmProviderType, LogOutputConfig,
    LogOutputFormat, LoggingConfig, ManifestConfig, SecretConfig, SecurityConfig,
    StructuredOutputMode, UserDirectoryConfig, UserDirectoryProviderConfig,
    deserialize_optional_secret, serialize_optional_secret,
};
#[allow(unused_imports)]
pub use bcs_config_api::{DmPolicy, RedisAuthMode};

#[allow(unused_imports)]
pub use bot_authority::{BotAuthorityConfig, TrustedAppScopeRuleConfig};
#[allow(unused_imports)]
pub use collaboration::{
    CollaborationConfig, CollaborationTemplateStorageKind, CollaborationTemplatesConfig,
    OpenApiV1Config,
};
#[allow(unused_imports)]
pub use http::{CorsConfig, ProviderHttpConfig};
pub use invite::InviteConfig;
pub use metrics::{MetricsConfig, MetricsMode};
pub use message_history::MessageHistoryConfig;
pub use principal::{GatewayPrincipalConfig, GroupSessionWsConfig};
pub use root::BcsConfig;
#[allow(unused_imports)]
pub use security::{SecurityGatewayConfig, SecurityGatewayProviderConfig};
#[allow(unused_imports)]
pub use session_files::{SessionFilesConfig, SessionFilesShareConfig};
pub use telemetry::TelemetryConfig;
