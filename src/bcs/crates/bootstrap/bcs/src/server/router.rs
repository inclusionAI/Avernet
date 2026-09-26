//! Router middlewares: debug, metrics, http_metrics_middleware.
//!
//! Split out from server.rs as part of the V1 API auth plugin chain (Task 1) refactor. Behavior preserved exactly.

use super::*;

/// Debug middleware to log incoming HTTP requests
pub(super) async fn debug_middleware(req: Request<Body>, next: Next) -> Response {
    static DEBUG: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    let debug = *DEBUG.get_or_init(is_debug_enabled);

    if debug {
        let method = req.method();
        let uri = req.uri();
        let path = uri.path();

        // BCS_DEBUG is also the E2E endpoint-coverage signal, so health must
        // be logged together with every other registered HTTP route.
        eprintln!("\x1b[2m[→BCS] {} {}\x1b[0m", method, path);
    }

    next.run(req).await
}

pub(super) async fn metrics_handler(State(state): State<Arc<BcsServerState>>) -> Response {
    let Some(metrics) = state.metrics.clone() else {
        return StatusCode::NOT_FOUND.into_response();
    };
    metrics.refresh_on_scrape(&state).await;
    (
        [(CONTENT_TYPE, "text/plain; version=0.0.4; charset=utf-8")],
        metrics.render(),
    )
        .into_response()
}

pub(super) struct HttpRequestMetricsGuard {
    pub(super) metrics: Arc<crate::metrics::MetricsRuntime>,
    pub(super) route: String,
    pub(super) method: String,
    pub(super) start: Instant,
    pub(super) completed: bool,
}

pub(super) async fn http_metrics_middleware(
    State(state): State<Arc<BcsServerState>>,
    req: Request<Body>,
    next: Next,
) -> Response {
    let Some(metrics) = state.metrics.clone() else {
        return next.run(req).await;
    };
    if req.uri().path() == metrics.endpoint_path {
        return next.run(req).await;
    }

    let method = req.method().as_str().to_string();
    let route = req
        .extensions()
        .get::<MatchedPath>()
        .map(|matched| matched.as_str().to_string())
        .unwrap_or_else(|| "unmatched".to_string());
    let guard = HttpRequestMetricsGuard::new(metrics, route, method);
    let response = next.run(req).await;
    let status = response.status();
    guard.complete(status);
    response
}
