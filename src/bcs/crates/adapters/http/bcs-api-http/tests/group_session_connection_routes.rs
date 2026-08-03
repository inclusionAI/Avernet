use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{
    PrincipalVerificationError, PrincipalVerifier, group_session_connection_router,
};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity,
    GroupSessionConnectionBinding, GroupSessionConnectionError, GroupSessionConnectionService,
    IssueGroupSessionConnectionToken, IssuedGroupSessionConnectionToken,
    VerifyGroupSessionConnectionToken,
};
use serde_json::{Value, json};
use time::OffsetDateTime;
use tower::ServiceExt;

struct HeaderVerifier {
    caller: AuthenticatedCaller,
}

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        match headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
        {
            Some("yes") => Ok(self.caller.clone()),
            Some("invalid") => Err(PrincipalVerificationError::Invalid("bad signature".into())),
            _ => Err(PrincipalVerificationError::Missing),
        }
    }
}

#[derive(Clone, Copy)]
enum ServiceMode {
    Success,
    NotFound,
    Forbidden,
    Unavailable,
}

struct FakeConnectionService {
    mode: ServiceMode,
    commands: Mutex<Vec<IssueGroupSessionConnectionToken>>,
}

impl FakeConnectionService {
    fn new(mode: ServiceMode) -> Self {
        Self {
            mode,
            commands: Mutex::new(Vec::new()),
        }
    }

    fn commands(&self) -> Vec<IssueGroupSessionConnectionToken> {
        self.commands.lock().expect("command lock").clone()
    }
}

#[async_trait]
impl GroupSessionConnectionService for FakeConnectionService {
    async fn issue_token(
        &self,
        command: IssueGroupSessionConnectionToken,
    ) -> Result<IssuedGroupSessionConnectionToken, GroupSessionConnectionError> {
        self.commands
            .lock()
            .expect("command lock")
            .push(command.clone());
        if command.caller.user.is_none() {
            return Err(ApplicationError::forbidden("Human caller required").into());
        }
        match self.mode {
            ServiceMode::Success => Ok(IssuedGroupSessionConnectionToken {
                token: "secret.jwt.value".into(),
                expires_at: OffsetDateTime::from_unix_timestamp(400)
                    .expect("valid test timestamp"),
            }),
            ServiceMode::NotFound => Err(ApplicationError::not_found(
                "session_not_found",
                "Session was not found",
            )
            .into()),
            ServiceMode::Forbidden => {
                Err(ApplicationError::forbidden("Session access denied").into())
            }
            ServiceMode::Unavailable => {
                Err(GroupSessionConnectionError::TokenServiceUnavailable)
            }
        }
    }

    async fn verify_token(
        &self,
        _command: VerifyGroupSessionConnectionToken,
    ) -> Result<GroupSessionConnectionBinding, GroupSessionConnectionError> {
        panic!("token route does not verify connection tokens")
    }
}

fn human_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: "tenant-a".into(),
        user: Some(AuthenticatedUserIdentity {
            id: "user-a".into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn request(auth: Option<&str>, uri: &str, body: Value) -> Request<Body> {
    let mut builder = Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-request-id", "request-123");
    if let Some(auth) = auth {
        builder = builder.header("x-test-auth", auth);
    }
    builder
        .body(Body::from(body.to_string()))
        .expect("request")
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

fn app(
    service: Arc<FakeConnectionService>,
    caller: AuthenticatedCaller,
) -> axum::Router {
    group_session_connection_router(service, Arc::new(HeaderVerifier { caller }))
}

#[tokio::test]
async fn returns_only_token_expiry_and_no_store_headers() {
    let service = Arc::new(FakeConnectionService::new(ServiceMode::Success));
    let response = app(service.clone(), human_caller())
        .oneshot(request(
            Some("yes"),
            "/openapi/v1/collaboration/sessions/session-a/token",
            json!({}),
        ))
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()["cache-control"], "no-store");
    assert_eq!(response.headers()["pragma"], "no-cache");
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["token"], "secret.jwt.value");
    assert_eq!(body["data"]["expires_at"], 400);
    assert_eq!(body["data"].as_object().expect("data object").len(), 2);
    assert_eq!(service.commands().len(), 1);
}

#[tokio::test]
async fn rejects_missing_or_invalid_gateway_principal_before_service_call() {
    for auth in [None, Some("invalid")] {
        let service = Arc::new(FakeConnectionService::new(ServiceMode::Success));
        let response = app(service.clone(), human_caller())
            .oneshot(request(
                auth,
                "/openapi/v1/collaboration/sessions/session-a/token",
                json!({}),
            ))
            .await
            .expect("response");

        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert!(service.commands().is_empty());
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], "unauthenticated");
    }
}

#[tokio::test]
async fn valid_principal_without_human_is_forbidden() {
    let service = Arc::new(FakeConnectionService::new(ServiceMode::Success));
    let mut caller = human_caller();
    caller.user = None;
    let response = app(service, caller)
        .oneshot(request(
            Some("yes"),
            "/openapi/v1/collaboration/sessions/session-a/token",
            json!({}),
        ))
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "forbidden");
}

#[tokio::test]
async fn preserves_session_errors_and_sanitizes_signer_failure() {
    let cases = [
        (ServiceMode::NotFound, StatusCode::NOT_FOUND, "session_not_found"),
        (ServiceMode::Forbidden, StatusCode::FORBIDDEN, "forbidden"),
        (
            ServiceMode::Unavailable,
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal_error",
        ),
    ];

    for (mode, status, error_code) in cases {
        let response = app(
            Arc::new(FakeConnectionService::new(mode)),
            human_caller(),
        )
        .oneshot(request(
            Some("yes"),
            "/openapi/v1/collaboration/sessions/session-a/token",
            json!({}),
        ))
        .await
        .expect("response");

        assert_eq!(response.status(), status);
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], error_code);
        assert!(!body.to_string().contains("secret.jwt.value"));
    }
}

#[tokio::test]
async fn ignores_request_controlled_claims_and_uses_only_path_and_principal() {
    let service = Arc::new(FakeConnectionService::new(ServiceMode::Success));
    let response = app(service.clone(), human_caller())
        .oneshot(request(
            Some("yes"),
            "/openapi/v1/collaboration/sessions/path-session/token?sid=query-session&gid=evil&uid=evil&ttl=99999",
            json!({
                "sid": "body-session",
                "gid": "evil",
                "uid": "evil",
                "ttl": 99999
            }),
        ))
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::OK);
    let commands = service.commands();
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].session_id, "path-session");
    assert_eq!(commands[0].caller.tenant, "tenant-a");
    assert_eq!(
        commands[0]
            .caller
            .user
            .as_ref()
            .expect("Human caller")
            .id,
        "user-a"
    );
}
