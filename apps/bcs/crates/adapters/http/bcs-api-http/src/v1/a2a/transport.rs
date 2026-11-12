use crate::v1::common::{Envelope, RequestId, application_error_response};
use axum::{
    Json,
    extract::Request,
    http::{HeaderMap, HeaderValue, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
};
use bcs_service_api::application::v1::{ApplicationError, a2a::parse_card_json};
use serde::de::DeserializeOwned;
use serde_json::json;

pub(super) fn bearer(headers: &HeaderMap) -> Result<String, ApplicationError> {
    let mut values = headers.get_all("authorization").iter();
    let value = values
        .next()
        .and_then(|v| v.to_str().ok())
        .ok_or(ApplicationError::Unauthenticated)?;
    let (scheme, token) = value
        .split_once(' ')
        .ok_or(ApplicationError::Unauthenticated)?;
    if values.next().is_some()
        || !scheme.eq_ignore_ascii_case("Bearer")
        || token.is_empty()
        || token.bytes().any(|b| b.is_ascii_whitespace() || b == b',')
    {
        return Err(ApplicationError::Unauthenticated);
    }
    Ok(token.into())
}

pub(super) fn body<T: DeserializeOwned>(
    headers: &HeaderMap,
    bytes: &[u8],
) -> Result<T, ApplicationError> {
    if !headers
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.split(';').next())
        .is_some_and(|v| v.trim().eq_ignore_ascii_case("application/json"))
    {
        return Err(ApplicationError::invalid(
            "invalid_request",
            "application/json is required",
        ));
    }
    let value = parse_card_json(bytes).map_err(|_| {
        ApplicationError::invalid("invalid_request", "Invalid or duplicate JSON fields")
    })?;
    if ["agent_card_url", "agent_card", "summary"]
        .iter()
        .any(|key| value.get(key).is_some_and(|v| v.is_null()))
    {
        return Err(ApplicationError::invalid(
            "invalid_request",
            "Explicit null is not allowed for Card source or summary",
        ));
    }
    serde_json::from_value(value)
        .map_err(|_| ApplicationError::invalid("invalid_request", "Invalid request fields"))
}

pub(super) fn reply(
    id: &RequestId,
    status: StatusCode,
    result: Result<serde_json::Value, ApplicationError>,
) -> Response {
    match result {
        Ok(value) => (
            status,
            Json(Envelope::success(
                u32::from(status.as_u16()) * 100,
                status.canonical_reason().unwrap_or("OK"),
                value,
                id.0.clone(),
            )),
        )
            .into_response(),
        Err(error) => application_error_response(id, error).into_response(),
    }
}

pub(super) fn protocol_error(id: &RequestId, status: StatusCode, code: &str) -> Response {
    (
        status,
        Json(Envelope::success(
            u32::from(status.as_u16()) * 100,
            status.canonical_reason().unwrap_or("Request failed"),
            json!({"error_code":code}),
            id.0.clone(),
        )),
    )
        .into_response()
}

pub(super) async fn boundary(request: Request, next: Next) -> Response {
    let id = RequestId::from_headers(request.headers());
    let mut response = if accepts_json(request.headers()) {
        next.run(request).await
    } else {
        protocol_error(&id, StatusCode::NOT_ACCEPTABLE, "not_acceptable")
    };
    // Framework extraction/method failures must use the same V1 error envelope.
    if response.status().is_client_error()
        && !response
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .is_some_and(|v| v.starts_with("application/json"))
    {
        let status = response.status();
        let allow = response.headers().get("allow").cloned();
        let code = if status == StatusCode::METHOD_NOT_ALLOWED {
            "method_not_allowed"
        } else if status == StatusCode::PAYLOAD_TOO_LARGE {
            "request_too_large"
        } else {
            "invalid_request"
        };
        response = protocol_error(&id, status, code);
        if let Some(allow) = allow {
            response.headers_mut().insert("allow", allow);
        }
    }
    response
        .headers_mut()
        .insert("cache-control", HeaderValue::from_static("no-store"));
    if response.status() == StatusCode::UNAUTHORIZED {
        response
            .headers_mut()
            .insert("www-authenticate", HeaderValue::from_static("Bearer"));
    }
    if response.status() == StatusCode::TOO_MANY_REQUESTS {
        response
            .headers_mut()
            .insert("retry-after", HeaderValue::from_static("1"));
    }
    if let Ok(value) = HeaderValue::from_str(&id.0) {
        response.headers_mut().insert("x-request-id", value);
    }
    response
}

fn accepts_json(headers: &HeaderMap) -> bool {
    if !headers.contains_key("accept") {
        return true;
    }
    let mut best: Option<(u8, f32)> = None;
    for value in headers.get_all("accept") {
        let Ok(value) = value.to_str() else {
            return false;
        };
        for part in value.split(',') {
            let mut fields = part.split(';');
            let specificity = match fields
                .next()
                .unwrap_or("")
                .trim()
                .to_ascii_lowercase()
                .as_str()
            {
                "application/json" => 2,
                "application/*" => 1,
                "*/*" => 0,
                _ => continue,
            };
            let mut q = 1.0;
            for field in fields {
                if let Some((key, value)) = field.trim().split_once('=') {
                    if key.eq_ignore_ascii_case("q") {
                        q = value
                            .parse::<f32>()
                            .ok()
                            .filter(|v| (0.0..=1.0).contains(v))
                            .unwrap_or(0.0);
                    }
                }
            }
            if best.is_none_or(|(s, _)| specificity > s) {
                best = Some((specificity, q));
            }
        }
    }
    best.is_some_and(|(_, q)| q > 0.0)
}
