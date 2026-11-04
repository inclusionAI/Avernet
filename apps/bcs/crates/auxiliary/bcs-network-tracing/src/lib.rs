//! Network adapters for the optional tracing hook; existing clients own policy.
use bcs_tracing_api::{Call, Kind, Outcome, RequestInfo, with_context};

pub use bcs_tracing_api::in_current_context;
pub use bcs_tracing_api::in_current_context_blocking;

/// End at response headers. Do not buffer/consume streaming response bodies.
pub async fn http_server(request: axum::extract::Request, next: axum::middleware::Next) -> axum::response::Response {
    if !bcs_tracing_api::is_enabled() { return next.run(request).await; }
    let incoming = request.headers().iter().filter_map(|(name, value)| {
        value.to_str().ok().map(|value| (name.to_string(), value.to_owned()))
    }).collect();
    let size = request.headers().get("content-length").and_then(|v| v.to_str().ok()).and_then(|v| v.parse().ok());
    let call = Call::start(&RequestInfo {
        kind: Kind::HttpServer, method: request.method().as_str(),
        endpoint: request.uri().path(), target_app: "", request_size: size,
    }, &incoming);
    let response = with_context(call.context(), next.run(request)).await;
    let size = response.headers().get("content-length").and_then(|v| v.to_str().ok()).and_then(|v| v.parse().ok());
    call.finish(Outcome::Http(response.status().as_u16()), size);
    response
}

mod http_client;
pub use http_client::{ClientExt, RequestBuilderExt};
#[cfg(feature = "blocking")]
mod blocking;
#[cfg(feature = "blocking")]
pub use blocking::BlockingRequestBuilderExt;
