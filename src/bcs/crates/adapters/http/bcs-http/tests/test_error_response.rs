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