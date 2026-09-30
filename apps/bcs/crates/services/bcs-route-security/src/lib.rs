//! Route-time security policy (intercept, pass / block / degraded
//! decisions) for BCS.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};

use bcs_config_api::PrivateEndpointAllowlistEntryConfig;
use ipnet::IpNet;
use tokio::net::lookup_host;
use url::{Host, Url};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutboundUrlGuard {
    block_private_networks: bool,
    allow_loopback: bool,
    private_endpoint_allowlist: Vec<CompiledPrivateEndpointAllowlistEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct CompiledPrivateEndpointAllowlistEntry {
    config: PrivateEndpointAllowlistEntryConfig,
    cidrs: Vec<IpNet>,
}

/// One request-time validation result. Callers must not cache or persist it
/// across outbound attempts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedRequestUrl {
    raw_url: String,
    dns_override_host: Option<String>,
    resolved_addrs: Vec<SocketAddr>,
}

impl ValidatedRequestUrl {
    pub fn as_str(&self) -> &str {
        &self.raw_url
    }

    pub fn dns_override(&self) -> Option<(&str, &[SocketAddr])> {
        self.dns_override_host
            .as_deref()
            .map(|host| (host, self.resolved_addrs.as_slice()))
    }
}

impl OutboundUrlGuard {
    pub fn new(block_private_networks: bool, allow_loopback: bool) -> Self {
        Self {
            block_private_networks,
            allow_loopback,
            private_endpoint_allowlist: Vec::new(),
        }
    }

    pub fn with_private_endpoint_allowlist(
        mut self,
        entries: &[PrivateEndpointAllowlistEntryConfig],
    ) -> Result<Self, String> {
        let mut compiled = Vec::with_capacity(entries.len());
        for entry in entries {
            entry.validate()?;
            let cidrs = entry
                .cidrs
                .iter()
                .map(|cidr| {
                    cidr.parse::<IpNet>().map_err(|_| {
                        format!(
                            "eventing.webhook private endpoint '{}' contains invalid CIDR '{cidr}'",
                            entry.host
                        )
                    })
                })
                .collect::<Result<Vec<_>, _>>()?;
            compiled.push(CompiledPrivateEndpointAllowlistEntry {
                config: entry.clone(),
                cidrs,
            });
        }
        self.private_endpoint_allowlist = compiled;
        Ok(self)
    }

    pub fn strict() -> Self {
        Self::new(true, false)
    }

    pub fn allowing_private_networks_for_tests() -> Self {
        Self::new(false, true)
    }

    pub fn validate_configured_http_url(&self, raw_url: &str) -> Result<(), OutboundUrlError> {
        let url = parse_http_url(raw_url)?;
        self.validate_host_literal(&url)
    }

    pub fn allows_allowlisted_host_port(&self, raw_url: &str) -> bool {
        let Ok(url) = parse_http_url(raw_url) else {
            return false;
        };
        let (Some(Host::Domain(host)), Some(port)) = (url.host(), url.port_or_known_default())
        else {
            return false;
        };
        self.private_endpoint_allowlist
            .iter()
            .any(|entry| entry.config.matches_host_and_port(host, port))
    }

    pub async fn validate_request_http_url(&self, raw_url: &str) -> Result<(), OutboundUrlError> {
        self.resolve_request_http_url(raw_url).await.map(|_| ())
    }

    pub async fn resolve_request_http_url(
        &self,
        raw_url: &str,
    ) -> Result<ValidatedRequestUrl, OutboundUrlError> {
        let url = parse_http_url(raw_url)?;
        self.validate_host_literal(&url)?;
        let (dns_override_host, resolved_addrs) = self.validate_resolved_addresses(&url).await?;
        Ok(ValidatedRequestUrl {
            raw_url: raw_url.to_string(),
            dns_override_host,
            resolved_addrs,
        })
    }

    fn validate_host_literal(&self, url: &Url) -> Result<(), OutboundUrlError> {
        let host = url.host().ok_or(OutboundUrlError::MissingHost)?;
        match host {
            Host::Ipv4(addr) => self.validate_ip(IpAddr::V4(addr)),
            Host::Ipv6(addr) => self.validate_ip(IpAddr::V6(addr)),
            Host::Domain(domain) => {
                if is_localhost_name(domain) && !self.allow_loopback {
                    return Err(OutboundUrlError::UnsafeHost(domain.to_string()));
                }
                Ok(())
            }
        }
    }

