//! Multi-environment configuration loader for BCS.
//!
//! This module provides environment-aware configuration loading with deep merge support.
//! It loads a base config file and merges it with an environment-specific config file.
//!
//! # Environment Detection
//!
//! Environment is detected from environment variables in this priority order:
//! 1. `SERVER_ENV`
//! 2. `REAL_SERVER_ENV`
//! 3. `ALIPAY_APP_ENV`
//!
//! # Environment Mapping
//!
//! - `prod` → prod config
//! - `pre`/`prepub` → pre config
//! - `gray` → gray config
//! - `dev`/`stable`/`sit` → dev config
//! - empty/unknown → local config (fallback)
//!
//! # File Naming Convention
//!
//! - Base config: `bcs-config.toml` (or `.json`)
//! - Environment-specific: `bcs-config-{env}.toml` (or `.json`)
//!
//! # Example
//!
//! ```ignore
//! use bcs::config_loader::ConfigLoader;
//!
//! let loader = ConfigLoader::new(PathBuf::from("configs"));
//! let config = loader.load::<BcsConfig>().unwrap();
//! ```

use std::path::{Path, PathBuf};
use serde::de::DeserializeOwned;
use serde_json::Value;

/// Environment types for configuration loading.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Environment {
    /// Local development (fallback when no env is set)
    Local,
    /// Development environment
    Dev,
    /// Pre-production environment
    Pre,
    /// Production environment
    Prod,
    /// Gray/canary environment
    Gray,
}

impl Environment {
    /// Resolve environment from standard env vars.
    ///
    /// Priority: `SERVER_ENV` > `REAL_SERVER_ENV` > `ALIPAY_APP_ENV`
    pub fn resolve() -> Self {
        let raw = std::env::var("SERVER_ENV")
            .or_else(|_| std::env::var("REAL_SERVER_ENV"))
            .or_else(|_| std::env::var("ALIPAY_APP_ENV"))
            .unwrap_or_default()
            .to_lowercase();

        match raw.as_str() {
            "prod" => Environment::Prod,
            "pre" | "prepub" => Environment::Pre,
            "gray" => Environment::Gray,
            "dev" | "stable" | "sit" => Environment::Dev,
            _ => Environment::Local,
        }
    }

    /// Get the config file suffix for this environment.
    ///
    /// Returns `-local`, `-dev`, `-pre`, `-prod`, `-gray`, or empty string for base.
    pub fn config_suffix(&self) -> &'static str {
        match self {
            Environment::Local => "-local",
            Environment::Dev => "-dev",
            Environment::Pre => "-pre",
            Environment::Prod => "-prod",
            Environment::Gray => "-gray",
        }
    }

    /// Get the environment name as a string.
    pub fn as_str(&self) -> &'static str {
        match self {
            Environment::Local => "local",
            Environment::Dev => "dev",
            Environment::Pre => "pre",
            Environment::Prod => "prod",
            Environment::Gray => "gray",
        }
    }
}

