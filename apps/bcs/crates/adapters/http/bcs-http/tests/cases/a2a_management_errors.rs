//! Public error contracts and compatibility admission must fail closed without
//! leaking upstream diagnostics, while retaining machine-readable reasons.
use async_trait::async_trait;
use axum::{
    Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{
    ServiceError, ServiceResult,
    application::a2a_bot::*,
    types::a2a_bot::{
        A2aBotRecord, A2aCardSource,
        a2a_management::{ManagementCode as Code, ManagementError},
    },
};
use bcs_services_container::Services;
use serde_json::{Value, json};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};
use tower::ServiceExt;

const PRIVATE: &str = "private-provider-credential";
struct Management {
    failure: Option<fn() -> ServiceError>,
    calls: AtomicUsize,
    visibility: std::sync::Mutex<Option<bcs_service_api::types::a2a_bot::A2aRegistrationVisibility>>,
}
impl Management {
    fn result(&self) -> ServiceResult<()> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        match self.failure {
            Some(error) => Err(error()),
            None => Ok(()),
        }
    }
}
fn card() -> ManagedA2aCard {
    ManagedA2aCard {
        provider_bot_ref: "remote".into(),
        provider_name: "orders".into(),
        agent_name: "订单助手".into(),
        bot_id: "bot".into(),
        agent_card_url: Some("https://example.org/card".into()),
        agent_card: json!({"name":"订单助手","custom":{"unchanged":true}}),
    }
}
#[async_trait]
impl A2aBotManagementService for Management {
    async fn resolve_provider_ref_target(&self, provider: &str, name: &str) -> ServiceResult<String> {
        assert_eq!(provider, "orders");
        assert_eq!(name, "remote");
        self.result()?;
        Ok("target-bot".into())
    }
    async fn get_agent_by_id(&self, token: &str, name: &str) -> ServiceResult<ManagedA2aCard> {
        assert_eq!(token, "test-admin");
        assert_eq!(name, "bot");
        self.result()?;
        Ok(card())
    }
    async fn register_agent(&self, command: RegisterAgentCommand) -> ServiceResult<ManagedA2aCard> {
        *self.visibility.lock().unwrap() = Some(command.visibility);
        assert_eq!(command.provider_admin_token, "test-admin");
        assert_eq!(command.owners, Some(vec!["owner".into()]));
        assert!(command.now_ms > 0);
        assert!(
            matches!(command.source,A2aCardSource::Url(ref url) if url=="https://example.org/card")
        );
        self.result()?;
        Ok(card())
    }
    async fn register_named(
        &self,
        command: RegisterNamedA2aBotCommand,
    ) -> ServiceResult<NamedA2aRegistration> {
        assert_eq!(command.provider_name, "orders");
        assert_eq!(command.provider_admin_token, "test-admin");
        assert_eq!(command.owners, vec!["owner"]);
        assert!(command.now_ms > 0);
        self.result()?;
        Ok(NamedA2aRegistration {
            card: NamedA2aCard {
                provider_bot_ref: "remote".into(),
                bot_id: "bot".into(),
                provider_name: "orders".into(),
                bot_name: "订单助手".into(),
                agent_card_url: None,
                agent_card: card().agent_card,
            },
            bot_runtime_token: None,
        })
    }
    async fn get(&self, _: &str, _: &str, _: &str) -> ServiceResult<Option<A2aBotRecord>> {
        panic!("legacy ID path")
    }
    async fn update_agent_by_id_with_visibility(&self,token:&str,name:&str,source:Option<A2aCardSource>,visibility:Option<bcs_service_api::types::a2a_bot::A2aRegistrationVisibility>)->ServiceResult<ManagedA2aCard> {
        if let Some(visibility)=visibility {
            assert_eq!(token,"test-admin");
            assert_eq!(name,"bot");
            assert!(source.is_none());
            *self.visibility.lock().unwrap()=Some(visibility);
            self.result()?;
            return Ok(card());
        }
        self.update_agent_by_id(token,name,source.unwrap()).await
    }
    async fn configure(&self, _: ConfigureA2aBotCommand) -> ServiceResult<A2aBotRecord> {
        panic!("legacy ID path")
    }
    async fn refresh(&self, _: &str, _: &str, _: &str, _: i64) -> ServiceResult<A2aBotRecord> {
        panic!("legacy ID path")
    }
    async fn refresh_due(&self, _: i64) -> ServiceResult<usize> {
        panic!("background path")
    }
}
fn app(failure: Option<fn() -> ServiceError>) -> (Router, Arc<Management>) {
    let service = Arc::new(Management {
        failure,
        calls: AtomicUsize::new(0),
        visibility: Default::default(),
    });
    (
        build_router(HttpAppState::new(
            Services::builder()
                .a2a_bot_management(service.clone())
                .build_for_test(),
        )),
        service,
    )
}
fn request(method: &str, path: &str, body: impl Into<Body>) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(path)
        .header("authorization", "Bearer test-admin")
        .header("content-type", "application/json")
        .body(body.into())
        .unwrap()
}
async fn read(response: axum::response::Response) -> Value {
    serde_json::from_slice(&to_bytes(response.into_body(), 65536).await.unwrap()).unwrap()
}
const QUERY: &str = "/a2a/agents/bot/agent-card";

