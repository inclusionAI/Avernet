//! Guarded, bounded public AgentCard fetch; no automatic discovery or redirects.
use std::time::Duration;
use async_trait::async_trait;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::port::a2a_card::{A2aCardPort, ValidatedA2aCard};
use bcs_service_api::port::repo::a2a_bot::{A2aBinding, A2aInterface, A2aUpdateMode};
use reqwest::header::CONTENT_TYPE;
use bcs_service_api::types::a2a_bot::a2a_management::{ManagementError,ManagementCode as Code};
use crate::{card::select_card_with_bindings, grpc_mesh::GrpcMeshConfig};

const MAX_CARD_BYTES: usize = 1_048_576;

pub struct HttpA2aCardFetcher {
    guard: OutboundUrlGuard,
    supported_extensions: Vec<String>,
    grpc: Option<GrpcMeshConfig>,
}

fn rejected(reason: &str) -> ServiceError {
    let (code,reason)=match reason {
        "size limit exceeded"=>(Code::CardTooLarge,"SIZE_LIMIT_EXCEEDED"),
        "fetch timeout"|"validation timeout"=>(Code::CardFetchTimeout,"TIMEOUT"),
        "fetch failed"|"incomplete body"|"HTTP response rejected"=>(Code::CardFetchFailed,"FETCH_FAILED"),
        "address rejected"|"TLS required outside loopback"|"invalid address"=>(Code::CardAddressRejected,"ADDRESS_REJECTED"),
        "HTTP client unavailable"=>(Code::ServiceUnavailable,"CLIENT_UNAVAILABLE"),
        _=>(Code::InvalidCard,"INVALID_DECLARATION"),
    };
    ManagementError::new(code,"agent_card",reason).into()
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
pub async fn guarded_client(guard: &OutboundUrlGuard, url: &str) -> ServiceResult<bcs_network_tracing::ClientWithDefaults<reqwest::Client>> {
    guarded_client_with_headers(guard, url, reqwest::header::HeaderMap::new()).await
}

pub(super) async fn guarded_client_with_headers(guard: &OutboundUrlGuard, url: &str, headers: reqwest::header::HeaderMap) -> ServiceResult<bcs_network_tracing::ClientWithDefaults<reqwest::Client>> {
    if !bcs_service_api::types::a2a_bot::http_interface_address_allowed(url) {
        return Err(rejected("address rejected"));
    }
    let checked = guard.resolve_request_http_url(url).await.map_err(|_| rejected("address rejected"))?;
    let mut builder = reqwest::Client::builder().no_proxy()
        .redirect(reqwest::redirect::Policy::none()).connect_timeout(Duration::from_secs(5));
    if let Some((host, addresses)) = checked.dns_override() {
        builder = builder.resolve_to_addrs(host, addresses);
    }
    builder.build().map(|client| bcs_network_tracing::ClientWithDefaults::new(client, headers))
        .map_err(|_| rejected("HTTP client unavailable"))
}

#[async_trait]
impl A2aCardPort for HttpA2aCardFetcher {
    async fn fetch(&self, url: &str, bearer_configured: bool) -> ServiceResult<ValidatedA2aCard> {
        // Covers DNS resolution, response headers and the complete bounded body.
        tokio::time::timeout(Duration::from_secs(10), async {
            let client = guarded_client(&self.guard, url).await?;
            let mut response = client.request(|http| http.get(url).header("Accept", "application/json")).send().await
                .map_err(|_| rejected("fetch failed"))?;
            if response.status()!=reqwest::StatusCode::OK { return Err(rejected("HTTP response rejected")); }
            let media_type = response.headers().get(CONTENT_TYPE).and_then(|v|v.to_str().ok())
                .and_then(|v|v.split(';').next()).map(str::trim);
            if media_type != Some("application/json") { return Err(rejected("JSON media type required")); }
            if response.content_length().is_some_and(|n| n > MAX_CARD_BYTES as u64) {
                return Err(rejected("size limit exceeded"));
            }
            let mut body = Vec::new();
            while let Some(chunk) = response.chunk().await.map_err(|_| rejected("incomplete body"))? {
                if body.len().saturating_add(chunk.len()) > MAX_CARD_BYTES { return Err(rejected("size limit exceeded")); }
                body.extend_from_slice(&chunk);
            }
            let raw = bcs_service_api::types::a2a_bot::a2a_management::parse_card_json(&body).map_err(|_|rejected("invalid JSON"))?;
            self.validate(raw, bearer_configured).await
        }).await.map_err(|_|rejected("fetch timeout"))?
    }
    async fn validate(&self, raw:serde_json::Value, bearer_configured:bool)->ServiceResult<ValidatedA2aCard> {
        if serde_json::to_vec(&raw).map_err(|_|rejected("invalid JSON"))?.len()>MAX_CARD_BYTES {return Err(rejected("size limit exceeded"));}
        tokio::time::timeout(Duration::from_secs(10),async {
            let extensions = self.supported_extensions.iter().map(String::as_str).collect::<Vec<_>>();
            let bindings: &[&str] = if self.grpc.is_some() { &["GRPC", "HTTP+JSON", "JSONRPC", "CUSTOM"] } else { &["HTTP+JSON", "JSONRPC", "CUSTOM"] };
            let card = select_card_with_bindings(raw, &extensions, bearer_configured, bindings,
                |target| if target.protocol_binding == "CUSTOM" {
                    bcs_a2a_binding_api::registry().fingerprint(&target.url).is_ok()
                } else if target.protocol_binding == "GRPC" {
                    self.grpc.as_ref().is_some_and(|mesh|mesh.supports(&target.url))
                } else {bcs_service_api::types::a2a_bot::http_interface_address_allowed(&target.url)
                    && self.guard.validate_configured_http_url(&target.url).is_ok()})
                .map_err(|error| {
                    use crate::card::CardError;
                    let code=match error {CardError::TextRequired=>Code::CardContentUnsupported,
                        CardError::RequiredExtensionUnsupported(_)=>Code::CardExtensionUnsupported,
                        CardError::AuthenticationUnsupported=>Code::CardAuthUnsupported,
                        CardError::InterfaceUnsupported=>Code::CardInterfaceUnsupported,
                        CardError::AddressRejected=>Code::CardAddressRejected,CardError::InvalidShape=>Code::InvalidCard};
                    ServiceError::from(ManagementError::new(code,"agent_card","UNSUPPORTED_DECLARATION"))
                })?;
            // Includes DNS and TLS checks for the selected interface, without
            // submitting a task or forwarding credentials to the Card host.
            let binding = if card.interface.protocol_binding == "CUSTOM" { A2aBinding::Custom } else if card.interface.protocol_binding == "GRPC" { A2aBinding::Grpc }
                else { guarded_client(&self.guard, &card.interface.url).await?; if card.interface.protocol_binding=="JSONRPC" {A2aBinding::JsonRpc} else {A2aBinding::HttpJson} };
            let update_mode=if card.raw.pointer("/capabilities/streaming").and_then(serde_json::Value::as_bool)==Some(true) {A2aUpdateMode::Stream} else {A2aUpdateMode::Poll};
            Ok(ValidatedA2aCard { raw: card.raw, interface: A2aInterface {
                url: card.interface.url, tenant: card.interface.tenant,
                protocol_version: card.interface.protocol_version,
                binding, update_mode,
            } })
        }).await.map_err(|_|rejected("validation timeout"))?
    }

}
