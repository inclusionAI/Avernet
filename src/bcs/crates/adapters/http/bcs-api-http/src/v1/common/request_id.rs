use axum::http::HeaderMap;

#[derive(Debug, Clone)]
pub struct RequestId(pub String);

impl RequestId {
    pub fn from_headers(headers: &HeaderMap) -> Self {
        let request_id = headers
            .get("x-request-id")
            .and_then(|value| value.to_str().ok())
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| ulid::Ulid::new().to_string());
        Self(request_id)
    }
}
