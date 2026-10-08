//! Redis-compatible cache configuration -- pure data.

use crate::deserialize_optional_secret;
use crate::redis_route_type::RedisRouteType;
use secrecy::{ExposeSecret, Secret};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Authentication mode used when connecting to the Redis-compatible endpoint.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum RedisAuthMode {
    /// Standard Redis password AUTH.
    Redis,
    /// Do not send Redis AUTH.
    Disabled,
}

impl Default for RedisAuthMode {
    fn default() -> Self {
        Self::Disabled
    }
}

/// Resolved Redis AUTH credentials.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RedisAuthCredentials {
    /// Optional Redis ACL username. When set, AUTH uses `AUTH username password`.
    pub username: Option<String>,
    /// Redis AUTH password.
    pub password: String,
}

/// Redis-compatible cache configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RedisCacheConfig {
    /// Cache host
    #[serde(default = "default_host")]
    pub host: String,

    /// Cache port
    #[serde(default = "default_port")]
    pub port: u16,

    /// Application name used by Redis-compatible providers that need it.
    #[serde(default = "default_app_name")]
    pub app_name: String,

    /// Cache name used by Redis-compatible providers that need it.
    #[serde(default = "default_cache_name")]
    pub cache_name: String,

    /// Route type used by Redis-compatible providers that need it.
    #[serde(default)]
    pub route_type: RedisRouteType,

    /// Connection pool size.
    #[serde(default = "default_pool_size")]
    pub pool_size: u32,

    /// Connection timeout in seconds
    #[serde(default = "default_timeout")]
    pub timeout_secs: u64,

    /// AUTH mode. Defaults to disabled for public local development.
    #[serde(default)]
    pub auth_mode: RedisAuthMode,

    /// Optional Redis ACL username for `auth_mode = "redis"`.
    #[serde(default)]
    pub username: Option<String>,

    /// Password for `auth_mode = "redis"`.
    #[serde(default, skip_serializing, deserialize_with = "deserialize_optional_secret")]
    pub password: Option<Secret<String>>,
}

fn default_host() -> String {
    "127.0.0.1".to_string()
}

fn default_port() -> u16 {
    16379
}

fn default_app_name() -> String {
    "bcs".to_string()
}

fn default_cache_name() -> String {
    "bcsCache".to_string()
}

fn default_pool_size() -> u32 {
    10
}

fn default_timeout() -> u64 {
    5
}

fn default_key_prefix() -> String {
    "bcs:".to_string()
}

impl RedisCacheConfig {
    /// Create default configuration (connects to 127.0.0.1:16379)
    pub fn new(app_name: impl Into<String>, cache_name: impl Into<String>) -> Self {
        Self {
            host: default_host(),
            port: default_port(),
            app_name: app_name.into(),
            cache_name: cache_name.into(),
            route_type: RedisRouteType::default(),
            pool_size: default_pool_size(),
            timeout_secs: default_timeout(),
            auth_mode: RedisAuthMode::default(),
            username: None,
            password: None,
        }
    }

    /// Set server host
    pub fn with_host(mut self, host: impl Into<String>) -> Self {
        self.host = host.into();
        self
    }

    /// Set server port
    pub fn with_port(mut self, port: u16) -> Self {
        self.port = port;
        self
    }

    /// Set route type
    pub fn with_route_type(mut self, route_type: RedisRouteType) -> Self {
        self.route_type = route_type;
        self
    }

    /// Set pool size
    pub fn with_pool_size(mut self, size: u32) -> Self {
        self.pool_size = size;
        self
    }

    /// Set connection timeout
    pub fn with_timeout(mut self, secs: u64) -> Self {
        self.timeout_secs = secs;
        self
    }

    /// Set AUTH mode.
    pub fn with_auth_mode(mut self, auth_mode: RedisAuthMode) -> Self {
        self.auth_mode = auth_mode;
        self
    }

    /// Set Redis ACL username.
    pub fn with_username(mut self, username: impl Into<String>) -> Self {
        self.username = Some(username.into());
        self
    }

    /// Set standard Redis password.
    pub fn with_password(mut self, password: impl Into<String>) -> Self {
        self.password = Some(Secret::new(password.into()));
        self
    }

