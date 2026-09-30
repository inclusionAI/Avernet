//! Configuration for the Bot Coordination Service.
//!
//! This file is the module root / facade. After the Task 1 split:
//! - [`types`] owns the struct / enum / `impl Default` definitions (excluding
//!   `impl Default for BcsConfig` and the public `impl BcsConfig` block, which
//!   live in `defaults` / `loading` / `validation`).
//! - [`defaults`] holds the `default_*` helpers and the `impl Default for
//!   BcsConfig` block.
//! - [`loading`] owns the `impl BcsConfig` loading methods (`load`,
//!   `load_with_env`, `try_load_with_env`, `from_file`) plus the load-time
//!   path normalization helpers.
//! - [`validation`] owns the `impl BcsConfig` validation methods plus the
//!   `validate_loaded_config*` / `validate_eventing_environment_policy`
//!   helpers and the policy-shaped tests.
//! - [`tests_auth`], [`tests_core`], [`tests_paths`] hold the rest of the
//!   behavior-preserving test split.
//!
//! The facade preserves every previously public path: `bcs::config::BcsConfig`,
//! `bcs::config::AuthChainConfig`, `bcs::config::validate_loaded_config`,
//! and so on.

// Re-export storage config contract types for convenience.
#[allow(unused_imports)]
pub use bcs_config_api::{
    BcsFuseConfig, CacheConfig, DatabaseConfig, DatabaseType, RedisCacheConfig,
};
// Re-export config contract types from bcs-config-api.
#[allow(unused_imports)]
pub use bcs_config_api::{
    AuthChainConfig, AuthSdkConfig,
    ApiAuthConfig, ApiConfig, GatewayApiAuthConfig,
    ChannelConfigSection, DingTalkAccountConfig, EventingConfig, FusionProviderConfig,
    HumanNotifyConfig, LeaderElectionConfig, LlmConfig, LlmProviderType, LogOutputConfig,
    LogOutputFormat, LoggingConfig, ManifestConfig, SecretConfig, SecurityConfig,
    StructuredOutputMode, UserDirectoryConfig, UserDirectoryProviderConfig,
    deserialize_optional_secret, serialize_optional_secret,
};
#[allow(unused_imports)]
pub use bcs_config_api::{DmPolicy, RedisAuthMode};

// Common std/secrecy/serde names made visible to child test modules via
// `use super::*;` (children can see parent's private `use` imports).
#[cfg(test)]
#[allow(unused_imports)]
use {
    std::collections::BTreeMap,
    std::path::{Path, PathBuf},
    secrecy::Secret,
    serde::{Deserialize, Serialize},
};

mod defaults;
mod loading;
mod tests_auth;
mod tests_core;
mod tests_paths;
mod types;
mod validation;

// Re-export leaf types + impl methods from sub-modules so existing callers
// see `crate::config::BcsConfig`, `crate::config::InviteConfig`, etc. unchanged.
#[allow(unused_imports)]
pub use defaults::*;
#[allow(unused_imports)]
pub use loading::*;
#[allow(unused_imports)]
pub use types::*;
#[allow(unused_imports)]
pub use validation::*;

// The test files are independent #[cfg(test)] sub-modules; their items are
// only the test functions and do not need re-exporting.

// Child test modules declare `use super::*;` to pull in the inherited pub use
// re-exports above (incl. bcs_config_api types and helpers). Plus they need
// access to private items in [`types`] / [`validation`] / [`loading`] (e.g.
// `validate_loaded_config`), which live in those sibling modules' scope. Test
// modules reach those via `use super::tests_core::{safe_remove_var, ...}` and
// `use super::validation::validate_loaded_config` etc. The pub use below
// re-exports private items for visibility in test children.

// Re-export validation items (validate_loaded_config etc.) under cfg(test)
// so tests inside `mod tests_*` access them via `use super::*`.
#[cfg(test)]
#[allow(unused_imports)]
pub(crate) use validation::*;

// child test fns in tests_auth/tests_core also call BcsConfig methods that
// live in loading/validation - already reachable via the type's inherent
// methods.
