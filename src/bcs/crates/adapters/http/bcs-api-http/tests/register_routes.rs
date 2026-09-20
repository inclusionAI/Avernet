use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use bcs_service_api::application::v1::{
    ApplicationError, BotRegistration, IssueRegisterToken,
    RegisterBot, RegisterService, RegisterTokenView,
};
use serde_json::json;
use tower::ServiceExt;

mod register_support;
use register_support::*;

// ---------------------------------------------------------------------------
// Fake register service.
// ---------------------------------------------------------------------------

struct FakeRegisterService {
    issued: Mutex<Vec<IssueRegisterToken>>,
    registered: Mutex<Vec<RegisterBot>>,
    /// when true, issue/register return a forbidden/unauthenticated error
    reject: bool,
}

#[async_trait]
impl RegisterService for FakeRegisterService {
    async fn issue_register_token(
        &self,
        command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        if self.reject {
            return Err(ApplicationError::forbidden("no human principal"));
        }
        self.issued.lock().expect("issued lock").push(command);
        Ok(RegisterTokenView {
            token: "reg-token-1".to_string(),
            expires_at: 123456,
            note: "Use this token for bot registration within 6 hours".to_string(),
            registration: None,
        })
    }

    async fn register_bot(
        &self,
        command: RegisterBot,
    ) -> Result<BotRegistration, ApplicationError> {
        if self.reject {
            return Err(ApplicationError::Unauthenticated);
        }
        self.registered.lock().expect("registered lock").push(command.clone());
        Ok(BotRegistration {
            bot_name: command.bot_name,
            bot_uuid: "bot-new-1".to_string(),
            bot_token: "bot-token-new-1".to_string(),
            registration: None,
        })
    }
}

fn fake_service(reject: bool) -> Arc<FakeRegisterService> {
    Arc::new(FakeRegisterService {
        issued: Mutex::new(Vec::new()),
        registered: Mutex::new(Vec::new()),
        reject,
    })
}

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn get_register_token_returns_envelope_with_token_data() {
    let service = fake_service(false);
    let app = test_router(service.clone());
    let response = app
        .oneshot(authenticated_request("GET", "/openapi/v1/collaboration/register/token", json!({})))
        .await
        .expect("token issue response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers().get("cache-control").and_then(|v| v.to_str().ok()), Some("no-store"));
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["request_id"], "request-123");
    assert_eq!(body["data"]["token"], "reg-token-1");
    assert_eq!(body["data"]["expires_at"], 123456);
    assert_eq!(body["data"].as_object().unwrap().len(), 3);
    let issued = service.issued.lock().expect("issued lock");
    assert_eq!(issued[0].caller.user.as_ref().expect("human").id, "staff-1");
}

#[tokio::test]
async fn get_register_token_maps_application_403() {
    // reject=true makes the fake return ApplicationError::Forbidden
    let service = fake_service(true);
    let app = test_router(service);
    let response = app
        .oneshot(authenticated_request("GET", "/openapi/v1/collaboration/register/token", json!({})))
        .await
        .expect("forbidden response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["code"], 40_300);
    assert_eq!(body["data"]["error_code"], "forbidden");
}

#[tokio::test]
async fn post_register_is_anonymous_and_returns_created_envelope() {
    let service = fake_service(false);
    let app = test_router(service.clone());
    // NO x-test-auth header — the public router must not require a principal.
    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/openapi/v1/collaboration/register?token=abc123&bot-name=%E6%B5%8B%E8%AF%95%E6%9C%BA%E5%99%A8%E4%BA%BA")
                .header("x-request-id", "request-123")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("register response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_100);
    assert_eq!(body["data"]["bot_name"], "测试机器人");
    assert_eq!(body["data"]["bot_uuid"], "bot-new-1");
    assert_eq!(body["data"]["bot_token"], "bot-token-new-1");
    assert_eq!(body["data"].as_object().unwrap().len(), 3);
    assert!(body["data"].get("registration").is_none());
    let registered = service.registered.lock().expect("registered lock");
    assert_eq!(registered[0].token, "abc123");
    assert_eq!(registered[0].bot_name, "测试机器人");
}

#[tokio::test]
async fn post_register_rejects_missing_token_and_name() {
    let service = fake_service(false);
    let app = test_router(service);
    let response = app
        .clone()
        .oneshot(bare_request("POST", "/openapi/v1/collaboration/register"))
        .await
        .expect("missing params response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["code"], 40_000);

    let response = app
        .oneshot(bare_request("POST", "/openapi/v1/collaboration/register?token=abc123"))
        .await
        .expect("missing name response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn post_register_maps_unauthenticated_token_failures() {
    let service = fake_service(true);
    let app = test_router(service);
    let response = app
        .oneshot(bare_request("POST", "/openapi/v1/collaboration/register?token=abc123&bot_name=tester"))
        .await
        .expect("401 response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 40_100);
}

#[tokio::test]
async fn provider_query_is_forwarded_at_issuance() {
    let service = fake_service(false);
    let response = test_router(service.clone()).oneshot(authenticated_request(
        "GET", "/openapi/v1/collaboration/register/token?provider_id=provider-a", json!({}),
    )).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(service.issued.lock().unwrap()[0].provider_id.as_deref(), Some("provider-a"));
}

#[tokio::test]
async fn gateway_query_accepts_optional_webhook_and_preserves_name_aliases() {
    use bcs_domain::provider_registration_token::ProviderRegistrationMode;
    for name_key in ["bot-name", "bot_name"] {
        for webhook in ["", "&webhook_url=https%3A%2F%2Fexample.test%2Fhook"] {
            let service = fake_service(false);
            let response = test_router(service.clone()).oneshot(bare_request("POST", &format!(
                "/openapi/v1/collaboration/register?token=capability&{name_key}=Test%20Bot&mode=gateway&provider_bot_ref=ref-1{webhook}&owner=attacker&provider_id=other"
            ))).await.unwrap();
            assert_eq!(response.status(), StatusCode::CREATED);
            assert_eq!(response.headers().get("cache-control").and_then(|v| v.to_str().ok()), Some("no-store"));
            let commands = service.registered.lock().unwrap();
            assert_eq!(commands[0].mode, Some(ProviderRegistrationMode::Gateway));
            assert_eq!(commands[0].provider_bot_ref.as_deref(), Some("ref-1"));
            assert_eq!(commands[0].webhook_url.as_deref(), if webhook.is_empty() { None } else { Some("https://example.test/hook") });
        }
    }
}

#[tokio::test]
async fn unknown_and_empty_modes_are_bad_requests_without_echoing_capabilities() {
    for mode in ["", "unknown", "Gateway", "secret-capability-value"] {
        let service = fake_service(false);
        let response = test_router(service.clone()).oneshot(bare_request("POST", &format!(
            "/openapi/v1/collaboration/register?token=secret-register-token&bot_name=Test&mode={mode}"
        ))).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let body = response_json(response).await;
        assert_eq!(body["code"], 40_000);
        assert!(!body.to_string().contains("secret"));
        assert!(service.registered.lock().unwrap().is_empty());
    }
}