#[tokio::test]
async fn application_failures_have_stable_status_codes_and_safe_details() {
    let cases: &[(fn() -> ServiceError, u16, Code)] = &[
        (
            || ServiceError::Unauthorized(PRIVATE.into()),
            401,
            Code::Unauthenticated,
        ),
        (
            || ServiceError::Forbidden(PRIVATE.into()),
            403,
            Code::PermissionDenied,
        ),
        (
            || ServiceError::BotNotFound(PRIVATE.into()),
            404,
            Code::BotNotFound,
        ),
        (
            || ServiceError::BotNotRegistered(PRIVATE.into()),
            404,
            Code::BotNotFound,
        ),
        (
            || ServiceError::Conflict(PRIVATE.into()),
            409,
            Code::ProviderBotRefConflict,
        ),
        (
            || ServiceError::InvalidOperation {
                message: PRIVATE.into(),
                request_id: Some(PRIVATE.into()),
            },
            400,
            Code::InvalidRequest,
        ),
        (
            || ServiceError::InternalError(PRIVATE.into()),
            500,
            Code::InternalError,
        ),
    ];
    for &(failure, status, code) in cases {
        let (app, service) = app(Some(failure));
        let response = app
            .oneshot(request("GET", QUERY, Body::empty()))
            .await
            .unwrap();
        assert_eq!(response.status().as_u16(), status);
        assert_eq!(response.headers()["cache-control"], "no-store");
        let id = response.headers()["x-request-id"]
            .to_str()
            .unwrap()
            .to_owned();
        if status == 401 {
            assert_eq!(response.headers()["www-authenticate"], "Bearer");
        }
        let body = read(response).await;
        assert_eq!(body["error"]["code"], json!(code));
        assert_eq!(body["error"]["request_id"], id);
        assert!(!body.to_string().contains(PRIVATE));
        assert_eq!(service.calls.load(Ordering::SeqCst), 1);
    }
}