    async fn validate_resolved_addresses(
        &self,
        url: &Url,
    ) -> Result<(Option<String>, Vec<SocketAddr>), OutboundUrlError> {
        bcs_observability::observe_result("http.dns_and_policy", async {
        let Some(Host::Domain(domain)) = url.host() else {
            return Ok((None, Vec::new()));
        };
        if !self.block_private_networks && self.allow_loopback {
            return Ok((None, Vec::new()));
        }
        let port = url
            .port_or_known_default()
            .ok_or(OutboundUrlError::MissingPort)?;
        let mut resolved = lookup_host((domain, port))
            .await
            .map_err(|error| OutboundUrlError::ResolveFailed(error.to_string()))?;
        let mut addrs = Vec::new();
        for addr in resolved.by_ref() {
            if let Err(error) = self.validate_ip(addr.ip())
                && !self.is_allowlisted_private_address(domain, port, addr.ip())
            {
                return Err(error);
            }
            addrs.push(addr);
        }
        if addrs.is_empty() {
            Err(OutboundUrlError::ResolveFailed(
                "host resolved to no addresses".to_string(),
            ))
        } else {
            Ok((Some(domain.to_string()), addrs))
        }
            }).await
    }

    fn is_allowlisted_private_address(&self, host: &str, port: u16, ip: IpAddr) -> bool {
        self.private_endpoint_allowlist.iter().any(|entry| {
            entry.config.matches_host_and_port(host, port)
                && entry.cidrs.iter().any(|cidr| cidr.contains(&ip))
        })
    }

    fn validate_ip(&self, ip: IpAddr) -> Result<(), OutboundUrlError> {
        if is_loopback_ip(ip) {
            return if self.allow_loopback {
                Ok(())
            } else {
                Err(OutboundUrlError::UnsafeAddress(ip))
            };
        }
        if !self.block_private_networks {
            return Ok(());
        }
        if is_publicly_routable_ip(ip) {
            return Ok(());
        }
        Err(OutboundUrlError::UnsafeAddress(ip))
    }
}

impl Default for OutboundUrlGuard {
    fn default() -> Self {
        Self::strict()
    }
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum OutboundUrlError {
    #[error("URL is not valid: {0}")]
    InvalidUrl(String),
    #[error("URL scheme must be http or https")]
    UnsupportedScheme,
    #[error("URL must not contain userinfo")]
    UserInfoNotAllowed,
    #[error("URL must include a host")]
    MissingHost,
    #[error("URL must include a port or known http(s) default")]
    MissingPort,
    #[error("host '{0}' is not allowed for outbound callbacks")]
    UnsafeHost(String),
    #[error("address '{0}' is not allowed for outbound callbacks")]
    UnsafeAddress(IpAddr),
    #[error("host resolution failed: {0}")]
    ResolveFailed(String),
}

fn parse_http_url(raw_url: &str) -> Result<Url, OutboundUrlError> {
    let url = Url::parse(raw_url).map_err(|error| OutboundUrlError::InvalidUrl(error.to_string()))?;
    match url.scheme() {
        "http" | "https" => {}
        _ => return Err(OutboundUrlError::UnsupportedScheme),
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err(OutboundUrlError::UserInfoNotAllowed);
    }
    if url.host().is_none() {
        return Err(OutboundUrlError::MissingHost);
    }
    Ok(url)
}

fn is_localhost_name(host: &str) -> bool {
    let host = host.trim_end_matches('.').to_ascii_lowercase();
    host == "localhost" || host.ends_with(".localhost")
}

fn is_publicly_routable_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(addr) => is_publicly_routable_ipv4(addr),
        IpAddr::V6(addr) => is_publicly_routable_ipv6(addr),
    }
}

fn is_loopback_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(addr) => addr.is_loopback(),
        IpAddr::V6(addr) => {
            addr.is_loopback()
                || addr
                    .to_ipv4_mapped()
                    .is_some_and(|mapped| mapped.is_loopback())
        }
    }
}

fn is_publicly_routable_ipv4(addr: Ipv4Addr) -> bool {
    let [a, b, c, d] = addr.octets();
    if a == 0 || a == 10 || a == 127 || a >= 224 {
        return false;
    }
    if a == 100 && (64..=127).contains(&b) {
        return false;
    }
    if a == 169 && b == 254 {
        return false;
    }
    if a == 172 && (16..=31).contains(&b) {
        return false;
    }
    if a == 192 && b == 168 {
        return false;
    }
    if a == 192 && b == 0 && c == 0 {
        return false;
    }
    if a == 192 && b == 0 && c == 2 {
        return false;
    }
    if a == 198 && (b == 18 || b == 19) {
        return false;
    }
    if a == 198 && b == 51 && c == 100 {
        return false;
    }
    if a == 203 && b == 0 && c == 113 {
        return false;
    }
    !(a == 255 && b == 255 && c == 255 && d == 255)
}

