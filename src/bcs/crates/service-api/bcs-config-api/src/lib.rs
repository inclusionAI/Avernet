//! BCS configuration contract types.
//!
//! Leaf / primitive config structs that are pure data — no dependencies on
//! BCS implementation crates. Previously lived in `crates/bcs/src/config.rs`.
//!
//! The top-level `BcsConfig` and config-loader entrypoints intentionally
//! stay in the `bcs` crate for now because they are still tied to bootstrap
//! wiring and runtime integration details. Those will migrate in a later step.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::net::IpAddr;

use ipnet::IpNet;
use secrecy::{ExposeSecret, Secret};
use serde::{Deserialize, Serialize};

pub mod bcsfuse;
pub mod mysql;
pub mod redis;
pub mod redis_route_type;

pub use bcsfuse::BcsFuseConfig;
pub use mysql::{DataSourceConfig, MysqlDbConfig, StatementProtocol};
pub use redis::{
    CacheConfig, RedisAuthCredentials, RedisAuthMode, RedisCacheConfig, RedisConnectionConfig,
    RedisPluginConfig, RedisRoutingConfig,
};
pub use redis_route_type::RedisRouteType;

// ---------------------------------------------------------------------------
// Manifest
// ---------------------------------------------------------------------------

/// Public frontend/runtime bundle manifest configuration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ManifestConfig {
    /// Response schema version.
    #[serde(default = "default_manifest_schema_version")]
    pub schema_version: u32,

    /// Frontend/runtime bundles exposed by `GET /manifest`.
    #[serde(default)]
    pub bundles: Vec<ManifestBundleConfig>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ManifestBundleConfig {
    pub name: String,
    #[serde(default, rename = "type", skip_serializing_if = "Option::is_none")]
    pub source_type: Option<ManifestBundleSourceType>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    #[serde(default, alias = "path", skip_serializing_if = "Option::is_none")]
    pub file: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ManifestBundleSourceType {
    Url,
    File,
}

fn default_manifest_schema_version() -> u32 {
    1
}

impl Default for ManifestConfig {
    fn default() -> Self {
        Self {
            schema_version: default_manifest_schema_version(),
            bundles: Vec::new(),
        }
    }
}

// ---------------------------------------------------------------------------
// Security
// ---------------------------------------------------------------------------

/// Security-related runtime policy configuration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SecurityConfig {
    /// User-controlled outbound HTTP callback URL policy.
    #[serde(default)]
    pub outbound_url: OutboundUrlSecurityConfig,
}

impl Default for SecurityConfig {
    fn default() -> Self {
        Self {
            outbound_url: OutboundUrlSecurityConfig::default(),
        }
    }
}

// ---------------------------------------------------------------------------
// Eventing
// ---------------------------------------------------------------------------

/// Public Event recording and delivery policy.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventingConfig {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub dispatcher_enabled: bool,
    #[serde(default = "default_eventing_poll_interval_ms")]
    pub fanout_poll_interval_ms: u64,
    #[serde(default = "default_eventing_poll_interval_ms")]
    pub delivery_poll_interval_ms: u64,
    #[serde(default = "default_eventing_worker_concurrency")]
    pub worker_concurrency: usize,
    #[serde(default = "default_eventing_per_host_concurrency")]
    pub per_host_concurrency: usize,
    #[serde(default = "default_eventing_lease_ms")]
    pub lease_ms: u64,
    #[serde(default = "default_eventing_drain_timeout_ms")]
    pub drain_timeout_ms: u64,
    #[serde(default = "default_event_retention_days")]
    pub event_retention_days: u32,
    #[serde(default)]
    pub retry: EventingRetryConfig,
    #[serde(default)]
    pub webhook: EventingWebhookConfig,
    #[serde(default)]
    pub limits: EventingLimitsConfig,
}

impl Default for EventingConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            dispatcher_enabled: false,
            fanout_poll_interval_ms: default_eventing_poll_interval_ms(),
            delivery_poll_interval_ms: default_eventing_poll_interval_ms(),
            worker_concurrency: default_eventing_worker_concurrency(),
            per_host_concurrency: default_eventing_per_host_concurrency(),
            lease_ms: default_eventing_lease_ms(),
            drain_timeout_ms: default_eventing_drain_timeout_ms(),
            event_retention_days: default_event_retention_days(),
            retry: EventingRetryConfig::default(),
            webhook: EventingWebhookConfig::default(),
            limits: EventingLimitsConfig::default(),
        }
    }
}

