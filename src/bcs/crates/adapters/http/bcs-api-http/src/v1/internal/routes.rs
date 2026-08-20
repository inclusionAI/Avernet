use std::sync::Arc;

use axum::Router;
use axum::extract::rejection::{JsonRejection, PathRejection};
use axum::extract::{Extension, Json, Path, State};
use axum::http::StatusCode;
use axum::middleware;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use bcs_service_api::application::v1::InternalBotAttributesService;
use tracing::{info, warn};

use super::auth::{InternalProviderAuthenticator, authenticate_internal_provider};
use super::dto::PatchBotInternalAttributesRequest;
use crate::v1::common::{
    Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};

pub fn router<S>(
    service: Arc<dyn InternalBotAttributesService>,
    authenticator: Arc<dyn InternalProviderAuthenticator>,
) -> Router<S> {
    Router::new()
        .route(
            "/internal/v1/bots/{bot_id}/attributes",
            get(get_attributes).patch(patch_attributes),
        )
        .route_layer(middleware::from_fn_with_state(
            authenticator,
            authenticate_internal_provider,
        ))
        .with_state(service)
}

async fn get_attributes(
    State(service): State<Arc<dyn InternalBotAttributesService>>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(bot_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service
        .get(bot_id.clone())
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    info!(
        request_id = %request_id.0,
        bot_id,
        "Internal Bot attributes read completed"
    );
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn patch_attributes(
    State(service): State<Arc<dyn InternalBotAttributesService>>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<PatchBotInternalAttributesRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(bot_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|_| {
        warn!(
            request_id = %request_id.0,
            failure = "invalid_json_body",
            "Internal Bot attributes patch rejected"
        );
        invalid_request(&request_id, "Request body is invalid")
    })?;
    if body.is_empty() {
        return Err(invalid_request(
            &request_id,
            "Bot internal attributes patch must contain at least one field",
        ));
    }
    info!(
        request_id = %request_id.0,
        bot_id,
        has_user_visibility = body.user_visibility.is_some(),
        has_friend_ext = body.friend_ext.is_some(),
        friend_ext_key_count = body.friend_ext.as_ref().map(|value| value.len()),
        has_friend_check_in_strategy = body.friend_check_in_strategy.is_some(),
        "Internal Bot attributes patch accepted"
    );
    let result = service
        .patch(body.into_command(bot_id))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    info!(
        request_id = %request_id.0,
        "Internal Bot attributes patch completed"
    );
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}
