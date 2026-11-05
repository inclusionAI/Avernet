//! Network adapters for the optional tracing hook; existing clients own policy.
use bcs_tracing_api::{Call, Kind, Outcome, RequestInfo, with_context};
use axum::extract::{MatchedPath, State};

pub use bcs_tracing_api::in_current_context;
pub use bcs_tracing_api::in_current_context_blocking;

/// Server span propagation for inner tracing layers; never rewrites ingress.
#[derive(Clone)]
pub struct HttpServerContext(pub axum::http::HeaderMap);

/// Composition supplies exact Axum route patterns, including configured probes.
/// Unknown routes and CORS preflights never create a business trace.
#[derive(Clone, Default)]
pub struct HttpServerFilter {
    excluded_routes: Vec<String>,
}

impl HttpServerFilter {
    pub fn new(excluded_routes: impl IntoIterator<Item = String>) -> Self {
        Self { excluded_routes: excluded_routes.into_iter().collect() }
    }

    fn includes(&self, request: &axum::extract::Request) -> bool {
        let Some(route) = request.extensions().get::<MatchedPath>() else { return false; };
        let preflight = request.method() == axum::http::Method::OPTIONS
            && request.headers().contains_key("origin")
            && request.headers().contains_key("access-control-request-method");
        !preflight && !self.excluded_routes.iter().any(|path| path == route.as_str())
    }
}

/// End at response headers. Do not buffer/consume streaming response bodies.
pub async fn http_server(State(filter): State<HttpServerFilter>, mut request: axum::extract::Request, next: axum::middleware::Next) -> axum::response::Response {
    if !bcs_tracing_api::is_enabled() { return next.run(request).await; }
    if !filter.includes(&request) {
        // A probe may call a traced client internally. Suppress the entire
        // excluded request so it cannot create standalone outbound records.
        return bcs_tracing_api::with_provider(None, with_context(None, next.run(request))).await;
    }
    let incoming = request.headers().iter().filter_map(|(name, value)| {
        value.to_str().ok().map(|value| (name.to_string(), value.to_owned()))
    }).collect();
    let size = request.headers().get("content-length").and_then(|v| v.to_str().ok()).and_then(|v| v.parse().ok());
    let call = Call::start(&RequestInfo {
        kind: Kind::HttpServer, method: request.method().as_str(),
        endpoint: request.uri().path(), route: request.extensions().get::<MatchedPath>().map(|route| route.as_str()),
        target_app: "", request_size: size,
    }, &incoming);
    let mut headers = axum::http::HeaderMap::new();
    for (name, value) in call.headers() {
        if let (Ok(name), Ok(value)) = (axum::http::HeaderName::from_bytes(name.as_bytes()), axum::http::HeaderValue::from_str(value)) {
            headers.insert(name, value);
        }
    }
    request.extensions_mut().insert(HttpServerContext(headers));
    let response = with_context(call.context(), next.run(request)).await;
    let size = response.headers().get("content-length").and_then(|v| v.to_str().ok()).and_then(|v| v.parse().ok());
    call.finish(Outcome::Http(response.status().as_u16()), size);
    response
}

mod http_client;
pub use http_client::{ClientExt, RequestBuilderExt};
mod default_headers;
pub use default_headers::{ClientWithDefaults, RequestWithDefaults};
#[cfg(feature = "blocking")]
mod blocking;
#[cfg(feature = "blocking")]
pub use blocking::BlockingRequestBuilderExt;