impl std::fmt::Display for Environment {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

/// Deep merge two JSON values.
///
/// - Objects: recursively merge keys
/// - Arrays: override with new array (config arrays typically replace, not extend)
/// - Primitives: override with new value
pub fn deep_merge(base: &mut Value, override_val: &Value) {
    match (base, override_val) {
        (Value::Object(base_map), Value::Object(override_map)) => {
            for (key, override_value) in override_map {
                if let Some(base_value) = base_map.get_mut(key) {
                    deep_merge(base_value, override_value);
                } else {
                    base_map.insert(key.clone(), override_value.clone());
                }
            }
        }
        (base, override_val) => {
            *base = override_val.clone();
        }
    }
}

/// Return a copy of `value` with sensitive config fields redacted for logging.
pub(crate) fn redact_sensitive_values(value: &Value) -> Value {
    let mut redacted = value.clone();
    redact_sensitive_values_in_place(&mut redacted);
    redacted
}

fn redact_sensitive_values_in_place(value: &mut Value) {
    match value {
        Value::Object(map) => {
            for (key, child) in map.iter_mut() {
                if is_sensitive_config_key(key) {
                    if !child.is_null() {
                        *child = Value::String("<redacted>".to_string());
                    }
                } else {
                    redact_sensitive_values_in_place(child);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                redact_sensitive_values_in_place(item);
            }
        }
        _ => {}
    }
}

fn is_sensitive_config_key(key: &str) -> bool {
    let lower = key.to_ascii_lowercase();
    matches!(
        lower.as_str(),
        "password"
            | "api_key"
            | "auth_token"
            | "client_secret"
            | "gateway_client_secret"
            | "extra_headers"
    ) || lower.ends_with("_password")
        || lower.ends_with("_api_key")
        || lower.ends_with("_secret")
        || lower.ends_with("_token")
}

/// Parse raw config file content (TOML or JSON) into a JSON `Value`.
///
/// TOML is converted through `toml::Value` so the result is merge-compatible
/// with JSON configs. `path_display` is used only for error messages.
pub(crate) fn parse_config_content(
    ext: &str,
    content: &str,
    path_display: &str,
) -> Result<Value, ConfigLoadError> {
    match ext.to_lowercase().as_str() {
        "json" => serde_json::from_str(content)
            .map_err(|e| ConfigLoadError::Parse(path_display.to_string(), e.to_string())),
        "toml" => {
            // Convert TOML to JSON Value for merge compatibility
            let toml_value: toml::Value = toml::from_str(content)
                .map_err(|e| ConfigLoadError::Parse(path_display.to_string(), e.to_string()))?;
            serde_json::to_value(toml_value)
                .map_err(|e| ConfigLoadError::Parse(path_display.to_string(), e.to_string()))
        }
        _ => Err(ConfigLoadError::UnsupportedFormat(path_display.to_string())),
    }
}

/// Recursively expand `${VAR}` / `${VAR:-default}` references in every string
/// value of a parsed config tree.
///
/// This runs after the env-overlay deep-merge and before deserialization, so
/// only final (env-overridden) string values are resolved from the process
/// environment, and the typed config struct receives the expanded values.
/// Non-string values (numbers, booleans, null) are left untouched, which means
/// `${VAR}` only works for string-typed fields; numeric/boolean fields must
/// continue to use the typed `BCS_*` env overrides (e.g. `BCS_REDIS_PORT`).
/// No nested expansion is performed on substituted values.
pub(crate) fn expand_env_vars(value: &mut Value) -> Result<(), ConfigLoadError> {
    match value {
        Value::Object(map) => {
            for (_, child) in map.iter_mut() {
                expand_env_vars(child)?;
            }
            Ok(())
        }
        Value::Array(items) => {
            for item in items.iter_mut() {
                expand_env_vars(item)?;
            }
            Ok(())
        }
        Value::String(s) => {
            *s = expand_env_string(s)?;
            Ok(())
        }
        _ => Ok(()),
    }
}

/// Expand `${VAR}` / `${VAR:-default}` tokens within a single string.
///
/// - `${VAR}` resolves to the env var's value. A missing variable is a hard
///   error (fail fast) so a missing secret aborts startup instead of silently
///   becoming an empty string.
/// - `${VAR:-default}` uses `default` when the variable is unset or empty,
///   providing the opt-out for optional values.
/// - An empty-but-set variable with no default expands to the empty string
///   (the variable is explicitly set).
/// - A literal `${` that is not a well-formed reference (`${NAME}` or
///   `${NAME:-...}`) is a hard error.
fn expand_env_string(input: &str) -> Result<String, ConfigLoadError> {
    let mut out = String::with_capacity(input.len());
    let mut rest = input;
    while let Some(start) = rest.find("${") {
        out.push_str(&rest[..start]);
        let after = &rest[start + 2..];
        let end = after
            .find('}')
            .ok_or_else(|| ConfigLoadError::EnvExpansion(format!(
                "config value has an unterminated environment reference: {rest:?}"
            )))?;
        let token = &after[..end];
        let remaining = &after[end + 1..];
        let (name, default) = match token.split_once(":-") {
            Some((n, d)) => (n, Some(d)),
            None => (token, None),
        };
        if !is_valid_env_name(name) {
            return Err(ConfigLoadError::EnvExpansion(format!(
                "invalid environment variable name `{name}` in config reference"
            )));
        }
        let segment = match std::env::var(name) {
            Ok(v) if !v.is_empty() => v,
            Ok(v) => match default {
                Some(d) => d.to_string(),
                None => v, // empty-but-set variable with no default
            },
            Err(_) => match default {
                Some(d) => d.to_string(),
                None => {
                    return Err(ConfigLoadError::EnvExpansion(format!(
                        "environment variable `{name}` referenced in config is not set"
                    )))
                }
            },
        };
        out.push_str(&segment);
        rest = remaining;
    }
    out.push_str(rest);
    Ok(out)
}

fn is_valid_env_name(name: &str) -> bool {
    let mut chars = name.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// Configuration loader with multi-environment support.
pub struct ConfigLoader {
    config_dir: PathBuf,
    environment: Environment,
}

/// Result of loading configuration with detailed info.
pub struct ConfigLoadResult<T> {
    /// The loaded configuration
    pub config: T,
    /// The config directory
    #[allow(dead_code)]
    pub config_dir: PathBuf,
    /// The resolved environment
    pub environment: Environment,
    /// Path to the base config file
    pub base_config_path: PathBuf,
    /// Path to the environment-specific config file (if loaded)
    pub env_config_path: Option<PathBuf>,
    /// The final merged JSON value
    pub merged_value: Value,
}

impl ConfigLoader {
    /// Create a new config loader for the given directory.
    ///
    /// Environment is automatically resolved from environment variables.
    pub fn new(config_dir: PathBuf) -> Self {
        Self {
            config_dir,
            environment: Environment::resolve(),
        }
    }

    /// Create a loader with an explicit environment (for testing).
    #[allow(dead_code)]
    pub fn with_environment(mut self, env: Environment) -> Self {
        self.environment = env;
        self
    }

    /// Get the current environment.
    #[allow(dead_code)]
    pub fn environment(&self) -> Environment {
        self.environment
    }

    /// Get the config directory.
    #[allow(dead_code)]
    pub fn config_dir(&self) -> &Path {
        &self.config_dir
    }

    /// Load and merge configuration files.
    ///
    /// This method:
    /// 1. Loads the base config file (required)
    /// 2. Loads the environment-specific config file (optional)
    /// 3. Deep merges them (env config overrides base)
    /// 4. Deserializes to the target type
    #[allow(dead_code)]
    pub fn load<T: DeserializeOwned>(&self) -> Result<T, ConfigLoadError> {
        let result = self.load_with_info()?;
        Ok(result.config)
    }

    /// Load configuration with detailed info about loaded files.
    ///
    /// Returns both the config and information about which files were loaded.
    pub fn load_with_info<T: DeserializeOwned>(&self) -> Result<ConfigLoadResult<T>, ConfigLoadError> {
        // 1. Load base config (required)
        let base_path = self.find_base_config()?;
        let base_value = self.load_config_value(&base_path)?;

        // 2. Load environment-specific config (optional)
        let env_path = self.find_env_config();
        let (mut merged_value, env_config_path) = if env_path.exists() {
            let env_value = self.load_config_value(&env_path)?;
            let mut merged = base_value;
            deep_merge(&mut merged, &env_value);
            (merged, Some(env_path))
        } else {
            (base_value, None)
        };

        // Expand `${VAR}` / `${VAR:-default}` references in string values after
        // the env-overlay merge, so only the final resolved values are read
        // from the process environment.
        expand_env_vars(&mut merged_value)?;

        // 3. Deserialize to target type
        let config = serde_json::from_value(merged_value.clone())
            .map_err(|e| ConfigLoadError::Deserialize(e.to_string()))?;

        Ok(ConfigLoadResult {
            config,
            config_dir: self.config_dir.clone(),
            environment: self.environment,
            base_config_path: base_path,
            env_config_path,
            merged_value,
        })
    }

    /// Find the base config file (bcs-config.toml or bcs-config.json).
    fn find_base_config(&self) -> Result<PathBuf, ConfigLoadError> {
        // Try TOML first, then JSON
        for ext in ["toml", "json"] {
            let path = self.config_dir.join(format!("bcs-config.{}", ext));
            if path.exists() {
                return Ok(path);
            }
        }
        Err(ConfigLoadError::BaseConfigNotFound(self.config_dir.display().to_string()))
    }

    /// Find the environment-specific config file.
    ///
    /// Returns the path even if it doesn't exist (for existence check).
    fn find_env_config(&self) -> PathBuf {
        let suffix = self.environment.config_suffix();

        // Try TOML first, then JSON
        for ext in ["toml", "json"] {
            let path = self.config_dir.join(format!("bcs-config{}.{}", suffix, ext));
            if path.exists() {
                return path;
            }
        }

        // Return default path (may not exist)
        self.config_dir.join(format!("bcs-config{}.toml", suffix))
    }

    /// Load a config file and convert to JSON Value.
    fn load_config_value(&self, path: &Path) -> Result<Value, ConfigLoadError> {
        let content = std::fs::read_to_string(path)
            .map_err(|e| ConfigLoadError::Io(path.display().to_string(), e))?;

        let ext = path.extension()
            .and_then(|e| e.to_str())
            .unwrap_or("toml");

        parse_config_content(ext, &content, &path.display().to_string())
    }
}

/// Errors that can occur during config loading.
#[derive(Debug, thiserror::Error)]
pub enum ConfigLoadError {
    /// Base config file not found
    #[error("Base config file not found in {0}")]
    BaseConfigNotFound(String),

    /// IO error while reading file
    #[error("IO error reading {0}: {1}")]
    Io(String, std::io::Error),

    /// Failed to parse config file
    #[error("Failed to parse {0}: {1}")]
    Parse(String, String),

    /// Failed to deserialize config
    #[error("Failed to deserialize config: {0}")]
    Deserialize(String),

    /// Unsupported config format
    #[error("Unsupported config format: {0}")]
    UnsupportedFormat(String),

    /// Failed to expand a `${VAR}` environment reference in config values
    #[error("Config environment reference expansion failed: {0}")]
    EnvExpansion(String),
}

impl ConfigLoadError {
    /// Create a BaseConfigNotFound error with the config directory path.
    #[allow(dead_code)]
    pub fn base_not_found(config_dir: &Path) -> Self {
        ConfigLoadError::BaseConfigNotFound(config_dir.display().to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serial_test::serial;

    // =========================================================================
    // Environment tests
    // =========================================================================

    #[test]
    #[serial]
    fn test_environment_resolve_prod() {
        unsafe { std::env::set_var("SERVER_ENV", "prod"); }
        assert_eq!(Environment::resolve(), Environment::Prod);
        unsafe { std::env::remove_var("SERVER_ENV"); }
    }

    #[test]
    #[serial]
    fn test_environment_resolve_gray() {
        unsafe { std::env::set_var("SERVER_ENV", "gray"); }
        assert_eq!(Environment::resolve(), Environment::Gray);
        unsafe { std::env::remove_var("SERVER_ENV"); }
    }

    #[test]
    #[serial]
    fn test_environment_resolve_pre() {
        unsafe { std::env::set_var("SERVER_ENV", "pre"); }
        assert_eq!(Environment::resolve(), Environment::Pre);
        unsafe { std::env::remove_var("SERVER_ENV"); }

        unsafe { std::env::set_var("SERVER_ENV", "prepub"); }
        assert_eq!(Environment::resolve(), Environment::Pre);
        unsafe { std::env::remove_var("SERVER_ENV"); }
    }

    #[test]
    #[serial]
    fn test_environment_resolve_dev() {
        unsafe { std::env::set_var("SERVER_ENV", "dev"); }
        assert_eq!(Environment::resolve(), Environment::Dev);
        unsafe { std::env::remove_var("SERVER_ENV"); }

        unsafe { std::env::set_var("SERVER_ENV", "stable"); }
        assert_eq!(Environment::resolve(), Environment::Dev);
        unsafe { std::env::remove_var("SERVER_ENV"); }

        unsafe { std::env::set_var("SERVER_ENV", "sit"); }
        assert_eq!(Environment::resolve(), Environment::Dev);
        unsafe { std::env::remove_var("SERVER_ENV"); }
    }

    #[test]
    #[serial]
    fn test_environment_resolve_local_fallback() {
        unsafe {
            std::env::remove_var("SERVER_ENV");
            std::env::remove_var("REAL_SERVER_ENV");
            std::env::remove_var("ALIPAY_APP_ENV");
        }
        assert_eq!(Environment::resolve(), Environment::Local);
    }

    #[test]
    #[serial]
    fn test_environment_resolve_unknown_maps_to_local() {
        unsafe { std::env::set_var("SERVER_ENV", "staging"); }
        assert_eq!(Environment::resolve(), Environment::Local);
        unsafe { std::env::remove_var("SERVER_ENV"); }
    }

    #[test]
    #[serial]
    fn test_environment_resolve_fallback_chain() {
        unsafe {
            std::env::remove_var("SERVER_ENV");
            std::env::remove_var("REAL_SERVER_ENV");
            std::env::set_var("ALIPAY_APP_ENV", "prod");
        }
        assert_eq!(Environment::resolve(), Environment::Prod);
        unsafe { std::env::remove_var("ALIPAY_APP_ENV"); }
    }

    #[test]
    fn test_environment_config_suffix() {
        assert_eq!(Environment::Local.config_suffix(), "-local");
        assert_eq!(Environment::Dev.config_suffix(), "-dev");
        assert_eq!(Environment::Pre.config_suffix(), "-pre");
        assert_eq!(Environment::Prod.config_suffix(), "-prod");
        assert_eq!(Environment::Gray.config_suffix(), "-gray");
    }

    // =========================================================================
    // Deep merge tests
    // =========================================================================

    #[test]
    fn test_deep_merge_objects() {
        let mut base = serde_json::json!({"a": 1, "b": {"nested": 2}});
        let override_val = serde_json::json!({"b": {"nested": 3, "new": 4}, "c": 5});
        deep_merge(&mut base, &override_val);
        assert_eq!(base, serde_json::json!({"a": 1, "b": {"nested": 3, "new": 4}, "c": 5}));
    }

    #[test]
    fn test_deep_merge_arrays_override() {
        let mut base = serde_json::json!({"items": [1, 2, 3]});
        let override_val = serde_json::json!({"items": [4, 5]});
        deep_merge(&mut base, &override_val);
        assert_eq!(base, serde_json::json!({"items": [4, 5]}));
    }

    #[test]
    fn test_deep_merge_primitives() {
        let mut base = serde_json::json!({"name": "old", "count": 10});
        let override_val = serde_json::json!({"name": "new", "count": 20});
        deep_merge(&mut base, &override_val);
        assert_eq!(base, serde_json::json!({"name": "new", "count": 20}));
    }

    #[test]
    fn test_deep_merge_nested_objects() {
        let mut base = serde_json::json!({
            "server": {
                "host": "localhost",
                "port": 8080,
                "tls": {
                    "enabled": false,
                    "cert": "old.pem"
                }
            }
        });
        let override_val = serde_json::json!({
            "server": {
                "port": 9090,
                "tls": {
                    "enabled": true
                }
            }
        });
        deep_merge(&mut base, &override_val);
        assert_eq!(base, serde_json::json!({
            "server": {
                "host": "localhost",
                "port": 9090,
                "tls": {
                    "enabled": true,
                    "cert": "old.pem"
                }
            }
        }));
    }

    #[test]
    fn test_deep_merge_null_override() {
        let mut base = serde_json::json!({"a": 1, "b": 2});
        let override_val = serde_json::json!({"a": null});
        deep_merge(&mut base, &override_val);
        assert_eq!(base, serde_json::json!({"a": null, "b": 2}));
    }

    #[test]
    fn test_redact_sensitive_values_masks_nested_passwords_without_mutating_source() {
        let source = serde_json::json!({
            "bind": "0.0.0.0",
            "cache": {
                "redis": {
                    "connection": {
                        "auth_mode": "redis",
                        "password": "redis-pass"
                    },
                    "provider": {
                        "password": "secondary-pass"
                    }
                }
            }
        });

        let redacted = redact_sensitive_values(&source);

        assert_eq!(redacted["bind"], "0.0.0.0");
        assert_eq!(
            redacted["cache"]["redis"]["connection"]["password"],
            "<redacted>"
        );
        assert_eq!(
            redacted["cache"]["redis"]["provider"]["password"],
            "<redacted>"
        );
        assert_eq!(source["cache"]["redis"]["connection"]["password"], "redis-pass");
    }

    #[test]
    fn test_redact_telemetry_extra_headers_as_a_sensitive_container() {
        let source = serde_json::json!({
            "telemetry": {
                "otlp_traces_endpoint": "https://collector.example.com/v1/traces",
                "extra_headers": {
                    "x-collector-route": "collector-local",
                    "authorization": "Bearer secret"
                }
            }
        });

        let redacted = redact_sensitive_values(&source);

        assert_eq!(
            redacted["telemetry"]["otlp_traces_endpoint"],
            "https://collector.example.com/v1/traces"
        );
        assert_eq!(redacted["telemetry"]["extra_headers"], "<redacted>");
        assert!(source["telemetry"]["extra_headers"].is_object());
    }

    // =========================================================================
    // ConfigLoader tests (require temp files)
    // =========================================================================

    #[test]
    fn test_config_loader_base_only() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("bcs-config.toml"), r#"
            bind = "0.0.0.0"
            port = 21000
            bots_base_dir = "/bots"
        "#).unwrap();

        let loader = ConfigLoader::new(dir.path().to_path_buf())
            .with_environment(Environment::Local);

        let config: serde_json::Value = loader.load().unwrap();
        assert_eq!(config["bind"], "0.0.0.0");
        assert_eq!(config["port"], 21000);
    }

    #[test]
    fn test_config_loader_merge() {
        let dir = tempfile::tempdir().unwrap();

        // Base config
        std::fs::write(dir.path().join("bcs-config.toml"), r#"
            bind = "127.0.0.1"
            port = 21000
            bots_base_dir = "/bots"

            [cache]
            type = "redis"

            [cache.redis.connection]
            type = "direct"
            host = "localhost"
            port = 6379
        "#).unwrap();

        // Dev override
        std::fs::write(dir.path().join("bcs-config-dev.toml"), r#"
            port = 22000

            [cache.redis.connection]
            auth_mode = "disabled"
        "#).unwrap();

        let loader = ConfigLoader::new(dir.path().to_path_buf())
            .with_environment(Environment::Dev);

        let config: serde_json::Value = loader.load().unwrap();

        // Merged values
        assert_eq!(config["bind"], "127.0.0.1");  // From base
        assert_eq!(config["port"], 22000);        // Overridden
        assert_eq!(config["cache"]["redis"]["connection"]["host"], "localhost");  // From base
        assert_eq!(config["cache"]["redis"]["connection"]["port"], 6379);  // From base
        assert_eq!(config["cache"]["redis"]["connection"]["auth_mode"], "disabled");  // Merged in
    }

    #[test]
    fn test_config_loader_missing_env_config() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("bcs-config.toml"), r#"
            bind = "0.0.0.0"
            port = 21000
            bots_base_dir = "/bots"
        "#).unwrap();

        let loader = ConfigLoader::new(dir.path().to_path_buf())
            .with_environment(Environment::Prod);

        // Should succeed with base config only
        let config: serde_json::Value = loader.load().unwrap();
        assert_eq!(config["bind"], "0.0.0.0");
    }

    #[test]
    fn test_config_loader_missing_base_config() {
        let dir = tempfile::tempdir().unwrap();
        let loader = ConfigLoader::new(dir.path().to_path_buf());

        let result: Result<serde_json::Value, _> = loader.load();
        assert!(result.is_err());
    }

    // =========================================================================
    // ${VAR} environment-reference expansion tests
    // =========================================================================

    #[test]
    fn expand_env_string_resolves_a_present_variable() {
        unsafe { std::env::set_var("BCS_TEST_CFG_TOKEN", "super-secret"); }
        assert_eq!(
            expand_env_string("prefix-${BCS_TEST_CFG_TOKEN}-suffix").unwrap(),
            "prefix-super-secret-suffix"
        );
        unsafe { std::env::remove_var("BCS_TEST_CFG_TOKEN"); }
    }

    #[test]
    fn expand_env_string_resolves_multiple_tokens_and_keeps_literal_text() {
        unsafe {
            std::env::set_var("BCS_TEST_CFG_HOST", "example.com");
            std::env::set_var("BCS_TEST_CFG_PORT", "8443");
        }
        assert_eq!(
            expand_env_string("https://${BCS_TEST_CFG_HOST}:${BCS_TEST_CFG_PORT}/path").unwrap(),
            "https://example.com:8443/path"
        );
        unsafe {
            std::env::remove_var("BCS_TEST_CFG_HOST");
            std::env::remove_var("BCS_TEST_CFG_PORT");
        }
    }

    #[test]
    fn expand_env_string_uses_default_when_unset() {
        unsafe { std::env::remove_var("BCS_TEST_CFG_ABSENT"); }
        assert_eq!(
            expand_env_string("${BCS_TEST_CFG_ABSENT:-fallback}").unwrap(),
            "fallback"
        );
    }

    #[test]
    fn expand_env_string_uses_env_over_default_when_set() {
        unsafe { std::env::set_var("BCS_TEST_CFG_PRESENT", "real"); }
        assert_eq!(
            expand_env_string("${BCS_TEST_CFG_PRESENT:-fallback}").unwrap(),
            "real"
        );
        unsafe { std::env::remove_var("BCS_TEST_CFG_PRESENT"); }
    }

    #[test]
    fn expand_env_string_errors_on_unset_without_default() {
        unsafe { std::env::remove_var("BCS_TEST_CFG_REQUIRED"); }
        let err = expand_env_string("${BCS_TEST_CFG_REQUIRED}").unwrap_err();
        let message = err.to_string();
        assert!(message.contains("BCS_TEST_CFG_REQUIRED"), "error should name the missing var: {message}");
    }

    #[test]
    fn expand_env_string_errors_on_invalid_name() {
        // Names must start with a letter or underscore.
        let err = expand_env_string("${1INVALID}").unwrap_err();
        assert!(err.to_string().contains("invalid environment variable name"));
    }

    #[test]
    fn expand_env_string_errors_on_unterminated_reference() {
        let err = expand_env_string("prefix-${NO_CLOSE").unwrap_err();
        assert!(err.to_string().contains("unterminated"));
    }

    #[test]
    fn expand_env_string_leaves_text_without_references_untouched() {
        assert_eq!(
            expand_env_string("plain value with no reference").unwrap(),
            "plain value with no reference"
        );
    }

    #[test]
    fn expand_env_vars_walks_objects_and_arrays_and_skips_non_strings() {
        unsafe { std::env::set_var("BCS_TEST_CFG_VAL", "expanded"); }
        let mut value = serde_json::json!({
            "url": "https://${BCS_TEST_CFG_VAL}/api",
            "keys": ["${BCS_TEST_CFG_VAL}", "literal"],
            "port": 21000,
            "nested": { "deep": "${BCS_TEST_CFG_VAL}" },
        });
        expand_env_vars(&mut value).unwrap();
        assert_eq!(value["url"], "https://expanded/api");
        assert_eq!(value["keys"][0], "expanded");
        assert_eq!(value["keys"][1], "literal");
        assert_eq!(value["port"], 21000); // numeric values are untouched
        assert_eq!(value["nested"]["deep"], "expanded");
        unsafe { std::env::remove_var("BCS_TEST_CFG_VAL"); }
    }

    #[test]
    #[serial]
    fn loader_expands_references_in_loaded_string_fields() {
        unsafe { std::env::set_var("BCS_TEST_CFG_BOTCHAT", "https://botchat.example.com"); }
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("bcs-config.toml"),
            r#"
                bind = "0.0.0.0"
                port = 21000
                bots_base_dir = "/bots"
                botchat_url = "${BCS_TEST_CFG_BOTCHAT}"
            "#,
        )
        .unwrap();

        let loader = ConfigLoader::new(dir.path().to_path_buf())
            .with_environment(Environment::Prod);
        let config: serde_json::Value = loader.load().unwrap();

        assert_eq!(config["botchat_url"], "https://botchat.example.com");
        unsafe { std::env::remove_var("BCS_TEST_CFG_BOTCHAT"); }
    }

    #[test]
    #[serial]
    fn loader_errors_when_a_required_reference_is_unset() {
        unsafe { std::env::remove_var("BCS_TEST_CFG_MISSING"); }
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("bcs-config.toml"),
            r#"
                bind = "0.0.0.0"
                port = 21000
                bots_base_dir = "/bots"
                botchat_url = "${BCS_TEST_CFG_MISSING}"
            "#,
        )
        .unwrap();

        let loader = ConfigLoader::new(dir.path().to_path_buf())
            .with_environment(Environment::Prod);
        let result: Result<serde_json::Value, _> = loader.load();
        let err = result.unwrap_err().to_string();
        assert!(err.contains("BCS_TEST_CFG_MISSING"), "error should name the missing var: {err}");
    }

    #[test]
    fn test_real_local_config_routes_chat_digest_to_dedicated_file() {
        let config_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../configs/bcs-config-local.toml");
        let content = std::fs::read_to_string(config_path).unwrap();
        let config: crate::config::BcsConfig = toml::from_str(&content).unwrap();

        assert_eq!(
            config.logging.tags.get("bcs_chat_digest").map(String::as_str),
            Some("off")
        );

        let digest = config
            .logging
            .outputs
            .iter()
            .find(|output| output.name == "chat-digest")
            .expect("local config should include chat-digest output");

        assert_eq!(digest.path, "./logs");
        assert_eq!(digest.file, "bcs-chat-digest.log");
        assert_eq!(digest.targets, vec!["bcs_chat_digest"]);
    }

    #[test]
    fn test_real_local_config_selects_env_secret_backend() {
        let config_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../configs/bcs-config-local.toml");
        let content = std::fs::read_to_string(config_path).unwrap();
        let config: crate::config::BcsConfig = toml::from_str(&content).unwrap();

        assert_eq!(config.secret.provider, "env");
        assert_eq!(
            config.secret.providers["env"]["prefix"].as_str(),
            Some("BCS_SECRET_")
        );
    }

    #[test]
    fn test_real_local_config_routes_all_errors_to_common_error_file() {
        let config_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../configs/bcs-config-local.toml");
        let content = std::fs::read_to_string(config_path).unwrap();
        let config: crate::config::BcsConfig = toml::from_str(&content).unwrap();

        let common_error = config
            .logging
            .outputs
            .iter()
            .find(|output| output.name == "common-error")
            .expect("local config should include common-error output");

        assert_eq!(common_error.path, "./logs");
        assert_eq!(common_error.file, "common-error.log");
        assert_eq!(common_error.level, "error");
        assert_eq!(common_error.targets, vec!["*"]);
    }

    #[test]
    fn test_real_base_config_accepts_dingtalk_lab_json_override() {
        let source_config_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../configs");
        let dir = tempfile::tempdir().unwrap();
        std::fs::copy(
            source_config_dir.join("bcs-config-local.toml"),
            dir.path().join("bcs-config.toml"),
        )
        .unwrap();
        std::fs::write(
            dir.path().join("bcs-config-local.json"),
            r#"{
              "bind": "127.0.0.1",
              "port": 21992,
              "bots_base_dir": "/tmp/dingtalk-lab/data/bots",
              "strict_container_validation": false,
              "bcs_endpoint": "http://127.0.0.1:21992",
              "botchat_url": "http://127.0.0.1:21992",
              "provider_stream_gray_enabled": true,
              "provider_stream_gray_created_by": ["197262", "999001"],
              "leader_election": {"enabled": false},
              "cache": {"type": "memory"},
              "database": {
                "type": "sqlite",
                "sqlite": {"path": "/tmp/dingtalk-lab/bcs.db"}
              },
              "collaboration": {
                "templates": {
                  "storage_type": "file",
                  "base_dir": "seeds/collaboration-templates",
                  "default_language": "zh-CN"
                }
              },
              "auth_sdk": {
                "client_id": null,
                "secret_key": null,
                "app_key": null,
                "app_name": null,
                "remote_server_domain": null,
                "use_remote_login_check": false
              },
              "auth": {"chain": ["local"], "require_authentication": false},
              "bcsfuse": {"enabled": false},
              "security_gateway": {"provider": "noop"},
              "channels": {"enabled": true, "providers": {"dingtalk": {"enabled": true}}},
              "dingtalk_accounts": [
                {
                  "account_id": "dummy_robot_code",
                  "client_id": "dummy_app_key",
                  "client_secret": "dummy_app_secret",
                  "robot_code": "dummy_robot_code",
                  "card_template_id": "dummy_template_id",
                  "card_template_key": "content",
                  "enable_streaming_cards": true,
                  "enable_scene_group": true,
                  "dm_policy": "open",
                  "allowlist": ["*"]
                }
              ]
            }"#,
        )
        .unwrap();

        let loader = ConfigLoader::new(dir.path().to_path_buf()).with_environment(Environment::Local);
        let config: crate::config::BcsConfig = loader.load().unwrap();

        assert_eq!(config.port, 21992);
        assert!(config.channels.dingtalk_enabled());
        assert!(config.channels.enabled_provider_configs().contains_key("dingtalk"));
        assert_eq!(config.dingtalk_accounts.len(), 1);
    }
}
