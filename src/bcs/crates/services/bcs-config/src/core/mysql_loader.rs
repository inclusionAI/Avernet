use std::path::Path;

use bcs_config_api::mysql::MysqlDbConfig;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ConfigLoadError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("yaml parse error: {0}")]
    Yaml(#[from] serde_yaml::Error),
    #[error("invalid value: {0}")]
    Invalid(String),
}

/// Loader for MySQL/OceanBase config.
///
/// Owns YAML parsing and env-var fallback that used to live in
/// the runtime database config adapter. Pure leaf [`MysqlDbConfig`] stays in
/// `bcs-config-api`.
pub struct MysqlDbLoader;

impl MysqlDbLoader {
    pub async fn from_yaml(path: impl AsRef<Path>) -> Result<MysqlDbConfig, ConfigLoadError> {
        let content = tokio::fs::read_to_string(path).await?;
        let cfg: MysqlDbConfig = serde_yaml::from_str(&content)?;
        Ok(cfg)
    }

    /// Load database config from `BCS_DB_*` environment variables.
    pub fn from_env() -> Result<MysqlDbConfig, ConfigLoadError> {
        let mut cfg = MysqlDbConfig::new();

        if let Some(db_name) = env_var("BCS_DB_NAME") {
            cfg.database = db_name;

            if let Some(user) = env_var("BCS_DB_USER") {
                cfg.connection.user = Some(user);
            }
            if let Some(host) = env_var("BCS_DB_HOST") {
                cfg.connection.host = Some(host);
            }
            if let Some(port) = env_var("BCS_DB_PORT") {
                cfg.connection.port = Some(port.parse().map_err(|e: std::num::ParseIntError| {
                    ConfigLoadError::Invalid(e.to_string())
                })?);
            }
            if let Some(password) = env_var("BCS_DB_PASSWORD") {
                cfg.connection.password = Some(password);
            }
            if let Some(stmt_cache_size) = env_var("BCS_DB_STMT_CACHE_SIZE") {
                cfg.stmt_cache_size =
                    stmt_cache_size
                        .parse()
                        .map_err(|e: std::num::ParseIntError| {
                            ConfigLoadError::Invalid(e.to_string())
                        })?;
            }
            if let Some(statement_protocol) = env_var("BCS_DB_STATEMENT_PROTOCOL") {
                cfg.statement_protocol = statement_protocol
                    .parse()
                    .map_err(ConfigLoadError::Invalid)?;
            }
        }

        Ok(cfg)
    }
}

fn env_var(name: &str) -> Option<String> {
    std::env::var(name).ok()
}
