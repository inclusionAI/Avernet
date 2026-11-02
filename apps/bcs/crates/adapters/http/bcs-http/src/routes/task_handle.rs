//! Manager task handles. V1 intentionally has no caller/session filter.
//! Deploy on the internal master-routed endpoint, not as an authorization token.
use axum::{Json, extract::State, http::StatusCode, response::{IntoResponse, Response}};
use serde::Deserialize;
use crate::state::HttpAppState;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StatusRequest { task_refs: Vec<String> }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CancelRequest { task_ref: String, reason: String }
fn error(error: bcs_service_api::ServiceError) -> Response {
    let status = if matches!(error, bcs_service_api::ServiceError::InvalidOperation { .. }) {
        StatusCode::BAD_REQUEST
    } else { StatusCode::SERVICE_UNAVAILABLE };
    (status, Json(serde_json::json!({"error": "task_handle_request_failed"}))).into_response()
}
pub async fn status(State(state): State<HttpAppState>, Json(input): Json<StatusRequest>) -> Response {
    match state.services.message_flow.task_status(input.task_refs).await {
        Ok(tasks) => Json(serde_json::json!({"tasks": tasks})).into_response(),
        Err(err) => error(err),
    }
}
pub async fn cancel(State(state): State<HttpAppState>, Json(input): Json<CancelRequest>) -> Response {
    match state.services.message_flow.cancel_task(input.task_ref, input.reason).await {
        Ok(task) => Json(task).into_response(),
        Err(err) => error(err),
    }
}