impl EventingConfig {
    pub fn validate(&self) -> Result<(), String> {
        validate_eventing_range(
            "fanout_poll_interval_ms",
            self.fanout_poll_interval_ms,
            10,
            60_000,
        )?;
        validate_eventing_range(
            "delivery_poll_interval_ms",
            self.delivery_poll_interval_ms,
            10,
            60_000,
        )?;
        validate_eventing_range(
            "worker_concurrency",
            self.worker_concurrency as u64,
            1,
            1024,
        )?;
        validate_eventing_range(
            "per_host_concurrency",
            self.per_host_concurrency as u64,
            1,
            1024,
        )?;
        if self.per_host_concurrency > self.worker_concurrency {
            return Err(
                "eventing.per_host_concurrency must not exceed worker_concurrency".to_string(),
            );
        }
        validate_eventing_range("lease_ms", self.lease_ms, 1_000, 600_000)?;
        validate_eventing_range("drain_timeout_ms", self.drain_timeout_ms, 100, 600_000)?;
        validate_eventing_range(
            "event_retention_days",
            self.event_retention_days as u64,
            1,
            3650,
        )?;
        self.retry.validate()?;
        self.webhook.validate()?;
        self.limits.validate()?;
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventingRetryConfig {
    #[serde(default = "default_eventing_retry_base_delay_ms")]
    pub base_delay_ms: u64,
    #[serde(default = "default_eventing_retry_max_delay_ms")]
    pub max_delay_ms: u64,
    #[serde(default = "default_eventing_retry_max_attempts")]
    pub max_attempts: u32,
    #[serde(default = "default_eventing_retry_max_elapsed_ms")]
    pub max_elapsed_ms: u64,
}

impl Default for EventingRetryConfig {
    fn default() -> Self {
        Self {
            base_delay_ms: default_eventing_retry_base_delay_ms(),
            max_delay_ms: default_eventing_retry_max_delay_ms(),
            max_attempts: default_eventing_retry_max_attempts(),
            max_elapsed_ms: default_eventing_retry_max_elapsed_ms(),
        }
    }
}

impl EventingRetryConfig {
    fn validate(&self) -> Result<(), String> {
        validate_eventing_range("retry.base_delay_ms", self.base_delay_ms, 1, 3_600_000)?;
        validate_eventing_range(
            "retry.max_delay_ms",
            self.max_delay_ms,
            self.base_delay_ms,
            86_400_000,
        )?;
        validate_eventing_range("retry.max_attempts", self.max_attempts as u64, 1, 100)?;
        validate_eventing_range(
            "retry.max_elapsed_ms",
            self.max_elapsed_ms,
            self.max_delay_ms,
            2_592_000_000,
        )?;
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventingWebhookConfig {
    #[serde(default = "default_eventing_connect_timeout_ms")]
    pub connect_timeout_ms: u64,
    #[serde(default = "default_eventing_request_timeout_ms")]
    pub request_timeout_ms: u64,
    #[serde(default = "default_eventing_max_request_timeout_ms")]
    pub max_request_timeout_ms: u64,
    #[serde(default = "default_eventing_max_event_body_bytes")]
    pub max_event_body_bytes: usize,
    #[serde(default = "default_eventing_max_response_body_bytes")]
    pub max_response_body_bytes: usize,
    #[serde(default)]
    pub allow_http_loopback: bool,
    #[serde(default)]
    pub allow_non_standard_ports: bool,
    #[serde(default)]
    pub private_endpoint_allowlist: Vec<PrivateEndpointAllowlistEntryConfig>,
}

impl Default for EventingWebhookConfig {
    fn default() -> Self {
        Self {
            connect_timeout_ms: default_eventing_connect_timeout_ms(),
            request_timeout_ms: default_eventing_request_timeout_ms(),
            max_request_timeout_ms: default_eventing_max_request_timeout_ms(),
            max_event_body_bytes: default_eventing_max_event_body_bytes(),
            max_response_body_bytes: default_eventing_max_response_body_bytes(),
            allow_http_loopback: false,
            allow_non_standard_ports: false,
            private_endpoint_allowlist: Vec::new(),
        }
    }
}

impl EventingWebhookConfig {
    fn validate(&self) -> Result<(), String> {
        validate_eventing_range(
            "webhook.connect_timeout_ms",
            self.connect_timeout_ms,
            100,
            30_000,
        )?;
        validate_eventing_range(
            "webhook.request_timeout_ms",
            self.request_timeout_ms,
            1_000,
            30_000,
        )?;
        validate_eventing_range(
            "webhook.max_request_timeout_ms",
            self.max_request_timeout_ms,
            self.request_timeout_ms,
            30_000,
        )?;
        if self.connect_timeout_ms > self.request_timeout_ms {
            return Err(
                "eventing.webhook.connect_timeout_ms must not exceed request_timeout_ms"
                    .to_string(),
            );
        }
        validate_eventing_range(
            "webhook.max_event_body_bytes",
            self.max_event_body_bytes as u64,
            1024,
            262_144,
        )?;
        validate_eventing_range(
            "webhook.max_response_body_bytes",
            self.max_response_body_bytes as u64,
            1,
            4096,
        )?;
        if self.private_endpoint_allowlist.len() > 64 {
            return Err(
                "eventing.webhook.private_endpoint_allowlist must contain at most 64 entries"
                    .to_string(),
            );
        }
        for entry in &self.private_endpoint_allowlist {
            entry.validate()?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrivateEndpointAllowlistEntryConfig {
    pub host: String,
    pub cidrs: Vec<String>,
    #[serde(default = "default_private_endpoint_ports")]
    pub ports: Vec<u16>,
}

impl PrivateEndpointAllowlistEntryConfig {
    pub fn validate(&self) -> Result<(), String> {
        validate_private_endpoint_host_pattern(&self.host)?;
        if self.cidrs.is_empty() || self.cidrs.len() > 16 {
            return Err(format!(
                "eventing.webhook private endpoint '{}' must contain 1 to 16 CIDRs",
                self.host
            ));
        }
        let mut cidrs = BTreeSet::new();
        for raw_cidr in &self.cidrs {
            let cidr = raw_cidr.parse::<IpNet>().map_err(|_| {
                format!(
                    "eventing.webhook private endpoint '{}' contains invalid CIDR '{raw_cidr}'",
                    self.host
                )
            })?;
            if cidr.addr() != cidr.network() {
                return Err(format!(
                    "eventing.webhook private endpoint CIDR '{raw_cidr}' must use its canonical network address"
                ));
            }
            if !is_supported_private_cidr(cidr) {
                return Err(format!(
                    "eventing.webhook private endpoint CIDR '{raw_cidr}' must be contained by RFC1918 or IPv6 ULA space"
                ));
            }
            if !cidrs.insert(cidr) {
                return Err(format!(
                    "eventing.webhook private endpoint '{}' contains duplicate CIDR '{raw_cidr}'",
                    self.host
                ));
            }
        }
        if self.ports.is_empty() || self.ports.len() > 16 || self.ports.contains(&0) {
            return Err(format!(
                "eventing.webhook private endpoint '{}' must contain 1 to 16 non-zero ports",
                self.host
            ));
        }
        let ports = self.ports.iter().copied().collect::<BTreeSet<_>>();
        if ports.len() != self.ports.len() {
            return Err(format!(
                "eventing.webhook private endpoint '{}' contains duplicate ports",
                self.host
            ));
        }
        Ok(())
    }

    pub fn matches_host_and_port(&self, host: &str, port: u16) -> bool {
        if !self.ports.contains(&port) {
            return false;
        }
        let host = host.trim_end_matches('.').to_ascii_lowercase();
        let pattern = self.host.to_ascii_lowercase();
        if let Some(suffix) = pattern.strip_prefix("*.") {
            return host.len() > suffix.len()
                && host.ends_with(suffix)
                && host.as_bytes()[host.len() - suffix.len() - 1] == b'.';
        }
        host == pattern
    }
}

fn default_private_endpoint_ports() -> Vec<u16> {
    vec![443]
}

fn validate_private_endpoint_host_pattern(pattern: &str) -> Result<(), String> {
    if pattern.is_empty()
        || pattern.len() > 253
        || !pattern.is_ascii()
        || pattern.ends_with('.')
    {
        return Err(format!(
            "eventing.webhook private endpoint host pattern '{pattern}' is invalid"
        ));
    }
    let host = pattern.strip_prefix("*.").unwrap_or(pattern);
    if host.is_empty()
        || host.contains('*')
        || host.eq_ignore_ascii_case("localhost")
        || host.to_ascii_lowercase().ends_with(".localhost")
        || host.parse::<IpAddr>().is_ok()
        || !host.split('.').all(valid_dns_label)
    {
        return Err(format!(
            "eventing.webhook private endpoint host pattern '{pattern}' is invalid; only an exact DNS name or a leading '*.suffix' wildcard is supported"
        ));
    }
    Ok(())
}

fn valid_dns_label(label: &str) -> bool {
    !label.is_empty()
        && label.len() <= 63
        && label
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        && label
            .as_bytes()
            .first()
            .is_some_and(u8::is_ascii_alphanumeric)
        && label
            .as_bytes()
            .last()
            .is_some_and(u8::is_ascii_alphanumeric)
}

fn is_supported_private_cidr(cidr: IpNet) -> bool {
    match cidr {
        IpNet::V4(network) => {
            let [a, b, _, _] = network.network().octets();
            (a == 10 && network.prefix_len() >= 8)
                || (a == 172 && (16..=31).contains(&b) && network.prefix_len() >= 12)
                || (a == 192 && b == 168 && network.prefix_len() >= 16)
        }
        IpNet::V6(network) => {
            let first_segment = network.network().segments()[0];
            (first_segment & 0xfe00) == 0xfc00 && network.prefix_len() >= 7
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventingLimitsConfig {
    #[serde(default = "default_max_group_subscriptions")]
    pub max_group_subscriptions: u32,
    #[serde(default = "default_max_filters_per_subscription")]
    pub max_filters_per_subscription: u32,
}

impl Default for EventingLimitsConfig {
    fn default() -> Self {
        Self {
            max_group_subscriptions: default_max_group_subscriptions(),
            max_filters_per_subscription: default_max_filters_per_subscription(),
        }
    }
}

impl EventingLimitsConfig {
    fn validate(&self) -> Result<(), String> {
        validate_eventing_range(
            "limits.max_group_subscriptions",
            self.max_group_subscriptions as u64,
            1,
            100,
        )?;
        validate_eventing_range(
            "limits.max_filters_per_subscription",
            self.max_filters_per_subscription as u64,
            1,
            64,
        )?;
        Ok(())
    }
}

fn validate_eventing_range(
    name: &str,
    value: u64,
    minimum: u64,
    maximum: u64,
) -> Result<(), String> {
    if !(minimum..=maximum).contains(&value) {
        return Err(format!(
            "eventing.{name} must be between {minimum} and {maximum}, got {value}"
        ));
    }
    Ok(())
}

fn default_eventing_poll_interval_ms() -> u64 {
    200
}

fn default_eventing_worker_concurrency() -> usize {
    64
}

fn default_eventing_per_host_concurrency() -> usize {
    8
}

fn default_eventing_lease_ms() -> u64 {
    30_000
}

fn default_eventing_drain_timeout_ms() -> u64 {
    10_000
}

fn default_event_retention_days() -> u32 {
    30
}

fn default_eventing_retry_base_delay_ms() -> u64 {
    5_000
}

fn default_eventing_retry_max_delay_ms() -> u64 {
    3_600_000
}

fn default_eventing_retry_max_attempts() -> u32 {
    12
}

fn default_eventing_retry_max_elapsed_ms() -> u64 {
    86_400_000
}

fn default_eventing_connect_timeout_ms() -> u64 {
    3_000
}

fn default_eventing_request_timeout_ms() -> u64 {
    10_000
}

fn default_eventing_max_request_timeout_ms() -> u64 {
    30_000
}

fn default_eventing_max_event_body_bytes() -> usize {
    262_144
}

fn default_eventing_max_response_body_bytes() -> usize {
    4_096
}

fn default_max_group_subscriptions() -> u32 {
    10
}

fn default_max_filters_per_subscription() -> u32 {
    64
}

/// Policy for user-controlled outbound HTTP URLs such as provider webhooks and
/// BaaS callback endpoints.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OutboundUrlSecurityConfig {
    /// Reject RFC1918, link-local, loopback, unspecified, multicast,
    /// documentation, and otherwise non-public addresses.
    #[serde(default = "default_true")]
    pub block_private_networks: bool,

    /// Allow loopback hosts and loopback-resolved addresses.
    #[serde(default)]
    pub allow_loopback: bool,
}

impl Default for OutboundUrlSecurityConfig {
    fn default() -> Self {
        Self {
            block_private_networks: true,
            allow_loopback: false,
        }
    }
}

// ---------------------------------------------------------------------------
// Leader election
// ---------------------------------------------------------------------------

/// Provider-specific leader-election options.
pub type LeaderElectionProviderConfig = BTreeMap<String, serde_json::Value>;

/// Leader election configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LeaderElectionConfig {
    /// Enable leader election (default: false for standalone mode).
    #[serde(default)]
    pub enabled: bool,

    /// Distributed election provider to load when enabled.
    #[serde(default)]
    pub provider: Option<String>,

    /// Lease timing used by lease-based election providers.
    #[serde(default)]
    pub lease: LeaderElectionLeaseConfig,

    /// Provider-specific options keyed by provider name.
    #[serde(default)]
    pub providers: BTreeMap<String, LeaderElectionProviderConfig>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LeaderElectionLeaseConfig {
    /// Lock TTL in seconds (default: 30).
    #[serde(default = "default_lease_ttl")]
    pub ttl_secs: u64,

    /// Renewal interval in seconds (default: 10).
    #[serde(default = "default_lease_renewal_interval")]
    pub renewal_interval_secs: u64,
}

fn default_lease_ttl() -> u64 {
    30
}

fn default_lease_renewal_interval() -> u64 {
    10
}

impl Default for LeaderElectionLeaseConfig {
    fn default() -> Self {
        Self {
            ttl_secs: default_lease_ttl(),
            renewal_interval_secs: default_lease_renewal_interval(),
        }
    }
}

impl Default for LeaderElectionConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            provider: None,
            lease: LeaderElectionLeaseConfig::default(),
            providers: BTreeMap::new(),
        }
    }
}

// ---------------------------------------------------------------------------
// Database storage
// ---------------------------------------------------------------------------

/// Unified database backend selector for DB-backed BCS stores.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DatabaseConfig {
    /// Database backend type.
    #[serde(rename = "type", default = "default_database_type")]
    pub database_type: DatabaseType,

    /// SQLite file-backed database configuration.
    #[serde(default)]
    pub sqlite: SqliteConfig,

    /// MySQL/OceanBase database configuration.
    #[serde(default = "default_database_mysql")]
    pub mysql: MysqlDbConfig,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DatabaseType {
    /// Local SQLite-backed storage for public development.
    Sqlite,
    /// Persistent MySQL/OceanBase-backed storage.
    Mysql,
    /// A linked extension-provided database plugin.
    Other(String),
}

impl DatabaseType {
    pub fn as_str(&self) -> &str {
        match self {
            Self::Sqlite => "sqlite",
            Self::Mysql => "mysql",
            Self::Other(value) => value.as_str(),
        }
    }
}

impl Serialize for DatabaseType {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        serializer.serialize_str(self.as_str())
    }
}

impl<'de> Deserialize<'de> for DatabaseType {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        Ok(match value.trim().to_ascii_lowercase().as_str() {
            "sqlite" => Self::Sqlite,
            "mysql" => Self::Mysql,
            _ => Self::Other(value),
        })
    }
}

fn default_database_type() -> DatabaseType {
    DatabaseType::Sqlite
}

fn default_database_mysql() -> MysqlDbConfig {
    MysqlDbConfig::default()
}

impl Default for DatabaseConfig {
    fn default() -> Self {
        Self {
            database_type: default_database_type(),
            sqlite: SqliteConfig::default(),
            mysql: default_database_mysql(),
        }
    }
}

// ---------------------------------------------------------------------------
// Channel(IM bridge)
// ---------------------------------------------------------------------------

/// Channel(IM bridge) configuration.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ChannelConfigSection {
    /// Enable channel bridge wiring.
    #[serde(default)]
    pub enabled: bool,

    /// Provider-specific channel configuration keyed by provider name.
    #[serde(default)]
    pub providers: BTreeMap<String, ChannelProviderConfig>,

    /// Backward-compatible flat switch for DingTalk.
    #[serde(default)]
    pub dingtalk_enabled: bool,

    /// Nested DingTalk switch matching `[channels.dingtalk]`.
    #[serde(default)]
    pub dingtalk: ChannelDingTalkConfig,
}

impl ChannelConfigSection {
    pub fn dingtalk_enabled(&self) -> bool {
        self.enabled
            && (self.dingtalk_enabled || self.dingtalk.enabled || self.provider_enabled("dingtalk"))
    }

    pub fn provider_enabled(&self, name: &str) -> bool {
        self.enabled
            && self
                .providers
                .get(name)
                .is_some_and(|provider| provider.enabled)
    }

    pub fn enabled_provider_configs(&self) -> BTreeMap<String, ChannelProviderConfig> {
        if !self.enabled {
            return BTreeMap::new();
        }
        let mut providers = self
            .providers
            .iter()
            .filter(|(_, provider)| provider.enabled)
            .map(|(name, provider)| (name.clone(), provider.clone()))
            .collect::<BTreeMap<_, _>>();
        if (self.dingtalk_enabled || self.dingtalk.enabled) && !providers.contains_key("dingtalk") {
            providers.insert(
                "dingtalk".to_string(),
                ChannelProviderConfig {
                    enabled: true,
                    options: BTreeMap::new(),
                },
            );
        }
        providers
    }
}

/// Generic channel provider configuration.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ChannelProviderConfig {
    #[serde(default)]
    pub enabled: bool,

    /// Provider-owned options. The public host only carries these through to
    /// the provider factory.
    #[serde(default, flatten)]
    pub options: BTreeMap<String, serde_json::Value>,
}

/// DingTalk channel configuration.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ChannelDingTalkConfig {
    #[serde(default)]
    pub enabled: bool,
}

// ---------------------------------------------------------------------------
// Human mention notification
// ---------------------------------------------------------------------------

/// Human mention notification configuration (`[human_notify]`).
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct HumanNotifyConfig {
    /// Selected backend name; the feature is off when absent.
    #[serde(default)]
    pub provider: Option<String>,

    /// Backend-specific configuration keyed by backend name.
    #[serde(default)]
    pub providers: BTreeMap<String, HumanNotifyProviderConfig>,
}

impl HumanNotifyConfig {
    /// Returns true when `provider` selects `name` and that entry is enabled.
    pub fn enabled_provider(&self, name: &str) -> bool {
        self.provider.as_deref() == Some(name)
            && self
                .providers
                .get(name)
                .is_some_and(|provider| provider.enabled)
    }
}

/// Generic human-mention notification backend configuration.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct HumanNotifyProviderConfig {
    #[serde(default)]
    pub enabled: bool,

