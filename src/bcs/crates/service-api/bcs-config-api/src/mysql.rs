//! MySQL/OceanBase connection config -- pure data.
//!
//! Loaders (`from_yaml`, `from_env`) live in `bcs-config`.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};
use std::str::FromStr;

/// Protocol used for parameterized statements.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StatementProtocol {
    /// Render parameters client-side and execute via COM_QUERY.
    Text,
    /// Execute via server-side prepared statements.
    Prepared,
}

impl Display for StatementProtocol {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            StatementProtocol::Text => f.write_str("text"),
            StatementProtocol::Prepared => f.write_str("prepared"),
        }
    }
}

impl FromStr for StatementProtocol {
    type Err = String;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value.trim().to_ascii_lowercase().as_str() {
            "text" => Ok(StatementProtocol::Text),
            "prepared" | "prepare" | "binary" => Ok(StatementProtocol::Prepared),
            other => Err(format!("invalid statement protocol: {other}")),
        }
    }
}

/// Data source configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DataSourceConfig {
    /// Datasource identifier used by BCS to select the connection pool.
    pub name: String,

    /// MySQL/OceanBase schema name used in the connection URL.
    pub database: String,

    /// Database username.
    pub user: String,

    /// Password
    #[serde(default)]
    pub password: String,

    /// Host address, default 127.0.0.1
    #[serde(default = "default_host")]
    pub host: String,

    /// Port, default 3306.
    #[serde(default = "default_port")]
    pub port: u16,

    /// Connection pool size
    #[serde(default = "default_pool_size")]
    pub pool_size: u32,

    /// Minimum connection pool size
    #[serde(default = "default_min_pool_size")]
    pub min_pool_size: u32,

    /// Connection timeout in seconds
    #[serde(default = "default_timeout")]
    pub timeout_secs: u64,

    /// Prepared statement cache size per connection.
    ///
    /// This only matters when `statement_protocol` is `prepared`. Keep it at 0
    /// for proxy-based MySQL/OceanBase deployments so prepared statements are not retained
    /// by mysql_async's connection-local cache.
    #[serde(default = "default_stmt_cache_size")]
    pub stmt_cache_size: u32,

    /// Protocol used for parameterized statements.
    ///
    /// Use `text` for proxy-based MySQL/OceanBase deployments to avoid server-side
    /// prepared statement cursor pressure. Use `prepared` only when the
    /// backend is known to handle prepared statement lifecycle correctly.
    #[serde(default = "default_statement_protocol")]
    pub statement_protocol: StatementProtocol,

    /// Connection provider configuration. The public provider is `direct`.
    ///
    /// Internal builds can use values such as `zdas` here. Unknown
    /// provider-specific fields are retained in `extra` so linked internal
    /// plugin crates can validate and consume them without changing this
    /// public config contract.
    #[serde(default)]
    pub connection: MysqlConnectionConfig,
}

/// MySQL-compatible connection provider configuration.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MysqlConnectionConfig {
    #[serde(rename = "type", default = "default_connection_type")]
    pub connection_type: String,

    #[serde(default)]
    pub host: Option<String>,

    #[serde(default)]
    pub port: Option<u16>,

    #[serde(default)]
    pub user: Option<String>,

    #[serde(default)]
    pub password: Option<String>,

    #[serde(default, flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

fn default_connection_type() -> String {
    "direct".to_string()
}

impl Default for MysqlConnectionConfig {
    fn default() -> Self {
        Self {
            connection_type: default_connection_type(),
            host: None,
            port: None,
            user: None,
            password: None,
            extra: BTreeMap::new(),
        }
    }
}

fn default_host() -> String {
    "127.0.0.1".to_string()
}

fn default_port() -> u16 {
    3306
}

fn default_pool_size() -> u32 {
    20
}

fn default_min_pool_size() -> u32 {
    5
}

fn default_timeout() -> u64 {
    30
}

fn default_stmt_cache_size() -> u32 {
    0
}

fn default_statement_protocol() -> StatementProtocol {
    StatementProtocol::Text
}

impl DataSourceConfig {
    /// Create new data source configuration
    pub fn new(database: impl Into<String>, user: impl Into<String>) -> Self {
        let db_name: String = database.into();
        Self {
            name: db_name.clone(),
            database: db_name,
            user: user.into(),
            password: String::new(),
            host: default_host(),
            port: default_port(),
            pool_size: default_pool_size(),
            min_pool_size: default_min_pool_size(),
            timeout_secs: default_timeout(),
            stmt_cache_size: default_stmt_cache_size(),
            statement_protocol: default_statement_protocol(),
            connection: MysqlConnectionConfig::default(),
        }
    }

