//! Browser task controls use the protected Principal boundary; internal MCP
//! retains the master-only /tasks endpoints. Handles are global in store env.
use crate::v1::common::ApiState;
use axum::{
    Json, Router,
    extract::{Extension, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::post,
};
use bcs_service_api::application::v1::{AuthenticatedCaller, require_authenticated_user};
use bcs_service_api::application::{TaskHandleError, TaskHandleService};
use serde::Deserialize;
use std::sync::Arc;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StatusRequest {
    #[serde(rename = "intent_ids", alias = "task_refs")]
    task_refs: Vec<String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CancelRequest {
    #[serde(rename = "intent_id", alias = "task_ref")]
    task_ref: String,
    reason: String,
}

pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/worker-tasks/status", post(status))
        .route("/worker-tasks/cancel", post(cancel))
}

fn service(
    state: &ApiState,
    caller: &AuthenticatedCaller,
) -> Result<Arc<dyn TaskHandleService>, Response> {
    if require_authenticated_user(caller).is_err() {
        return Err((
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({"error":"human_required"})),
        )
            .into_response());
    }
    state.worker_task_service.clone().ok_or_else(|| {
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({"error":"task_service_unavailable"})),
        )
            .into_response()
    })
}
fn error(error: TaskHandleError) -> Response {
    let status = match error {
        TaskHandleError::InvalidRequest => StatusCode::BAD_REQUEST,
        TaskHandleError::Unavailable => StatusCode::SERVICE_UNAVAILABLE,
    };
    (
        status,
        Json(serde_json::json!({"error":"task_handle_request_failed"})),
    )
        .into_response()
}
async fn status(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Json(input): Json<StatusRequest>,
) -> Response {
    let service = match service(&state, &caller) {
        Ok(service) => service,
        Err(response) => return response,
    };
    match service.task_status(input.task_refs).await {
        Ok(tasks) => Json(serde_json::json!({"tasks":tasks})).into_response(),
        Err(err) => error(err),
    }
}
async fn cancel(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Json(input): Json<CancelRequest>,
) -> Response {
    let service = match service(&state, &caller) {
        Ok(service) => service,
        Err(response) => return response,
    };
    match service.cancel_task(input.task_ref, input.reason).await {
        Ok(task) => Json(task).into_response(),
        Err(err) => error(err),
    }
}
