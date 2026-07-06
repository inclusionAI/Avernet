use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde::Deserialize;
use serde_json::Value;

use super::{reject_judge_definition_value_when_unavailable, reject_judge_yaml_when_unavailable};
use crate::state::HttpAppState;
use bcs_service_api::{
    CancelStateMachineRunCommand, CollaborationDefinitionRef, CollaborationRuntimeError,
    StartStateMachineRunCommand,
};

#[derive(Debug, Deserialize)]
pub struct StartStateMachineRunRequest {
    #[serde(default)]
    pub definition_yaml: Option<String>,
    #[serde(default)]
    pub definition: Option<Value>,
    #[serde(default)]
    pub definition_ref: Option<CollaborationDefinitionRef>,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub input: Value,
}

#[derive(Debug, Deserialize)]
pub struct CancelStateMachineRunRequest {
    #[serde(default)]
    pub reason: Option<String>,
}

pub async fn start_state_machine_run(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    Json(body): Json<StartStateMachineRunRequest>,
) -> Response {
    if let Some(definition_yaml) = body.definition_yaml.as_deref() {
        if let Err(error) =
            reject_judge_yaml_when_unavailable(&state, definition_yaml, "definition_yaml")
        {
            return error.into_response();
        }
    }
    if let Some(definition) = &body.definition {
        if let Err(error) =
            reject_judge_definition_value_when_unavailable(&state, definition, "definition")
        {
            return error.into_response();
        }
    }

    match state
        .services
        .collaboration_runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id,
            session_id: body.session_id,
            definition_yaml: body.definition_yaml,
            definition: body.definition,
            definition_ref: body.definition_ref,
            input: body.input,
            caller_id: None,
        })
        .await
    {
        Ok(outcome) => (StatusCode::ACCEPTED, Json(outcome.view)).into_response(),
        Err(error) => collaboration_error_to_response(error),
    }
}

pub async fn get_state_machine_run(
    State(state): State<HttpAppState>,
    Path(run_id): Path<String>,
) -> Response {
    match state
        .services
        .collaboration_runtime
        .get_state_machine_run(&run_id)
        .await
    {
        Ok(Some(view)) => Json(view).into_response(),
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "not_found"})),
        )
            .into_response(),
        Err(error) => collaboration_error_to_response(error),
    }
}

pub async fn get_state_machine_run_graph(
    State(state): State<HttpAppState>,
    Path(run_id): Path<String>,
) -> Response {
    match state
        .services
        .collaboration_runtime
        .get_state_machine_run_graph(&run_id)
        .await
    {
        Ok(Some(view)) => Json(view).into_response(),
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "not_found"})),
        )
            .into_response(),
        Err(error) => collaboration_error_to_response(error),
    }
}

pub async fn get_state_machine_node_run(
    State(state): State<HttpAppState>,
    Path((run_id, node_id)): Path<(String, String)>,
) -> Response {
    match state
        .services
        .collaboration_runtime
        .get_state_machine_node_run(&run_id, &node_id)
        .await
    {
        Ok(Some(view)) => Json(view).into_response(),
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "not_found"})),
        )
            .into_response(),
        Err(error) => collaboration_error_to_response(error),
    }
}

pub async fn cancel_state_machine_run(
    State(state): State<HttpAppState>,
    Path(run_id): Path<String>,
    Json(body): Json<CancelStateMachineRunRequest>,
) -> Response {
    match state
        .services
        .collaboration_runtime
        .cancel_state_machine_run(CancelStateMachineRunCommand {
            run_id,
            reason: body.reason,
        })
        .await
    {
        Ok(view) => Json(view).into_response(),
        Err(error) => collaboration_error_to_response(error),
    }
}

pub(crate) fn collaboration_error_to_response(error: CollaborationRuntimeError) -> Response {
    let (status, code) = match &error {
        CollaborationRuntimeError::RunNotFound(_) => (StatusCode::NOT_FOUND, "not_found"),
        CollaborationRuntimeError::DefinitionNotFound(_, _) => (StatusCode::NOT_FOUND, "not_found"),
        CollaborationRuntimeError::InvalidDefinition(_) => {
            (StatusCode::BAD_REQUEST, "invalid_definition")
        }
        CollaborationRuntimeError::InvalidParticipantBinding(_) => {
            (StatusCode::BAD_REQUEST, "invalid_participant_binding")
        }
        CollaborationRuntimeError::InvalidRequest(_) => (StatusCode::BAD_REQUEST, "invalid_request"),
        CollaborationRuntimeError::Conflict(_) => (StatusCode::CONFLICT, "conflict"),
        CollaborationRuntimeError::Internal(_) => (StatusCode::INTERNAL_SERVER_ERROR, "internal_error"),
    };
    (
        status,
        Json(serde_json::json!({
            "error": code,
            "message": error.to_string()
        })),
    )
        .into_response()
}
