use async_trait::async_trait;
use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_service_api::application::v1::{a2a::*, *};
use serde_json::{Value, json};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};
use tower::ServiceExt;
#[allow(dead_code)]
mod register_support;

struct UnusedRegister;
#[async_trait]
impl RegisterService for UnusedRegister {
    async fn issue_register_token(
        &self,
        _: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        panic!("wrong registration use case")
    }
    async fn register_bot(&self, _: RegisterBot) -> Result<BotRegistration, ApplicationError> {
        panic!("wrong registration use case")
    }
}
#[derive(Default)]
struct Service {
    calls: AtomicUsize,
}
fn card() -> ManagedA2aCard {
    ManagedA2aCard {
        provider_bot_ref: "remote".into(),
        provider_name: "orders".into(),
        agent_name: "Agent".into(),
        bot_id: "bot/opaque".into(),
        agent_card_url: None,
        agent_card: json!({"name":"Agent","unknown":{"keep":true}}),
    }
}
impl Service {
    fn authenticate(&self, token: &str) -> Result<(), ApplicationError> {
        if token != "admin" {
            return Err(ApplicationError::forbidden("Provider access denied"));
        }
        self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}
#[async_trait]
impl A2aService for Service {
    async fn register(
        &self,
        command: RegisterA2aAgent,
    ) -> Result<ManagedA2aCard, ApplicationError> {
        self.authenticate(&command.token)?;
        assert_eq!(command.provider_bot_ref, "remote");
        assert!(command.owners.is_none());
        assert_eq!(command.visibility, A2aRegistrationVisibility::Private);
        assert_eq!(
            command.source.agent_card,
            Some(json!({"name":"Agent","unknown":{"keep":true}}))
        );
        Ok(card())
    }
    async fn find(
        &self,
        token: &str,
        reference: &str,
    ) -> Result<Vec<ManagedA2aCard>, ApplicationError> {
        self.authenticate(token)?;
        assert_eq!(reference, "remote");
        Ok(vec![card()])
    }
    async fn get(&self, token: &str, bot: &str) -> Result<ManagedA2aCard, ApplicationError> {
        self.authenticate(token)?;
        assert_eq!(bot, "bot/opaque");
        Ok(card())
    }
    async fn update(&self, command: UpdateA2aAgent) -> Result<ManagedA2aCard, ApplicationError> {
        self.authenticate(&command.token)?;
        assert_eq!(command.bot_id, "bot/opaque");
        assert_eq!(command.visibility, Some(A2aRegistrationVisibility::Public));
        assert!(command.source.agent_card.is_none());
        Ok(card())
    }
    async fn refresh(&self, token: &str, bot: &str) -> Result<ManagedA2aCard, ApplicationError> {
        self.authenticate(token)?;
        assert_eq!(bot, "bot/opaque");
        Ok(card())
    }
    async fn register_named(
        &self,
        command: RegisterNamedA2aAgent,
    ) -> Result<NamedA2aRegistration, ApplicationError> {
        self.authenticate(&command.token)?;
        assert_eq!(command.provider_name, "orders");
        assert_eq!(command.provider_bot_ref, "remote");
        Ok(NamedA2aRegistration {
            card: bcs_service_api::application::a2a_bot::NamedA2aCard {
                bot_id: "bot/opaque".into(),
                provider_bot_ref: "remote".into(),
                provider_name: "orders".into(),
                bot_name: "Agent".into(),
                agent_card_url: None,
                agent_card: card().agent_card,
            },
            bot_runtime_token: Some("runtime-test".into()),
        })
    }
}

fn app(service: Arc<Service>) -> axum::Router {
    bcs_api_http::router(
        register_support::test_state(Arc::new(UnusedRegister))
            .with_invite_code_gate_enabled(true)
            .with_a2a_service(service),
    )
}
fn request(method: &str, suffix: &str, bearer: Option<&str>, body: Value) -> Request<Body> {
    let mut request = Request::builder()
        .method(method)
        .uri(format!("/api/v1/collaboration/a2a{suffix}"))
        .header("content-type", "application/json")
        .header("x-request-id", "a2a-request");
    if let Some(token) = bearer {
        request = request.header("authorization", format!("Bearer {token}"));
    }
    request
        .body(if body.is_null() {
            Body::empty()
        } else {
            Body::from(body.to_string())
        })
        .unwrap()
}
async fn read(response: axum::response::Response, status: StatusCode) -> Value {
    assert_eq!(response.status(), status);
    assert_eq!(response.headers()["cache-control"], "no-store");
    assert_eq!(response.headers()["x-request-id"], "a2a-request");
    let value: Value = serde_json::from_slice(
        &to_bytes(response.into_body(), 2 * 1024 * 1024)
            .await
            .unwrap(),
    )
    .unwrap();
    assert_eq!(value["request_id"], "a2a-request");
    assert_eq!(value["code"], u32::from(status.as_u16()) * 100);
    assert_eq!(value.as_object().unwrap().len(), 4);
    value["data"].clone()
}

#[tokio::test]
async fn management_uses_v1_envelopes_without_gateway_or_invite_principals() {
    let app = app(Arc::new(Service::default()));
    let response = app
        .clone()
        .oneshot(request(
            "POST",
            "/agents",
            Some("admin"),
            json!({
        "provider_bot_ref":"remote","visibility":"private","agent_card":card().agent_card}),
        ))
        .await
        .unwrap();
    let location = response.headers()["location"].to_str().unwrap().to_string();
    assert_eq!(
        location,
        "/api/v1/collaboration/a2a/agents/bot%2Fopaque/agent-card"
    );
    let data = read(response, StatusCode::CREATED).await;
    assert_eq!(data["agent_card"]["unknown"]["keep"], true);
    assert!(data.get("bot_runtime_token").is_none());
    assert!(data.get("provider_id").is_none());
    for (method, suffix, body) in [
        ("GET", "/agents/bot%2Fopaque/agent-card", Value::Null),
        (
            "PUT",
            "/agents/bot%2Fopaque/agent-card",
            json!({"visibility":"public"}),
        ),
        (
            "POST",
            "/agents/bot%2Fopaque/agent-card:refresh",
            Value::Null,
        ),
        ("POST", "/agents/bot%2Fopaque/agent-card:refresh", json!({})),
    ] {
        let data = read(
            app.clone()
                .oneshot(request(method, suffix, Some("admin"), body))
                .await
                .unwrap(),
            StatusCode::OK,
        )
        .await;
        assert_eq!(data["bot_id"], "bot/opaque");
    }
    let data = read(
        app.oneshot(request(
            "GET",
            "/agents?provider_bot_ref=remote",
            Some("admin"),
            Value::Null,
        ))
        .await
        .unwrap(),
        StatusCode::OK,
    )
    .await;
    assert_eq!(data["items"][0]["bot_id"], "bot/opaque");
}

#[tokio::test]
async fn named_registration_has_v1_response() {
    let app = app(Arc::new(Service::default()));
    let data = read(
        app.clone()
            .oneshot(request(
                "POST",
                "/providers/orders/bots",
                Some("admin"),
                json!({
        "provider_bot_ref":"remote","owners":["owner"],"agent_card":card().agent_card}),
            ))
            .await
            .unwrap(),
        StatusCode::CREATED,
    )
    .await;
    assert_eq!(data["bot_runtime_token"], "runtime-test");
    assert!(data.get("provider_id").is_none());
}

#[tokio::test]
async fn errors_use_v1_request_ids_and_fail_before_service_dispatch() {
    let service = Arc::new(Service::default());
    let app = app(service.clone());
    for (method, suffix, body, expected, code) in [
        (
            "GET",
            "/agents",
            Value::Null,
            StatusCode::BAD_REQUEST,
            "invalid_request",
        ),
        (
            "GET",
            "/agents?provider_bot_ref=x&provider_bot_ref=y",
            Value::Null,
            StatusCode::BAD_REQUEST,
            "invalid_request",
        ),
        (
            "PUT",
            "/agents/bot/agent-card",
            json!({"bot_id":"injected"}),
            StatusCode::BAD_REQUEST,
            "invalid_request",
        ),
        (
            "POST",
            "/agents",
            json!({"provider_bot_ref":"remote","agent_card":null}),
            StatusCode::BAD_REQUEST,
            "invalid_request",
        ),
        (
            "DELETE",
            "/agents",
            Value::Null,
            StatusCode::METHOD_NOT_ALLOWED,
            "method_not_allowed",
        ),
    ] {
        let response = app
            .clone()
            .oneshot(request(method, suffix, Some("admin"), body))
            .await
            .unwrap();
        let data = read(response, expected).await;
        assert_eq!(data["error_code"], code);
    }
    assert_eq!(service.calls.load(Ordering::SeqCst), 0);
    let data = read(
        app.clone()
            .oneshot(request(
                "GET",
                "/agents?provider_bot_ref=remote",
                None,
                Value::Null,
            ))
            .await
            .unwrap(),
        StatusCode::UNAUTHORIZED,
    )
    .await;
    assert_eq!(data["error_code"], "unauthenticated");
    let data = read(
        app.oneshot(request(
            "GET",
            "/agents?provider_bot_ref=remote",
            Some("wrong"),
            Value::Null,
        ))
        .await
        .unwrap(),
        StatusCode::FORBIDDEN,
    )
    .await;
    assert_eq!(data["error_code"], "forbidden");
}

#[tokio::test]
async fn malformed_json_duplicate_bearers_and_unconfigured_services_return_v1_errors() {
    let app = app(Arc::new(Service::default()));
    let mut duplicate = request(
        "GET",
        "/agents?provider_bot_ref=remote",
        Some("admin"),
        Value::Null,
    );
    duplicate
        .headers_mut()
        .append("authorization", "Bearer admin".parse().unwrap());
    read(
        app.clone().oneshot(duplicate).await.unwrap(),
        StatusCode::UNAUTHORIZED,
    )
    .await;
    for bytes in ["{", r#"{"provider_bot_ref":"a","provider_bot_ref":"b"}"#] {
        let mut req = request("POST", "/agents", Some("admin"), Value::Null);
        *req.body_mut() = Body::from(bytes);
        read(
            app.clone().oneshot(req).await.unwrap(),
            StatusCode::BAD_REQUEST,
        )
        .await;
    }
    let mut req = request(
        "GET",
        "/agents?provider_bot_ref=remote",
        Some("admin"),
        Value::Null,
    );
    req.headers_mut()
        .insert("accept", "text/plain".parse().unwrap());
    read(app.oneshot(req).await.unwrap(), StatusCode::NOT_ACCEPTABLE).await;
    let app = bcs_api_http::router(register_support::test_state(Arc::new(UnusedRegister)));
    read(
        app.oneshot(request(
            "GET",
            "/agents?provider_bot_ref=remote",
            Some("admin"),
            Value::Null,
        ))
        .await
        .unwrap(),
        StatusCode::SERVICE_UNAVAILABLE,
    )
    .await;
}

#[tokio::test]
async fn accept_precedence_size_limits_and_invalid_credentials_have_v1_errors() {
    let app = app(Arc::new(Service::default()));
    for (accept, status) in [
        ("application/json", StatusCode::OK),
        ("application/*;q=0.5", StatusCode::OK),
        ("*/*", StatusCode::OK),
        ("application/json;q=0,*/*;q=1", StatusCode::NOT_ACCEPTABLE),
        ("application/json;q=invalid", StatusCode::NOT_ACCEPTABLE),
    ] {
        let mut req = request(
            "GET",
            "/agents?provider_bot_ref=remote",
            Some("admin"),
            Value::Null,
        );
        req.headers_mut().insert("accept", accept.parse().unwrap());
        read(app.clone().oneshot(req).await.unwrap(), status).await;
    }
    let mut req = request("POST", "/agents", Some("admin"), Value::Null);
    *req.body_mut() = Body::from(vec![b' '; 2 * 1024 * 1024 + 1]);
    read(
        app.clone().oneshot(req).await.unwrap(),
        StatusCode::PAYLOAD_TOO_LARGE,
    )
    .await;
    for token in ["", "a b", "a,b"] {
        let req = request(
            "GET",
            "/agents?provider_bot_ref=remote",
            Some(token),
            Value::Null,
        );
        read(
            app.clone().oneshot(req).await.unwrap(),
            StatusCode::UNAUTHORIZED,
        )
        .await;
    }
    for (method, path, body) in [
        ("GET", "/agents/bot/agent-card?unexpected=1", Value::Null),
        ("POST", "/agents?unexpected=1", json!({})),
        (
            "POST",
            "/agents/bot/agent-card:refresh",
            json!({"unexpected":true}),
        ),
    ] {
        read(
            app.clone()
                .oneshot(request(method, path, Some("admin"), body))
                .await
                .unwrap(),
            StatusCode::BAD_REQUEST,
        )
        .await;
    }
}

#[tokio::test]
async fn generated_request_id_is_pinned_once_for_success_and_errors() {
    let app = app(Arc::new(Service::default()));
    for token in [Some("admin"), None] {
        let mut req = request("GET", "/agents?provider_bot_ref=remote", token, Value::Null);
        req.headers_mut().remove("x-request-id");
        let response = app.clone().oneshot(req).await.unwrap();
        let id = response.headers()["x-request-id"]
            .to_str()
            .unwrap()
            .to_owned();
        assert!(!id.is_empty());
        let value: Value =
            serde_json::from_slice(&to_bytes(response.into_body(), 8192).await.unwrap()).unwrap();
        assert_eq!(value["request_id"], id);
    }
}

#[tokio::test]
async fn chat_and_run_routes_are_not_exposed_by_v1_a2a() {
    let service = Arc::new(Service::default());
    let app = app(service.clone());
    for (method, path) in [
        ("POST", "/providers/orders/bots/remote/chat-async"),
        ("GET", "/chat/runs/run"),
        ("POST", "/chat/runs/run/cancel"),
    ] {
        let response = app
            .clone()
            .oneshot(request(method, path, Some("admin"), Value::Null))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    }
    assert_eq!(service.calls.load(Ordering::SeqCst), 0);
}