    /// Set name (identifier)
    pub fn with_name(mut self, name: impl Into<String>) -> Self {
        self.name = name.into();
        self
    }

    /// Set password
    pub fn with_password(mut self, password: impl Into<String>) -> Self {
        self.password = password.into();
        self
    }

    /// Set host
    pub fn with_host(mut self, host: impl Into<String>) -> Self {
        self.host = host.into();
        self
    }

    /// Set port
    pub fn with_port(mut self, port: u16) -> Self {
        self.port = port;
        self
    }

    /// Set pool size
    pub fn with_pool_size(mut self, size: u32) -> Self {
        self.pool_size = size;
        self
    }

    /// Set min pool size
    pub fn with_min_pool_size(mut self, size: u32) -> Self {
        self.min_pool_size = size;
        self
    }

    /// Set prepared statement cache size.
    pub fn with_stmt_cache_size(mut self, size: u32) -> Self {
        self.stmt_cache_size = size;
        self
    }

    /// Set statement protocol.
    pub fn with_statement_protocol(mut self, protocol: StatementProtocol) -> Self {
        self.statement_protocol = protocol;
        self
    }

    /// Set connection provider config.
    pub fn with_connection(mut self, connection: MysqlConnectionConfig) -> Self {
        self.connection = connection;
        self
    }

    /// Generate MySQL connection URL
    /// Username and password are URL-encoded to handle special characters like ':'
    /// Generate MySQL connection URL for mysql_async.
    pub fn to_mysql_url(&self) -> String {
        format!(
            "mysql://{}:{}@{}:{}/{}",
            urlencoding::encode(&self.user),
            urlencoding::encode(&self.password),
            self.host,
            self.port,
            self.database
        )
    }

    /// Generate connection URL without password (for logging)
    pub fn to_safe_url(&self) -> String {
        format!(
            "mysql://{}:***@{}:{}/{}",
            urlencoding::encode(&self.user),
            self.host,
            self.port,
            self.database
        )
    }
}

/// MySQL/OceanBase configuration for the single BCS database.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MysqlDbConfig {
    /// MySQL/OceanBase schema name used in the connection URL.
    #[serde(default = "default_database")]
    pub database: String,

    /// Connection pool size
    #[serde(default = "default_pool_size")]
    pub pool_size: u32,

    /// Minimum connection pool size
    #[serde(default = "default_min_pool_size")]
    pub min_pool_size: u32,

    /// Connection timeout in seconds
    #[serde(default = "default_timeout")]
    pub timeout_secs: u64,

    /// Prepared statement cache size per connection.
    #[serde(default = "default_stmt_cache_size")]
    pub stmt_cache_size: u32,

    /// Protocol used for parameterized statements.
    #[serde(default = "default_statement_protocol")]
    pub statement_protocol: StatementProtocol,

    /// Connection provider configuration. The public provider is `direct`.
    #[serde(default)]
    pub connection: MysqlConnectionConfig,
}

impl Default for MysqlDbConfig {
    fn default() -> Self {
        Self {
            database: default_database(),
            pool_size: default_pool_size(),
            min_pool_size: default_min_pool_size(),
            timeout_secs: default_timeout(),
            stmt_cache_size: default_stmt_cache_size(),
            statement_protocol: default_statement_protocol(),
            connection: MysqlConnectionConfig::default(),
        }
    }
}

fn default_database() -> String {
    "bcs".to_string()
}

fn default_database_handle_name() -> String {
    "bcs".to_string()
}

impl MysqlDbConfig {
    /// Create new MySQL/OceanBase configuration
    pub fn new() -> Self {
        Self::default()
    }

    /// Set database/schema name.
    pub fn with_database(mut self, database: impl Into<String>) -> Self {
        self.database = database.into();
        self
    }

    /// Set connection provider config.
    pub fn with_connection(mut self, connection: MysqlConnectionConfig) -> Self {
        self.connection = connection;
        self
    }

    /// Set statement protocol.
    pub fn with_statement_protocol(mut self, protocol: StatementProtocol) -> Self {
        self.statement_protocol = protocol;
        self
    }

    /// Logical datasource name used internally by MySQL-backed BCS stores.
    pub fn datasource_name(&self) -> String {
        default_database_handle_name()
    }