    /// Backend-owned options. The public host only carries these through to
    /// the backend factory.
    #[serde(default, flatten)]
    pub options: BTreeMap<String, serde_json::Value>,
}

// ---------------------------------------------------------------------------
// Auth SDK
// ---------------------------------------------------------------------------

/// Configuration for optional deployment-provided user identity extraction.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AuthSdkConfig {
    #[serde(default)]
    pub client_id: Option<String>,
    #[serde(default)]
    pub secret_key: Option<String>,
    #[serde(default)]
    pub app_key: Option<String>,
    #[serde(default)]
    pub app_name: Option<String>,
    #[serde(default)]
    pub remote_server_domain: Option<String>,
    #[serde(default = "default_true")]
    pub use_remote_login_check: bool,
}

impl AuthSdkConfig {
    /// Pure check on this struct's fields, no env access.
    ///
    /// Use [`AuthSdkConfig::is_complete_with_env_view`] when you also want to
    /// account for env-provided overrides.
    pub fn fields_complete(&self) -> bool {
        self.client_id.as_deref().is_some_and(|s| !s.is_empty())
            && self.secret_key.as_deref().is_some_and(|s| !s.is_empty())
            && self.app_key.as_deref().is_some_and(|s| !s.is_empty())
    }

    /// Check completeness, considering an injected env view.
    ///
    /// Caller supplies the env view. This keeps `bcs-config-api` free of env
    /// access once the deprecated bridge is removed.
    pub fn is_complete_with_env_view(&self, env: &dyn AuthSdkEnvView) -> bool {
        let has_client_id =
            self.client_id.as_deref().is_some_and(|s| !s.is_empty()) || env.has("SDK_CLIENT_ID");
        let has_secret_key =
            self.secret_key.as_deref().is_some_and(|s| !s.is_empty()) || env.has("SDK_SECRET_KEY");
        let has_app_key =
            self.app_key.as_deref().is_some_and(|s| !s.is_empty()) || env.has("SDK_APP_KEY");
        has_client_id && has_secret_key && has_app_key
    }
}

