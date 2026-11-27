//! Response diagnostics for the scoped pre-release A2A submission.
use reqwest::header::{HeaderMap, HeaderValue};
use serde_json::{Map, Value};

const MAX_BODY_BYTES: u64 = 512;
const DIAGNOSTIC_FIELDS: &[&str] = &[
    "success", "code", "message", "msg", "reason", "errorCode", "errorMessage", "errorMsg",
    "error_code", "error_message", "error_description", "sub_code", "sub_msg",
    "resultCode", "resultMsg", "traceId", "requestId",
];

pub(super) fn submission_failure(error: &a2a::A2AError) -> &'static str {
    if crate::http_client::identity_failure(error).is_some() {
        return "identity_not_sent";
    }
    match error.message.as_str() {
        crate::transport_error::NOT_CONNECTED => "connection_not_established",
        "A2A subscription connection failed" => "request_interrupted",
        "A2A subscription header timeout" => "response_header_timeout",
        "A2A error response incomplete" | "RPC error response incomplete"
        | "A2A subscription interrupted" => "body_read_error",
        "A2A error response timed out" | "RPC error response timed out" => "body_read_timeout",
        "submission ended before first Task" => "missing_first_event",
        "A2A subscription ended mid-event" => "incomplete_sse_event",
        "A2A JSON-RPC failure" => "jsonrpc_error",
        _ if error.code == a2a::error_code::INVALID_AGENT_RESPONSE => "invalid_agent_response",
        _ if error.code == a2a::error_code::PARSE_ERROR => "invalid_sse_event",
        _ => "a2a_error",
    }
}

#[derive(Clone)]
pub(super) struct SensitiveRequestValues(Vec<HeaderValue>);

impl SensitiveRequestValues {
    pub(super) fn from_headers(headers: &HeaderMap) -> Self {
        Self(headers.values().filter(|value| value.is_sensitive()).cloned().collect())
    }
}

pub(super) struct Diagnostic {
    declared_bytes: Option<u64>,
    body_bytes_seen: usize,
    preview: Vec<u8>,
    sensitive: Option<SensitiveRequestValues>,
    http_status: u16,
    content_type: &'static str,
}

impl Diagnostic {
    pub(super) fn from_response(
        response: &mut reqwest::Response, content_type: &'static str,
    ) -> Option<Self> {
        let sensitive = response.extensions_mut().remove::<SensitiveRequestValues>()?;
        Some(Self {
            declared_bytes: response.content_length(),
            body_bytes_seen: 0,
            preview: Vec::new(),
            sensitive: Some(sensitive),
            http_status: response.status().as_u16(),
            content_type,
        })
    }

    pub(super) fn preview_budget(&self) -> usize {
        self.declared_bytes.unwrap_or(MAX_BODY_BYTES + 1).min(MAX_BODY_BYTES + 1) as usize
    }

    pub(super) fn observe(&mut self, chunk: &[u8]) {
        if self.sensitive.is_none() {
            return;
        }
        self.body_bytes_seen = self.body_bytes_seen.saturating_add(chunk.len());
        let remaining = MAX_BODY_BYTES as usize - self.preview.len();
        self.preview.extend_from_slice(&chunk[..chunk.len().min(remaining)]);
        if self.body_bytes_seen > MAX_BODY_BYTES as usize {
            self.finish("preview_limit");
        } else if self.declared_bytes == Some(self.body_bytes_seen as u64) {
            self.finish("complete");
        }
    }

    pub(super) fn finish(&mut self, body_state: &'static str) {
        let Some(sensitive) = self.sensitive.take() else { return; };
        let (body_format, fields) = if body_state == "complete" {
            diagnostic_fields(&self.preview, &sensitive)
        } else {
            ("unavailable", Value::Null)
        };
        tracing::warn!(
            log_class = "business_key",
            event_code = "a2a.test_response",
            http_status = self.http_status,
            content_type = self.content_type,
            declared_body_bytes = self.declared_bytes,
            body_bytes_seen = self.body_bytes_seen,
            body_state,
            body_format,
            response_fields = %fields,
            "A2A test submission received an HTTP response"
        );
    }
}

