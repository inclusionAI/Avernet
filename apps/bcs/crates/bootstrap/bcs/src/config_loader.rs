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
            | "app_key"
            | "caller_identity"
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
mod tests;