fn is_publicly_routable_ipv6(addr: Ipv6Addr) -> bool {
    if let Some(mapped) = addr.to_ipv4_mapped() {
        return is_publicly_routable_ipv4(mapped);
    }
    let segments = addr.segments();
    if addr.is_unspecified() || addr.is_loopback() {
        return false;
    }
    if (segments[0] & 0xfe00) == 0xfc00 {
        return false;
    }
    if (segments[0] & 0xffc0) == 0xfe80 {
        return false;
    }
    if (segments[0] & 0xff00) == 0xff00 {
        return false;
    }
    !(segments[0] == 0x2001 && segments[1] == 0x0db8)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn configured_url_rejects_private_and_local_addresses() {
        let guard = OutboundUrlGuard::strict();
        for url in [
            "http://127.0.0.1:8080/hook",
            "http://10.0.0.1/hook",
            "http://172.16.0.1/hook",
            "http://192.168.1.10/hook",
            "http://169.254.169.254/latest/meta-data/",
            "http://0.0.0.0/hook",
            "http://[::1]/hook",
            "http://[fc00::1]/hook",
            "http://[fe80::1]/hook",
            "http://localhost/hook",
        ] {
            assert!(guard.validate_configured_http_url(url).is_err(), "{url}");
        }
    }

    #[test]
    fn configured_url_accepts_public_http_urls() {
        let guard = OutboundUrlGuard::strict();
        assert!(guard.validate_configured_http_url("https://example.com/hook").is_ok());
        assert!(guard.validate_configured_http_url("https://1.1.1.1/hook").is_ok());
        assert!(guard.validate_configured_http_url("http://[2606:4700:4700::1111]/hook").is_ok());
    }

    #[test]
    fn configured_url_rejects_non_http_and_userinfo() {
        let guard = OutboundUrlGuard::strict();
        assert!(guard.validate_configured_http_url("file:///etc/passwd").is_err());
        assert!(guard.validate_configured_http_url("https://user@example.com/hook").is_err());
    }

    #[test]
    fn test_guard_allows_private_addresses_without_dns() {
        let guard = OutboundUrlGuard::allowing_private_networks_for_tests();
        assert!(guard.validate_configured_http_url("http://127.0.0.1:8080/hook").is_ok());
    }

    #[test]
    fn configured_url_can_allow_private_but_still_block_loopback() {
        let guard = OutboundUrlGuard::new(false, false);
        assert!(guard.validate_configured_http_url("http://10.0.0.1/hook").is_ok());
        assert!(guard.validate_configured_http_url("http://127.0.0.1/hook").is_err());
        assert!(guard.validate_configured_http_url("http://localhost/hook").is_err());
    }

    #[test]
    fn configured_url_can_allow_loopback_while_blocking_other_private_addresses() {
        let guard = OutboundUrlGuard::new(true, true);
        assert!(guard.validate_configured_http_url("http://127.0.0.1/hook").is_ok());
        assert!(guard.validate_configured_http_url("http://localhost/hook").is_ok());
        assert!(guard.validate_configured_http_url("http://10.0.0.1/hook").is_err());
    }

    #[test]
    fn private_endpoint_allowlist_requires_host_port_and_cidr_to_match() {
        let entry = PrivateEndpointAllowlistEntryConfig {
            host: "*.hooks.example.internal".to_string(),
            cidrs: vec!["10.20.0.0/16".to_string()],
            ports: vec![443, 8443],
        };
        let guard = OutboundUrlGuard::strict()
            .with_private_endpoint_allowlist(&[entry])
            .expect("valid private endpoint allowlist");
        let allowed = IpAddr::V4(Ipv4Addr::new(10, 20, 7, 9));
        let other_private = IpAddr::V4(Ipv4Addr::new(10, 21, 7, 9));

        assert!(guard.is_allowlisted_private_address(
            "worker.hooks.example.internal",
            443,
            allowed
        ));
        assert!(guard.is_allowlisted_private_address(
            "a.b.hooks.example.internal",
            8443,
            allowed
        ));
        assert!(!guard.is_allowlisted_private_address(
            "hooks.example.internal",
            443,
            allowed
        ));
        assert!(!guard.is_allowlisted_private_address(
            "worker.hooks.example.internal",
            9443,
            allowed
        ));
        assert!(!guard.is_allowlisted_private_address(
            "worker.hooks.example.internal",
            443,
            other_private
        ));
        assert!(guard.allows_allowlisted_host_port(
            "https://worker.hooks.example.internal:8443/events"
        ));
        assert!(!guard.allows_allowlisted_host_port(
            "https://hooks.example.internal:8443/events"
        ));
    }

    #[tokio::test]
    async fn request_url_for_ip_literal_has_no_dns_override() {
        let guard = OutboundUrlGuard::strict();
        let url = guard
            .resolve_request_http_url("https://1.1.1.1/hook")
            .await
            .expect("public IP literal should pass");

        assert_eq!(url.as_str(), "https://1.1.1.1/hook");
        assert!(url.dns_override().is_none());
    }
}