impl Drop for Diagnostic {
    fn drop(&mut self) {
        // Covers cancellation or a deadline while waiting for the first reply.
        let state = if self.declared_bytes == Some(0) { "complete" } else { "body_interrupted" };
        self.finish(state);
    }
}

fn diagnostic_fields(body: &[u8], sensitive: &SensitiveRequestValues) -> (&'static str, Value) {
    let (format, payload) = if let Ok(value) = serde_json::from_slice::<Value>(body) {
        let format = if value.get("jsonrpc").and_then(Value::as_str) == Some("2.0") {
            "jsonrpc"
        } else {
            "json"
        };
        (format, value)
    } else if body.is_empty() {
        return ("empty", Value::Null);
    } else if let Ok(text) = std::str::from_utf8(body) {
        let data = text.lines().find_map(|line| line.strip_prefix("data:"));
        match data.and_then(|line| serde_json::from_str(line.trim()).ok()) {
            Some(value) => ("sse_json", value),
            None => return ("unstructured", Value::Null),
        }
    } else {
        return ("non_utf8", Value::Null);
    };
    let mut fields = select_fields(&payload, sensitive);
    for name in ["error", "error_response"] {
        let Some(error) = payload.get(name) else { continue };
        let error = if error.is_object() {
            Value::Object(select_fields(error, sensitive))
        } else {
            diagnostic_scalar(error, sensitive).unwrap_or(Value::Null)
        };
        fields.insert(name.into(), error);
    }
    (format, Value::Object(fields))
}

fn select_fields(payload: &Value, sensitive: &SensitiveRequestValues) -> Map<String, Value> {
    DIAGNOSTIC_FIELDS.iter().filter_map(|&name| {
        diagnostic_scalar(payload.get(name)?, sensitive).map(|value| (name.into(), value))
    }).collect()
}

fn diagnostic_scalar(value: &Value, sensitive: &SensitiveRequestValues) -> Option<Value> {
    let text = match value {
        Value::String(text) => std::borrow::Cow::Borrowed(text.as_str()),
        Value::Number(number) => std::borrow::Cow::Owned(number.to_string()),
        Value::Bool(_) => return Some(value.clone()),
        _ => return None,
    };
    // COSEC: Gateway diagnostics can echo the test user or signed credentials.
    if sensitive.0.iter().filter_map(|value| value.to_str().ok())
        .any(|secret| !secret.is_empty() && text.contains(secret))
    {
        return Some(Value::String("[redacted]".into()));
    }
    Some(value.clone())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn retains_gateway_errors_with_echoed_sensitive_values_removed() {
        let mut headers = HeaderMap::new();
        let mut credential = HeaderValue::from_static("fake");
        credential.set_sensitive(true);
        headers.insert("x-identity", credential);
        let mut user = HeaderValue::from_static("12345678");
        user.set_sensitive(true);
        headers.insert("x-test-user", user);
        let sensitive = SensitiveRequestValues::from_headers(&headers);

        let body = br#"{"success":false,"code":"ACCESS_DENIED","message":"user 12345678 denied","error":{"code":-32000,"message":"bad fake","data":{"secret":"ignored"}},"data":{"token":"ignored"}}"#;
        let expected = json!({
            "success": false, "code": "ACCESS_DENIED", "message": "[redacted]",
            "error": {"code": -32000, "message": "[redacted]"}
        });
        assert_eq!(diagnostic_fields(body, &sensitive), ("json", expected.clone()));
        let sse = format!("data: {}\n\n", std::str::from_utf8(body).unwrap());
        assert_eq!(diagnostic_fields(sse.as_bytes(), &sensitive), ("sse_json", expected));
        assert_eq!(diagnostic_fields(b"unstructured diagnostic", &sensitive), ("unstructured", Value::Null));
    }
}
