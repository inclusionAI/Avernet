//! Channel provider plugin contract.
//!
//! Public BCS owns channel binding orchestration and HTTP hosting. Providers
//! own protocol-specific config validation, redaction, ingress parsing, stream
//! lifecycles, and outbound delivery.

use std::collections::BTreeMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::{ChannelInboundError, InboundMessage};
use bcs_service_api::lifecycle::ServiceLifecycle;
use bcs_service_api::port::channel_delivery::ChannelDeliveryPort;
use bytes::Bytes;
use thiserror::Error;

/// Provider-facing result type.
pub type ChannelProviderResult<T> = Result<T, ChannelProviderError>;

/// Compatibility alias for plugins compiled against the previous ingress error name.
pub type ChannelIngressError = ChannelInboundError;

/// Provider contract failures.
#[derive(Debug, Error)]
pub enum ChannelProviderError {
    #[error("invalid channel provider config: {0}")]
    InvalidConfig(String),
    #[error("channel provider error: {0}")]
    Provider(String),
}

/// Registered channel provider.
#[async_trait]
pub trait ChannelProvider: Send + Sync {
    fn channel_type(&self) -> &'static str;

    fn validate_config(&self, config: &serde_json::Value) -> ChannelProviderResult<()>;

    fn redact_config(&self, config: &serde_json::Value) -> serde_json::Value;

    /// Resolve a BCS Human actor into a provider-native direct-message recipient.
    ///
    /// Providers that do not define a stable actor-to-recipient convention may
    /// return `None`; direct HumanInput delivery will then be unavailable.
    fn resolve_direct_recipient(
        &self,
        _actor_id: &str,
    ) -> ChannelProviderResult<Option<String>> {
        Ok(None)
    }

    fn delivery(&self) -> Arc<dyn ChannelDeliveryPort>;

    fn http_ingress(&self) -> Option<Arc<dyn ChannelHttpIngressPort>>;

    fn stream_lifecycle(
        &self,
        sink: Arc<dyn ChannelInboundSink>,
    ) -> Option<Arc<dyn ServiceLifecycle>>;
}

/// Provider-declared HTTP method.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ChannelHttpMethod {
    Get,
    Post,
    Put,
    Patch,
    Delete,
}

/// Provider-declared host route.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ChannelHttpRouteSpec {
    pub method: ChannelHttpMethod,
    pub path: String,
}

/// Header list preserved from the host request.
pub type ChannelHeaders = Vec<(String, String)>;

/// Neutral HTTP request passed from the host to a provider handler.
#[derive(Debug, Clone)]
pub struct ChannelHttpRequest {
    pub method: ChannelHttpMethod,
    pub path: String,
    pub query: Option<String>,
    pub headers: ChannelHeaders,
    pub body: Bytes,
}

/// Neutral HTTP response returned by a provider handler.
#[derive(Debug, Clone)]
pub struct ChannelHttpResponse {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub body: Bytes,
}

impl ChannelHttpResponse {
    pub fn json_ok(body: serde_json::Value) -> Self {
        Self {
            status: 200,
            headers: vec![("content-type".to_string(), "application/json".to_string())],
            body: Bytes::from(body.to_string()),
        }
    }
}

/// Provider-owned HTTP ingress handler.
#[async_trait]
pub trait ChannelHttpIngressPort: Send + Sync {
    fn route_specs(&self) -> Vec<ChannelHttpRouteSpec>;

    async fn handle_http(
        &self,
        request: ChannelHttpRequest,
        sink: Arc<dyn ChannelInboundSink>,
    ) -> ChannelHttpResponse;
}

/// BCS-owned sink used by providers to submit normalized inbound messages.
#[async_trait]
pub trait ChannelInboundSink: Send + Sync {
    async fn submit(&self, msg: InboundMessage) -> Result<(), ChannelIngressError>;
}

/// Provider HTTP route dispatcher built by the host from enabled providers.
#[derive(Clone)]
pub struct ChannelHttpIngressRegistry {
    routes: BTreeMap<ChannelHttpRouteSpec, Arc<dyn ChannelHttpIngressPort>>,
    sink: Arc<dyn ChannelInboundSink>,
}