#[tokio::test]
async fn management_codes_preserve_actionable_fields_and_retry_headers() {
    // These are the public HTTP contract, including fetch/validation/CAS failures.
    for (code, status) in [
        (Code::InvalidRequest, 400),
        (Code::Unauthenticated, 401),
        (Code::PermissionDenied, 403),
        (Code::BotNotFound, 404),
        (Code::MethodNotAllowed, 405),
        (Code::NotAcceptable, 406),
        (Code::BotNameConflict, 409),
        (Code::SourceAgentConflict, 409),
        (Code::SourceAgentChangeNotAllowed, 409),
        (Code::TargetChanged, 409),
        (Code::CardUrlRequired, 409),
        (Code::RefreshSuperseded, 409),
        (Code::RequestTooLarge, 413),
        (Code::UnsupportedMediaType, 415),
        (Code::InvalidOwner, 422),
        (Code::InvalidCard, 422),
        (Code::CardSourceInvalid, 422),
        (Code::CardTooLarge, 422),
        (Code::CardAddressRejected, 422),
        (Code::CardInterfaceUnsupported, 422),
        (Code::CardContentUnsupported, 422),
        (Code::CardAuthUnsupported, 422),
        (Code::CardExtensionUnsupported, 422),
        (Code::RateLimited, 429),
        (Code::CardFetchFailed, 502),
        (Code::CardFetchTimeout, 504),
        (Code::ServiceUnavailable, 503),
        (Code::InternalError, 500),
    ] {
        let service = Arc::new(CodeFailure(code));
        let app = build_router(HttpAppState::new(
            Services::builder()
                .a2a_bot_management(service)
                .build_for_test(),
        ));
        let response = app
            .oneshot(request("GET", QUERY, Body::empty()))
            .await
            .unwrap();
        assert_eq!(response.status().as_u16(), status, "{code:?}");
        if code == Code::RateLimited {
            assert_eq!(response.headers()["retry-after"], "1");
        }
        let id = response.headers()["x-request-id"]
            .to_str()
            .unwrap()
            .to_owned();
        let body = read(response).await;
        assert_eq!(body["error"]["code"], json!(code));
        assert_eq!(body["error"]["request_id"], id);
        assert!(!body["error"]["message"].as_str().unwrap().is_empty());
        assert_eq!(
            body["error"]["details"],
            json!([{"field":"agent_card","reason":"CONTRACT_FAILURE"}])
        );
    }
}
struct CodeFailure(Code);
#[async_trait]
impl A2aBotManagementService for CodeFailure {
    async fn get_agent_by_id(&self, _: &str, _: &str) -> ServiceResult<ManagedA2aCard> {
        Err(ManagementError::new(self.0, "agent_card", "CONTRACT_FAILURE").into())
    }
    async fn get(&self, _: &str, _: &str, _: &str) -> ServiceResult<Option<A2aBotRecord>> {
        panic!("legacy ID path")
    }
    async fn configure(&self, _: ConfigureA2aBotCommand) -> ServiceResult<A2aBotRecord> {
        panic!("legacy ID path")
    }
    async fn refresh(&self, _: &str, _: &str, _: &str, _: i64) -> ServiceResult<A2aBotRecord> {
        panic!("legacy ID path")
    }
    async fn refresh_due(&self, _: i64) -> ServiceResult<usize> {
        panic!("background path")
    }
}

#[tokio::test]
async fn request_limits_null_fields_and_accept_precedence_reject_before_service_calls() {
    let (app, service) = app(None);
    for (method, path, body, accept, status) in [
        ("GET", QUERY, "{}".into(), "*/*", 400),
        ("POST", "/a2a/agents?agent_name=x", "{}".into(), "*/*", 400),
        (
            "POST",
            "/a2a/agents",
            json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card":null}).to_string(),
            "*/*",
            400,
        ),
        (
            "POST",
            "/a2a/agents",
            json!({"provider_bot_ref":"remote","owners":["owner"],"summary":null}).to_string(),
            "*/*",
            400,
        ),
        (
            "POST",
            "/a2a/agents",
            " ".repeat(2 * 1024 * 1024 + 1),
            "*/*",
            413,
        ),
        (
            "GET",
            QUERY,
            String::new(),
            "*/*;q=1, application/json;q=0",
            406,
        ),
        (
            "GET",
            QUERY,
            String::new(),
            "application/*;q=1, application/json;q=invalid",
            406,
        ),
        ("GET", QUERY, String::new(), "application/json;q=2", 406),
    ] {
        let mut req = request(method, path, body);
        req.headers_mut().insert("accept", accept.parse().unwrap());
        let response = app.clone().oneshot(req).await.unwrap();
        assert_eq!(
            response.status().as_u16(),
            status,
            "{method} {path} {accept}"
        );
        assert!(read(response).await["error"]["details"].is_array());
    }
    let mut req = request("GET", QUERY, Body::empty());
    req.headers_mut().insert(
        "accept",
        axum::http::HeaderValue::from_bytes(b"\xff").unwrap(),
    );
    assert_eq!(
        app.clone().oneshot(req).await.unwrap().status(),
        StatusCode::NOT_ACCEPTABLE
    );
    assert_eq!(service.calls.load(Ordering::SeqCst), 0);
    for accept in [
        "application/*;q=0.7",
        "*/*;q=0, application/json;q=1",
        "application/json;Q=0.8",
    ] {
        let mut req = request("GET", QUERY, Body::empty());
        req.headers_mut().insert("accept", accept.parse().unwrap());
        assert_eq!(
            app.clone().oneshot(req).await.unwrap().status(),
            StatusCode::OK
        );
    }
}

