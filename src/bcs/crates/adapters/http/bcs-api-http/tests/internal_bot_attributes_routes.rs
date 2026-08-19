#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode};
use bcs_api_http::v1::internal::{
    InternalProviderAuthError, InternalProviderAuthenticator, router,
};
use bcs_service_api::application::v1::{
    ApplicationError, BotInternalAttributes, FriendCheckInStrategy, InternalBotAttributesService,
    PatchBotInternalAttributes, UserVisibility,
};
use serde_json::{Value, json};
use tower::ServiceExt;

struct AcceptTrustedProvider;

#[async_trait]
impl InternalProviderAuthenticator for AcceptTrustedProvider {
    async fn authenticate(
        &self,
        token: &str,
        provider_id: &str,
    ) -> Result<(), InternalProviderAuthError> {
        if token == "provider-admin-token" && provider_id == "backend-provider" {
            Ok(())
        } else {
            Err(InternalProviderAuthError::Unauthorized)
        }
    }
}

struct RejectProvider(InternalProviderAuthError);

#[async_trait]
impl InternalProviderAuthenticator for RejectProvider {
    async fn authenticate(
        &self,
        _token: &str,
        _provider_id: &str,
    ) -> Result<(), InternalProviderAuthError> {
        Err(self.0)
    }
}

#[derive(Default)]
struct FakeInternalBotAttributesService {
    patch: Mutex<Option<PatchBotInternalAttributes>>,
}

struct MissingBotService;

#[async_trait]
impl InternalBotAttributesService for MissingBotService {
    async fn get(&self, _bot_id: String) -> Result<BotInternalAttributes, ApplicationError> {
        Err(ApplicationError::not_found(
            "bot_not_found",
            "Bot was not found",
        ))
    }

    async fn patch(
        &self,
        _command: PatchBotInternalAttributes,
    ) -> Result<BotInternalAttributes, ApplicationError> {
        unreachable!("missing Bot GET test must not patch")
    }
}

#[async_trait]
impl InternalBotAttributesService for FakeInternalBotAttributesService {
    async fn get(&self, bot_id: String) -> Result<BotInternalAttributes, ApplicationError> {
        assert_eq!(bot_id, "bot-1");
        Ok(BotInternalAttributes {
            user_visibility: UserVisibility::Protected,
            friend_ext: serde_json::Map::from_iter([(
                "source".to_string(),
                Value::String("backend".to_string()),
            )]),
            friend_check_in_strategy: FriendCheckInStrategy::Approval,
        })
    }

    async fn patch(
        &self,
        command: PatchBotInternalAttributes,
    ) -> Result<BotInternalAttributes, ApplicationError> {
        *self.patch.lock().expect("patch lock") = Some(command);
        Ok(BotInternalAttributes {
            user_visibility: UserVisibility::Private,
            friend_ext: serde_json::Map::new(),
            friend_check_in_strategy: FriendCheckInStrategy::DeptFree,
        })
    }
}

#[tokio::test]
async fn internal_get_uses_provider_auth_without_gateway_principal() {
    let app = router(
        Arc::new(FakeInternalBotAttributesService::default()),
        Arc::new(AcceptTrustedProvider),
    );

    let response = app
        .oneshot(
            Request::builder()
                .uri("/internal/v1/bots/bot-1/attributes")
                .header("authorization", "Bearer provider-admin-token")
                .header("x-bcn-provider-id", "backend-provider")
                .header("x-request-id", "request-internal-get")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(
        body,
        json!({
            "code": 20000,
            "message": "OK",
            "data": {
                "user_visibility": "protected",
                "friend_ext": {"source": "backend"},
                "friend_check_in_strategy": "APPROVAL"
            },
            "request_id": "request-internal-get"
        })
    );
}

#[tokio::test]
async fn internal_patch_forwards_strict_attributes_and_empty_friend_ext_clear() {
    let service = Arc::new(FakeInternalBotAttributesService::default());
    let app = router(service.clone(), Arc::new(AcceptTrustedProvider));

    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/internal/v1/bots/bot-1/attributes")
                .header("content-type", "application/json")
                .header("authorization", "Bearer provider-admin-token")
                .header("x-bcn-provider-id", "backend-provider")
                .header("x-request-id", "request-internal-patch")
                .body(Body::from(
                    json!({
                        "user_visibility": "private",
                        "friend_ext": {},
                        "friend_check_in_strategy": "DEPT_FREE"
                    })
                    .to_string(),
                ))
                .expect("request"),
        )
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response_json(response).await["data"],
        json!({
            "user_visibility": "private",
            "friend_ext": {},
            "friend_check_in_strategy": "DEPT_FREE"
        })
    );
    let patch = service.patch.lock().expect("patch lock");
    let patch = patch.as_ref().expect("patch command");
    assert_eq!(patch.bot_id, "bot-1");
    assert_eq!(patch.user_visibility, Some(UserVisibility::Private));
    assert_eq!(patch.friend_ext, Some(serde_json::Map::new()));
    assert_eq!(
        patch.friend_check_in_strategy,
        Some(FriendCheckInStrategy::DeptFree)
    );
}