/// Abstract env view for AuthSdk completeness check, injected by caller.
pub trait AuthSdkEnvView {
    fn has(&self, var: &str) -> bool;
}

// ---------------------------------------------------------------------------
// Secret backend
// ---------------------------------------------------------------------------

/// Provider-specific secret-backend options.
pub type SecretProviderConfig = BTreeMap<String, serde_json::Value>;

/// Secret backend selector.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SecretConfig {
    /// Secret provider to load. Public/local builds support `noop` and `env`;
    /// linked extension crates can register additional providers.
    #[serde(default = "default_secret_provider")]
    pub provider: String,

    /// Provider-specific options keyed by provider name.
    #[serde(default)]
    pub providers: BTreeMap<String, SecretProviderConfig>,
}

impl Default for SecretConfig {
    fn default() -> Self {
        Self {
            provider: default_secret_provider(),
            providers: BTreeMap::new(),
        }
    }
}

fn default_secret_provider() -> String {
    "noop".to_string()
}

// ---------------------------------------------------------------------------
// User directory
// ---------------------------------------------------------------------------

/// Provider-specific user-directory options.
pub type UserDirectoryProviderConfig = BTreeMap<String, serde_json::Value>;

/// Optional external user directory used to resolve stable user ids to display
/// metadata. This does not authenticate requests or store users.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UserDirectoryConfig {
    /// Enable user-directory lookups. Default is off for public/local builds.
    #[serde(default)]
    pub enabled: bool,

    /// User-directory provider to load when enabled.
    #[serde(default)]
    pub provider: Option<String>,

    /// Provider-specific options keyed by provider name.
    #[serde(default)]
    pub providers: BTreeMap<String, UserDirectoryProviderConfig>,
}

// ---------------------------------------------------------------------------
// Auth plugin chain
// ---------------------------------------------------------------------------

/// Configuration for the auth plugin chain (which plugins are enabled, in what
/// order). Consumed by bootstrap to build `bcs_auth_api::AuthConfig`.
///
/// An empty `chain` means "not configured" — bootstrap falls back to the
/// build-profile default (`bcs_auth_api::AuthConfig::default`).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AuthChainConfig {
    /// Ordered list of enabled plugin names: `agentpass` | `cookie` | `session`
    /// | `local`. Empty/omitted → fall back to the build-profile default.
    #[serde(default)]
    pub chain: Vec<String>,
    /// When true, anonymous requests are rejected with 401.
    #[serde(default)]
    pub require_authentication: bool,
    /// Local-mock plugin: user_id to emit (only used when `local` is in chain).
    #[serde(default)]
    pub mock_user_id: Option<String>,
    /// Local-mock plugin: display name to emit.
    #[serde(default)]
    pub mock_user_name: Option<String>,
    /// Local-mock plugin: allow X-Mock-* request headers to override identity.
    #[serde(default)]
    pub allow_mock_headers: bool,
    /// OAuth session settings. Required for the `oauth_session` plugin and the
    /// `/auth/*` routes. Omitted → OAuth disabled (routes return 404, the
    /// `oauth_session` plugin logs a warning if requested).
    #[serde(default)]
    pub oauth: Option<OAuthSettings>,
}