impl ChannelHttpIngressRegistry {
    pub fn new(
        providers: Vec<Arc<dyn ChannelProvider>>,
        sink: Arc<dyn ChannelInboundSink>,
    ) -> ChannelProviderResult<Self> {
        let mut routes = BTreeMap::new();
        for provider in providers {
            let Some(ingress) = provider.http_ingress() else {
                continue;
            };
            for spec in ingress.route_specs() {
                if spec.path.trim().is_empty() {
                    return Err(ChannelProviderError::InvalidConfig(format!(
                        "channel provider '{}' declared an empty HTTP route",
                        provider.channel_type()
                    )));
                }
                if routes.insert(spec.clone(), ingress.clone()).is_some() {
                    return Err(ChannelProviderError::InvalidConfig(format!(
                        "duplicate channel HTTP route {:?} {}",
                        spec.method, spec.path
                    )));
                }
            }
        }
        Ok(Self { routes, sink })
    }

    pub fn route_specs(&self) -> Vec<ChannelHttpRouteSpec> {
        self.routes.keys().cloned().collect()
    }

    pub async fn handle_http(
        &self,
        request: ChannelHttpRequest,
    ) -> Option<ChannelHttpResponse> {
        let spec = ChannelHttpRouteSpec {
            method: request.method,
            path: request.path.clone(),
        };
        let ingress = self.routes.get(&spec)?;
        Some(ingress.handle_http(request, self.sink.clone()).await)
    }
}

/// Runtime registry of enabled channel providers.
#[derive(Default, Clone)]
pub struct ChannelProviderRegistry {
    providers: BTreeMap<String, Arc<dyn ChannelProvider>>,
}

impl ChannelProviderRegistry {
    pub fn new(providers: Vec<Arc<dyn ChannelProvider>>) -> ChannelProviderResult<Self> {
        let mut by_name = BTreeMap::new();
        for provider in providers {
            let channel_type = provider.channel_type().trim();
            if channel_type.is_empty() {
                return Err(ChannelProviderError::InvalidConfig(
                    "channel provider type is empty".to_string(),
                ));
            }
            if by_name
                .insert(channel_type.to_string(), provider)
                .is_some()
            {
                return Err(ChannelProviderError::InvalidConfig(format!(
                    "duplicate channel provider '{channel_type}'"
                )));
            }
        }
        Ok(Self { providers: by_name })
    }

    pub fn empty() -> Self {
        Self {
            providers: BTreeMap::new(),
        }
    }

    pub fn get(&self, channel_type: &str) -> Option<Arc<dyn ChannelProvider>> {
        self.providers.get(channel_type).cloned()
    }

    pub fn providers(&self) -> Vec<Arc<dyn ChannelProvider>> {
        self.providers.values().cloned().collect()
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use async_trait::async_trait;
    use bcs_service_api::application::ChannelInboundFailureKind;
    use bcs_service_api::port::channel_delivery::{
        ChannelBindingRef, ChannelDeliveryPort, ChannelDeliveryResult, ChannelOutboundEvent,
    };
    use bcs_service_api::ServiceResult;
    use bytes::Bytes;

    use super::*;

    struct TestDelivery;

    #[async_trait]
    impl ChannelDeliveryPort for TestDelivery {
        async fn is_available(&self, _binding: &ChannelBindingRef) -> bool {
            true
        }

        async fn deliver_event(
            &self,
            _event: ChannelOutboundEvent,
        ) -> ServiceResult<ChannelDeliveryResult> {
            Ok(ChannelDeliveryResult {
                delivered: true,
                provider_message_ref: None,
                error: None,
            })
        }
    }

    struct TestIngress;

    #[async_trait]
    impl ChannelHttpIngressPort for TestIngress {
        fn route_specs(&self) -> Vec<ChannelHttpRouteSpec> {
            vec![ChannelHttpRouteSpec {
                method: ChannelHttpMethod::Post,
                path: "/channels/test/callback".to_string(),
            }]
        }

        async fn handle_http(
            &self,
            _request: ChannelHttpRequest,
            _sink: Arc<dyn ChannelInboundSink>,
        ) -> ChannelHttpResponse {
            ChannelHttpResponse {
                status: 200,
                headers: Vec::new(),
                body: Bytes::new(),
            }
        }
    }

    struct NoopSink;

    #[async_trait]
    impl ChannelInboundSink for NoopSink {
        async fn submit(&self, _msg: InboundMessage) -> Result<(), ChannelIngressError> {
            Ok(())
        }
    }

    #[test]
    #[allow(deprecated)]
    fn legacy_ingress_error_constructors_map_to_typed_contract() {
        let invalid = ChannelIngressError::InvalidMessage("missing text".to_string());
        let service = ChannelIngressError::Service("dispatch unavailable".to_string());

        assert_eq!(invalid.kind, ChannelInboundFailureKind::InvalidInbound);
        assert!(!invalid.retryable);
        assert_eq!(service.kind, ChannelInboundFailureKind::Internal);
        assert!(service.retryable);
    }

    struct TestProvider;

    #[async_trait]
    impl ChannelProvider for TestProvider {
        fn channel_type(&self) -> &'static str {
            "test"
        }

        fn validate_config(&self, _config: &serde_json::Value) -> ChannelProviderResult<()> {
            Ok(())
        }

        fn redact_config(&self, config: &serde_json::Value) -> serde_json::Value {
            config.clone()
        }

        fn delivery(&self) -> Arc<dyn ChannelDeliveryPort> {
            Arc::new(TestDelivery)
        }

        fn http_ingress(&self) -> Option<Arc<dyn ChannelHttpIngressPort>> {
            Some(Arc::new(TestIngress))
        }

        fn stream_lifecycle(
            &self,
            _sink: Arc<dyn ChannelInboundSink>,
        ) -> Option<Arc<dyn ServiceLifecycle>> {
            None
        }
    }

