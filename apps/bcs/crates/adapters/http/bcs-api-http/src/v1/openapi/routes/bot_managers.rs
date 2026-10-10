//! Human-only manager list routes (plan Task 13, spec §6):
//! `GET /openapi/v1/collaboration/bots/{bot_id}/managers` and the
//! idempotent direct grant/revoke under
//! `.../managers/{user_id}`.
//!
//! Delivery-adapter responsibilities ONLY (the routes never query the
//! database): parse the request, forward the VERIFIED Human Principal and
//! exact path identities into the application use cases, and project
//! their results into the shared envelope. Authorization, fixed error
//! codes, and the owner/manager invariants live in the application lane.
//!
//! The PUT grant carries NO business body (spec §6): any JSON body
//! content — most importantly a `team` field — is a 400; the direct API
//! never grows a team parameter and a body value never becomes an actor
//! or a scope.

use std::sync::Arc;

use axum::Router;
use axum::body::Bytes;
use axum::Json;
use axum::extract::rejection::{PathRejection, QueryRejection};
use axum::extract::{Extension, Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, put};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, BotManagerService, GrantBotManager, ListBotManagers,
    RevokeBotManager,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::bot_management::{
    BotManagerGrantedDto, BotManagerPageDto, BotManagerRevokedDto, ListBotManagersQuery,
};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/bots/{bot_id}/managers", get(list_managers))
        .route(
            "/bots/{bot_id}/managers/{user_id}",
            put(grant_manager).delete(revoke_manager),
        )
}

fn service(
    state: &ApiState,
    request_id: &RequestId,
) -> Result<Arc<dyn BotManagerService>, ErrorResponse> {
    state.bot_manager_service.clone().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::internal("Bot manager V1 service is not configured"),
        )
    })
}

async fn list_managers(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<ListBotManagersQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(bot_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) =
        query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .list_managers(ListBotManagers {
            caller,
            bot_id,
            offset: query.offset,
            limit: query.limit,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(
            20_000,
            "OK",
            BotManagerPageDto::from(result),
            request_id.0,
        )),
    )
        .into_response())
}

/// No business body is allowed on the direct grant (spec §6). An absent
/// body or an empty JSON object is the request contract; `{"team": ...}`
/// — or any other body content — is rejected as 400 before any
/// application call.
fn reject_business_body(
    request_id: &RequestId,
    bytes: &Bytes,
) -> Result<(), ErrorResponse> {
    if bytes.is_empty() {
        return Ok(());
    }
    let parsed: serde_json::Value = serde_json::from_slice(bytes)
        .map_err(|error| invalid_request(request_id, format!("body must be empty JSON: {error}")))?;
    if parsed.as_object().is_some_and(|object| object.is_empty()) {
        Ok(())
    } else {
        Err(invalid_request(
            request_id,
            "the manager grant carries no business body; a 'team' field or any other \
             content is rejected — use the dedicated team sync / transfer lanes",
        ))
    }
}

async fn grant_manager(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
    bytes: Bytes,
) -> Result<Response, ErrorResponse> {
    let Path((bot_id, user_id)) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    reject_business_body(&request_id, &bytes)?;
    let result = service(&state, &request_id)?
        .grant_manager(GrantBotManager { caller, bot_id, user_id })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(
            20_000,
            "OK",
            BotManagerGrantedDto::from(result),
            request_id.0,
        )),
    )
        .into_response())
}

async fn revoke_manager(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
    _bytes: Bytes,
) -> Result<Response, ErrorResponse> {
    let Path((bot_id, user_id)) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .revoke_manager(RevokeBotManager { caller, bot_id, user_id })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(
            20_000,
            "OK",
            BotManagerRevokedDto::from(result),
            request_id.0,
        )),
    )
        .into_response())
}