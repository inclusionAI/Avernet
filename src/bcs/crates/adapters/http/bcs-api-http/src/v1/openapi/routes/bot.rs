use std::sync::Arc;

use axum::Router;
use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use bcs_service_api::application::v1::{
    ApplicationError, BotList, BotService, GetBot, ListBotCandidates, ListMyBots, Principal,
    QueryBots, UpdateBot,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::bot::{
    ListBotCandidatesQuery, ListMyBotsQuery, QueryBotsRequest, UpdateBotRequest,
};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/openapi/v1/bots/{bot_id}/candidates", get(list_candidates))
        .route("/openapi/v1/bots/query", post(query_bots))
        .route("/openapi/v1/bots/mine", get(list_mine))
        .route("/openapi/v1/bots/{bot_id}", get(get_bot).patch(update_bot))
}

fn service(state: &ApiState, request_id: &RequestId) -> Result<Arc<dyn BotService>, ErrorResponse> {
    state.bot_service.clone().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::internal("Bot V1 service is not configured"),
        )
    })
}

async fn list_candidates(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<ListBotCandidatesQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(bot_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .list_candidates(ListBotCandidates {
            principal,
            bot_id,
            purpose: query.purpose.into(),
            name: query.name,
            offset: query.offset,
            limit: query.limit,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn query_bots(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    body: Result<Json<QueryBotsRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let items = service(&state, &request_id)?
        .query(QueryBots {
            principal,
            bot_ids: body.bot_ids,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(
            20_000,
            "OK",
            BotList { items },
            request_id.0,
        )),
    )
        .into_response())
}

async fn get_bot(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(bot_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .get(GetBot { principal, bot_id })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn update_bot(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<UpdateBotRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(bot_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .update(UpdateBot {
            principal,
            bot_id,
            patch: body.into(),
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn list_mine(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ListMyBotsQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .list_mine(ListMyBots {
            principal,
            kind: query.kind,
            name: query.name,
            status: query.status,
            reachability: query.reachability,
            offset: query.offset,
            limit: query.limit,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}