/// OAuth session + provider settings, deserialized from `[auth.oauth]`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct OAuthSettings {
    /// HMAC secret for signing/verifying session JWTs. Required to mount
    /// `/auth/*`; an absent/empty secret keeps OAuth disabled.
    #[serde(
        default,
        serialize_with = "serialize_optional_secret",
        deserialize_with = "deserialize_optional_secret"
    )]
    pub jwt_secret: Option<Secret<String>>,
    /// Idle timeout in minutes for issued session JWTs. Default: 30.
    #[serde(default = "default_oauth_idle_timeout_minutes")]
    pub idle_timeout_minutes: u64,
    /// Public base URL used to build redirect URIs:
    /// `{base_url}/auth/callback/{provider}`.
    pub base_url: String,
    /// Override for the session cookie `Secure` attribute. When omitted it is
    /// derived from `base_url` (https → secure). Set `false` for local HTTP dev.
    #[serde(default)]
    pub cookie_secure: Option<bool>,
    /// Path to redirect to after a successful login (the `Location` emitted by
    /// `/auth/callback/{provider}` and the OpenAPI v1 login flow). Defaults to
    /// `/`. Must be a relative path starting with a single `/`; an absolute URL
    /// or protocol-relative `//host` is rejected at load time (open-redirect
    /// guard). A query string is allowed, e.g. `/?login=success`.
    #[serde(default = "default_oauth_success_redirect_path")]
    pub success_redirect_path: String,
    /// OAuth provider instances keyed by instance name, deserialized from
    /// `[auth.oauth.providers.<name>]`. The key is the instance name used as the
    /// `HashMap` key, the `/auth/url` entry, and the `{provider}` in
    /// `/auth/callback/{provider}`. Empty → no providers → `/auth/*` stays
    /// unmounted. `BTreeMap` keeps iteration/logging order deterministic.
    #[serde(default)]
    pub providers: BTreeMap<String, ProviderSettings>,
}

/// One OAuth provider instance, deserialized from `[auth.oauth.providers.<name>]`.
///
/// The shape is uniform across provider kinds; the concrete implementation is
/// selected by `kind` (defaulting to the map key when omitted).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderSettings {
    /// Which built-in provider implementation to construct (`google` | `github`
    /// | `wechat` | `alipay` | …). When omitted, defaults to the map key — so
    /// the common 1:1 case (`[auth.oauth.providers.github]`) needs no `kind`.
    /// Set it explicitly to run multiple instances of one kind under distinct names.
    #[serde(default)]
    pub kind: Option<String>,
    /// OAuth client id.
    pub client_id: String,
    /// OAuth client secret.
    #[serde(
        default,
        serialize_with = "serialize_optional_secret",
        deserialize_with = "deserialize_optional_secret"
    )]
    pub client_secret: Option<Secret<String>>,
    /// RSA private key in PEM format — used by Alipay for request signing.
    #[serde(
        default,
        serialize_with = "serialize_optional_secret",
        deserialize_with = "deserialize_optional_secret"
    )]
    pub private_key: Option<Secret<String>>,
    /// Alipay RSA public key in PEM format — used to verify gateway responses.
    #[serde(
        default,
        serialize_with = "serialize_optional_secret",
        deserialize_with = "deserialize_optional_secret"
    )]
    pub alipay_public_key: Option<Secret<String>>,
}

impl ProviderSettings {
    /// The provider kind to construct: explicit `kind`, else the instance name.
    pub fn resolved_kind<'a>(&'a self, name: &'a str) -> &'a str {
        self.kind.as_deref().unwrap_or(name)
    }
}

impl OAuthSettings {
    /// Validate the resolved OAuth settings at config-load time, so operator
    /// mistakes fail fast at startup instead of surfacing as a runtime 404.
    ///
    /// Checks (per provider instance): non-empty `client_id`, and a
    /// route-safe instance name (no `/`, no whitespace) since the name becomes
    /// the `{provider}` path segment. Unknown `kind` cannot be checked here —
    /// the set of valid kinds lives in the bootstrap factory — so it is
    /// validated there; this keeps the contract crate free of impl knowledge.
    pub fn validate(&self) -> Result<(), String> {
        // success_redirect_path must be a same-origin relative path: a leading
        // single `/` only. An absolute URL (http://host) or a protocol-relative
        // `//host` would let a misconfigured value redirect users off-site
        // right after they authenticate, so reject it at load time.
        let redirect = self.success_redirect_path.trim();
        if !redirect.starts_with('/') || redirect.starts_with("//") {
            return Err(format!(
                "auth.oauth.success_redirect_path must be a relative path starting with a single '/' (got {:?})",
                self.success_redirect_path
            ));
        }
        for (name, p) in &self.providers {
            if name.is_empty() {
                return Err("auth.oauth.providers has an empty instance name".to_string());
            }
            if name.contains('/') || name.chars().any(|c| c.is_whitespace()) {
                return Err(format!(
                    "auth.oauth.providers.{name}: instance name must not contain '/' or whitespace (it is used in /auth/callback/{{provider}})"
                ));
            }
            if p.client_id.trim().is_empty() {
                return Err(format!(
                    "auth.oauth.providers.{name}: client_id must not be empty"
                ));
            }
        }
        Ok(())
    }
}

fn default_oauth_idle_timeout_minutes() -> u64 {
    30
}

fn default_oauth_success_redirect_path() -> String {
    "/".to_string()
}

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

/// Logging configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LoggingConfig {
    #[serde(default = "default_log_level")]
    pub default_level: String,
    #[serde(default = "default_true")]
    pub console: bool,
    #[serde(default)]
    pub modules: HashMap<String, String>,
    #[serde(default)]
    pub tags: HashMap<String, String>,
    #[serde(default = "default_log_outputs")]
    pub outputs: Vec<LogOutputConfig>,
}

/// A single log file output configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogOutputConfig {
    pub name: String,
    pub path: String,
    pub file: String,
    #[serde(default = "default_log_level")]
    pub level: String,
    #[serde(default = "default_rotation")]
    pub rotation: String,
    #[serde(default)]
    pub format: LogOutputFormat,
    pub targets: Vec<String>,
    #[serde(default)]
    pub max_keep_days: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LogOutputFormat {
    Text,
    Json,
}

impl Default for LogOutputFormat {
    fn default() -> Self {
        Self::Text
    }
}

fn default_log_level() -> String {
    "info".into()
}
fn default_rotation() -> String {
    "daily".into()
}
fn default_true() -> bool {
    true
}

fn default_log_outputs() -> Vec<LogOutputConfig> {
    vec![
        LogOutputConfig {
            name: "common-error".to_string(),
            path: "./logs".to_string(),
            file: "common-error.log".to_string(),
            level: "error".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Text,
            targets: vec!["*".to_string()],
            max_keep_days: 7,
        },
        LogOutputConfig {
            name: "messages".to_string(),
            path: "./logs".to_string(),
            file: "bcs-messages.log".to_string(),
            level: "info".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Json,
            targets: vec!["bcs_message".to_string()],
            max_keep_days: 7,
        },
        LogOutputConfig {
            name: "chat-digest".to_string(),
            path: "./logs".to_string(),
            file: "bcs-chat-digest.log".to_string(),
            level: "info".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Text,
            targets: vec!["bcs_chat_digest".to_string()],
            max_keep_days: 7,
        },
        LogOutputConfig {
            name: "group-messages".to_string(),
            path: "./logs".to_string(),
            file: "group-messages.log".to_string(),
            level: "info".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Text,
            targets: vec!["ding_group_message".to_string()],
            max_keep_days: 30,
        },
    ]
}