    /// Resolve the password argument for Redis AUTH.
    ///
    pub fn auth_password(&self) -> Result<Option<String>, String> {
        Ok(self.auth_credentials()?.map(|credentials| credentials.password))
    }

    /// Resolve Redis AUTH credentials.
    pub fn auth_credentials(&self) -> Result<Option<RedisAuthCredentials>, String> {
        match self.auth_mode {
            RedisAuthMode::Redis => match &self.password {
                Some(password) if !password.expose_secret().is_empty() => Ok(Some(
                    RedisAuthCredentials {
                        username: self
                            .username
                            .as_ref()
                            .filter(|username| !username.is_empty())
                            .cloned(),
                        password: password.expose_secret().to_string(),
                    },
                )),
                _ => Err("redis password is required when auth_mode=redis".to_string()),
            },
            RedisAuthMode::Disabled => Ok(None),
        }
    }

    /// Generate Redis connection URL
    pub fn to_redis_url(&self) -> String {
        format!("redis://{}:{}", self.host, self.port)
    }
}

impl Default for RedisCacheConfig {
    fn default() -> Self {
        Self::new("bcs", "bcsCache")
    }
}

/// Capability-local cache selector used by the `[cache]` section.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CacheConfig {
    #[serde(rename = "type", default = "default_cache_type")]
    pub cache_type: String,

    #[serde(default)]
    pub redis: RedisPluginConfig,
}

fn default_cache_type() -> String {
    "memory".to_string()
}

impl CacheConfig {
    pub fn is_configured(&self) -> bool {
        self.cache_type != "memory" || self.redis.is_configured()
    }
}

impl Default for CacheConfig {
    fn default() -> Self {
        Self {
            cache_type: default_cache_type(),
            redis: RedisPluginConfig::default(),
        }
    }
}

/// New Redis-compatible cache plugin config used by `[cache.redis]`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RedisPluginConfig {
    #[serde(default = "default_pool_size")]
    pub pool_size: u32,

    #[serde(default = "default_timeout")]
    pub timeout_secs: u64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub key_prefix: Option<String>,

    #[serde(default)]
    pub connection: RedisConnectionConfig,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub routing: Option<RedisRoutingConfig>,
}

impl RedisPluginConfig {
    pub fn is_configured(&self) -> bool {
        self.key_prefix.is_some()
            || self.routing.is_some()
            || self.connection.is_configured()
    }

    pub fn effective_key_prefix(&self) -> String {
        self.key_prefix.clone().unwrap_or_else(default_key_prefix)
    }

    pub fn to_runtime_redis_config(&self) -> RedisCacheConfig {
        let connection = &self.connection;
        RedisCacheConfig {
            host: connection.host.clone().unwrap_or_else(default_host),
            port: connection.port.unwrap_or_else(default_port),
            app_name: connection
                .app_name
                .clone()
                .unwrap_or_else(default_app_name),
            cache_name: connection
                .cache_name
                .clone()
                .unwrap_or_else(default_cache_name),
            route_type: connection.route_type.unwrap_or_default(),
            pool_size: self.pool_size,
            timeout_secs: self.timeout_secs,
            auth_mode: connection.auth_mode,
            username: connection.username.clone(),
            password: connection.password.clone(),
        }
    }
}

impl Default for RedisPluginConfig {
    fn default() -> Self {
        Self {
            pool_size: default_pool_size(),
            timeout_secs: default_timeout(),
            key_prefix: None,
            connection: RedisConnectionConfig::default(),
            routing: None,
        }
    }
}

/// Redis-compatible connection provider configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RedisConnectionConfig {
    #[serde(rename = "type", default = "default_connection_type")]
    pub connection_type: String,

    #[serde(default)]
    pub host: Option<String>,

    #[serde(default)]
    pub port: Option<u16>,

    #[serde(default)]
    pub app_name: Option<String>,

    #[serde(default)]
    pub cache_name: Option<String>,

    #[serde(default)]
    pub route_type: Option<RedisRouteType>,

    #[serde(default)]
    pub auth_mode: RedisAuthMode,

    #[serde(default)]
    pub username: Option<String>,

    #[serde(default, skip_serializing, deserialize_with = "deserialize_optional_secret")]
    pub password: Option<Secret<String>>,

    #[serde(default, flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

