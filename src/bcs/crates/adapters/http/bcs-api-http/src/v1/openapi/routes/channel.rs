use std::sync::Arc;

use axum::Router;
use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, patch, post};
use bcs_service_api::application::v1::{require_authenticated_user, AuthenticatedCaller, ApplicationError};
use bcs_service_api::{ChannelService, ChannelUseCaseError, ServiceError};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::channel::{
    ChannelBindingDto, ChannelBindingPage, CreateChannelBindingRequest, ListBindingsByTargetQuery,
    UpdateChannelBindingRequest, normalize_target_query,
};

/// Channels/bindings OpenAPI router — mounted under `/openapi/v1/collaboration`.
///
/// Human-only is enforced in the adapter via `require_authenticated_user(caller)`;
/// the shared `ChannelService` carries no actor (merged-app design). All five
/// operations reuse the legacy `ChannelService` implementation.
pub fn router() -> Router<ApiState> {
    Router::new()
        .route(
            "/channels/bindings",
            post(create_binding).get(list_bindings),
        )
        .route("/channels/bindings/by-target", get(list_bindings_by_target))
        .route(
            "/channels/bindings/{id}",
            patch(update_binding).delete(delete_binding),
        )
}

/// Resolve the shared `ChannelService` slot; fail-closed as `internal` if the
/// bootstrap composition root did not mount it yet (mirrors `bot::service`).
fn channel_service(state: &ApiState, request_id: &RequestId) -> Result<Arc<dyn ChannelService>, ErrorResponse> {
    state.channel_service.clone().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::internal("Channel V1 service is not configured"),
        )
    })
}

/// Translate the shared application error into the V1 vocabulary so the
/// delivery adapter handles only application errors (matches `channel_error`).
fn map_channel_error(error: ChannelUseCaseError) -> ApplicationError {
    match error {
        ChannelUseCaseError::NotFound(id) => ApplicationError::not_found(
            "channel_binding_not_found",
            format!("channel binding not found: {id}"),
        ),
        ChannelUseCaseError::Conflict(message) => {
            ApplicationError::conflict("channel_binding_conflict", message)
        }
        ChannelUseCaseError::InvalidParams(message) => {
            ApplicationError::invalid("invalid_channel_params", message)
        }
        ChannelUseCaseError::Internal(ServiceError::Conflict(message)) => {
            ApplicationError::conflict("channel_binding_conflict", message)
        }
        ChannelUseCaseError::Internal(other) => ApplicationError::internal(other.to_string()),
    }
}

async fn create_binding(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    body: Result<Json<CreateChannelBindingRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let user = require_authenticated_user(&caller)
        .map_err(|error| application_error_response(&request_id, error))?;
    let binding = channel_service(&state, &request_id)?
        .create_binding(body.into_command(user.id.clone()))
        .await
        .map_err(|error| application_error_response(&request_id, map_channel_error(error)))?;
    Ok((
        StatusCode::CREATED,
        Json(Envelope::success(
            20_100,
            "Created",
            ChannelBindingDto::from(binding),
            request_id.0,
        )),
    )
        .into_response())
}

async fn list_bindings(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
) -> Result<Response, ErrorResponse> {
    require_authenticated_user(&caller)
        .map_err(|error| application_error_response(&request_id, error))?;
    let bindings = channel_service(&state, &request_id)?
        .list_bindings()
        .await
        .map_err(|error| application_error_response(&request_id, map_channel_error(error)))?;
    let items = bindings.into_iter().map(ChannelBindingDto::from).collect();
    Ok((
        StatusCode::OK,
        Json(Envelope::success(
            20_000,
            "OK",
            ChannelBindingPage { items },
            request_id.0,
        )),
    )
        .into_response())
}

async fn list_bindings_by_target(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ListBindingsByTargetQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let (target, channel_type) = normalize_target_query(query)
        .map_err(|message| invalid_request(&request_id, message))?;
    require_authenticated_user(&caller)
        .map_err(|error| application_error_response(&request_id, error))?;
    let bindings = channel_service(&state, &request_id)?
        .list_bindings_by_target(target, channel_type)
        .await
        .map_err(|error| application_error_response(&request_id, map_channel_error(error)))?;
    let items = bindings.into_iter().map(ChannelBindingDto::from).collect();
    Ok((
        StatusCode::OK,
        Json(Envelope::success(
            20_000,
            "OK",
            ChannelBindingPage { items },
            request_id.0,
        )),
    )
        .into_response())
}

async fn update_binding(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<UpdateChannelBindingRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    require_authenticated_user(&caller)
        .map_err(|error| application_error_response(&request_id, error))?;
    let service = channel_service(&state, &request_id)?;
    let outcome = match (body.active, body.config) {
        (Some(active), None) => service.set_binding_status(&id, active).await,
        (None, Some(config)) => service.update_binding_config(&id, config).await,
        (Some(_), Some(_)) => {
            return Err(invalid_request(
                &request_id,
                "update active and config separately",
            ));
        }
        (None, None) => {
            return Err(invalid_request(&request_id, "active or config is required"));
        }
    };
    outcome.map_err(|error| application_error_response(&request_id, map_channel_error(error)))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(
            20_000,
            "OK",
            serde_json::Value::Null,
            request_id.0,
        )),
    )
        .into_response())
}

async fn delete_binding(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    require_authenticated_user(&caller)
        .map_err(|error| application_error_response(&request_id, error))?;
    channel_service(&state, &request_id)?
        .delete_binding(&id)
        .await
        .map_err(|error| application_error_response(&request_id, map_channel_error(error)))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(
            20_000,
            "OK",
            serde_json::Value::Null,
            request_id.0,
        )),
    )
        .into_response())
}
