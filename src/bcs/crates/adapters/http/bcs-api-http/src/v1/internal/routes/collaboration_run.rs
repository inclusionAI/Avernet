use std::sync::Arc;

use axum::Json;
use axum::body::Bytes;
use axum::extract::rejection::{JsonRejection, PathRejection};
use axum::extract::{Extension, Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use bcs_service_api::application::v1::{ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity};
use bcs_service_api::application::{
    AuthenticatedHumanCaller, CollaborationRuntimeError, CollaborationRuntimeService,
    HumanResponseSource, ListPendingHumanNodesCommand, PendingHumanNodeView,
    RespondHumanNodeCommand, RerunStateMachineCommand, StateMachineRunAccessCommand,
    StateMachineRunView,
};
use serde::{Deserialize, Serialize};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};

#[derive(Debug, Deserialize)]
struct RespondHumanNodeRequest {
    pub content: String,
}

#[derive(Debug, Deserialize)]
struct CancelStateMachineRunRequest {
    #[serde(default)]
    pub reason: Option<String>,
}

#[derive(Debug, Serialize)]
struct RerunStateMachineRunResponse {
    #[serde(flatten)]
    view: StateMachineRunView,
    idempotent_replay: bool,
}

pub fn protected_router() -> Router<ApiState> {
    Router::new()
        .route("/state-machine-runs/{run_id}", get(get_run))
        .route("/state-machine-runs/{run_id}/graph", get(get_graph))
        .route("/state-machine-runs/{run_id}/reruns", post(rerun_run))
        .route(
            "/state-machine-runs/{run_id}/nodes/{node_id}",
            get(get_node),
        )
        .route(
            "/state-machine-runs/{run_id}/nodes/{node_id}/respond",
            post(respond_human_node),
        )
        .route(
            "/state-machine-runs/{run_id}/pending-human-nodes",
            get(list_pending_human_nodes),
        )
        .route(
            "/state-machine-runs/{run_id}/cancel",
            post(cancel_state_machine_run),
        )
}

