//! Test-only IM transport registered through the production plugin boundary.
//! BCS owns notification rendering, queueing, persistence and reply routing.

use super::*;
use async_trait::async_trait;
use bcs_channel_api::{ChannelProvider, ChannelProviderResult};
use bcs_service_api::port::channel_delivery::{ChannelBindingRef, ChannelDeliveryPort, ChannelDeliveryResult, ChannelOutboundEvent};

struct HttpChannel {
    endpoint: String,
    client: reqwest::Client,
}

#[async_trait]
impl ChannelDeliveryPort for HttpChannel {
    async fn is_available(&self, _: &ChannelBindingRef) -> bool { true }

    async fn deliver_event(&self, event: ChannelOutboundEvent) -> bcs_service_api::ServiceResult<ChannelDeliveryResult> {
        let result = self.client.post(&self.endpoint).json(&json!({
            "purpose": format!("{:?}", event.purpose),
            "recipient": event.im_user_id,
            "conversation_type": event.im_conversation_type,
            "text": event.text,
            "payload": event.raw_payload,
        })).send().await.map_err(|error| bcs_service_api::ServiceError::InternalError(error.to_string()))?;
        let result = result.error_for_status().map_err(|error| bcs_service_api::ServiceError::InternalError(error.to_string()))?;
        let body: Value = result.json().await.map_err(|error| bcs_service_api::ServiceError::InternalError(error.to_string()))?;
        Ok(ChannelDeliveryResult { delivered: true,
            provider_message_ref: body["message_ref"].as_str().map(str::to_owned), error: None })
    }
}

struct Provider(Arc<HttpChannel>);

#[async_trait]
impl ChannelProvider for Provider {
    fn channel_type(&self) -> &'static str { "loop-live-im" }
    fn validate_config(&self, _: &Value) -> ChannelProviderResult<()> { Ok(()) }
    fn redact_config(&self, config: &Value) -> Value { config.clone() }
    fn resolve_direct_recipient(&self, actor: &str) -> ChannelProviderResult<Option<String>> {
        Ok((actor == "human_11111111").then(|| "local-reviewer".into()))
    }
    fn delivery(&self) -> Arc<dyn ChannelDeliveryPort> { self.0.clone() }
    fn http_ingress(&self) -> Option<Arc<dyn bcs_channel_api::ChannelHttpIngressPort>> { None }
    fn stream_lifecycle(&self, _: Arc<dyn bcs_channel_api::ChannelInboundSink>) -> Option<Arc<dyn bcs_service_api::lifecycle::ServiceLifecycle>> { None }
}

inventory::submit! {
    bcs::plugins::ChannelProviderFactory {
        name: "loop-live-im",
        build: |context| Ok(Arc::new(Provider(Arc::new(HttpChannel {
            endpoint: context.provider_config.options["endpoint"].as_str().expect("test notification endpoint").into(),
            client: reqwest::Client::builder().no_proxy().timeout(Duration::from_secs(5)).build().unwrap(),
        })))),
    }
}
