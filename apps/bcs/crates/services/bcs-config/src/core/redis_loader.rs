use bcs_config_api::{RedisAuthMode, RedisCacheConfig, RedisRouteType};

use super::mysql_loader::ConfigLoadError;

pub struct RedisCacheLoader;

impl RedisCacheLoader {
    /// Load `RedisCacheConfig` from `BCS_REDIS_*` environment variables.
    pub fn config_from_env() -> Result<RedisCacheConfig, ConfigLoadError> {
        let mut cfg = RedisCacheConfig::new(
            env_var("BCS_REDIS_APP_NAME").unwrap_or_else(|| "bcs".to_string()),
            env_var("BCS_REDIS_CACHE_NAME").unwrap_or_else(|| "bcsCache".to_string()),
        );

        if let Some(host) = env_var("BCS_REDIS_HOST") {
            cfg.host = host;
        }
        if let Some(port) = env_var("BCS_REDIS_PORT") {
            cfg.port = port
                .parse()
                .map_err(|e: std::num::ParseIntError| ConfigLoadError::Invalid(e.to_string()))?;
        }
        if let Some(route_type) = env_var("BCS_REDIS_ROUTE_TYPE") {
            cfg.route_type = route_type
                .parse::<RedisRouteType>()
                .map_err(|e| ConfigLoadError::Invalid(format!("invalid route_type: {e}")))?;
        }
        if let Some(pool_size) = env_var("BCS_REDIS_POOL_SIZE") {
            cfg.pool_size = pool_size
                .parse()
                .map_err(|e: std::num::ParseIntError| ConfigLoadError::Invalid(e.to_string()))?;
        }
        if let Some(password) = env_var("BCS_REDIS_PASSWORD") {
            cfg = cfg.with_password(password);
        }
        if let Some(username) = env_var("BCS_REDIS_USERNAME") {
            cfg = cfg.with_username(username);
        }
        if let Some(auth_mode) = env_var("BCS_REDIS_AUTH_MODE") {
            cfg.auth_mode = match auth_mode.as_str() {
                "redis" => RedisAuthMode::Redis,
                "disabled" => RedisAuthMode::Disabled,
                other => {
                    return Err(ConfigLoadError::Invalid(format!(
                        "invalid auth_mode: {other}"
                    )));
                }
            };
        }

        Ok(cfg)
    }
}

fn env_var(name: &str) -> Option<String> {
    std::env::var(name).ok()
}