async fn rerun_run(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Bytes,
) -> Result<Response, ErrorResponse> {
    if !body.is_empty() {
        return Err(invalid_request(
            &request_id,
            "state-machine rerun request must not contain a body",
        ));
    }
    let Path(source_run_id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let outcome = service(&state, &request_id)?
        .rerun_state_machine_run(RerunStateMachineCommand {
            source_run_id,
            authenticated_human: authenticated_human(&caller),
        })
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    let status = if outcome.created {
        StatusCode::CREATED
    } else {
        StatusCode::OK
    };
    Ok((
        status,
        Json(Envelope::success(
            20_000,
            "OK",
            RerunStateMachineRunResponse {
                view: outcome.view,
                idempotent_replay: !outcome.created,
            },
            request_id.0,
        )),
    )
        .into_response())
}

fn service(state: &ApiState, request_id: &RequestId) -> Result<Arc<dyn CollaborationRuntimeService>, ErrorResponse> {
    state
        .collaboration_runtime_service
        .clone()
        .ok_or_else(|| {
            application_error_response(
                request_id,
                ApplicationError::internal("V1 Collaboration Runtime service is not configured"),
            )
        })
}

/// HTTP-layer auth: project the gateway-signed User into BCS's Human Actor
/// model. `actor_id` follows the legacy `human_{staff_no}` convention. Returns
/// `None` when no User principal is present (callers decide whether that is
/// allowed).
fn authenticated_human(caller: &AuthenticatedCaller) -> Option<AuthenticatedHumanCaller> {
    caller.user.as_ref().map(
        |AuthenticatedUserIdentity { id, display_name, .. }| AuthenticatedHumanCaller {
            actor_id: format!("human_{id}"),
            display_name: display_name.clone(),
        },
    )
}

fn runtime_error(request_id: &RequestId, error: CollaborationRuntimeError) -> ErrorResponse {
    application_error_response(request_id, collaboration_runtime_error_to_application_error(error))
}

fn collaboration_runtime_error_to_application_error(
    error: CollaborationRuntimeError,
) -> ApplicationError {
    match error {
        CollaborationRuntimeError::RunNotFound(_)
        | CollaborationRuntimeError::NodeNotFound { .. }
        | CollaborationRuntimeError::DefinitionNotFound(_, _) => {
            ApplicationError::not_found("not_found", error.to_string())
        }
        CollaborationRuntimeError::InvalidDefinition(_)
        | CollaborationRuntimeError::InvalidParticipantBinding(_)
        | CollaborationRuntimeError::InvalidRequest(_) => {
            ApplicationError::invalid("invalid_request", error.to_string())
        }
        CollaborationRuntimeError::Unauthenticated => ApplicationError::Unauthenticated,
        CollaborationRuntimeError::Forbidden(_) => {
            ApplicationError::forbidden_code("forbidden", error.to_string())
        }
        CollaborationRuntimeError::JudgeUnavailable(_) => {
            ApplicationError::bad_gateway("judge_unavailable", error.to_string())
        }
        CollaborationRuntimeError::Conflict(_) => {
            ApplicationError::conflict("conflict", error.to_string())
        }
        CollaborationRuntimeError::Internal(_) => ApplicationError::internal(error.to_string()),
    }
}

async fn get_run(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(run_id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let human = authenticated_human(&caller);
    let view = service(&state, &request_id)?
        .get_state_machine_run_with_access(StateMachineRunAccessCommand {
            run_id,
            authenticated_human: human,
        })
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    view_view_or_404(view, &request_id)
}

async fn get_graph(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(run_id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let view = service(&state, &request_id)?
        .get_state_machine_run_graph_with_access(StateMachineRunAccessCommand {
            run_id,
            authenticated_human: authenticated_human(&caller),
        })
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    view_view_or_404(view, &request_id)
}

async fn get_node(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path((run_id, node_id)) =
        path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let view = service(&state, &request_id)?
        .get_state_machine_node_run_with_access(
            StateMachineRunAccessCommand {
                run_id,
                authenticated_human: authenticated_human(&caller),
            },
            &node_id,
        )
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    view_view_or_404(view, &request_id)
}

async fn list_pending_human_nodes(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(run_id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let human = authenticated_human(&caller).ok_or_else(|| {
        application_error_response(&request_id, ApplicationError::Unauthenticated)
    })?;
    let nodes: Vec<PendingHumanNodeView> = service(&state, &request_id)?
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id,
            caller_actor_id: human.actor_id,
        })
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", nodes, request_id.0)),
    )
        .into_response())
}

async fn respond_human_node(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
    body: Result<Json<RespondHumanNodeRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path((run_id, node_id)) =
        path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let Json(body) = body.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let human = authenticated_human(&caller).ok_or_else(|| {
        application_error_response(&request_id, ApplicationError::Unauthenticated)
    })?;
    let outcome = service(&state, &request_id)?
        .respond_human_node(RespondHumanNodeCommand {
            run_id,
            node_id,
            caller_actor_id: human.actor_id,
            content: body.content,
            source: HumanResponseSource::Http,
        })
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", outcome, request_id.0)),
    )
        .into_response())
}

async fn cancel_state_machine_run(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<CancelStateMachineRunRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(run_id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let Json(body) = body.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let view = service(&state, &request_id)?
        .cancel_state_machine_run_with_access(
            StateMachineRunAccessCommand {
                run_id,
                authenticated_human: authenticated_human(&caller),
            },
            body.reason,
        )
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", view, request_id.0)),
    )
        .into_response())
}

fn view_view_or_404<T: Serialize>(
    view: Option<T>,
    request_id: &RequestId,
) -> Result<Response, ErrorResponse> {
    match view {
        Some(view) => Ok((
            StatusCode::OK,
            Json(Envelope::success(20_000, "OK", view, request_id.0.clone())),
        )
            .into_response()),
        None => Err(application_error_response(
            request_id,
            ApplicationError::not_found("not_found", "state machine run not found"),
        )),
    }
}