    /// Convert the single public config into the manager's runtime datasource shape.
    pub fn to_datasource_config(&self) -> DataSourceConfig {
        let connection = &self.connection;
        DataSourceConfig {
            name: self.datasource_name(),
            database: self.database.clone(),
            user: connection.user.clone().unwrap_or_default(),
            password: connection.password.clone().unwrap_or_default(),
            host: connection.host.clone().unwrap_or_else(default_host),
            port: connection.port.unwrap_or_else(default_port),
            pool_size: self.pool_size,
            min_pool_size: self.min_pool_size,
            timeout_secs: self.timeout_secs,
            stmt_cache_size: self.stmt_cache_size,
            statement_protocol: self.statement_protocol,
            connection: self.connection.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_datasource_config() {
        let config = DataSourceConfig::new("testdb", "testuser")
            .with_password("testpass")
            .with_host("10.0.0.1")
            .with_port(3306);

        assert_eq!(config.name, "testdb");
        assert_eq!(config.database, "testdb");
        assert_eq!(config.user, "testuser");
        assert_eq!(config.password, "testpass");
        assert_eq!(config.host, "10.0.0.1");
        assert_eq!(config.port, 3306);
    }

    #[test]
    fn test_mysql_url() {
        let config = DataSourceConfig::new("testdb", "testuser")
            .with_password("testpass")
            .with_host("10.0.0.1")
            .with_port(3306);

        assert_eq!(config.stmt_cache_size, 0);
        assert_eq!(config.statement_protocol, StatementProtocol::Text);
        assert_eq!(
            config.to_mysql_url(),
            "mysql://testuser:testpass@10.0.0.1:3306/testdb"
        );
    }

    #[test]
    fn test_mysql_url_with_special_chars() {
        // Test URL encoding for usernames with special characters like ':'
        let config = DataSourceConfig::new("testdb", "user:name")
            .with_password("pass:word")
            .with_host("10.0.0.1")
            .with_port(3306);

        let url = config.to_mysql_url();
        // Colons should be encoded as %3A
        assert!(url.contains("user%3Aname"));
        assert!(url.contains("pass%3Aword"));
        assert!(!url.contains(":name:")); // Raw colons should not be present
    }

    #[test]
    fn test_mysql_config() {
        let config = MysqlDbConfig::new().with_database("db1");

        assert_eq!(config.database, "db1");
        assert_eq!(config.datasource_name(), "bcs");
    }

    #[test]
    fn mysql_config_builds_runtime_datasource() {
        let config = MysqlDbConfig::new()
            .with_database("db1")
            .with_connection(MysqlConnectionConfig {
                connection_type: "direct".to_string(),
                host: Some("10.0.0.1".to_string()),
                port: Some(3307),
                user: Some("user1".to_string()),
                password: Some("secret".to_string()),
                extra: BTreeMap::new(),
            });

        let ds = config.to_datasource_config();
        assert_eq!(ds.name, "bcs");
        assert_eq!(ds.database, "db1");
        assert_eq!(ds.host, "10.0.0.1");
        assert_eq!(ds.port, 3307);
        assert_eq!(ds.user, "user1");
        assert_eq!(ds.password, "secret");
    }

    #[test]
    fn mysql_single_database_config_deserializes() {
        let toml = r#"
database = "bcs"
statement_protocol = "text"

[connection]
type = "direct"
host = "127.0.0.1"
port = 3306
user = "bcs"
password = "bcsbcs"
"#;

        let config: MysqlDbConfig = toml::from_str(toml).unwrap();

        assert_eq!(config.database, "bcs");
        let datasource = config.to_datasource_config();
        assert_eq!(datasource.name, "bcs");
        assert_eq!(datasource.database, "bcs");
        assert_eq!(datasource.host, "127.0.0.1");
        assert_eq!(datasource.port, 3306);
        assert_eq!(datasource.user, "bcs");
        assert_eq!(datasource.connection.connection_type, "direct");
    }

    #[test]
    fn mysql_single_database_preserves_internal_connection_type() {
        let toml = r#"
database = "bcs"

[connection]
type = "zdas"
host = "127.0.0.1"
port = 11306
user = "mesh-routing-user"
password = ""
component = "zdas"
"#;

        let config: MysqlDbConfig = toml::from_str(toml).unwrap();

        assert_eq!(config.connection.connection_type, "zdas");
        assert_eq!(
            config.connection.extra.get("component").and_then(|v| v.as_str()),
            Some("zdas")
        );
    }
}
