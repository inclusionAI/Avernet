//! `GET /bots/{bot_id}/admission` — workbench admission (service-to-service).
//!
//! Calls the edge-permission `AdmissionService` (injected via `HttpAppState`).
//! Transitional: `NoopAdmissionService` until Installment 3 wires the real impl.
use axum::Json;
use axum::extract::{Path, Query, State};
use tracing::info;

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

    // A3: when the caller asserts actor_kind=human but passes a bare id (no
    // `human_` prefix), prefix it so AdmissionService's id-by-prefix (D11)
    // classifies the actor as a human. Log the rewrite for auditability.
    let actor = if q.actor_kind.as_deref() == Some("human") && !q.actor.starts_with("human_") {
        let prefixed = format!("human_{}", q.actor);
        info!(
            admission_actor_prefix = %prefixed,
            original_actor = %q.actor,
            bot_id = %bot_id,
            "admission: prefixed bare actor id with human_ (actor_kind=human)"
        );
        prefixed
    } else {
        q.actor.clone()
    };
    let originator = q.originator.unwrap_or_else(|| actor.clone());

    let result = state
        .admission
        .check_admission(&actor, &bot_id, &originator, &env)
        .await?;
    Ok(Json(result))
}
