//! Secure single-attempt Webhook delivery for public BCS Events.

use std::time::{Duration, SystemTime};

use async_trait::async_trait;
use bcs_route_security::{OutboundUrlError, OutboundUrlGuard};
use bcs_service_api::port::{
    EventDeliveryDisposition, EventDeliveryError, EventDeliveryPort, EventDeliveryRequest,
    EventDeliveryResponse,
};
use futures::StreamExt;
use reqwest::header::CONTENT_TYPE;
use url::Url;

pub const DEFAULT_RESPONSE_BODY_LIMIT: usize = 4 * 1024;
pub const DEFAULT_RETRY_AFTER_CAP_MS: u64 = 60 * 60 * 1_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WebhookEndpointPolicy {
    pub require_https: bool,
    pub allow_http_loopback: bool,
    pub reject_query_and_fragment: bool,
    pub allow_non_standard_ports: bool,
}

impl WebhookEndpointPolicy {
    pub fn production() -> Self {
        Self {
            require_https: true,
            allow_http_loopback: false,
            reject_query_and_fragment: true,
            allow_non_standard_ports: false,
        }
    }

    pub fn local_test() -> Self {
        Self {
            require_https: false,
            allow_http_loopback: true,
            reject_query_and_fragment: true,
            allow_non_standard_ports: true,
        }
    }

    pub fn local(allow_http_loopback: bool, allow_non_standard_ports: bool) -> Self {
        Self {
            require_https: true,
            allow_http_loopback,
            reject_query_and_fragment: true,
            allow_non_standard_ports,
        }
    }
}

#[derive(Debug, Clone)]
pub struct WebhookClient {
    url_guard: OutboundUrlGuard,
    endpoint_policy: WebhookEndpointPolicy,
    connect_timeout: Option<Duration>,
    response_body_limit: usize,
    retry_after_cap_ms: u64,
}

impl WebhookClient {
    pub fn production() -> Self {
        Self::new(
            OutboundUrlGuard::strict(),
            WebhookEndpointPolicy::production(),
        )
    }

    pub fn new(url_guard: OutboundUrlGuard, endpoint_policy: WebhookEndpointPolicy) -> Self {
        Self {
            url_guard,
            endpoint_policy,
            connect_timeout: None,
            response_body_limit: DEFAULT_RESPONSE_BODY_LIMIT,
            retry_after_cap_ms: DEFAULT_RETRY_AFTER_CAP_MS,
        }
    }

    pub fn with_response_body_limit(mut self, limit: usize) -> Self {
        self.response_body_limit = limit.max(1);
        self
    }

    pub fn with_connect_timeout(mut self, timeout: Duration) -> Self {
        self.connect_timeout = Some(timeout);
        self
    }

    pub fn with_retry_after_cap_ms(mut self, cap_ms: u64) -> Self {
        self.retry_after_cap_ms = cap_ms;
        self
    }

    async fn deliver_once(
        &self,
        request: EventDeliveryRequest,
    ) -> Result<EventDeliveryResponse, EventDeliveryError> {
        bcs_observability::observe_result("webhook.deliver_once", async {
        validate_request(&request)?;
        let endpoint = &request.endpoint_url;
        if let Err(category) =
            validate_endpoint_policy(endpoint, self.endpoint_policy, &self.url_guard)
        {
            return Ok(classified_response(
                EventDeliveryDisposition::Terminal,
                None,
                None,
                0,
                category,
                "endpoint rejected by Webhook policy",
            ));
        }
        let guarded_url = match self.url_guard.resolve_request_http_url(endpoint).await {
            Ok(url) => url,
            Err(OutboundUrlError::ResolveFailed(_)) => {
                return Ok(classified_response(
                    EventDeliveryDisposition::Retryable,
                    None,
                    None,
                    0,
                    "dns_resolution",
                    "endpoint DNS resolution failed",
                ));
            }
            Err(error) => {
                return Ok(classified_response(
                    EventDeliveryDisposition::Terminal,
                    None,
                    None,
                    0,
                    guard_error_category(&error),
                    "endpoint rejected by outbound URL guard",
                ));
            }
        };

        let mut client_builder = reqwest::Client::builder()
            .timeout(Duration::from_millis(request.request_timeout_ms))
            .redirect(reqwest::redirect::Policy::none());
        if let Some(connect_timeout) = self.connect_timeout {
            client_builder = client_builder.connect_timeout(connect_timeout);
        }
        if let Some((host, addresses)) = guarded_url.dns_override() {
            client_builder = client_builder.resolve_to_addrs(host, addresses);
        }
        let client = client_builder.build().map_err(|_| {
            EventDeliveryError::Internal("failed to initialize Webhook HTTP client".to_string())
        })?;
        let response = match client
            .post(guarded_url.as_str())
            .header(CONTENT_TYPE, "application/json; charset=utf-8")
            .body(request.body)
            .send()
            .await
        {
            Ok(response) => response,
            Err(error) => {
                let (category, summary) = if error.is_timeout() {
                    ("timeout", "Webhook request timed out")
                } else if error.is_connect() {
                    ("network", "Webhook connection failed")
                } else if error.is_builder() {
                    ("invalid_request", "Webhook request could not be built")
                } else {
                    ("transport", "Webhook transport failed")
                };
                let disposition = if error.is_builder() {
                    EventDeliveryDisposition::Terminal
                } else {
                    EventDeliveryDisposition::Retryable
                };
                return Ok(classified_response(
                    disposition,
                    None,
                    None,
                    0,
                    category,
                    summary,
                ));
            }
        };

        let status = response.status().as_u16();
        let retry_after_ms = if matches!(status, 429 | 503) {
            response
                .headers()
                .get(reqwest::header::RETRY_AFTER)
                .and_then(|value| value.to_str().ok())
                .and_then(|value| {
                    parse_retry_after_ms(value, SystemTime::now(), self.retry_after_cap_ms)
                })
        } else {
            None
        };
        let mut observed = 0_u64;
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = match chunk {
                Ok(chunk) => chunk,
                Err(_) => {
                    return Ok(classified_response(
                        EventDeliveryDisposition::Retryable,
                        Some(status),
                        retry_after_ms,
                        observed,
                        "response_body",
                        "Webhook response body could not be read",
                    ));
                }
            };
            let remaining = (self.response_body_limit as u64).saturating_sub(observed);
            observed = observed.saturating_add((chunk.len() as u64).min(remaining));
            if chunk.len() as u64 > remaining || observed == self.response_body_limit as u64 {
                break;
            }
        }

        Ok(classify_http_status(status, retry_after_ms, observed))
            }).await
    }
}