    struct DuplicateRouteProvider;

    #[async_trait]
    impl ChannelProvider for DuplicateRouteProvider {
        fn channel_type(&self) -> &'static str {
            "duplicate"
        }

        fn validate_config(&self, _config: &serde_json::Value) -> ChannelProviderResult<()> {
            Ok(())
        }

        fn redact_config(&self, config: &serde_json::Value) -> serde_json::Value {
            config.clone()
        }

        fn delivery(&self) -> Arc<dyn ChannelDeliveryPort> {
            Arc::new(TestDelivery)
        }

        fn http_ingress(&self) -> Option<Arc<dyn ChannelHttpIngressPort>> {
            Some(Arc::new(TestIngress))
        }

        fn stream_lifecycle(
            &self,
            _sink: Arc<dyn ChannelInboundSink>,
        ) -> Option<Arc<dyn ServiceLifecycle>> {
            None
        }
    }

    #[test]
    fn registry_returns_provider_by_name() {
        let registry = ChannelProviderRegistry::new(vec![Arc::new(TestProvider)]).unwrap();

        assert!(registry.get("test").is_some());
        assert!(registry.get("missing").is_none());
        assert_eq!(
            registry
                .get("test")
                .expect("provider")
                .resolve_direct_recipient("human_user-1")
                .expect("default recipient resolution"),
            None
        );
    }

    #[test]
    fn registry_preserves_provider_route_specs() {
        let registry = ChannelProviderRegistry::new(vec![Arc::new(TestProvider)]).unwrap();
        let provider = registry.get("test").expect("provider exists");
        let ingress = provider.http_ingress().expect("ingress exists");
        let specs = ingress.route_specs();

        assert_eq!(specs.len(), 1);
        assert_eq!(specs[0].method, ChannelHttpMethod::Post);
        assert_eq!(specs[0].path, "/channels/test/callback");
    }

    #[test]
    fn registry_rejects_duplicate_provider_names() {
        let error = match ChannelProviderRegistry::new(vec![
            Arc::new(TestProvider),
            Arc::new(TestProvider),
        ]) {
            Ok(_) => panic!("duplicate provider should fail"),
            Err(error) => error,
        };

        assert!(error.to_string().contains("duplicate channel provider 'test'"));
    }

    #[test]
    fn http_ingress_registry_rejects_duplicate_routes() {
        let error = match ChannelHttpIngressRegistry::new(
            vec![Arc::new(TestProvider), Arc::new(DuplicateRouteProvider)],
            Arc::new(NoopSink),
        ) {
            Ok(_) => panic!("duplicate route should fail"),
            Err(error) => error,
        };

        assert!(error.to_string().contains("duplicate channel HTTP route"));
    }

    #[tokio::test]
    async fn http_ingress_registry_dispatches_registered_route() {
        let registry = ChannelHttpIngressRegistry::new(
            vec![Arc::new(TestProvider)],
            Arc::new(NoopSink),
        )
        .expect("registry");

        let response = registry
            .handle_http(ChannelHttpRequest {
                method: ChannelHttpMethod::Post,
                path: "/channels/test/callback".to_string(),
                query: None,
                headers: Vec::new(),
                body: Bytes::from("hello"),
            })
            .await
            .expect("registered route");

        assert_eq!(response.status, 200);
    }
}
