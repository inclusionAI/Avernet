//! Worker peer routes. Mount only behind authentication which installs the
//! verified exact node/incarnation as an Instance extension. Body/header identity
//! alone is never authentication; absent middleware identity is rejected.
use axum::{
    Extension, Json, Router,
    extract::{DefaultBodyLimit, State},
    http::StatusCode,
    routing::post,
};
use bcs_dist_model::{Instance, RequestId, ShardId};
use bcs_service_api::application::dist_runtime::{
    DistWorkerRuntimeService, HandoffOutcome, HandoffRequest, ShardClaim,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
#[derive(Serialize, Deserialize)]
pub struct HandoffDoneRequest {
    pub source: Instance,
    pub target: ShardClaim,
    pub request_id: RequestId,
}
#[derive(Serialize, Deserialize)]
pub struct WakeRequest {
    pub target: Instance,
    pub shard: ShardId,
}
type Service = State<Arc<dyn DistWorkerRuntimeService>>;
fn caller(peer: Option<Extension<Instance>>) -> Result<Instance, StatusCode> {
    peer.map(|x| x.0).ok_or(StatusCode::UNAUTHORIZED)
}
/// Explicit composition only: no default mount or fallback authentication.
pub fn worker_router(service: Arc<dyn DistWorkerRuntimeService>) -> Router {
    Router::new()
        .route("/internal/bcn/worker/handoff", post(handoff))
        .route("/internal/bcn/worker/source-done", post(source_done))
        .route("/internal/bcn/worker/wake", post(wake))
        .layer(DefaultBodyLimit::max(16_384))
        .with_state(service)
}
async fn handoff(
    State(service): Service,
    peer: Option<Extension<Instance>>,
    Json(body): Json<HandoffRequest>,
) -> Result<Json<HandoffOutcome>, StatusCode> {
    service
        .accept_handoff(caller(peer)?, body)
        .await
        .map(Json)
        .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)
}
async fn source_done(
    State(service): Service,
    peer: Option<Extension<Instance>>,
    Json(body): Json<HandoffDoneRequest>,
) -> Result<Json<bool>, StatusCode> {
    let source = caller(peer)?;
    if body.source != source {
        return Err(StatusCode::FORBIDDEN);
    }
    service
        .handoff_done(source, body.target, body.request_id)
        .await
        .map(Json)
        .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)
}
async fn wake(
    State(service): Service,
    peer: Option<Extension<Instance>>,
    Json(body): Json<WakeRequest>,
) -> Result<StatusCode, StatusCode> {
    let source = caller(peer)?;
    if service
        .snapshot()
        .await
        .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?
        .instance
        != body.target
    {
        return Err(StatusCode::CONFLICT);
    }
    service
        .wake(source, body.shard)
        .await
        .map(|_| StatusCode::NO_CONTENT)
        .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)
}