impl Default for LoggingConfig {
    fn default() -> Self {
        Self {
            default_level: "info".into(),
            console: true,
            modules: HashMap::new(),
            tags: HashMap::new(),
            outputs: default_log_outputs(),
        }
    }
}

// ---------------------------------------------------------------------------
// DingTalk account
// ---------------------------------------------------------------------------

/// DingTalk account configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DingTalkAccountConfig {
    pub account_id: String,
    #[serde(default)]
    pub client_id: Option<String>,
    #[serde(
        default,
        serialize_with = "serialize_optional_secret",
        deserialize_with = "deserialize_optional_secret"
    )]
    pub client_secret: Option<Secret<String>>,
    pub robot_code: Option<String>,
    pub card_template_id: Option<String>,
    #[serde(default = "default_card_key")]
    pub card_template_key: String,
    #[serde(default)]
    pub enable_streaming_cards: bool,
    #[serde(default)]
    pub enable_scene_group: bool,
    #[serde(default)]
    pub dm_policy: DmPolicy,
    #[serde(default)]
    pub allowlist: Vec<String>,
    #[serde(default)]
    pub default_bot_id: Option<String>,
    #[serde(default)]
    pub is_default_reply_bot: bool,
    #[serde(default)]
    pub gateway_mode: bool,
    pub gateway_host: Option<String>,
    #[serde(default)]
    pub gateway_port: u16,
    pub app_code: Option<String>,
    #[serde(
        default,
        serialize_with = "serialize_optional_secret",
        deserialize_with = "deserialize_optional_secret"
    )]
    pub gateway_client_secret: Option<Secret<String>>,
    #[serde(default)]
    pub gateway_use_tls: bool,
    #[serde(default)]
    pub gateway_cookie: Option<String>,
    #[serde(default)]
    pub gateway_ws_url: Option<String>,
    pub device_id: Option<String>,
}

/// DM (Direct Message) access policy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum DmPolicy {
    /// Only allowlisted users can DM (default).
    #[default]
    Allowlist,
    /// All users can DM.
    Open,
    /// DM is disabled.
    Disabled,
}

fn default_card_key() -> String {
    "content".to_string()
}

/// Serializer helper for `Option<Secret<String>>` used across multiple
/// config structs. Public so downstream crates (e.g. `bcs`) can reuse it
/// on fields they still own (e.g. `auth_token` on the top-level
/// `BcsConfig`).
pub fn serialize_optional_secret<S: serde::Serializer>(
    secret: &Option<Secret<String>>,
    serializer: S,
) -> Result<S::Ok, S::Error> {
    match secret {
        Some(s) => serializer.serialize_str(s.expose_secret()),
        None => serializer.serialize_none(),
    }
}

/// Deserializer counterpart of [`serialize_optional_secret`].
pub fn deserialize_optional_secret<'de, D>(
    deserializer: D,
) -> Result<Option<Secret<String>>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let opt: Option<String> = Option::deserialize(deserializer)?;
    Ok(opt.map(Secret::new))
}

impl Default for DingTalkAccountConfig {
    fn default() -> Self {
        Self {
            account_id: String::new(),
            client_id: None,
            client_secret: None,
            robot_code: None,
            card_template_id: None,
            card_template_key: default_card_key(),
            enable_streaming_cards: false,
            enable_scene_group: false,
            dm_policy: DmPolicy::default(),
            allowlist: Vec::new(),
            default_bot_id: None,
            is_default_reply_bot: false,
            gateway_mode: false,
            gateway_host: None,
            gateway_port: 0,
            app_code: None,
            gateway_client_secret: None,
            gateway_use_tls: false,
            gateway_cookie: None,
            gateway_ws_url: None,
            device_id: None,
        }
    }
}

// ---------------------------------------------------------------------------
// Fusion provider
// ---------------------------------------------------------------------------

/// Fusion LLM provider configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FusionProviderConfig {
    /// Provider type (e.g., "openai", "anthropic", "openrouter").
    pub provider: String,
    /// Model to use for fusion.
    pub model: String,
    /// API key (optional, may come from environment).
    pub api_key: Option<String>,
    /// Base URL (optional, for custom endpoints).
    pub base_url: Option<String>,
}

// ---------------------------------------------------------------------------
// LLM provider
// ---------------------------------------------------------------------------

fn default_llm_provider_type() -> LlmProviderType {
    LlmProviderType::None
}

fn default_llm_base_url() -> String {
    "https://api.openai.com/v1".to_string()
}

fn default_llm_model() -> String {
    "gpt-4.1-mini".to_string()
}

fn default_llm_api_key_env() -> Option<String> {
    Some("OPENAI_API_KEY".to_string())
}

fn default_llm_timeout_ms() -> u64 {
    120_000
}

fn default_llm_temperature() -> f32 {
    0.0
}

fn default_llm_max_tokens() -> u32 {
    4_096
}

fn default_structured_output_mode() -> StructuredOutputMode {
    StructuredOutputMode::JsonSchema
}

/// Supported LLM provider protocols.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LlmProviderType {
    /// No LLM provider is configured.
    None,
    /// OpenAI Chat Completions compatible HTTP API.
    OpenAiCompatible,
    /// Anthropic Messages HTTP API.
    Anthropic,
    /// A linked extension-provided LLM provider.
    Other(String),
}

impl LlmProviderType {
    pub fn as_str(&self) -> &str {
        match self {
            Self::None => "none",
            Self::OpenAiCompatible => "openai_compatible",
            Self::Anthropic => "anthropic",
            Self::Other(value) => value.as_str(),
        }
    }
}

impl Serialize for LlmProviderType {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        serializer.serialize_str(self.as_str())
    }
}

impl<'de> Deserialize<'de> for LlmProviderType {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        Ok(match value.trim().to_ascii_lowercase().as_str() {
            "none" => Self::None,
            "openai_compatible" => Self::OpenAiCompatible,
            "anthropic" => Self::Anthropic,
            _ => Self::Other(value),
        })
    }
}

impl Default for LlmProviderType {
    fn default() -> Self {
        default_llm_provider_type()
    }
}

/// Structured output transport strategy for LLM judge calls.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StructuredOutputMode {
    /// Use the provider's native JSON Schema constrained-output mechanism.
    JsonSchema,
    /// Use JSON-object mode and rely on local schema validation when supported.
    JsonObject,
    /// Convert the schema to a provider-native forced tool call.
    ToolCall,
}

impl Default for StructuredOutputMode {
    fn default() -> Self {
        default_structured_output_mode()
    }
}