impl Default for WebhookClient {
    fn default() -> Self {
        Self::production()
    }
}

#[async_trait]
impl EventDeliveryPort for WebhookClient {
    async fn deliver(
        &self,
        request: EventDeliveryRequest,
    ) -> Result<EventDeliveryResponse, EventDeliveryError> {
        self.deliver_once(request).await
    }
}

pub fn parse_retry_after_ms(value: &str, now: SystemTime, cap_ms: u64) -> Option<u64> {
    let millis = if let Ok(seconds) = value.trim().parse::<u64>() {
        seconds.saturating_mul(1_000)
    } else {
        let retry_at = httpdate::parse_http_date(value).ok()?;
        retry_at
            .duration_since(now)
            .unwrap_or_default()
            .as_millis()
            .try_into()
            .unwrap_or(u64::MAX)
    };
    Some(millis.min(cap_ms))
}

fn validate_request(request: &EventDeliveryRequest) -> Result<(), EventDeliveryError> {
    if request.request_timeout_ms == 0 {
        return Err(EventDeliveryError::InvalidRequest(
            "request timeout must be non-zero".to_string(),
        ));
    }
    if request.endpoint_url.is_empty() {
        return Err(EventDeliveryError::InvalidRequest(
            "endpoint URL must be non-empty".to_string(),
        ));
    }
    Ok(())
}

fn validate_endpoint_policy(
    raw_url: &str,
    policy: WebhookEndpointPolicy,
    url_guard: &OutboundUrlGuard,
) -> Result<(), &'static str> {
    let url = Url::parse(raw_url).map_err(|_| "invalid_url")?;
    let allowlisted_http = url.scheme() == "http"
        && url_guard.allows_allowlisted_host_port(raw_url);
    let http_allowed = url.scheme() == "http"
        && (!policy.require_https
            || (policy.allow_http_loopback && is_loopback_host(&url))
            || allowlisted_http);
    if url.scheme() != "https" && !http_allowed {
        return Err("https_required");
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err("userinfo_not_allowed");
    }
    if policy.reject_query_and_fragment && (url.query().is_some() || url.fragment().is_some()) {
        return Err("url_components_not_allowed");
    }
    if !policy.allow_non_standard_ports {
        let standard_port = match url.scheme() {
            "https" => 443,
            "http" => 80,
            _ => return Err("invalid_scheme"),
        };
        if url.port_or_known_default() != Some(standard_port)
            && !url_guard.allows_allowlisted_host_port(raw_url)
        {
            return Err("non_standard_port_not_allowed");
        }
    }
    Ok(())
}

fn is_loopback_host(url: &Url) -> bool {
    match url.host() {
        Some(url::Host::Domain(domain)) => domain.eq_ignore_ascii_case("localhost"),
        Some(url::Host::Ipv4(address)) => address.is_loopback(),
        Some(url::Host::Ipv6(address)) => address.is_loopback(),
        None => false,
    }
}

