//! Eventing configuration and outbound endpoint policy.

use crate::default_true;
use std::net::IpAddr;
use std::collections::BTreeSet;
use ipnet::IpNet;
use serde::{Deserialize, Serialize};

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
    /// Optional idle ceiling; omission preserves the fanout polling interval.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fanout_idle_poll_max_interval_ms: Option<u64>,
    /// Optional idle ceiling; omission preserves the delivery polling interval.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delivery_idle_poll_max_interval_ms: Option<u64>,
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
            fanout_idle_poll_max_interval_ms: None,
            delivery_idle_poll_max_interval_ms: None,
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
        for (field, ceiling, base) in [
            ("fanout_idle_poll_max_interval_ms", self.fanout_idle_poll_max_interval_ms, self.fanout_poll_interval_ms),
            ("delivery_idle_poll_max_interval_ms", self.delivery_idle_poll_max_interval_ms, self.delivery_poll_interval_ms),
        ] {
            if let Some(ceiling) = ceiling {
                validate_eventing_range(field, ceiling, base, 60_000)?;
            }
        }
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
    /// Reject private and otherwise non-public webhook target addresses.
    /// Eventing owns this policy independently from the shared outbound URL policy.
    #[serde(default = "default_true")]
    pub block_private_networks: bool,
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
            block_private_networks: true,
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

#[cfg(test)]
mod polling_tests {
    use super::*;

    #[test]
    fn omitted_idle_ceilings_preserve_custom_legacy_intervals() {
        let config: EventingConfig = serde_json::from_value(serde_json::json!({
            "fanout_poll_interval_ms": 5000,
            "delivery_poll_interval_ms": 3000
        })).expect("legacy config");
        assert!(config.validate().is_ok());
        assert_eq!(config.fanout_idle_poll_max_interval_ms, None);
        assert_eq!(config.delivery_idle_poll_max_interval_ms, None);
        let json = serde_json::to_value(config).expect("serialize config");
        assert!(json.get("fanout_idle_poll_max_interval_ms").is_none());
    }

    #[test]
    fn idle_ceilings_validate_each_worker_base_and_global_bound() {
        for (fanout, delivery, valid) in [
            (200, 200, true), (1000, 2000, true), (60_000, 60_000, true),
            (199, 1000, false), (1000, 199, false), (0, 1000, false),
            (60_001, 1000, false), (1000, 60_001, false),
        ] {
            let config = EventingConfig {
                fanout_idle_poll_max_interval_ms: Some(fanout),
                delivery_idle_poll_max_interval_ms: Some(delivery),
                ..EventingConfig::default()
            };
            assert_eq!(config.validate().is_ok(), valid, "ceilings {fanout}/{delivery}");
        }
    }
}
