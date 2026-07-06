//! `GET /admin/secret/:name` — diagnostic route for verifying the secret
//! pipeline (mist in prod, env/in-memory in dev) end-to-end. Localhost-only
//! by design: the route refuses any peer whose IP is not loopback so a stray
//! reverse-proxy can't accidentally leak secrets.
//!
//! Routes go through `services.secret` (the application-layer
//! `SecretService`) rather than holding a port directly, per CLAUDE.md
//! "HTTP state exposed to route handlers must expose application services,
//! not core services or ports".

use axum::{
    extract::{ConnectInfo, Path, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use bcs_service_api::application::SecretServiceError;
use serde_json::json;
use std::net::SocketAddr;

use crate::state::HttpAppState;

pub async fn pull_secret(
    State(state): State<HttpAppState>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    Path(name): Path<String>,
) -> Response {
    let ip = addr.ip();
    if !ip.is_loopback() {
        tracing::warn!(remote = %ip, "rejecting /admin/secret request from non-loopback");
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "loopback_only" })),
        )
            .into_response();
    }

    match state.services.secret.get_secret(&name).await {
        Ok(secret) => (
            StatusCode::OK,
            Json(json!({
                "name": secret.name,
                "user": secret.user,
                "value": secret.value,
            })),
        )
            .into_response(),
        Err(err) => {
            let (code, kind) = match &err {
                SecretServiceError::NotFound(_) => (StatusCode::NOT_FOUND, "not_found"),
                SecretServiceError::Unavailable(_) => (StatusCode::SERVICE_UNAVAILABLE, "unavailable"),
                SecretServiceError::InvalidInput(_) => (StatusCode::BAD_REQUEST, "invalid_input"),
            };
            tracing::warn!(secret = %name, error = %err, "SecretService.get_secret failed");
            (
                code,
                Json(json!({ "error": kind, "message": err.to_string() })),
            )
                .into_response()
        }
    }
}
