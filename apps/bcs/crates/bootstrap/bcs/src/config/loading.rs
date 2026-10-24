use std::path::{Path, PathBuf};

use super::root::BcsConfig;
use super::validation::validate_loaded_config_for_environment;

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

    /// Validate the run-store backend selectors. Unknown values (e.g. a typo
    /// like `"persisent"`) must fail startup rather than silently fall back to
    /// `memory`, since these control the advertised governance guarantee.
    pub fn validate_run_store_selectors(&self) -> Result<(), String> {
        match self.async_chat_run_store.as_str() {
            "memory" | "persistent" => {}
            other => {
                return Err(format!(
                    "async_chat_run_store must be 'memory' or 'persistent', got '{other}'"
                ))
            }
        }
        match self.bot_run_context_store.as_str() {
            "memory" | "redis" => {}
            other => {
                return Err(format!(
                    "bot_run_context_store must be 'memory' or 'redis', got '{other}'"
                ))
            }
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
        validate_loaded_config_for_environment(&config, result.environment)
            .map_err(|err| err.to_string())?;
        config.validate_run_store_selectors()?;
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

                if let Err(e) = validate_loaded_config_for_environment(&config, result.environment)
                {
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
        let path_display = path.display().to_string();

        // Parse to a JSON `Value`, expand `${VAR}` references, then deserialize.
        // Routing through `Value` (instead of parsing straight into `Self`) keeps
        // env-reference expansion consistent with the multi-env loader path and
        // applies it to standalone/single-file configs too.
        let parse_and_expand = |fmt: &str| -> Result<Self, Box<dyn std::error::Error>> {
            let mut value = crate::config_loader::parse_config_content(fmt, &content, &path_display)?;
            crate::config_loader::expand_env_vars(&mut value)?;
            Ok(serde_json::from_value(value)?)
        };

        let mut config = match ext.to_lowercase().as_str() {
            "json" => parse_and_expand("json")?,
            "toml" => parse_and_expand("toml")?,
            // Unknown extension: try JSON first, then TOML. Only fall through to
            // TOML when the content fails to *parse* as JSON — a valid JSON
            // document must surface its own expansion/deserialize errors instead
            // of being retried (and mis-reported) as a TOML parse error.
            _ => match crate::config_loader::parse_config_content(
                "json",
                &content,
                &path_display,
            ) {
                Ok(mut value) => {
                    crate::config_loader::expand_env_vars(&mut value)?;
                    serde_json::from_value(value)?
                }
                Err(_) => parse_and_expand("toml")?,
            },
        };

        let local_path_base_dir = local_path_base_dir_for_config_file(path);
        normalize_local_paths(&mut config, &local_path_base_dir);
        validate_loaded_config_for_environment(
            &config,
            crate::config_loader::Environment::resolve(),
        )?;
        Ok(config)
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
