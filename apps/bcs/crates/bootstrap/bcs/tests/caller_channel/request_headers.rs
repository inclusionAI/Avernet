//! Synthetic deployment policy, linked only into the live-process fixture.
use std::sync::Arc;
use bcs_domain::RedactedToken;
use bcs_service_api::port::provider_request_headers::{ProviderRequestHeaderContext, ProviderRequestHeaderPolicy};

struct Policy;

impl ProviderRequestHeaderPolicy for Policy {
    fn headers(&self, context: &ProviderRequestHeaderContext<'_>) -> Vec<(String, RedactedToken)> {
        if context.method != "chat.send" || context.provider_bot_ref != "caller-bot" {
            return Vec::new();
        }
        let Some(credential) = context.extensions.and_then(|ext| ext.get("caller_execution_token")) else {
            return Vec::new();
        };
        let Some(token) = credential.get("token").and_then(|value| value.as_str()) else {
            return Vec::new();
        };
        vec![("x-e2e-caller".into(), RedactedToken::new(token))]
    }
}

pub fn install() -> anyhow::Result<()> {
    bcs_provider_http::install_provider_request_header_policy(Arc::new(Policy))
        .map_err(anyhow::Error::msg)
}
