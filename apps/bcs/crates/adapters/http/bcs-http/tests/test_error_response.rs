use axum::body::to_bytes;
use axum::response::IntoResponse;
use bcs_http::error::HttpAdapterError;
use bcs_service_api::ServiceError;
use futures::FutureExt; // for now_or_never()

/// Helper: extract response body as serde_json::Value.
fn body_json(response: axum::response::Response) -> serde_json::Value {
    let body_bytes = to_bytes(response.into_body(), usize::MAX)
        .now_or_never()
        .expect("body read")
        .expect("body ok");
    serde_json::from_slice(&body_bytes).expect("valid json")
}

#[test]
fn test_service_error_bot_not_found_json() {
    let err = HttpAdapterError::Service(ServiceError::BotNotFound("alice".into()));
    let response = err.into_response();
    assert_eq!(response.status().as_u16(), 404);
    let body = body_json(response);
    assert_eq!(body["status"], 404);
    assert_eq!(body["code"], "bot_not_found");
    assert_eq!(body["params"]["bot_id"], "alice");
    assert_eq!(body["message"], "Bot 'alice' not found");
    assert_eq!(body["error"], "Bot 'alice' not found");
}

#[test]
fn test_gone_error_json() {
    let err = HttpAdapterError::Gone("invite link has expired".into());
    let response = err.into_response();
    assert_eq!(response.status().as_u16(), 410);
    let body = body_json(response);
    assert_eq!(body["status"], 410);
    assert_eq!(body["code"], "gone");
    assert_eq!(body["params"]["reason"], "invite link has expired");
    assert_eq!(body["message"], "invite link has expired");
    assert_eq!(body["error"], "invite link has expired");
}

#[test]
fn test_bad_request_json() {
    let err = HttpAdapterError::BadRequest("missing field 'name'".into());
    let response = err.into_response();
    assert_eq!(response.status().as_u16(), 400);
    let body = body_json(response);
    assert_eq!(body["status"], 400);
    assert_eq!(body["code"], "bad_request");
    assert_eq!(body["params"]["reason"], "missing field 'name'");
}

#[test]
fn test_unauthorized_json() {
    let err = HttpAdapterError::Unauthorized("no valid token".into());
    let response = err.into_response();
    assert_eq!(response.status().as_u16(), 401);
    let body = body_json(response);
    assert_eq!(body["code"], "unauthorized");
    assert_eq!(body["params"]["reason"], "no valid token");
}

#[test]
fn test_service_unauthorized_json() {
    let err = HttpAdapterError::Service(ServiceError::Unauthorized("bad credentials".into()));
    let response = err.into_response();
    assert_eq!(response.status().as_u16(), 401);
    let body = body_json(response);
    assert_eq!(body["code"], "unauthorized");
}

#[test]
fn test_service_internal_error_code() {
    let err = HttpAdapterError::Service(ServiceError::InternalError("db connection failed".into()));
    let response = err.into_response();
    assert_eq!(response.status().as_u16(), 500);
    let body = body_json(response);
    assert_eq!(body["code"], "internal_error");
    assert_eq!(body["params"]["reason"], "db connection failed");
}

#[test]
fn test_service_io_error_code_is_internal_with_null_params() {
    let io_err = std::io::Error::new(std::io::ErrorKind::NotFound, "secret file /tmp/x");
    let service_err = ServiceError::from(io_err);
    let err = HttpAdapterError::Service(service_err);
    let response = err.into_response();
    assert_eq!(response.status().as_u16(), 500);
    let body = body_json(response);
    assert_eq!(body["code"], "internal_error");
    assert_eq!(body["params"], serde_json::Value::Null);
}

#[test]
fn test_resolved_code_never_returns_delegated() {
    assert_eq!(
        HttpAdapterError::BadRequest("x".into()).as_ref(),
        "bad_request"
    );
    assert_eq!(
        HttpAdapterError::Conflict("x".into()).as_ref(),
        "conflict"
    );
    assert_eq!(
        HttpAdapterError::Gone("x".into()).as_ref(),
        "gone"
    );
}

// ---------------------------------------------------------------------------
// Final review (Finding 2): the legacy `/register` route's 500 bodies are
// sanitized — fixed generic text client-side, the full cause server-side.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod register_sanitization {
    use axum::{
        body::Body,
        http::{Request, StatusCode},
        routing::{get, post},
        Router,
    };
    use bcs_domain::{RegisterTokenPayload, register_token_encode};
    use bcs_services_container::Services;
    use bcs_http::state::HttpAppState;
    use futures::FutureExt;
    use std::io::Write;
    use std::sync::{Arc, Mutex};
    use tower::ServiceExt;
    use tracing::instrument::WithSubscriber;

    fn body_string(response: axum::response::Response) -> String {
        let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
            .now_or_never()
            .expect("body read")
            .expect("body ok");
        String::from_utf8(bytes.to_vec()).expect("UTF-8 body")
    }

    #[derive(Clone, Default)]
    struct LogBuffer(Arc<Mutex<Vec<u8>>>);

    impl Write for LogBuffer {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.0.lock().unwrap().extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    fn secret() -> Vec<u8> {
        b"register-sanitization-secret-01".to_vec()
    }

    fn app() -> Router {
        // Noop `bot_management` (the test-container default) fails
        // connect_bot with an internal diagnostic — the exact 500 lane the
        // sanitization must cover.
        let state = HttpAppState::new(Services::builder().build_for_test())
            .with_invite_config(secret(), 86400, None, None, None);
        Router::new()
            .route(
                "/register",
                post(bcs_http::routes::register::register_bot),
            )
            .route(
                "/register/token",
                get(bcs_http::routes::register::get_register_token),
            )
            .with_state(state)
    }

    fn valid_token() -> String {
        let exp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
            + 3600;
        register_token_encode(
            &RegisterTokenPayload {
                v: 1,
                id: "human_staff-9".to_string(),
                exp,
            },
            &secret(),
        )
    }

    #[tokio::test]
    async fn register_connect_failure_returns_a_sanitized_body_with_the_cause_logged() {
        let buffer = LogBuffer::default();
        let writer = buffer.clone();
        let subscriber = tracing_subscriber::fmt().json()
            .with_writer(move || writer.clone())
            .finish();

        let app = app();
        async {
            let response = app
                .clone()
                .oneshot(
                    Request::builder()
                        .method("POST")
                        .uri(format!("/register?token={}&bot-name=tester", valid_token()))
                        .body(Body::empty())
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(response.status().as_u16(), StatusCode::INTERNAL_SERVER_ERROR);
            let body = body_string(response);
            assert_eq!(
                body,
                r#"{"error":"internal","message":"bot connect failed"}"#,
                "the client body is a fixed generic text"
            );
            assert!(
                !body.contains("bot management service is not configured"),
                "the raw internal diagnostic must not reach the client"
            );
        }
        .with_subscriber(subscriber)
        .await;

        let text = String::from_utf8(buffer.0.lock().unwrap().clone())
            .expect("UTF-8 log output");
        assert!(
            text.contains("bot management service is not configured"),
            "the full cause stays in the server-side log"
        );
    }
}