/// LLM provider configuration for structured judge calls.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LlmConfig {
    #[serde(rename = "type", default = "default_llm_provider_type")]
    pub provider_type: LlmProviderType,
    #[serde(default = "default_llm_base_url")]
    pub base_url: String,
    #[serde(default = "default_llm_api_key_env")]
    pub api_key_env: Option<String>,
    #[serde(
        default,
        serialize_with = "serialize_optional_secret",
        deserialize_with = "deserialize_optional_secret"
    )]
    pub api_key: Option<Secret<String>>,
    #[serde(default = "default_llm_model")]
    pub model: String,
    #[serde(default = "default_llm_timeout_ms")]
    pub timeout_ms: u64,
    #[serde(default = "default_llm_temperature")]
    pub temperature: f32,
    #[serde(default = "default_llm_max_tokens")]
    pub max_tokens: u32,
    #[serde(default = "default_structured_output_mode")]
    pub structured_output: StructuredOutputMode,
}

impl Default for LlmConfig {
    fn default() -> Self {
        Self {
            provider_type: default_llm_provider_type(),
            base_url: default_llm_base_url(),
            api_key_env: default_llm_api_key_env(),
            api_key: None,
            model: default_llm_model(),
            timeout_ms: default_llm_timeout_ms(),
            temperature: default_llm_temperature(),
            max_tokens: default_llm_max_tokens(),
            structured_output: default_structured_output_mode(),
        }
    }
}

impl LlmConfig {
    pub fn is_enabled(&self) -> bool {
        !matches!(self.provider_type, LlmProviderType::None)
    }
}

// ---------------------------------------------------------------------------
// SQLite local mode
// ---------------------------------------------------------------------------

/// SQLite file-backed database configuration for local mode.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SqliteConfig {
    /// Path to the SQLite database file.
    #[serde(default = "default_sqlite_path")]
    pub path: String,
}

fn default_sqlite_path() -> String {
    "bcs.db".to_string()
}

