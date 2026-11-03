//! A synthetic external channel, linked only into the test-utils executable.
//! It submits through the same public ingress sink as real channel plugins.
use std::sync::Arc;
use async_trait::async_trait;
use bcs_channel_api::*;
use bcs_service_api::{InboundMessage, ServiceResult};
use bcs_service_api::application::caller_execution::ExecutionToken;
use bcs_service_api::lifecycle::ServiceLifecycle;
use bcs_service_api::port::channel_delivery::{
    ChannelBindingRef, ChannelDeliveryPort, ChannelDeliveryResult, ChannelOutboundEvent,
};
use bcs_service_api::types::BindingTarget;
use serde::Deserialize;
use serde_json::{Value, json};

struct Provider { ingress_key: String }

inventory::submit! {
    bcs::plugins::ChannelProviderFactory {
        name: "caller-e2e",
        build: |ctx| {
            let ingress_key = ctx.provider_config.options.get("ingress_key")
                .and_then(Value::as_str).filter(|s| !s.trim().is_empty())
                .ok_or_else(|| bcs::BcsError::InvalidConfig("test ingress key required".into()))?;
            Ok(Arc::new(Provider { ingress_key: ingress_key.to_owned() }))
        },
    }
}

#[async_trait]
impl ChannelProvider for Provider {
    fn channel_type(&self) -> &'static str { "caller-e2e" }
    fn validate_config(&self, config: &Value) -> ChannelProviderResult<()> {
        match config.get("execution_mode").and_then(Value::as_str) {
            Some("default" | "caller") => Ok(()),
            _ => Err(ChannelProviderError::InvalidConfig("execution mode required".into())),
        }
    }
    fn validate_binding_config(&self, config: &Value, target: &BindingTarget) -> ChannelProviderResult<()> {
        self.validate_config(config)?;
        if self.requires_caller_execution(config)? && !matches!(target, BindingTarget::Bot { .. }) {
            return Err(ChannelProviderError::InvalidConfig("caller requires a Bot binding".into()));
        }
        Ok(())
    }
    fn requires_caller_execution(&self, config: &Value) -> ChannelProviderResult<bool> {
        self.validate_config(config)?;
        Ok(config["execution_mode"] == "caller")
    }
    fn redact_config(&self, config: &Value) -> Value { config.clone() }
    fn delivery(&self) -> Arc<dyn ChannelDeliveryPort> { Arc::new(Delivery) }
    fn http_ingress(&self) -> Option<Arc<dyn ChannelHttpIngressPort>> {
        Some(Arc::new(Provider { ingress_key: self.ingress_key.clone() }))
    }
    fn stream_lifecycle(&self, _: Arc<dyn ChannelInboundSink>) -> Option<Arc<dyn ServiceLifecycle>> { None }
}

// Intentionally no Debug: test ingress follows the same secret handling rules.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Callback {
    account: String,
    conversation: String,
    conversation_type: String,
    sender: String,
    message_id: String,
    text: String,
    #[serde(rename = "credential")]
    execution: Option<String>,
}

fn response(status: u16, accepted: bool) -> ChannelHttpResponse {
    let mut response = ChannelHttpResponse::json_ok(json!({"accepted": accepted}));
    response.status = status;
    response
}

#[async_trait]
impl ChannelHttpIngressPort for Provider {
    fn route_specs(&self) -> Vec<ChannelHttpRouteSpec> {
        vec![ChannelHttpRouteSpec { method: ChannelHttpMethod::Post, path: "/channels/caller-e2e/callback".into() }]
    }
    async fn handle_http(&self, request: ChannelHttpRequest, sink: Arc<dyn ChannelInboundSink>) -> ChannelHttpResponse {
        if !request.headers.iter().any(|(name, value)| name.eq_ignore_ascii_case("x-e2e-ingress-key") && value == &self.ingress_key) {
            return response(401, false);
        }
        let Ok(callback) = serde_json::from_slice::<Callback>(&request.body) else {
            return response(400, false);
        };
        let msg = InboundMessage {
            execution_token: callback.execution.map(|v| ExecutionToken::new(v, "iam-token".into())),
            channel_type: "caller-e2e".into(), account_ref: callback.account,
            im_conversation_id: callback.conversation, conversation_type: callback.conversation_type,
            im_user_id: callback.sender, im_user_nick: None, text: callback.text,
            attachments: None, is_at_bot: true, msg_id: callback.message_id,
        };
        match sink.submit(msg).await {
            Ok(()) => response(200, true),
            Err(_) => response(422, false),
        }
    }
}

struct Delivery;
#[async_trait]
impl ChannelDeliveryPort for Delivery {
    async fn is_available(&self, _: &ChannelBindingRef) -> bool { true }
    async fn deliver_event(&self, _: ChannelOutboundEvent) -> ServiceResult<ChannelDeliveryResult> {
        Ok(ChannelDeliveryResult { delivered: true, provider_message_ref: None, error: None })
    }
}