#[tokio::test]
async fn internal_patch_rejects_empty_unknown_null_and_wrong_attribute_shapes() {
    let service = Arc::new(FakeInternalBotAttributesService::default());
    let app = router(service.clone(), Arc::new(AcceptTrustedProvider));
    let cases = [
        json!({}),
        json!({"user_visibility": "public", "forged": true}),
        json!({"user_visibility": null}),
        json!({"user_visibility": "PUBLIC"}),
        json!({"friend_ext": []}),
        json!({"friend_check_in_strategy": "dept_free"}),
    ];

    for body in cases {
        let response = app
            .clone()
            .oneshot(internal_request("PATCH", body))
            .await
            .expect("invalid patch response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            response_json(response).await["data"]["error_code"],
            "invalid_request"
        );
    }
    assert!(service.patch.lock().expect("patch lock").is_none());
}

#[tokio::test]
async fn internal_auth_failures_use_envelopes_and_never_reach_the_service() {
    let cases = [
        (None, Some("backend-provider"), StatusCode::UNAUTHORIZED),
        (
            Some("Basic token"),
            Some("backend-provider"),
            StatusCode::UNAUTHORIZED,
        ),
        (
            Some("Bearer   "),
            Some("backend-provider"),
            StatusCode::UNAUTHORIZED,
        ),
        (
            Some("Bearer provider-admin-token"),
            None,
            StatusCode::FORBIDDEN,
        ),
        (
            Some("Bearer provider-admin-token"),
            Some("   "),
            StatusCode::FORBIDDEN,
        ),
    ];

    for (authorization, provider_id, expected_status) in cases {
        let service = Arc::new(FakeInternalBotAttributesService::default());
        let app = router(service, Arc::new(AcceptTrustedProvider));
        let mut request = Request::builder()
            .uri("/internal/v1/bots/bot-1/attributes")
            .header("x-request-id", "request-auth-failure");
        if let Some(value) = authorization {
            request = request.header("authorization", value);
        }
        if let Some(value) = provider_id {
            request = request.header("x-bcn-provider-id", value);
        }
        let response = app
            .oneshot(request.body(Body::empty()).expect("request"))
            .await
            .expect("auth failure response");
        assert_eq!(response.status(), expected_status);
        let body = response_json(response).await;
        assert_eq!(body["request_id"], "request-auth-failure");
        assert!(body["data"]["error_code"].is_string());
    }

    for (error, expected_status) in [
        (
            InternalProviderAuthError::Unauthorized,
            StatusCode::UNAUTHORIZED,
        ),
        (InternalProviderAuthError::Forbidden, StatusCode::FORBIDDEN),
    ] {
        let app = router(
            Arc::new(FakeInternalBotAttributesService::default()),
            Arc::new(RejectProvider(error)),
        );
        let response = app
            .oneshot(internal_request("GET", Value::Null))
            .await
            .expect("authenticator rejection response");
        assert_eq!(response.status(), expected_status);
    }
}

#[tokio::test]
async fn internal_get_maps_missing_bot_to_not_found_envelope() {
    let app = router(Arc::new(MissingBotService), Arc::new(AcceptTrustedProvider));
    let response = app
        .oneshot(internal_request("GET", Value::Null))
        .await
        .expect("missing Bot response");

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert_eq!(
        response_json(response).await["data"]["error_code"],
        "bot_not_found"
    );
}

fn internal_request(method: &str, body: Value) -> Request<Body> {
    let body = if body.is_null() {
        Body::empty()
    } else {
        Body::from(body.to_string())
    };
    Request::builder()
        .method(method)
        .uri("/internal/v1/bots/bot-1/attributes")
        .header("content-type", "application/json")
        .header("authorization", "Bearer provider-admin-token")
        .header("x-bcn-provider-id", "backend-provider")
        .header("x-request-id", "request-internal")
        .body(body)
        .expect("request")
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}
