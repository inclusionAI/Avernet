//! Redis-compatible implementation crate for the `bcs-cache-api` contract.
//!
//! This adapter talks to Redis directly through `redis-rs`. It stores cache
//! values as raw bytes and uses the business key exactly as provided by callers.
//! Namespacing should be part of the caller-owned key format.

use std::collections::BTreeMap;
use std::time::Duration;

use async_trait::async_trait;
use bcs_cache_api::{CacheError, CachePlugin, CacheResult, CacheSetMode, CacheTtl};
use bcs_config_api::RedisCacheConfig;
use redis::{AsyncCommands, ExistenceCheck, SetExpiry, SetOptions};

#[derive(Clone)]
pub struct RedisCachePlugin {
    connection: redis::aio::MultiplexedConnection,
    config: RedisCacheConfig,
    zone: String,
}

impl RedisCachePlugin {
    pub async fn connect(config: RedisCacheConfig, zone: impl Into<String>) -> CacheResult<Self> {
        let client = redis::Client::open(connection_info(&config)?).map_err(cache_backend)?;
        let timeout = Duration::from_secs(config.timeout_secs.max(1));
        let connection_config = redis::AsyncConnectionConfig::new()
            .set_connection_timeout(timeout)
            .set_response_timeout(timeout);
        let connection = client
            .get_multiplexed_async_connection_with_config(&connection_config)
            .await
            .map_err(cache_backend)?;

        Ok(Self::from_connection(connection, config, zone))
    }

    pub fn from_connection(
        connection: redis::aio::MultiplexedConnection,
        config: RedisCacheConfig,
        zone: impl Into<String>,
    ) -> Self {
        Self {
            connection,
            config,
            zone: zone.into(),
        }
    }

    pub fn config(&self) -> &RedisCacheConfig {
        &self.config
    }

    pub fn zone(&self) -> &str {
        &self.zone
    }

    pub async fn ping(&self) -> CacheResult<bool> {
        let mut connection = self.connection.clone();
        let pong: String = redis::cmd("PING")
            .query_async(&mut connection)
            .await
            .map_err(cache_backend)?;
        Ok(pong == "PONG")
    }

    fn connection(&self) -> redis::aio::MultiplexedConnection {
        self.connection.clone()
    }
}

#[async_trait]
impl CachePlugin for RedisCachePlugin {
    async fn get_value(&self, key: &str) -> CacheResult<Option<Vec<u8>>> {
        let mut connection = self.connection();
        connection.get(key).await.map_err(cache_backend)
    }

    async fn get_values(&self, keys: &[String]) -> CacheResult<Vec<Option<Vec<u8>>>> {
        if keys.is_empty() {
            return Ok(Vec::new());
        }

        let mut connection = self.connection();
        redis::cmd("MGET")
            .arg(keys)
            .query_async(&mut connection)
            .await
            .map_err(cache_backend)
    }

    async fn set_value(
        &self,
        key: &str,
        value: Vec<u8>,
        ttl: Option<Duration>,
        mode: CacheSetMode,
    ) -> CacheResult<bool> {
        let mut connection = self.connection();

        match (mode, ttl) {
            (CacheSetMode::Upsert, None) => {
                let _: () = connection.set(key, value).await.map_err(cache_backend)?;
                Ok(true)
            }
            (CacheSetMode::Upsert, Some(ttl)) => {
                let _: () = connection
                    .set_ex(key, value, ttl_seconds(ttl))
                    .await
                    .map_err(cache_backend)?;
                Ok(true)
            }
            (CacheSetMode::InsertOnly, ttl) => {
                set_conditionally(&mut connection, key, value, ExistenceCheck::NX, ttl).await
            }
            (CacheSetMode::UpdateOnly, ttl) => {
                set_conditionally(&mut connection, key, value, ExistenceCheck::XX, ttl).await
            }
        }
    }

    async fn delete(&self, key: &str) -> CacheResult<bool> {
        let mut connection = self.connection();
        let count: i64 = connection.del(key).await.map_err(cache_backend)?;
        Ok(count > 0)
    }

    async fn expire(&self, key: &str, ttl: Duration) -> CacheResult<bool> {
        let mut connection = self.connection();
        connection
            .expire(key, ttl_seconds_i64(ttl)?)
            .await
            .map_err(cache_backend)
    }

    async fn ttl(&self, key: &str) -> CacheResult<CacheTtl> {
        let mut connection = self.connection();
        match connection.ttl(key).await.map_err(cache_backend)? {
            ttl if ttl < -1 => Ok(CacheTtl::Missing),
            -1 => Ok(CacheTtl::Persistent),
            ttl => Ok(CacheTtl::ExpiresIn(Duration::from_secs(ttl as u64))),
        }
    }

