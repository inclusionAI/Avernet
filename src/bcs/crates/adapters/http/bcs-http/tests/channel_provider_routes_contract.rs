use std::sync::Arc;

use async_trait::async_trait;
use axum::{
    body::{Body, Bytes, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_channel_api::{
    ChannelHttpIngressPort, ChannelHttpIngressRegistry, ChannelHttpMethod, ChannelHttpRequest,
    ChannelHttpResponse, ChannelHttpRouteSpec, ChannelInboundSink, ChannelIngressError,
    ChannelProvider, ChannelProviderRegistry, ChannelProviderResult,
};
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_service_api::{
    ServiceResult,
    application::InboundMessage,
    port::channel_delivery::{
        ChannelBindingRef, ChannelDeliveryPort, ChannelDeliveryResult, ChannelOutboundEvent,
    },
};
use bcs_services_container::Services;
use tokio::sync::Mutex;
use tower::ServiceExt;

fn static_auth_chain(staff_no: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(staff_no.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]))
}

#[tokio::test]
async fn provider_declared_http_route_is_mounted_and_dispatched() {
    let observed = Arc::new(Mutex::new(None));
    let provider = Arc::new(TestProvider {
        ingress: Arc::new(TestIngress {
            observed: observed.clone(),
        }),
    });
    let provider_registry = ChannelProviderRegistry::new(vec![provider.clone()])
        .expect("provider registry");
    let ingress = ChannelHttpIngressRegistry::new(
        provider_registry.providers(),
        Arc::new(NoopSink),
    )
    .expect("http ingress registry");
    let app = build_router(
        HttpAppState::new(Services::noop())
            .with_channel_http_ingress(Some(Arc::new(ingress))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/channels/test/callback?hello=world")
                .header("content-type", "text/plain")
                .header("x-test", "seen")
                .body(Body::from("ping"))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::ACCEPTED);
    assert_eq!(
        response.headers().get("x-provider").and_then(|value| value.to_str().ok()),
        Some("test")
    );
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    assert_eq!(&body[..], br#"{"ok":true}"#);

    let request = observed.lock().await.clone().expect("provider request");
    assert_eq!(request.method, ChannelHttpMethod::Post);
    assert_eq!(request.path, "/channels/test/callback");
    assert_eq!(request.query.as_deref(), Some("hello=world"));
    assert!(request.headers.iter().any(|(name, value)| {
        name == "x-test" && value == "seen"
    }));
    assert_eq!(&request.body[..], b"ping");
}

#[tokio::test]
async fn channel_binding_management_routes_require_human_identity() {
    let app = build_router(HttpAppState::new(Services::noop()));
    let requests = vec![
        Request::builder()
            .method("POST")
            .uri("/channels/bindings")
            .header("content-type", "application/json")
            .body(Body::from(
                r#"{
                    "channel_type": "dingtalk",
                    "account_ref": "robot_1",
                    "target": { "bot": { "bot_id": "bot_1" } },
                    "outbound_visibility": "full_transcript",
                    "env": "local",
                    "config": {}
                }"#,
            ))
            .unwrap(),
        Request::builder()
            .method("GET")
            .uri("/channels/bindings")
            .body(Body::empty())
            .unwrap(),
        Request::builder()
            .method("GET")
            .uri(
                "/channels/bindings/by-target?target_type=bot&target_id=bot_1&channel_type=dingtalk",
            )
            .body(Body::empty())
            .unwrap(),
        Request::builder()
            .method("PATCH")
            .uri("/channels/bindings/binding_1")
            .header("content-type", "application/json")
            .body(Body::from(r#"{"active":false}"#))
            .unwrap(),
        Request::builder()
            .method("DELETE")
            .uri("/channels/bindings/binding_1")
            .body(Body::empty())
            .unwrap(),
    ];

    for request in requests {
        let response = app.clone().oneshot(request).await.unwrap();

        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        let body: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(body["error"], "valid human identity is required");
    }
}

#[tokio::test]
async fn channel_binding_target_query_is_mounted_for_human_identity() {
    let chain = static_auth_chain("alice");
    let app = build_router(
        HttpAppState::new(Services::noop())
            .with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(
                    "/channels/bindings/by-target?target_type=group&target_id=group_1&channel_type=dingtalk",
                )
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let body: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(body, serde_json::json!({ "items": [] }));
}

#[derive(Clone)]
struct ObservedRequest {
    method: ChannelHttpMethod,
    path: String,
    query: Option<String>,
    headers: Vec<(String, String)>,
    body: Bytes,
}

struct NoopSink;

#[async_trait]
impl ChannelInboundSink for NoopSink {
    async fn submit(&self, _msg: InboundMessage) -> Result<(), ChannelIngressError> {
        Ok(())
    }
}

struct TestProvider {
    ingress: Arc<TestIngress>,
}

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
        Arc::new(NoopDelivery)
    }

    fn http_ingress(&self) -> Option<Arc<dyn ChannelHttpIngressPort>> {
        Some(self.ingress.clone())
    }

    fn stream_lifecycle(
        &self,
        _sink: Arc<dyn ChannelInboundSink>,
    ) -> Option<Arc<dyn bcs_service_api::lifecycle::ServiceLifecycle>> {
        None
    }
}

struct TestIngress {
    observed: Arc<Mutex<Option<ObservedRequest>>>,
}

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
        request: ChannelHttpRequest,
        _sink: Arc<dyn ChannelInboundSink>,
    ) -> ChannelHttpResponse {
        *self.observed.lock().await = Some(ObservedRequest {
            method: request.method,
            path: request.path,
            query: request.query,
            headers: request.headers,
            body: request.body,
        });
        ChannelHttpResponse {
            status: StatusCode::ACCEPTED.as_u16(),
            headers: vec![("x-provider".to_string(), "test".to_string())],
            body: Bytes::from_static(br#"{"ok":true}"#),
        }
    }
}

struct NoopDelivery;

#[async_trait]
impl ChannelDeliveryPort for NoopDelivery {
    async fn is_available(&self, _binding: &ChannelBindingRef) -> bool {
        true
    }

    async fn deliver_event(
        &self,
        _event: ChannelOutboundEvent,
    ) -> ServiceResult<ChannelDeliveryResult> {
        Ok(ChannelDeliveryResult {
            delivered: true,
            error: None,
        })
    }
}
