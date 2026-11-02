//! Guarded, bounded public AgentCard fetch; no automatic discovery or redirects.
use std::time::Duration;
use async_trait::async_trait;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::port::a2a_card::{A2aCardPort, ValidatedA2aCard};
use bcs_service_api::port::repo::a2a_bot::{A2aBinding, A2aInterface, A2aUpdateMode};
use reqwest::header::{CACHE_CONTROL, CONTENT_TYPE};
use crate::{card::select_card_with_bindings, grpc_mesh::GrpcMeshConfig};

const MAX_CARD_BYTES: usize = 1_048_576;

pub struct HttpA2aCardFetcher {
    guard: OutboundUrlGuard,
    supported_extensions: Vec<String>,
    grpc: Option<GrpcMeshConfig>,
}

fn rejected(reason: &str) -> ServiceError {
    ServiceError::InvalidOperation { message: format!("A2A Card: {reason}"), request_id: None }
}

impl HttpA2aCardFetcher {
    pub fn new(guard: OutboundUrlGuard, supported_extensions: Vec<String>) -> Self {
        Self { guard, supported_extensions, grpc:None }
    }

    pub fn with_grpc_mesh(mut self, config: GrpcMeshConfig) -> ServiceResult<Self> {
        config.validate().map_err(|_|rejected("invalid gRPC mesh configuration"))?;
        self.grpc=Some(config); Ok(self)
    }
}

/// Re-resolve and pin policy-approved addresses on each outbound attempt.
/// Credentials and URLs are never interpolated into externally visible errors.
pub async fn guarded_client(guard: &OutboundUrlGuard, url: &str) -> ServiceResult<reqwest::Client> {
    guarded_client_with_headers(guard, url, reqwest::header::HeaderMap::new()).await
}

pub(super) async fn guarded_client_with_headers(guard: &OutboundUrlGuard, url: &str, headers: reqwest::header::HeaderMap) -> ServiceResult<reqwest::Client> {
    let parsed = reqwest::Url::parse(url).map_err(|_| rejected("invalid address"))?;
    let loopback = parsed.host_str().is_some_and(|host| host == "localhost"
        || host.parse::<std::net::IpAddr>().is_ok_and(|ip| ip.is_loopback())
        || host == "[::1]");
    if parsed.scheme() != "https" && !(parsed.scheme() == "http" && loopback) {
        return Err(rejected("TLS required outside loopback"));
    }
    let checked = guard.resolve_request_http_url(url).await.map_err(|_| rejected("address rejected"))?;
    let mut builder = reqwest::Client::builder().default_headers(headers).no_proxy()
        .redirect(reqwest::redirect::Policy::none()).connect_timeout(Duration::from_secs(5));
    if let Some((host, addresses)) = checked.dns_override() {
        builder = builder.resolve_to_addrs(host, addresses);
    }
    builder.build().map_err(|_| rejected("HTTP client unavailable"))
}

#[async_trait]
impl A2aCardPort for HttpA2aCardFetcher {
    async fn fetch(&self, url: &str, bearer_configured: bool) -> ServiceResult<ValidatedA2aCard> {
        // Covers DNS resolution, response headers and the complete bounded body.
        tokio::time::timeout(Duration::from_secs(10), async {
            let client = guarded_client(&self.guard, url).await?;
            let mut response = client.get(url).header("Accept", "application/json").send().await
                .map_err(|_| rejected("fetch failed"))?;
            if !response.status().is_success() { return Err(rejected("HTTP response rejected")); }
            let media_type = response.headers().get(CONTENT_TYPE).and_then(|v|v.to_str().ok())
                .and_then(|v|v.split(';').next()).map(str::trim);
            if media_type != Some("application/json") { return Err(rejected("JSON media type required")); }
            for header in response.headers().get_all(CACHE_CONTROL) {
                let value = header.to_str().map_err(|_|rejected("invalid cache declaration"))?;
                if value.split(',').any(|v| {
                    let directive = v.split('=').next().unwrap_or("").trim();
                    directive.eq_ignore_ascii_case("no-store") || directive.eq_ignore_ascii_case("no-cache")
                }) { return Err(rejected("no-cache/no-store not supported in phase one")); }
            }
            if response.content_length().is_some_and(|n| n > MAX_CARD_BYTES as u64) {
                return Err(rejected("size limit exceeded"));
            }
            let mut body = Vec::new();
            while let Some(chunk) = response.chunk().await.map_err(|_| rejected("incomplete body"))? {
                if body.len().saturating_add(chunk.len()) > MAX_CARD_BYTES { return Err(rejected("size limit exceeded")); }
                body.extend_from_slice(&chunk);
            }
            let raw = serde_json::from_slice(&body).map_err(|_|rejected("invalid JSON"))?;
            self.validate(raw, bearer_configured).await
        }).await.map_err(|_|rejected("fetch timeout"))?
    }
    async fn validate(&self, raw:serde_json::Value, bearer_configured:bool)->ServiceResult<ValidatedA2aCard> {
        if serde_json::to_vec(&raw).map_err(|_|rejected("invalid JSON"))?.len()>MAX_CARD_BYTES {return Err(rejected("size limit exceeded"));}
        tokio::time::timeout(Duration::from_secs(10),async {
            let extensions = self.supported_extensions.iter().map(String::as_str).collect::<Vec<_>>();
            let bindings: &[&str] = if self.grpc.is_some() { &["GRPC", "HTTP+JSON"] } else { &["HTTP+JSON"] };
            let card = select_card_with_bindings(raw, &extensions, bearer_configured, bindings,
                |target| if target.protocol_binding == "GRPC" {
                    self.grpc.as_ref().is_some_and(|mesh|mesh.supports(&target.url))
                } else {self.guard.validate_configured_http_url(&target.url).is_ok()})
                .map_err(|_|rejected("incompatible declaration"))?;
            // Includes DNS and TLS checks for the selected interface, without
            // submitting a task or forwarding credentials to the Card host.
            let binding = if card.interface.protocol_binding == "GRPC" { A2aBinding::Grpc }
                else { guarded_client(&self.guard, &card.interface.url).await?; A2aBinding::HttpJson };
            Ok(ValidatedA2aCard { raw: card.raw, interface: A2aInterface {
                url: card.interface.url, tenant: card.interface.tenant,
                protocol_version: card.interface.protocol_version,
                binding, update_mode: A2aUpdateMode::Stream,
            } })
        }).await.map_err(|_|rejected("validation timeout"))?
    }

}
