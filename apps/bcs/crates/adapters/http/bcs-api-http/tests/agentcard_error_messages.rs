//! Real HTTP serialization through the V1 management facade, for every operation and error code.
use async_trait::async_trait;
use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_app_a2a_bot::v1::A2aApiService;
use bcs_service_api::{
    ServiceResult,
    application::{a2a_bot::*, v1::*},
    types::a2a_bot::{
        A2aBotRecord,
        a2a_management::{ManagementCode as Code, ManagementError},
    },
};
use serde_json::{Value, json};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};
use tower::ServiceExt;
#[allow(dead_code)]
mod register_support;

struct Unused;
#[async_trait]
impl RegisterService for Unused {
    async fn issue_register_token(
        &self,
        _: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        unreachable!()
    }
    async fn register_bot(&self, _: RegisterBot) -> Result<BotRegistration, ApplicationError> {
        unreachable!()
    }
}
struct Management {
    error: ManagementError,
    calls: AtomicUsize,
}
impl Management {
    fn reject<T>(&self) -> ServiceResult<T> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Err(self.error.clone().into())
    }
}
#[async_trait]
impl A2aBotManagementService for Management {
    async fn register_agent(&self, _: RegisterAgentCommand) -> ServiceResult<ManagedA2aCard> {
        self.reject()
    }
    async fn find_agents_by_provider_ref(
        &self,
        _: &str,
        _: &str,
    ) -> ServiceResult<Vec<ManagedA2aCard>> {
        self.reject()
    }
    async fn get_agent_by_id(&self, _: &str, _: &str) -> ServiceResult<ManagedA2aCard> {
        self.reject()
    }
    async fn update_agent(&self, _: UpdateAgentCommand) -> ServiceResult<ManagedA2aCard> {
        self.reject()
    }
    async fn refresh_agent_by_id(&self, _: &str, _: &str) -> ServiceResult<ManagedA2aCard> {
        self.reject()
    }
    async fn register_named(
        &self,
        _: RegisterNamedA2aBotCommand,
    ) -> ServiceResult<NamedA2aRegistration> {
        self.reject()
    }
    async fn get(&self, _: &str, _: &str, _: &str) -> ServiceResult<Option<A2aBotRecord>> {
        unreachable!()
    }
    async fn configure(&self, _: ConfigureA2aBotCommand) -> ServiceResult<A2aBotRecord> {
        unreachable!()
    }
    async fn refresh(&self, _: &str, _: &str, _: &str, _: i64) -> ServiceResult<A2aBotRecord> {
        unreachable!()
    }
    async fn refresh_due(&self, _: i64) -> ServiceResult<usize> {
        unreachable!()
    }
}
fn app(error: ManagementError) -> (axum::Router, Arc<Management>) {
    let management = Arc::new(Management {
        error,
        calls: AtomicUsize::new(0),
    });
    let app = bcs_api_http::router(
        register_support::test_state(Arc::new(Unused))
            .with_a2a_service(Arc::new(A2aApiService::new(management.clone()))),
    );
    (app, management)
}
async fn call(app: axum::Router, method: &str, suffix: &str, body: Value) -> (StatusCode, Value) {
    let request = Request::builder()
        .method(method)
        .uri(format!("/api/v1/collaboration/a2a{suffix}"))
        .header("content-type", "application/json")
        .header("authorization", "Bearer private-credential")
        .header("x-request-id", "request-card-error")
        .body(Body::from(if body.is_null() {
            String::new()
        } else {
            body.to_string()
        }))
        .unwrap();
    let response = app.oneshot(request).await.unwrap();
    assert_eq!(response.headers()["x-request-id"], "request-card-error");
    let status = response.status();
    let body =
        serde_json::from_slice(&to_bytes(response.into_body(), 16_384).await.unwrap()).unwrap();
    (status, body)
}
fn operations() -> [(&'static str, &'static str, Value); 6] {
    [
        (
            "POST",
            "/agents",
            json!({"provider_bot_ref":"demo","agent_card":{}}),
        ),
        ("GET", "/agents?provider_bot_ref=demo", Value::Null),
        ("GET", "/agents/bot/agent-card", Value::Null),
        ("PUT", "/agents/bot/agent-card", json!({"agent_card":{}})),
        ("POST", "/agents/bot/agent-card:refresh", json!({})),
        (
            "POST",
            "/providers/demo/bots",
            json!({"provider_bot_ref":"demo","owners":["owner"],"agent_card":{}}),
        ),
    ]
}

