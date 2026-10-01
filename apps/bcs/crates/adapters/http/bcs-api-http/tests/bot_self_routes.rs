use std::sync::Arc;

use async_trait::async_trait;
use axum::http::StatusCode;
use bcs_service_api::application::v1::*;
use tower::ServiceExt;

#[allow(dead_code)]
mod register_support;
use register_support::{bare_request, response_json, test_router, test_state};

struct UnusedRegisterService;

#[async_trait]
impl RegisterService for UnusedRegisterService {
    async fn issue_register_token(&self, _: IssueRegisterToken) -> Result<RegisterTokenView, ApplicationError> {
        panic!("self lookup must not issue registration tokens")
    }

    async fn register_bot(&self, _: RegisterBot) -> Result<BotRegistration, ApplicationError> {
        panic!("self lookup must not register a Bot")
    }
}

#[tokio::test]
async fn missing_bearer_is_unauthorized_without_gateway_principal() {
    let response = test_router(Arc::new(UnusedRegisterService))
        .oneshot(bare_request("GET", "/api/v1/collaboration/bots/me"))
        .await.expect("response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(response.headers()["cache-control"], "no-store");
    let body = response_json(response).await;
    assert_eq!(body["code"], 40100);
    assert_eq!(body["data"]["error_code"], "unauthenticated");
    assert_eq!(body["request_id"], "request-123");
}

struct SelfService;

#[async_trait]
impl BotSelfService for SelfService {
    async fn get_me(&self, token: &str) -> Result<BotSelfView, ApplicationError> {
        match token {
            "invalid" | "expired" => return Err(ApplicationError::Unauthenticated),
            "human" => return Err(ApplicationError::forbidden("Agent identity required")),
            "sdk-down" => return Err(ApplicationError::bad_gateway("agent_identity_unavailable", "Identity service unavailable")),
            "db-down" => return Err(ApplicationError::internal("Registration store unavailable")),
            "conflict" => return Err(ApplicationError::conflict("agent_registration_conflict", "Agent registration is ambiguous")),
            "registered" | "unregistered" => {},
            _ => panic!("raw Bearer credential was not preserved"),
        }
        let bot = (token == "registered").then(|| bcs_service_api::types::AgentBotRegistration {
            bot_id: "bot-001".into(), name: Some("Poolab Assistant".into()),
            summary: Some("负责研发任务的 Agent".into()), provider_id: Some("provider-poolab".into()),
            provider_bot_ref: Some("agent-001".into()),
        });
        Ok(BotSelfView {
            registration_status: if bot.is_some() { BotRegistrationStatus::Registered } else { BotRegistrationStatus::Unregistered },
            agent_code: "agent-001".into(), bot,
        })
    }
}

fn app() -> axum::Router {
    bcs_api_http::router(test_state(Arc::new(UnusedRegisterService))
        .with_bot_self_service(Arc::new(SelfService))
        .with_invite_code_gate_enabled(true))
}

#[tokio::test]
async fn self_returns_exact_safe_envelope_and_ignores_identity_selectors() {
    for token in ["registered", "unregistered"] {
        let mut request = bare_request("GET", "/api/v1/collaboration/bots/me?bot_id=other&agent_code=other&provider_id=other");
        request.headers_mut().insert("authorization", format!("bEaReR {token}").parse().unwrap());
        request.headers_mut().insert("x-avernet-principal", "forged".parse().unwrap());
        let response = app().oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()["cache-control"], "no-store");
        let bot = if token == "registered" {
            serde_json::json!({"bot_id":"bot-001","name":"Poolab Assistant","summary":"负责研发任务的 Agent",
                "provider_id":"provider-poolab","provider_bot_ref":"agent-001"})
        } else { serde_json::Value::Null };
        assert_eq!(response_json(response).await, serde_json::json!({
            "code":20000,"message":"OK","request_id":"request-123",
            "data":{"registration_status":token,"identity":{"agent_code":"agent-001"},"bot":bot}
        }));
    }
}

#[tokio::test]
async fn authentication_and_backend_failures_never_look_like_unregistered() {
    for (token, status, code) in [
        ("invalid",401,"unauthenticated"), ("expired",401,"unauthenticated"),
        ("human",403,"forbidden"), ("sdk-down",502,"agent_identity_unavailable"),
        ("db-down",500,"internal_error"), ("conflict",409,"agent_registration_conflict"),
    ] {
        let mut request = bare_request("GET", "/api/v1/collaboration/bots/me");
        request.headers_mut().insert("authorization", format!("Bearer {token}").parse().unwrap());
        let response = app().oneshot(request).await.unwrap();
        assert_eq!(response.status().as_u16(), status);
        assert_eq!(response.headers()["cache-control"], "no-store");
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], code);
        assert!(body["data"].get("registration_status").is_none());
    }
}

#[tokio::test]
async fn malformed_and_duplicate_authorization_fail_before_service() {
    for value in ["", "Basic abc", "Bearer", "Bearer ", "Bearer a b", "Bearer a,b"] {
        let mut request = bare_request("GET", "/api/v1/collaboration/bots/me");
        request.headers_mut().insert("authorization", value.parse().unwrap());
        let response = app().oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(response.headers()["cache-control"], "no-store");
    }
    let mut request = bare_request("GET", "/api/v1/collaboration/bots/me");
    request.headers_mut().append("authorization", "Bearer registered".parse().unwrap());
    request.headers_mut().append("authorization", "Bearer unregistered".parse().unwrap());
    assert_eq!(app().oneshot(request).await.unwrap().status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn self_is_not_mounted_in_public_openapi() {
    // The public Bot-by-id route may match the literal id "me", but must still
    // require Gateway authentication rather than reaching the self service.
    let mut request = bare_request("GET", "/openapi/v1/collaboration/bots/me");
    request.headers_mut().insert("authorization", "Bearer registered".parse().unwrap());
    let response = app().oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}