fn classify_http_status(
    status: u16,
    retry_after_ms: Option<u64>,
    response_bytes_observed: u64,
) -> EventDeliveryResponse {
    let disposition = match status {
        200..=299 => EventDeliveryDisposition::Succeeded,
        410 => EventDeliveryDisposition::DisableSubscription,
        408 | 425 | 429 | 500..=599 => EventDeliveryDisposition::Retryable,
        _ => EventDeliveryDisposition::Terminal,
    };
    let (category, summary) = if disposition == EventDeliveryDisposition::Succeeded {
        (None, None)
    } else {
        (
            Some("http_status".to_string()),
            Some(format!("Webhook endpoint returned HTTP {status}")),
        )
    };
    EventDeliveryResponse {
        disposition,
        http_status: Some(status),
        retry_after_ms,
        response_bytes_observed,
        error_category: category,
        error_summary: summary,
    }
}

fn classified_response(
    disposition: EventDeliveryDisposition,
    http_status: Option<u16>,
    retry_after_ms: Option<u64>,
    response_bytes_observed: u64,
    category: &str,
    summary: &str,
) -> EventDeliveryResponse {
    EventDeliveryResponse {
        disposition,
        http_status,
        retry_after_ms,
        response_bytes_observed,
        error_category: Some(category.to_string()),
        error_summary: Some(summary.to_string()),
    }
}

fn guard_error_category(error: &OutboundUrlError) -> &'static str {
    match error {
        OutboundUrlError::UnsupportedScheme => "url_scheme",
        OutboundUrlError::UserInfoNotAllowed => "userinfo_not_allowed",
        OutboundUrlError::UnsafeHost(_) | OutboundUrlError::UnsafeAddress(_) => "private_address",
        OutboundUrlError::ResolveFailed(_) => "dns_resolution",
        OutboundUrlError::InvalidUrl(_)
        | OutboundUrlError::MissingHost
        | OutboundUrlError::MissingPort => "invalid_url",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retry_after_delta_and_http_date_are_capped() {
        let now = SystemTime::UNIX_EPOCH + Duration::from_secs(1_000);
        assert_eq!(parse_retry_after_ms("5", now, 3_000), Some(3_000));
        let date = httpdate::fmt_http_date(now + Duration::from_secs(2));
        assert_eq!(parse_retry_after_ms(&date, now, 10_000), Some(2_000));
        assert_eq!(parse_retry_after_ms("invalid", now, 10_000), None);
    }

    #[test]
    fn status_classification_matches_contract() {
        for status in [200, 204, 299] {
            assert_eq!(
                classify_http_status(status, None, 0).disposition,
                EventDeliveryDisposition::Succeeded
            );
        }
        for status in [408, 425, 429, 500, 503, 599] {
            assert_eq!(
                classify_http_status(status, None, 0).disposition,
                EventDeliveryDisposition::Retryable
            );
        }
        assert_eq!(
            classify_http_status(410, None, 0).disposition,
            EventDeliveryDisposition::DisableSubscription
        );
        for status in [300, 302, 400, 409, 422, 499] {
            assert_eq!(
                classify_http_status(status, None, 0).disposition,
                EventDeliveryDisposition::Terminal
            );
        }
    }

    #[test]
    fn local_endpoint_policy_allows_http_loopback_only() {
        let policy = WebhookEndpointPolicy::local(true, true);
        let guard = OutboundUrlGuard::new(true, true);

        assert!(
            validate_endpoint_policy("http://127.0.0.1:28082/events", policy, &guard).is_ok()
        );
        assert!(
            validate_endpoint_policy("http://events.example.com/events", policy, &guard).is_err()
        );
        assert!(
            validate_endpoint_policy(
                "http://127.0.0.1:28082/events",
                WebhookEndpointPolicy::production(),
                &guard,
            )
            .is_err()
        );
    }

    #[test]
    fn private_endpoint_allowlist_can_enable_http_and_an_exact_non_standard_port() {
        let entry = bcs_config_api::PrivateEndpointAllowlistEntryConfig {
            host: "*.hooks.example.internal".to_string(),
            cidrs: vec!["10.20.0.0/16".to_string()],
            ports: vec![80, 8443],
        };
        let guard = OutboundUrlGuard::strict()
            .with_private_endpoint_allowlist(&[entry])
            .expect("valid private endpoint allowlist");

        assert!(
            validate_endpoint_policy(
                "https://worker.hooks.example.internal:8443/events",
                WebhookEndpointPolicy::production(),
                &guard,
            )
            .is_ok()
        );
        assert!(
            validate_endpoint_policy(
                "https://worker.hooks.example.internal:9443/events",
                WebhookEndpointPolicy::production(),
                &guard,
            )
            .is_err()
        );
        assert!(
            validate_endpoint_policy(
                "http://worker.hooks.example.internal/events",
                WebhookEndpointPolicy::production(),
                &guard,
            )
            .is_ok()
        );
        assert!(
            validate_endpoint_policy(
                "http://worker.hooks.example.internal:8080/events",
                WebhookEndpointPolicy::production(),
                &guard,
            )
            .is_err()
        );
    }
}