#[tokio::test]
async fn url_registration_and_legacy_named_registration_keep_distinct_response_contracts() {
    let (app, service) = app(None);
    let body = json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card_url":"https://example.org/card"}).to_string();
    let response = app
        .clone()
        .oneshot(request("POST", "/a2a/agents", body.clone()))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::CREATED);
    let location = response.headers()["location"].to_str().unwrap();
    assert_eq!(location, "/a2a/agents/bot/agent-card");
    let value = read(response).await;
    assert_eq!(value["agent_card"]["custom"]["unchanged"], true);
    assert!(value.get("bot_runtime_token").is_none());
    let response = app
        .clone()
        .oneshot(request("POST", "/a2a/providers/orders/bots", body))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::CREATED);
    let value = read(response).await;
    assert_eq!(value["bot_name"], "订单助手");
    assert!(value.get("bot_runtime_token").is_none());
    for body in [
        json!({"provider_bot_ref":"remote","owners":[]}),
        json!({"provider_bot_ref":"remote","owners":[],"agent_card":{},"agent_card_url":"https://example.org/card"}),
    ] {
        let response = app
            .clone()
            .oneshot(request(
                "POST",
                "/a2a/providers/orders/bots",
                body.to_string(),
            ))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }
    let mut req = request(
        "POST",
        "/a2a/providers/orders/bots",
        json!({"provider_bot_ref":"remote","owners":[]}).to_string(),
    );
    req.headers_mut().remove("authorization");
    assert_eq!(
        app.oneshot(req).await.unwrap().status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(service.calls.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn legacy_registration_errors_are_redacted_and_unconfigured_routes_fail_closed() {
    for (failure, status) in [
        (
            (|| ServiceError::Unauthorized(PRIVATE.into())) as fn() -> ServiceError,
            401,
        ),
        (|| ServiceError::Forbidden(PRIVATE.into()), 403),
        (|| ServiceError::ProviderNotFound(PRIVATE.into()), 404),
        (|| ServiceError::BotNotFound(PRIVATE.into()), 404),
        (|| ServiceError::BotNotRegistered(PRIVATE.into()), 404),
        (|| ServiceError::Conflict(PRIVATE.into()), 409),
        (
            || ServiceError::InvalidOperation {
                message: PRIVATE.into(),
                request_id: None,
            },
            400,
        ),
        (|| ServiceError::InternalError(PRIVATE.into()), 500),
        (
            || ManagementError::new(Code::CardFetchTimeout, "agent_card", "TIMEOUT").into(),
            504,
        ),
    ] {
        let (app, _) = app(Some(failure));
        let response = app
            .oneshot(request(
                "POST",
                "/a2a/providers/orders/bots",
                json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card":{}}).to_string(),
            ))
            .await
            .unwrap();
        assert_eq!(response.status().as_u16(), status);
        assert!(!read(response).await.to_string().contains(PRIVATE));
    }
    let app = build_router(HttpAppState::new(Services::builder().build_for_test()));
    for (method, path, body, status) in [
        ("GET", QUERY, "", 503),
        (
            "POST",
            "/a2a/providers/orders/bots",
            r#"{"provider_bot_ref":"remote","owners":[]}"#,
            503,
        ),
        (
            "POST",
            "/a2a/providers/orders/bots/agent/chat-async",
            r#"{"message":"hello"}"#,
            503,
        ),
        ("GET", "/a2a/chat/runs/run-1", "", 401),
        ("POST", "/a2a/chat/runs/run-1/cancel", "", 401),
    ] {
        let mut req = request(method, path, body);
        if path.starts_with("/a2a/chat/") {
            req.headers_mut().remove("authorization");
        }
        let response = app.clone().oneshot(req).await.unwrap();
        assert_eq!(response.status().as_u16(), status, "{path}");
        assert!(read(response).await.get("error").is_some());
    }
}

/// A host implementing only the original ID API must not accidentally admit
/// requests through newer public or name-based methods.
struct LegacyOnly;
#[async_trait]
impl A2aBotManagementService for LegacyOnly {
    async fn get(&self, _: &str, _: &str, _: &str) -> ServiceResult<Option<A2aBotRecord>> {
        panic!("unexpected ID fallback")
    }
    async fn configure(&self, _: ConfigureA2aBotCommand) -> ServiceResult<A2aBotRecord> {
        panic!("unexpected ID fallback")
    }
    async fn refresh(&self, _: &str, _: &str, _: &str, _: i64) -> ServiceResult<A2aBotRecord> {
        panic!("unexpected ID fallback")
    }
    async fn refresh_due(&self, _: i64) -> ServiceResult<usize> {
        panic!("unexpected background fallback")
    }
}
#[tokio::test]
async fn legacy_service_implementations_cannot_silently_admit_new_public_methods() {
    let app = build_router(HttpAppState::new(
        Services::builder()
            .a2a_bot_management(Arc::new(LegacyOnly))
            .build_for_test(),
    ));
    for (method, path, body) in [
        (
            "POST",
            "/a2a/agents",
            json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card":{}}).to_string(),
        ),
        ("GET", QUERY, String::new()),
        (
            "PUT",
            "/a2a/agents/bot/agent-card",
            json!({"agent_card":{}}).to_string(),
        ),
        (
            "POST",
            "/a2a/agents/bot/agent-card:refresh",
            json!({}).to_string(),
        ),
        (
            "POST",
            "/a2a/providers/orders/bots",
            json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card":{}}).to_string(),
        ),
    ] {
        let response = app
            .clone()
            .oneshot(request(method, path, body))
            .await
            .unwrap();
        assert_eq!(
            response.status(),
            StatusCode::INTERNAL_SERVER_ERROR,
            "{method} {path}"
        );
        let body = read(response).await;
        assert!(!body.to_string().contains("not configured"));
        assert!(body.get("bot_runtime_token").is_none());
    }
    // The same fail-closed guarantee applies to non-HTTP hosts of this Service API.
    assert!(
        LegacyOnly
            .resolve_provider_ref_target("orders", "agent")
            .await
            .is_err()
    );

}

#[tokio::test]
async fn named_chat_does_not_resolve_targets_before_caller_authentication() {
    let (app, service) = app(None);
    let mut req = request(
        "POST",
        "/a2a/providers/orders/bots/agent/chat-async",
        r#"{"message":"hello"}"#,
    );
    req.headers_mut().remove("authorization");
    let response = app.oneshot(req).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(
        read(response).await,
        json!({"error":"Caller authentication required"})
    );
    assert_eq!(service.calls.load(Ordering::SeqCst), 0);
}

struct Runs;
#[async_trait]
impl bcs_service_api::A2aChatRunService for Runs {
    async fn start_async_chat(
        &self,
        command: bcs_service_api::AsyncA2aChatCommand,
    ) -> ServiceResult<bcs_service_api::AsyncA2aChatAccepted> {
        assert_eq!(command.target_bot_id, "target-bot");
        assert!(
            matches!(command.caller,bcs_service_api::CallerContext::Bot(ref bot) if bot.bot_uuid=="caller-bot")
        );
        if command.message == "reject" {
            return Err(ServiceError::Forbidden(PRIVATE.into()));
        }
        assert_eq!(command.message, "hello");
        Ok(bcs_service_api::AsyncA2aChatAccepted {
            run_id: command.run_id,
            bot_uuid: command.target_bot_id,
            session_id: command.session_key,
            status: "pending".into(),
            expires_at_ms: 100,
            delivery: None,
        })
    }
    async fn get_run(
        &self,
        command: bcs_service_api::ChatRunQueryCommand,
    ) -> ServiceResult<bcs_service_api::A2aRunStatus> {
        assert!(
            matches!(command.caller,bcs_service_api::CallerContext::Bot(ref bot) if bot.bot_uuid=="caller-bot")
        );
        assert_eq!(command.since_version, 3);
        Ok(bcs_service_api::A2aRunStatus {
            run_id: command.run_id,
            status: "failed".into(),
            response: Some(
                json!({"content":"partial answer","error_message":PRIVATE,"bot_uuid":PRIVATE,
                "from_bot_id":PRIVATE,"session_id":"session-1","version":4,"is_terminal":true}),
            ),
        })
    }
    async fn cancel_run(
        &self,
        command: bcs_service_api::ChatRunCancelCommand,
    ) -> ServiceResult<bcs_service_api::A2aRunStatus> {
        assert!(
            matches!(command.caller,bcs_service_api::CallerContext::Bot(ref bot) if bot.bot_uuid=="caller-bot")
        );
        Ok(bcs_service_api::A2aRunStatus {
            run_id: command.run_id,
            status: "cancelled".into(),
            response: Some(json!({"content":"partial answer","bot_uuid":PRIVATE,"version":5})),
        })
    }
}

#[tokio::test]
async fn named_chat_and_run_routes_preserve_caller_checks_and_redact_internal_fields() {
    use bcs_service_api::{BotCapabilities, BotRegistryCoreService};
    let dir = tempfile::tempdir().unwrap();
    let registry = Arc::new(bcs_bot::BotCore::with_base_dir(dir.path().to_path_buf()));
    registry
        .register("caller-bot".into(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("test-admin".into(), "caller-bot".into())
        .await;
    let management = Arc::new(Management {
        visibility: Default::default(),
        failure: None,
        calls: AtomicUsize::new(0),
    });
    let app = build_router(HttpAppState::new(
        Services::builder()
            .registry(registry)
            .a2a_bot_management(management.clone())
            .a2a_chat_runs(Arc::new(Runs))
            .build_for_test(),
    ));
    let path = "/a2a/providers/orders/bots/remote/chat-async";
    let response = app
        .clone()
        .oneshot(request("POST", path, r#"{"message":"hello"}"#))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let accepted = read(response).await;
    assert_eq!(accepted["provider_name"], "orders");
    assert_eq!(accepted["provider_bot_ref"], "remote");
    assert_eq!(accepted["status"], "pending");
    assert_eq!(accepted["expires_at_ms"], 100);
    assert!(accepted.get("bot_uuid").is_none());
    let run = accepted["run_id"].as_str().unwrap();
    let response = app
        .clone()
        .oneshot(request(
            "GET",
            &format!("/a2a/chat/runs/{run}?since_version=3"),
            Body::empty(),
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let result = read(response).await;
    assert_eq!(result["run_id"], run);
    assert_eq!(result["state"], "failed");
    assert_eq!(result["response"]["content"], "partial answer");
    assert_eq!(result["version"], 4);
    assert!(!result.to_string().contains(PRIVATE));
    assert!(result.get("bot_uuid").is_none());
    assert_eq!(
        result["error_message"],
        "Run failed; provide run_id to the platform operator"
    );
    let response = app
        .clone()
        .oneshot(request(
            "POST",
            &format!("/a2a/chat/runs/{run}/cancel"),
            Body::empty(),
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let result = read(response).await;
    assert_eq!(result["run_id"], run);
    assert_eq!(result["cancelled"], true);
    assert_eq!(result["version"], 5);
    assert!(!result.to_string().contains(PRIVATE));
    let response = app
        .oneshot(request("POST", path, r#"{"message":"reject"}"#))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(
        read(response).await,
        json!({"error":"Chat admission failed","status":403})
    );
    assert_eq!(management.calls.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn registration_visibility_defaults_and_invalid_values_fail_before_admission() {
    use bcs_service_api::types::a2a_bot::A2aRegistrationVisibility::{Protected, Public, Private};
    for (value, expected) in [(None, Protected), (Some("protected"), Protected), (Some("public"), Public), (Some("private"), Private)] {
        let (app, service) = app(None);
        let mut body = json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card_url":"https://example.org/card"});
        if let Some(value) = value { body["visibility"] = json!(value); }
        let response = app.oneshot(request("POST", "/a2a/agents", body.to_string())).await.unwrap();
        assert_eq!(response.status(), StatusCode::CREATED);
        assert_eq!(*service.visibility.lock().unwrap(), Some(expected));
    }
    for value in [json!("secret"), json!("PUBLIC"), json!(null), json!(true)] {
        let (app, service) = app(None);
        let body = json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card_url":"https://example.org/card","visibility":value});
        let response = app.oneshot(request("POST", "/a2a/agents", body.to_string())).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert_eq!(service.calls.load(Ordering::SeqCst), 0);
    }
}

#[tokio::test]
async fn visibility_only_update_is_routed_and_empty_or_invalid_updates_are_rejected() {
    use bcs_service_api::types::a2a_bot::A2aRegistrationVisibility::Public;
    let (app,service)=app(None);
    let response=app.clone().oneshot(request("PUT","/a2a/agents/bot/agent-card",json!({"visibility":"public"}).to_string())).await.unwrap();
    assert_eq!(response.status(),StatusCode::OK);
    assert_eq!(*service.visibility.lock().unwrap(),Some(Public));
    for body in [json!({}),json!({"visibility":"secret"})] {
        let response=app.clone().oneshot(request("PUT","/a2a/agents/bot/agent-card",body.to_string())).await.unwrap();
        assert_eq!(response.status(),StatusCode::BAD_REQUEST);
    }
    assert_eq!(service.calls.load(Ordering::SeqCst),1);
}