    async fn hash_get(&self, key: &str, field: &str) -> CacheResult<Option<Vec<u8>>> {
        let mut connection = self.connection();
        connection.hget(key, field).await.map_err(cache_backend)
    }

    async fn hash_get_all(&self, key: &str) -> CacheResult<BTreeMap<String, Vec<u8>>> {
        let mut connection = self.connection();
        connection.hgetall(key).await.map_err(cache_backend)
    }

    async fn hash_set(&self, key: &str, field: &str, value: Vec<u8>) -> CacheResult<()> {
        let mut connection = self.connection();
        let _: i64 = redis::cmd("HSET")
            .arg(key)
            .arg(field)
            .arg(value)
            .query_async(&mut connection)
            .await
            .map_err(cache_backend)?;
        Ok(())
    }

    async fn hash_set_many(&self, key: &str, fields: BTreeMap<String, Vec<u8>>) -> CacheResult<()> {
        if fields.is_empty() {
            return Ok(());
        }

        let mut command = redis::cmd("HSET");
        command.arg(key);
        for (field, value) in fields {
            command.arg(field).arg(value);
        }

        let mut connection = self.connection();
        let _: i64 = command
            .query_async(&mut connection)
            .await
            .map_err(cache_backend)?;
        Ok(())
    }

    async fn hash_delete(&self, key: &str, field: &str) -> CacheResult<bool> {
        let mut connection = self.connection();
        let count: i64 = connection.hdel(key, field).await.map_err(cache_backend)?;
        Ok(count > 0)
    }
}

async fn set_conditionally(
    connection: &mut redis::aio::MultiplexedConnection,
    key: &str,
    value: Vec<u8>,
    existence: ExistenceCheck,
    ttl: Option<Duration>,
) -> CacheResult<bool> {
    let mut options = SetOptions::default().conditional_set(existence);
    if let Some(ttl) = ttl {
        options = options.with_expiration(SetExpiry::EX(ttl_seconds(ttl)));
    }

    let result: Option<String> = redis::cmd("SET")
        .arg(key)
        .arg(value)
        .arg(options)
        .query_async(connection)
        .await
        .map_err(cache_backend)?;

    Ok(result.is_some())
}

fn ttl_seconds(ttl: Duration) -> u64 {
    ttl.as_secs().max(1)
}

fn ttl_seconds_i64(ttl: Duration) -> CacheResult<i64> {
    i64::try_from(ttl_seconds(ttl))
        .map_err(|_| CacheError::InvalidInput("cache ttl exceeds i64 seconds".to_string()))
}

fn connection_info(config: &RedisCacheConfig) -> CacheResult<redis::ConnectionInfo> {
    let credentials = config.auth_credentials().map_err(CacheError::InvalidInput)?;
    let (username, password) = match credentials {
        Some(credentials) => (credentials.username, Some(credentials.password)),
        None => (None, None),
    };

    let mut redis = redis::RedisConnectionInfo {
        db: 0,
        ..Default::default()
    };
    redis.username = username;
    redis.password = password;

    Ok(redis::ConnectionInfo {
        addr: redis::ConnectionAddr::Tcp(config.host.clone(), config.port),
        redis,
    })
}

fn cache_backend(err: redis::RedisError) -> CacheError {
    let message = err.to_string();
    if message.to_ascii_uppercase().contains("WRONGTYPE") {
        CacheError::WrongType(message)
    } else {
        CacheError::Backend(message)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_config_api::RedisAuthMode;

    #[test]
    fn ttl_rounds_subsecond_to_one_second() {
        assert_eq!(ttl_seconds(Duration::from_millis(1)), 1);
    }

    #[test]
    fn connection_info_includes_acl_username_when_configured() {
        let config = RedisCacheConfig::new("bcs", "cache")
            .with_auth_mode(RedisAuthMode::Redis)
            .with_username("bcs")
            .with_password("secret");
        let info = connection_info(&config).expect("connection info");

        assert_eq!(info.redis.username.as_deref(), Some("bcs"));
        assert_eq!(info.redis.password.as_deref(), Some("secret"));
    }

    #[test]
    fn connection_info_supports_password_only_auth() {
        let config = RedisCacheConfig::new("bcs", "cache")
            .with_auth_mode(RedisAuthMode::Redis)
            .with_password("secret");
        let info = connection_info(&config).expect("connection info");

        assert_eq!(info.redis.username, None);
        assert_eq!(info.redis.password.as_deref(), Some("secret"));
    }

}
