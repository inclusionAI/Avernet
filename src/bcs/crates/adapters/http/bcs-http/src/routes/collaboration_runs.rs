use axum::{
    Json,
    extract::{OriginalUri, Path, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use serde::Deserialize;
use serde_json::Value;

use super::{reject_judge_definition_value_when_unavailable, reject_judge_yaml_when_unavailable};
use crate::state::HttpAppState;
use bcs_service_api::{
    AuthenticatedHumanCaller, CollaborationDefinitionRef, CollaborationRuntimeError,
    HumanResponseSource, HumanRunAccessCommand, ListPendingHumanNodesCommand,
    RespondHumanNodeCommand, StartStateMachineRunCommand,
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

#[derive(Debug, Deserialize)]
pub struct RespondHumanNodeRequest {
    pub content: String,
}

pub async fn start_state_machine_run(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    headers: HeaderMap,
    OriginalUri(uri): OriginalUri,
    Json(body): Json<StartStateMachineRunRequest>,
) -> Response {
    let authenticated_human = match authenticated_human(&state, &headers, &uri).await {
        Ok(human) => human,
        Err(response) => return response,
    };
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
            caller_id: Some(authenticated_human.actor_id.clone()),
            authenticated_human: Some(authenticated_human),
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
    headers: HeaderMap,
    OriginalUri(uri): OriginalUri,
) -> Response {
    let human = match authenticated_human(&state, &headers, &uri).await {
        Ok(human) => human,
        Err(response) => return response,
    };
    match state
        .services
        .collaboration_runtime
        .get_state_machine_run_for_human(HumanRunAccessCommand {
            run_id,
            caller_actor_id: human.actor_id,
        })
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
    headers: HeaderMap,
    OriginalUri(uri): OriginalUri,
) -> Response {
    let human = match authenticated_human(&state, &headers, &uri).await {
        Ok(human) => human,
        Err(response) => return response,
    };
    match state
        .services
        .collaboration_runtime
        .get_state_machine_run_graph_for_human(HumanRunAccessCommand {
            run_id,
            caller_actor_id: human.actor_id,
        })
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
    headers: HeaderMap,
    OriginalUri(uri): OriginalUri,
) -> Response {
    let human = match authenticated_human(&state, &headers, &uri).await {
        Ok(human) => human,
        Err(response) => return response,
    };
    match state
        .services
        .collaboration_runtime
        .get_state_machine_node_run_for_human(
            HumanRunAccessCommand {
                run_id,
                caller_actor_id: human.actor_id,
            },
            &node_id,
        )
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
    headers: HeaderMap,
    OriginalUri(uri): OriginalUri,
    Json(body): Json<CancelStateMachineRunRequest>,
) -> Response {
    let human = match authenticated_human(&state, &headers, &uri).await {
        Ok(human) => human,
        Err(response) => return response,
    };
    match state
        .services
        .collaboration_runtime
        .cancel_state_machine_run_for_human(
            HumanRunAccessCommand {
                run_id,
                caller_actor_id: human.actor_id,
            },
            body.reason,
        )
        .await
    {
        Ok(view) => Json(view).into_response(),
        Err(error) => collaboration_error_to_response(error),
    }
}

pub async fn list_pending_human_nodes(
    State(state): State<HttpAppState>,
    Path(run_id): Path<String>,
    headers: HeaderMap,
    OriginalUri(uri): OriginalUri,
) -> Response {
    let human = match authenticated_human(&state, &headers, &uri).await {
        Ok(human) => human,
        Err(response) => return response,
    };
    match state
        .services
        .collaboration_runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id,
            caller_actor_id: human.actor_id,
        })
        .await
    {
        Ok(nodes) => Json(nodes).into_response(),
        Err(error) => collaboration_error_to_response(error),
    }
}

pub async fn respond_human_node(
    State(state): State<HttpAppState>,
    Path((run_id, node_id)): Path<(String, String)>,
    headers: HeaderMap,
    OriginalUri(uri): OriginalUri,
    Json(body): Json<RespondHumanNodeRequest>,
) -> Response {
    let human = match authenticated_human(&state, &headers, &uri).await {
        Ok(human) => human,
        Err(response) => return response,
    };
    match state
        .services
        .collaboration_runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id,
            node_id,
            caller_actor_id: human.actor_id,
            content: body.content,
            source: HumanResponseSource::Http,
        })
        .await
    {
        Ok(outcome) => (StatusCode::OK, Json(outcome)).into_response(),
        Err(error) => collaboration_error_to_response(error),
    }
}

async fn authenticated_human(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<AuthenticatedHumanCaller, Response> {
    let identity = state.user_identity.extract(headers, uri).await;
    let Some(identity) = identity else {
        return Err(unauthenticated_response());
    };
    let Some(staff_no) = identity.staff_no.filter(|value| !value.trim().is_empty()) else {
        return Err(unauthenticated_response());
    };
    let display_name = identity
        .nick_name
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    Ok(AuthenticatedHumanCaller {
        actor_id: format!("human_{staff_no}"),
        display_name,
    })
}

fn unauthenticated_response() -> Response {
    (
        StatusCode::UNAUTHORIZED,
        Json(serde_json::json!({
            "error": "unauthenticated",
            "message": "valid Human identity is required"
        })),
    )
        .into_response()
}

pub(crate) fn collaboration_error_to_response(error: CollaborationRuntimeError) -> Response {
    let (status, code) = match &error {
        CollaborationRuntimeError::RunNotFound(_) => (StatusCode::NOT_FOUND, "not_found"),
        CollaborationRuntimeError::NodeNotFound { .. } => (StatusCode::NOT_FOUND, "not_found"),
        CollaborationRuntimeError::DefinitionNotFound(_, _) => (StatusCode::NOT_FOUND, "not_found"),
        CollaborationRuntimeError::InvalidDefinition(_) => {
            (StatusCode::BAD_REQUEST, "invalid_definition")
        }
        CollaborationRuntimeError::InvalidParticipantBinding(_) => {
            (StatusCode::BAD_REQUEST, "invalid_participant_binding")
        }
        CollaborationRuntimeError::InvalidRequest(_) => {
            (StatusCode::BAD_REQUEST, "invalid_request")
        }
        CollaborationRuntimeError::Unauthenticated => (StatusCode::UNAUTHORIZED, "unauthenticated"),
        CollaborationRuntimeError::Forbidden(_) => (StatusCode::FORBIDDEN, "forbidden"),
        CollaborationRuntimeError::JudgeUnavailable(_) => {
            (StatusCode::SERVICE_UNAVAILABLE, "judge_unavailable")
        }
        CollaborationRuntimeError::Conflict(_) => (StatusCode::CONFLICT, "conflict"),
        CollaborationRuntimeError::Internal(_) => {
            (StatusCode::INTERNAL_SERVER_ERROR, "internal_error")
        }
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
