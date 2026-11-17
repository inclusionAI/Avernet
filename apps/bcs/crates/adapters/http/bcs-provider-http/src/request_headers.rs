//! Runtime-only header policy selected by the hosting composition root.
use std::sync::{Arc, OnceLock};
use bcs_protocol::ProviderWebhookRequest;
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::port::provider_request_headers::{ProviderRequestHeaderContext, ProviderRequestHeaderPolicy};

static POLICY: OnceLock<Arc<dyn ProviderRequestHeaderPolicy>> = OnceLock::new();

/// Install once before serving. Unconfigured public runtimes are unchanged.
pub fn install_provider_request_header_policy(policy: Arc<dyn ProviderRequestHeaderPolicy>) -> Result<(), &'static str> {
    POLICY.set(policy).map_err(|_| "provider request header policy already installed")
}

pub(super) fn apply(request: reqwest::RequestBuilder, body: &ProviderWebhookRequest) -> ServiceResult<reqwest::RequestBuilder> {
    let Some(policy) = POLICY.get() else { return Ok(request); };
    let context = ProviderRequestHeaderContext {
        method: &body.method, provider_id: &body.to_bot.provider_id,
        provider_bot_ref: &body.to_bot.provider_bot_ref, extensions: body.extensions.as_ref(),
    };
    let invalid = || ServiceError::DeliveryNotSent { code: "provider_request_build_failed", retryable: false };
    let mut headers = reqwest::header::HeaderMap::new();
    for (name, value) in policy.headers(&context) {
        if !super::network::is_safe_provider_bypass_header(&name) { return Err(invalid()); }
        let name = reqwest::header::HeaderName::from_bytes(name.as_bytes()).map_err(|_| invalid())?;
        let mut value = reqwest::header::HeaderValue::from_str(value.expose_secret()).map_err(|_| invalid())?;
        value.set_sensitive(true);
        headers.insert(name, value);
    }
    Ok(request.headers(headers))
}
