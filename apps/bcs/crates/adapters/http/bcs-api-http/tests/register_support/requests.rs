use axum::body::{Body, to_bytes};
use axum::http::Request;
use serde_json::Value;

pub(crate) fn bare_request(method: &str, uri: &str) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("x-request-id", "request-123")
        .body(Body::empty())
        .expect("request")
}

pub(crate) async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}
