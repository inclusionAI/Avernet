use std::sync::Arc;

use axum::Router;
use axum::extract::rejection::{PathRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, BotService, SearchBotCandidates,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::bot::SearchBotCandidatesQuery;

pub fn router() -> Router<ApiState> {
    Router::new().route("/bots/{bot_id}/candidates/search", get(search_candidates))
}

fn service(state: &ApiState, request_id: &RequestId) -> Result<Arc<dyn BotService>, ErrorResponse> {
    state.bot_service.clone().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::internal("Bot V1 service is not configured"),
        )
    })
}

async fn search_candidates(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<SearchBotCandidatesQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(bot_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .search_candidates(SearchBotCandidates {
            caller,
            bot_id,
            purpose: query.purpose.into(),
            query: query.q,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}
