//! `GET /bots/{bot_id}/admission` — workbench admission (service-to-service).
//!
//! Calls the edge-permission `AdmissionService` (injected via `HttpAppState`).
//! Transitional: `NoopAdmissionService` until Installment 3 wires the real impl.
use axum::Json;
use axum::extract::{Path, Query, State};

use bcs_domain::edge_permission::AdmissionResult;
use bcs_protocol::http::admission::AdmissionQuery;

use crate::error::HttpAdapterError;
use crate::state::HttpAppState;

pub async fn get_admission(
    State(state): State<HttpAppState>,
    Path(bot_id): Path<String>,
    Query(q): Query<AdmissionQuery>,
) -> Result<Json<AdmissionResult>, HttpAdapterError> {
    let env = q.env.unwrap_or_else(|| state.manifest_env.clone());
    let originator = q.originator.unwrap_or_else(|| q.actor.clone());
    let result = state
        .admission
        .check_admission(&q.actor, &bot_id, &originator, &env)
        .await?;
    Ok(Json(result))
}