#[tokio::test]
async fn all_management_error_codes_are_safe_actionable_and_keep_status_on_all_six_operations() {
    let cases = [
        (Code::InvalidRequest, 400),
        (Code::Unauthenticated, 401),
        (Code::PermissionDenied, 403),
        (Code::BotNotFound, 404),
        (Code::MethodNotAllowed, 422),
        (Code::NotAcceptable, 422),
        (Code::ProviderBotRefConflict, 409),
        (Code::BotNameConflict, 409),
        (Code::SourceAgentConflict, 409),
        (Code::SourceAgentChangeNotAllowed, 409),
        (Code::TargetChanged, 409),
        (Code::CardUrlRequired, 409),
        (Code::RefreshSuperseded, 409),
        (Code::RequestTooLarge, 413),
        (Code::UnsupportedMediaType, 422),
        (Code::InvalidOwner, 422),
        (Code::InvalidCard, 422),
        (Code::CardSourceInvalid, 422),
        (Code::CardTooLarge, 413),
        (Code::CardAddressRejected, 422),
        (Code::CardInterfaceUnsupported, 422),
        (Code::CardContentUnsupported, 422),
        (Code::CardAuthUnsupported, 422),
        (Code::CardExtensionUnsupported, 422),
        (Code::RateLimited, 429),
        (Code::CardFetchFailed, 502),
        (Code::CardFetchTimeout, 502),
        (Code::ServiceUnavailable, 503),
        (Code::InternalError, 500),
    ];
    for (code, status) in cases {
        let (router, management) = app(ManagementError::new(
            code,
            "private-field",
            "PRIVATE_REASON",
        ));
        for (method, path, request) in operations() {
            let (actual, body) = call(router.clone(), method, path, request).await;
            assert_eq!(actual.as_u16(), status, "{code:?} {method} {path}");
            let expected = match code {
                Code::Unauthenticated => json!("unauthenticated"),
                Code::InternalError => json!("internal_error"),
                _ => serde_json::to_value(code).unwrap(),
            };
            assert_eq!(body["code"], status * 100);
            assert_eq!(body["data"]["error_code"], expected);
            assert_eq!(body["request_id"], "request-card-error");
            let message = body["message"].as_str().unwrap();
            assert!(!message.contains("A2A management request failed"), "{body}");
            assert!(message.len() >= 16, "{body}");
            let wire = body.to_string();
            for private in ["private-field", "PRIVATE_REASON", "private-credential"] {
                assert!(!wire.contains(private), "{body}");
            }
            assert_eq!(body.as_object().unwrap().len(), 4);
        }
        assert_eq!(management.calls.load(Ordering::SeqCst), 6);
    }
}

#[tokio::test]
async fn known_card_reasons_reach_the_wire_and_unknown_diagnostics_remain_private() {
    for (code, field, reason, expected) in [
        (
            Code::InvalidCard,
            "agent_card.supportedInterfaces[2].url",
            "ABSOLUTE_URL_REQUIRED",
            "agent_card.supportedInterfaces[2].url: a valid absolute URL is required",
        ),
        (
            Code::InvalidRequest,
            "provider_bot_ref",
            "INVALID_PROVIDER_BOT_REF",
            "provider_bot_ref: use 1 to 128 ASCII letters",
        ),
        (
            Code::InvalidOwner,
            "owners",
            "ONE_OWNER_REQUIRED",
            "owners: exactly one valid owner is required",
        ),
        (
            Code::CardSourceInvalid,
            "agent_card_url",
            "INVALID_URL",
            "agent_card_url: use an absolute HTTP(S) URL",
        ),
        (
            Code::PermissionDenied,
            "private-field",
            "PROVIDER_DISABLED",
            "Provider is disabled",
        ),
        (
            Code::InvalidRequest,
            "catalog_labels.private-credential[0]",
            "UNKNOWN_VALUE",
            "catalog_labels: a label value is not allowed",
        ),
        (
            Code::InternalError,
            "private-field",
            "PRIVATE_REASON",
            "contact support with request_id for diagnosis",
        ),
    ] {
        let (router, _) = app(ManagementError::new(code, field, reason));
        for (method, path, request) in operations() {
            let (_, body) = call(router.clone(), method, path, request).await;
            assert!(
                body["message"].as_str().unwrap().contains(expected),
                "{body}"
            );
            assert!(
                !body.to_string().contains("private-field")
                    && !body.to_string().contains("PRIVATE_REASON")
                    && !body.to_string().contains("private-credential")
            );
        }
    }
}

#[tokio::test]
async fn protocol_rejections_explain_how_to_correct_the_request_without_dispatch() {
    let (router, management) = app(ManagementError::new(
        Code::InvalidCard,
        "private-field",
        "PRIVATE_REASON",
    ));
    for (method, accept, body, status, expected) in [
        (
            "DELETE",
            "application/json",
            String::new(),
            405,
            "use POST to register or refresh",
        ),
        (
            "POST",
            "text/html",
            String::new(),
            406,
            "Accept must allow application/json",
        ),
        (
            "POST",
            "application/json",
            "x".repeat(2 * 1024 * 1024 + 1),
            413,
            "2 MiB size limit",
        ),
    ] {
        let response = router
            .clone()
            .oneshot(
                Request::builder()
                    .method(method)
                    .uri("/api/v1/collaboration/a2a/agents")
                    .header("content-type", "application/json")
                    .header("authorization", "Bearer private-credential")
                    .header("accept", accept)
                    .header("x-request-id", "request-protocol")
                    .body(Body::from(body))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status().as_u16(), status);
        let body: Value =
            serde_json::from_slice(&to_bytes(response.into_body(), 16_384).await.unwrap()).unwrap();
        assert!(
            body["message"].as_str().unwrap().contains(expected),
            "{body}"
        );
        assert_eq!(body["request_id"], "request-protocol");
    }
    assert_eq!(management.calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn missing_provider_credentials_explain_the_required_authentication_before_dispatch() {
    let (router, management) = app(ManagementError::new(
        Code::InvalidCard,
        "private-field",
        "PRIVATE_REASON",
    ));
    for (method, path, body) in operations() {
        let request = Request::builder()
            .method(method)
            .uri(format!("/api/v1/collaboration/a2a{path}"))
            .header("content-type", "application/json")
            .header("x-request-id", "request-auth")
            .body(Body::from(if body.is_null() {
                String::new()
            } else {
                body.to_string()
            }))
            .unwrap();
        let response = router.clone().oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(response.headers()["www-authenticate"], "Bearer");
        let body: Value =
            serde_json::from_slice(&to_bytes(response.into_body(), 16_384).await.unwrap()).unwrap();
        assert_eq!(body["data"]["error_code"], "unauthenticated");
        assert!(
            body["message"]
                .as_str()
                .unwrap()
                .contains("Provider-admin token"),
            "{body}"
        );
    }
    assert_eq!(management.calls.load(Ordering::SeqCst), 0);
}