impl Default for SqliteConfig {
    fn default() -> Self {
        Self {
            path: default_sqlite_path(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn channel_config_accepts_nested_dingtalk_switch() {
        let cfg: ChannelConfigSection = toml::from_str(
            r#"
            enabled = true

            [dingtalk]
            enabled = true
        "#,
        )
        .expect("parse channel config");

        assert!(cfg.enabled);
        assert!(cfg.dingtalk.enabled);
        assert!(cfg.dingtalk_enabled());
    }

    #[test]
    fn channel_config_accepts_flat_dingtalk_switch() {
        let cfg: ChannelConfigSection = toml::from_str(
            r#"
            enabled = true
            dingtalk_enabled = true
        "#,
        )
        .expect("parse channel config");

        assert!(cfg.enabled);
        assert!(cfg.dingtalk_enabled);
        assert!(cfg.dingtalk_enabled());
    }

    #[test]
    fn channel_config_accepts_provider_map() {
        let cfg: ChannelConfigSection = toml::from_str(
            r#"
            enabled = true

            [providers.test_im]
            enabled = true
            callback_path = "/channels/test/callback"

            [providers.disabled_im]
            enabled = false
        "#,
        )
        .expect("parse channel provider config");

        assert!(cfg.provider_enabled("test_im"));
        assert!(!cfg.provider_enabled("disabled_im"));
        assert_eq!(
            cfg.providers["test_im"].options["callback_path"],
            "/channels/test/callback"
        );
        assert_eq!(
            cfg.enabled_provider_configs()
                .keys()
                .cloned()
                .collect::<Vec<_>>(),
            vec!["test_im".to_string()]
        );
    }

    #[test]
    fn channel_config_maps_legacy_dingtalk_switch_to_provider() {
        let cfg: ChannelConfigSection = toml::from_str(
            r#"
            enabled = true
            dingtalk_enabled = true
        "#,
        )
        .expect("parse channel config");

        let providers = cfg.enabled_provider_configs();
        assert!(providers.contains_key("dingtalk"));
        assert!(providers["dingtalk"].enabled);
    }

    #[test]
    fn default_logging_outputs_include_chat_digest_file() {
        let logging = LoggingConfig::default();

        let digest = logging
            .outputs
            .iter()
            .find(|output| output.name == "chat-digest")
            .expect("chat digest log output should be configured by default");

        assert_eq!(digest.file, "bcs-chat-digest.log");
        assert_eq!(digest.format, LogOutputFormat::Text);
        assert_eq!(digest.targets, vec!["bcs_chat_digest"]);
        assert_eq!(digest.max_keep_days, 7);
    }

    #[test]
    fn default_logging_outputs_include_json_message_file() {
        let logging = LoggingConfig::default();

        let messages = logging
            .outputs
            .iter()
            .find(|output| output.name == "messages")
            .expect("message log output should be configured by default");

        assert_eq!(messages.file, "bcs-messages.log");
        assert_eq!(messages.format, LogOutputFormat::Json);
        assert_eq!(messages.targets, vec!["bcs_message"]);
        assert_eq!(messages.max_keep_days, 7);
    }

    #[test]
    fn default_logging_outputs_include_common_error_file() {
        let logging = LoggingConfig::default();

        let common_error = logging
            .outputs
            .iter()
            .find(|output| output.name == "common-error")
            .expect("common error log output should be configured by default");

        assert_eq!(common_error.file, "common-error.log");
        assert_eq!(common_error.level, "error");
        assert_eq!(common_error.format, LogOutputFormat::Text);
        assert_eq!(common_error.targets, vec!["*"]);
        assert_eq!(common_error.max_keep_days, 7);
    }

    #[test]
    fn database_config_default_selects_sqlite_without_mysql() {
        let database = DatabaseConfig::default();

        assert_eq!(database.database_type, DatabaseType::Sqlite);
        assert_eq!(database.sqlite.path, "bcs.db");
        assert_eq!(database.mysql.database, "bcs");
    }

    #[test]
    fn explicit_database_mysql_block_uses_mysql_defaults() {
        let toml = r#"
            type = "mysql"

            [mysql]
        "#;

        let database: DatabaseConfig = toml::from_str(toml).expect("parse database config");

        assert_eq!(database.database_type, DatabaseType::Mysql);
        assert_eq!(database.mysql.database, "bcs");
    }

    #[test]
    fn oauth_providers_map_parses_and_resolves_kind() {
        let toml = r#"
            base_url = "https://bcs.example.com"
            jwt_secret = "s"

            [providers.google]
            client_id = "gid"
            client_secret = "gsecret"

            [providers.github-partner]
            kind = "github"
            client_id = "ghid"
        "#;
        let cfg: OAuthSettings = toml::from_str(toml).expect("parse providers map");
        assert_eq!(cfg.providers.len(), 2);

        // kind omitted → defaults to the instance (map) name.
        let g = &cfg.providers["google"];
        assert_eq!(g.kind, None);
        assert_eq!(g.resolved_kind("google"), "google");

        // explicit kind decoupled from the instance name.
        let p = &cfg.providers["github-partner"];
        assert_eq!(p.resolved_kind("github-partner"), "github");

        cfg.validate().expect("valid config");
    }

    #[test]
    fn oauth_validate_rejects_empty_client_id() {
        let toml = r#"
            base_url = "https://bcs.example.com"
            [providers.google]
            client_id = ""
        "#;
        let cfg: OAuthSettings = toml::from_str(toml).unwrap();
        let err = cfg.validate().expect_err("empty client_id rejected");
        assert!(err.contains("client_id"), "got: {err}");
    }

    #[test]
    fn oauth_validate_rejects_route_unsafe_name() {
        let toml = r#"
            base_url = "https://bcs.example.com"
            [providers."bad/name"]
            client_id = "id"
        "#;
        let cfg: OAuthSettings = toml::from_str(toml).unwrap();
        let err = cfg.validate().expect_err("'/' in name rejected");
        assert!(err.contains("bad/name"), "got: {err}");
    }

    #[test]
    fn provider_settings_rejects_unknown_field() {
        // deny_unknown_fields guards against typos in a provider block.
        let toml = r#"
            client_id = "id"
            typo_field = "x"
        "#;
        let err = toml::from_str::<ProviderSettings>(toml).expect_err("unknown field rejected");
        assert!(err.to_string().contains("typo_field"), "got: {err}");
    }

    #[test]
    fn oauth_provider_settings_with_alipay_keys() {
        let toml = r#"
            base_url = "https://bcs.example.com"
            jwt_secret = "s"

            [providers.alipay]
            kind = "alipay"
            client_id = "2021001234567890"
            private_key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEv..."
            alipay_public_key = "-----BEGIN PUBLIC KEY-----\nMIIBIj..."
        "#;
        let cfg: OAuthSettings = toml::from_str(toml).expect("parse alipay provider");
        let alipay = &cfg.providers["alipay"];
        assert_eq!(alipay.resolved_kind("alipay"), "alipay");
        assert!(alipay.private_key.is_some());
        assert!(alipay.alipay_public_key.is_some());
        assert!(alipay.client_secret.is_none());
        cfg.validate().expect("valid config");
    }

    #[test]
    fn wechat_provider_settings_parses() {
        let toml = r#"
            base_url = "https://bcs.example.com"
            jwt_secret = "s"

            [providers.wechat]
            kind = "wechat"
            client_id = "wx1234567890"
            client_secret = "my_wechat_secret"
        "#;
        let cfg: OAuthSettings = toml::from_str(toml).expect("parse wechat provider");
        let wechat = &cfg.providers["wechat"];
        assert_eq!(wechat.resolved_kind("wechat"), "wechat");
        assert!(wechat.private_key.is_none());
        assert!(wechat.alipay_public_key.is_none());
        cfg.validate().expect("valid config");
    }

    #[test]
    fn eventing_defaults_are_safe_for_phase_zero_rollout() {
        let config = EventingConfig::default();

        assert!(!config.enabled);
        assert!(!config.dispatcher_enabled);
        assert_eq!(config.worker_concurrency, 64);
        assert_eq!(config.webhook.request_timeout_ms, 10_000);
        assert_eq!(config.limits.max_filters_per_subscription, 64);
        config.validate().expect("default Eventing config is valid");
    }

    #[test]
    fn eventing_enabled_and_dispatcher_enabled_are_independent_flags() {
        let record_only: EventingConfig = toml::from_str(
            r#"
            enabled = true
            dispatcher_enabled = false
            "#,
        )
        .expect("parse record-only Eventing config");
        let entirely_disabled: EventingConfig = toml::from_str(
            r#"
            enabled = false
            dispatcher_enabled = false
            "#,
        )
        .expect("parse disabled Eventing config");

        assert!(record_only.enabled);
        assert!(!record_only.dispatcher_enabled);
        assert!(!entirely_disabled.enabled);
    }

    #[test]
    fn eventing_config_rejects_unknown_fields() {
        let unknown = toml::from_str::<EventingConfig>("unknown_field = true")
            .expect_err("unknown Eventing field rejected");
        assert!(unknown.to_string().contains("unknown_field"));
    }

    #[test]
    fn eventing_config_validates_timeout_concurrency_retention_and_limits() {
        let mut concurrency = EventingConfig::default();
        concurrency.worker_concurrency = 0;
        assert!(
            concurrency
                .validate()
                .expect_err("zero workers rejected")
                .contains("worker_concurrency")
        );

        let mut timeout = EventingConfig::default();
        timeout.webhook.request_timeout_ms = 30_001;
        assert!(
            timeout
                .validate()
                .expect_err("timeout rejected")
                .contains("request_timeout_ms")
        );

        let mut retention = EventingConfig::default();
        retention.event_retention_days = 0;
        assert!(
            retention
                .validate()
                .expect_err("retention rejected")
                .contains("event_retention_days")
        );

        let mut limits = EventingConfig::default();
        limits.limits.max_filters_per_subscription = 65;
        assert!(
            limits
                .validate()
                .expect_err("filter limit rejected")
                .contains("max_filters_per_subscription")
        );
    }

    #[test]
    fn private_endpoint_allowlist_validates_wildcard_cidr_and_ports() {
        let config = toml::from_str::<EventingConfig>(
            r#"
            [[webhook.private_endpoint_allowlist]]
            host = "*.hooks.example.internal"
            cidrs = ["10.20.0.0/16"]
            ports = [443, 8443]
            "#,
        )
        .expect("private endpoint allowlist should deserialize");
        config.validate().expect("valid allowlist should pass");
        let entry = &config.webhook.private_endpoint_allowlist[0];
        assert!(entry.matches_host_and_port("a.hooks.example.internal", 8443));
        assert!(entry.matches_host_and_port("a.b.hooks.example.internal", 443));
        assert!(!entry.matches_host_and_port("hooks.example.internal", 443));
        assert!(!entry.matches_host_and_port("evilhooks.example.internal", 443));
        assert!(!entry.matches_host_and_port("a.hooks.example.internal", 9443));
    }

    #[test]
    fn private_endpoint_allowlist_rejects_unsafe_or_ambiguous_rules() {
        for (host, cidr, ports) in [
            ("hooks.*.internal", "10.0.0.0/8", vec![443]),
            ("*.localhost", "10.0.0.0/8", vec![443]),
            ("*.hooks.internal", "10.0.0.1/8", vec![443]),
            ("*.hooks.internal", "169.254.0.0/16", vec![443]),
            ("*.hooks.internal", "10.0.0.0/8", vec![]),
        ] {
            let mut config = EventingConfig::default();
            config.webhook.private_endpoint_allowlist = vec![
                PrivateEndpointAllowlistEntryConfig {
                    host: host.to_string(),
                    cidrs: vec![cidr.to_string()],
                    ports,
                },
            ];
            assert!(config.validate().is_err(), "rule should be rejected: {host}");
        }
    }

    #[test]
    fn human_notify_config_parses_provider_and_options() {
        let value: toml::Value = toml::from_str(
            r#"
provider = "dingtalk"

[providers.dingtalk]
enabled = true
robot_code = "ding-robot"
client_secret = "s3cret"
"#,
        )
        .expect("toml must parse");
        let config: HumanNotifyConfig = value.try_into().expect("config must deserialize");
        assert_eq!(config.provider.as_deref(), Some("dingtalk"));
        let provider = config
            .providers
            .get("dingtalk")
            .expect("dingtalk provider entry");
        assert!(provider.enabled);
        assert!(config.enabled_provider("dingtalk"));
        assert_eq!(
            provider.options.get("robot_code").and_then(|v| v.as_str()),
            Some("ding-robot")
        );
    }

    #[test]
    fn human_notify_config_defaults_to_disabled() {
        let config = HumanNotifyConfig::default();
        assert!(config.provider.is_none());
        assert!(config.providers.is_empty());
        assert!(!config.enabled_provider("dummy"));
    }
}
