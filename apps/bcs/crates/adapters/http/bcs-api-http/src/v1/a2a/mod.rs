//! V1 A2A registration and Card management. BCS validates Provider-admin credentials;
//! the remote Agent's A2A protocol is not served by this adapter.
mod management;
mod transport;

use super::common::ApiState;
use axum::{
    Router,
    extract::DefaultBodyLimit,
    middleware,
    routing::post,
};
use bcs_service_api::application::v1::{ApplicationError, a2a::A2aService};
use std::sync::Arc;

pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/api/v1/collaboration/a2a/agents", axum::routing::any(management::handle))
        .route("/api/v1/collaboration/a2a/agents/{bot_id}/agent-card", axum::routing::any(management::handle))
        .route("/api/v1/collaboration/a2a/agents/{bot_id}/agent-card:refresh", axum::routing::any(management::handle))
        .route("/api/v1/collaboration/a2a/providers/{provider_name}/bots", post(management::register_named))
        .layer(DefaultBodyLimit::max(2 * 1024 * 1024))
        .layer(middleware::from_fn(transport::boundary))
}

fn service(state: &ApiState) -> Result<&Arc<dyn A2aService>, ApplicationError> {
    state.a2a_service.as_ref().ok_or_else(|| {
        ApplicationError::unavailable("a2a_service_unavailable", "A2A service is not configured")
    })
}
