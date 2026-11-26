//! REST resource-path encoding and bounded Task snapshots. Models are SDK types.
use crate::digest::{
    DigestGuard, RESULT_SERVER_OR_PARSE_ERROR, RESULT_TIMEOUT,
    classify_reqwest_error, classify_rest_http_error,
};
use crate::http_client::HttpClient;
use a2a::{A2AError, Task, TypedDetail};

pub(super) fn task_url(base: &str, id: &str, suffix: &str) -> Result<String, A2AError> {
    if id.is_empty() || id == "." || id == ".." { return Err(A2AError::invalid_params("Task ID is not addressable as an HTTP resource")); }
    let id = percent_encoding::utf8_percent_encode(id, percent_encoding::NON_ALPHANUMERIC);
    Ok(format!("{}/tasks/{id}{suffix}", base.trim_end_matches('/')))
}

pub(super) async fn task_response(http: &HttpClient, request: bcs_network_tracing::RequestWithDefaults<reqwest::RequestBuilder>, guard: DigestGuard) -> Result<Task, A2AError> {
    json_response(http, request, guard).await
}

pub(super) async fn json_response<T:serde::de::DeserializeOwned>(http: &HttpClient, request: bcs_network_tracing::RequestWithDefaults<reqwest::RequestBuilder>, mut guard: DigestGuard) -> Result<T, A2AError> {
    match tokio::time::timeout(std::time::Duration::from_secs(15), async {
        let request = request.map(|request| request.header("A2A-Version", "1.0"));
        let mut response = http.send_observed(request, "A2A Task request interrupted", &mut guard).await?;
        let status = response.status();
        guard.record_headers(status.as_u16());
        let mut body = Vec::new();
        loop {
            match response.chunk().await {
                Ok(Some(chunk)) => {
                    guard.add_resp_bytes(chunk.len());
                    if body.len().saturating_add(chunk.len()) > 8 * 1024 * 1024 {
                        guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "SIZE_LIMIT");
                        return Err(A2AError::invalid_request("Task snapshot exceeds size limit"));
                    }
                    body.extend_from_slice(&chunk);
                }
                Ok(None) => break,
                Err(err) => {
                    let (rc, ec) = classify_reqwest_error(&err);
                    guard.finish_err(rc, ec);
                    return Err(A2AError::internal("A2A Task response incomplete"));
                }
            }
        }
        if !status.is_success() {
            let err = http_error(status.as_u16(), &body);
            let (rc, ec) = classify_rest_http_error(status.as_u16(), &err);
            guard.finish_err(rc, &ec);
            return Err(err);
        }
        match serde_json::from_slice(&body) {
            Ok(parsed) => {
                guard.finish_ok();
                Ok(parsed)
            }
            Err(_) => {
                guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "PARSE_ERROR");
                Err(A2AError::parse_error("invalid Task snapshot"))
            }
        }
    }).await {
        Ok(result) => result,
        Err(_) => {
            guard.finish_err(RESULT_TIMEOUT, "TIMEOUT");
            Err(A2AError::internal("A2A Task request timed out"))
        }
    }
}

/// ProtoJSON Status wraps details in `error`; accept the SDK's legacy flat
/// shape too. Only typed protocol ErrorInfo determines the protocol reason.
pub(super) fn http_error(status: u16, body: &[u8]) -> A2AError {
    let problem: serde_json::Value = serde_json::from_slice(body).unwrap_or(serde_json::Value::Null);
    let status_object = problem.get("error").filter(|value| value.is_object()).unwrap_or(&problem);
    let details: Vec<TypedDetail> = status_object.get("details").and_then(|value| value.as_array())
        .into_iter().flatten().filter(|value| value.get("@type").and_then(serde_json::Value::as_str).is_some_and(|kind| !kind.is_empty()))
        .filter_map(|value| serde_json::from_value(value.clone()).ok()).collect();
    let code = details.iter().filter(|detail| detail.type_url == "type.googleapis.com/google.rpc.ErrorInfo")
        .filter_map(|detail| detail.value.get("reason").and_then(serde_json::Value::as_str))
        .find_map(a2a::reason_to_error_code).unwrap_or(a2a::error_code::INTERNAL_ERROR);
    // Keep untrusted response bodies out of error strings and diagnostics;
    // typed details suffice for callers to distinguish protocol failures.
    A2AError::new(code, format!("A2A HTTP {status}")).with_details(details)
}
