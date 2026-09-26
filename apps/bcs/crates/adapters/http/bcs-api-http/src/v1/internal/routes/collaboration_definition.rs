use std::sync::Arc;

use axum::Router;
use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, Json, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, CollaborationDefinitionService,
    ValidateCollaborationDefinition,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::collaboration_definition::ValidateCollaborationDefinitionRequest;

pub fn router() -> Router<ApiState> {
    Router::new().route("/definitions/validate", post(validate))
}

fn service(
    state: &ApiState,
    request_id: &RequestId,
) -> Result<Arc<dyn CollaborationDefinitionService>, ErrorResponse> {
    state.collaboration_definition_service.clone().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::internal("V1 Collaboration Definition service is not configured"),
        )
    })
}

/// Versioned projection of legacy `POST /collaboration/definitions/validate`.
///
/// Mirrors the legacy behavior exactly: an invalid document is returned as
/// `200` with `valid: false` inside the standard envelope. Only request binding
/// failure and service wiring failure surface as HTTP errors (`400` /
/// `internal`). The body field is `definition_yaml` only; the legacy `yaml`
/// serde alias is intentionally not carried over.
pub async fn validate(
    State(state): State<ApiState>,
    Extension(_caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    body: Result<Json<ValidateCollaborationDefinitionRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    if body.definition_yaml.trim().is_empty() {
        return Err(invalid_request(
            &request_id,
            "definition_yaml must not be empty",
        ));
    }
    let outcome = service(&state, &request_id)?
        .validate_definition_yaml(ValidateCollaborationDefinition {
            definition_yaml: body.definition_yaml,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", outcome, request_id.0)),
    )
        .into_response())
}
