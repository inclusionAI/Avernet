//! Runtime environment resolution for BCS.
//!
//! Migrated from `bcs-config-api::resolve_env` per Phase 1 R14 cleanup.

use bcs_config_api::AuthSdkEnvView;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuntimeEnv {
    Prod,
    Gray,
    Pre,
    Local,
    Dev,
}

impl RuntimeEnv {
    /// Convert to lowercase string identifier (used by infrastructure clients).
    pub fn as_str(&self) -> &'static str {
        match self {
            RuntimeEnv::Prod => "prod",
            RuntimeEnv::Gray => "gray",
            RuntimeEnv::Pre => "pre",
            RuntimeEnv::Local => "local",
            RuntimeEnv::Dev => "dev",
        }
    }
}

/// Resolve environment from known BCS env vars.
///
/// Priority: SERVER_ENV > REAL_SERVER_ENV > ALIPAY_APP_ENV
pub fn resolve_env() -> RuntimeEnv {
    let raw = std::env::var("SERVER_ENV")
        .or_else(|_| std::env::var("REAL_SERVER_ENV"))
        .or_else(|_| std::env::var("ALIPAY_APP_ENV"))
        .unwrap_or_default()
        .to_lowercase();

    match raw.as_str() {
        "prod" => RuntimeEnv::Prod,
        "gray" => RuntimeEnv::Gray,
        "pre" | "prepub" => RuntimeEnv::Pre,
        "local" => RuntimeEnv::Local,
        _ => RuntimeEnv::Dev,
    }
}

/// Backward-compatible string form for callers still using string env.
///
/// New code should use [`resolve_env`] returning [`RuntimeEnv`].
pub fn resolve_env_str() -> String {
    resolve_env().as_str().to_string()
}

/// Real process-environment view for Auth SDK completeness checks.
pub struct ProcessEnvView;

impl AuthSdkEnvView for ProcessEnvView {
    fn has(&self, var: &str) -> bool {
        std::env::var(var).is_ok_and(|s| !s.is_empty())
    }
}