fn default_connection_type() -> String {
    "direct".to_string()
}

impl Default for RedisConnectionConfig {
    fn default() -> Self {
        Self {
            connection_type: default_connection_type(),
            host: None,
            port: None,
            app_name: None,
            cache_name: None,
            route_type: None,
            auth_mode: RedisAuthMode::default(),
            username: None,
            password: None,
            extra: BTreeMap::new(),
        }
    }
}

impl RedisConnectionConfig {
    pub fn is_configured(&self) -> bool {
        self.connection_type != default_connection_type()
            || self.host.is_some()
            || self.port.is_some()
            || self.app_name.is_some()
            || self.cache_name.is_some()
            || self.route_type.is_some()
            || self.auth_mode != RedisAuthMode::default()
            || self.username.is_some()
            || self.password.is_some()
            || !self.extra.is_empty()
    }
}

/// Optional key routing metadata for non-direct Redis-compatible providers.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RedisRoutingConfig {
    #[serde(rename = "type", default = "default_routing_type")]
    pub routing_type: String,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_route: Option<String>,

    #[serde(default, flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

fn default_routing_type() -> String {
    "direct".to_string()
}

impl Default for RedisRoutingConfig {
    fn default() -> Self {
        Self {
            routing_type: default_routing_type(),
            default_route: None,
            extra: BTreeMap::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use secrecy::ExposeSecret;

    #[test]
    fn test_default_config() {
        let config = RedisCacheConfig::default();
        assert_eq!(config.host, "127.0.0.1");
        assert_eq!(config.port, 16379);
        assert_eq!(config.app_name, "bcs");
        assert_eq!(config.cache_name, "bcsCache");
        assert_eq!(config.pool_size, 10);
    }

    #[test]
    fn test_builder_pattern() {
        let config = RedisCacheConfig::new("myapp", "mycache")
            .with_host("10.0.0.1")
            .with_port(6379)
            .with_route_type(RedisRouteType::C)
            .with_pool_size(20);

        assert_eq!(config.host, "10.0.0.1");
        assert_eq!(config.port, 6379);
        assert_eq!(config.app_name, "myapp");
        assert_eq!(config.cache_name, "mycache");
        assert_eq!(config.route_type, RedisRouteType::C);
        assert_eq!(config.pool_size, 20);
    }

    #[test]
    fn default_auth_mode_skips_auth() {
        let config = RedisCacheConfig::new("testapp", "testcache").with_route_type(RedisRouteType::G);

        assert_eq!(config.auth_mode, RedisAuthMode::Disabled);
        assert_eq!(config.auth_password().unwrap(), None);
    }

    #[test]
    fn test_redis_auth_mode_uses_configured_password() {
        let toml = r#"
host = "redis.example.com"
port = 6379
auth_mode = "redis"
password = "redis-pass"
"#;

        let config: RedisCacheConfig = toml::from_str(toml).unwrap();

        assert_eq!(config.auth_mode, RedisAuthMode::Redis);
        assert_eq!(
            config.password.as_ref().unwrap().expose_secret(),
            "redis-pass"
        );
        assert_eq!(
            config.auth_password().unwrap(),
            Some("redis-pass".to_string())
        );
    }

    #[test]
    fn test_redis_auth_mode_supports_acl_username() {
        let toml = r#"
host = "redis.example.com"
port = 6379
auth_mode = "redis"
username = "bcs_user"
password = "redis-pass"
"#;

        let config: RedisCacheConfig = toml::from_str(toml).unwrap();
        let credentials = config.auth_credentials().unwrap().unwrap();

        assert_eq!(config.username.as_deref(), Some("bcs_user"));
        assert_eq!(credentials.username.as_deref(), Some("bcs_user"));
        assert_eq!(credentials.password, "redis-pass");
    }

    #[test]
    fn test_disabled_auth_mode_skips_auth() {
        let config = RedisCacheConfig::new("app", "cache")
            .with_auth_mode(RedisAuthMode::Disabled)
            .with_password("redis-pass");

        assert_eq!(config.auth_password().unwrap(), None);
    }

    #[test]
    fn test_skip_auth_is_not_accepted() {
        let toml = r#"
host = "127.0.0.1"
port = 6379
skip_auth = true
"#;

        let err = toml::from_str::<RedisCacheConfig>(toml).expect_err("skip_auth should be removed");
        assert!(err.to_string().contains("skip_auth"));
    }

    #[test]
    fn test_redis_config_defaults_provider_fields() {
        let toml = r#"
host = "redis.example.com"
port = 6379
timeout_secs = 5
"#;

        let config: RedisCacheConfig = toml::from_str(toml).unwrap();

        assert_eq!(config.app_name, "bcs");
        assert_eq!(config.cache_name, "bcsCache");
        assert_eq!(config.route_type, RedisRouteType::default());
        assert_eq!(config.pool_size, 10);
    }

    #[test]
    fn test_redis_config_accepts_provider_fields() {
        let toml = r#"
host = "redis.example.com"
port = 6379
app_name = "provider-app"
cache_name = "provider-cache"
route_type = "C"
pool_size = 20
timeout_secs = 5
"#;

        let config: RedisCacheConfig = toml::from_str(toml).unwrap();

        assert_eq!(config.app_name, "provider-app");
        assert_eq!(config.cache_name, "provider-cache");
        assert_eq!(config.route_type, RedisRouteType::C);
        assert_eq!(config.pool_size, 20);
    }

    #[test]
    fn test_redis_auth_mode_requires_password() {
        let config = RedisCacheConfig::new("app", "cache").with_auth_mode(RedisAuthMode::Redis);

        let err = config.auth_password().unwrap_err();
        assert!(err.contains("password is required"));
    }

    #[test]
    fn test_redis_url() {
        let config = RedisCacheConfig::new("app", "cache")
            .with_host("10.0.0.1")
            .with_port(6379);

        assert_eq!(config.to_redis_url(), "redis://10.0.0.1:6379");
    }

    #[test]
    fn cache_config_direct_redis_deserializes() {
        let json = r#"{
            "type": "redis",
            "redis": {
                "timeout_secs": 5,
                "key_prefix": "bcs:",
                "connection": {
                    "type": "direct",
                    "host": "127.0.0.1",
                    "port": 6379,
                    "auth_mode": "disabled"
                }
            }
        }"#;

        let config: CacheConfig = serde_json::from_str(json).unwrap();

        assert_eq!(config.cache_type, "redis");
        assert_eq!(config.redis.connection.connection_type, "direct");
        assert_eq!(config.redis.connection.port, Some(6379));
        assert_eq!(config.redis.effective_key_prefix(), "bcs:");
        let runtime = config.redis.to_runtime_redis_config();
        assert_eq!(runtime.host, "127.0.0.1");
        assert_eq!(runtime.port, 6379);
        assert_eq!(runtime.cache_name, "bcsCache");
    }

    #[test]
    fn redis_plugin_config_defaults_effective_key_prefix() {
        let config = RedisPluginConfig::default();
        assert_eq!(config.effective_key_prefix(), "bcs:");
    }

    #[test]
    fn cache_config_preserves_external_connection_type() {
        let json = r#"{
            "type": "redis",
            "redis": {
                "connection": {
                    "type": "external-cache",
                    "host": "127.0.0.1",
                    "port": 16379,
                    "app_name": "bcs",
                    "cache_name": "bcsCache",
                    "route_type": "G",
                    "component": "external-cache"
                },
                "routing": {
                    "type": "external_context",
                    "default_route": "default"
                }
            }
        }"#;

        let config: CacheConfig = serde_json::from_str(json).unwrap();

        assert_eq!(config.redis.connection.connection_type, "external-cache");
        assert_eq!(
            config.redis.connection.extra.get("component").and_then(|v| v.as_str()),
            Some("external-cache")
        );
        assert_eq!(
            config.redis.routing.as_ref().map(|routing| routing.routing_type.as_str()),
            Some("external_context")
        );
    }
}
