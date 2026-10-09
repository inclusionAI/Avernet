//! Trusted team-manager sources slice (plan Task 13, spec §6.1):
//! `PUT /api/v1/bots/{bot_id}/manager-sources/teams/{team_id}` (the
//! platform's normal snapshot sync / move entry) plus the INTERNAL
//! single-member repair endpoints under `.../members`
//! (`POST` add-one, `DELETE` remove-one).
//!
//! Slice boundaries (spec §6.1, binding):
//! - This slice is mounted OUTSIDE the generic Gateway-Principal /
//!   invite-code middleware (`v1::router` merges it separately): no
//!   Human/App/Bot Principal authenticates here, and the lane is NOT
//!   anonymous — a trusted team-manager service credential in the
//!   `Authorization: Bearer` header is REQUIRED. The route never mounts
//!   when the composition root did not arm the credential boundary
//!   (`ApiState::team_manager_routes_enabled`).
//! - The credential is parsed HERE (fail-fast 401 on a missing/blank
//!   header) and verified through the application contract
//!   (`TeamManagerSyncService::verify_service_credential`): the verified
//!   identity + scopes flow onward in the command; the credential value
//!   is dropped, never logged, echoed, or persisted.
//! - Nothing about a request body may express identity or scopes:
//!   `deny_unknown_fields` rejects `service_id`-style smuggles, and the
//!   command built carries the VERIFIED service only.

use std::sync::Arc;

use axum::Router;
use axum::Json;
use axum::extract::rejection::{PathRejection, QueryRejection};
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{post, put};
use bcs_service_api::application::v1::{
    ApplicationError, TeamManagerMemberRepair, TeamManagerSyncService, VerifiedTeamService,
};
use serde::Deserialize;

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::bot_management::{
    TeamManagerSyncRequest, TeamMemberRepairRequest, TeamSyncReceiptDto,
};

const IDEMPOTENCY_KEY_HEADER: &str = "idempotency-key";

/// Failure-branch diagnostics for the credential-gated slice (PR #2568
/// round-2: CI's bcs.log tail carried only http_access lines for the 403
/// lanes, so the server-side reason was invisible to test triage). Only
/// the fixed application code and its already-sanitized detail are logged;
/// the credential value and the signing key never join the log.
fn warn_team_sync_rejection(request_id: &RequestId, error: &ApplicationError) {
    if error.code() == "invalid_manager_sync_source" {
        tracing::warn!(
            request_id = %request_id.0,
            "team-manager sync rejected: {error}"
        );
    }
}

pub fn router() -> Router<ApiState> {
    Router::new()
        .route(
            "/{bot_id}/manager-sources/teams/{team_id}",
            put(sync_team_manager_sources),
        )
        .route(
            "/{bot_id}/manager-sources/teams/{team_id}/members",
            post(repair_add_team_member).delete(repair_remove_team_member),
        )
}

fn service(
    state: &ApiState,
    request_id: &RequestId,
) -> Result<Arc<dyn TeamManagerSyncService>, ErrorResponse> {
    state.team_manager_sync_service.clone().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::internal("Team manager sync service is not configured"),
        )
    })
}

/// Parse and verify the team-manager service credential. A missing or
/// blank Bearer credential is 401; an unverifiable one surfaces the
/// application's 403 `invalid_manager_sync_source`. The raw value is
/// dropped after verification and never reaches an error body.
async fn verified_service(
    state: &ApiState,
    headers: &HeaderMap,
    request_id: &RequestId,
) -> Result<(Arc<dyn TeamManagerSyncService>, VerifiedTeamService), ErrorResponse> {
    let service = service(state, request_id)?;
    let mut values = headers.get_all(axum::http::header::AUTHORIZATION).iter();
    let value = values
        .next()
        .and_then(|value| value.to_str().ok())
        .ok_or_else(|| application_error_response(&request_id, ApplicationError::Unauthenticated))?;
    if values.next().is_some() {
        return Err(application_error_response(
            &request_id,
            ApplicationError::Unauthenticated,
        ));
    }
    let (scheme, credential) = value
        .split_once(' ')
        .ok_or_else(|| application_error_response(&request_id, ApplicationError::Unauthenticated))?;
    if !scheme.eq_ignore_ascii_case("Bearer")
        || credential.trim().is_empty()
        || credential
            .bytes()
            .any(|byte| byte.is_ascii_whitespace() || byte == b',')
    {
        return Err(application_error_response(
            &request_id,
            ApplicationError::Unauthenticated,
        ));
    }
    let verified = service
        .verify_service_credential(credential)
        .await
        .map_err(|error| {
            // Error rendering is centralized; the credential never joins
            // the message/materialized body paths.
            warn_team_sync_rejection(&request_id, &error);
            application_error_response(&request_id, error)
        })?;
    Ok((service, verified))
}

fn cache_control_no_store(mut response: Response) -> Response {
    if let Ok(value) = "no-store".parse() {
        response
            .headers_mut()
            .insert(axum::http::header::CACHE_CONTROL, value);
    }
    response
}

async fn sync_team_manager_sources(
    State(state): State<ApiState>,
    headers: HeaderMap,
    path: Result<Path<(String, String)>, PathRejection>,
    body: Result<Json<TeamManagerSyncRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let (service, verified) = verified_service(&state, &headers, &request_id).await?;
    let Path((bot_id, team_id)) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| {
        // A missing body is the same category as an unparsable one: the
        // snapshot contract requires the three-field body (spec §6.1).
        if matches!(
            error,
            axum::extract::rejection::JsonRejection::MissingJsonContentType(_)
        ) {
            invalid_request(
                &request_id,
                "the team sync body (operation, manager_user_ids, idempotency_key) is required",
            )
        } else {
            invalid_request(&request_id, error.body_text())
        }
    })?;
    let command = body
        .into_command(verified, bot_id, team_id)
        .map_err(|message| invalid_request(&request_id, message))?;
    let receipt = service
        .sync(command)
        .await
        .map_err(|error| {
            warn_team_sync_rejection(&request_id, &error);
            application_error_response(&request_id, error)
        })?;
    Ok(cache_control_no_store(
        (
            StatusCode::OK,
            Json(Envelope::success(20_000, "OK", TeamSyncReceiptDto::from(receipt), request_id.0)),
        )
            .into_response(),
    ))
}

/// Internal operations repair (spec §6.1): add ONE user to the team
/// source. Body shape is exactly `user_id` + `idempotency_key`
/// (additionalProperties false); the same credential lane audited by the
/// sync lane authorizes it.
async fn repair_add_team_member(
    State(state): State<ApiState>,
    headers: HeaderMap,
    path: Result<Path<(String, String)>, PathRejection>,
    body: Result<Json<TeamMemberRepairRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let (service, verified) = verified_service(&state, &headers, &request_id).await?;
    let Path((bot_id, team_id)) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let idempotency_key = body.idempotency_key.trim().to_string();
    let user_id = body.user_id.trim().to_string();
    if user_id.is_empty() {
        return Err(invalid_request(&request_id, "'user_id' must be non-blank"));
    }
    if idempotency_key.is_empty() {
        return Err(invalid_request(
            &request_id,
            "'idempotency_key' must be non-blank",
        ));
    }
    let receipt = service
        .repair_add_team_member(TeamManagerMemberRepair {
            service: verified,
            bot_id,
            team_id,
            user_id,
            idempotency_key,
        })
        .await
        .map_err(|error| {
            warn_team_sync_rejection(&request_id, &error);
            application_error_response(&request_id, error)
        })?;
    Ok(cache_control_no_store(
        (
            StatusCode::OK,
            Json(Envelope::success(20_000, "OK", TeamSyncReceiptDto::from(receipt), request_id.0)),
        )
            .into_response(),
    ))
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TeamMemberRemovalQuery {
    pub user_id: String,
}

/// Internal operations repair (spec §6.1): remove ONE user from the team
/// source. Identity rides the `?user_id=` query and the durable
/// idempotency rides the `Idempotency-Key` header; both are required.
async fn repair_remove_team_member(
    State(state): State<ApiState>,
    headers: HeaderMap,
    path: Result<Path<(String, String)>, PathRejection>,
    query: Result<Query<TeamMemberRemovalQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let (service, verified) = verified_service(&state, &headers, &request_id).await?;
    let Path((bot_id, team_id)) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) =
        query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let user_id = query.user_id.trim().to_string();
    if user_id.is_empty() {
        return Err(invalid_request(&request_id, "'user_id' must be non-blank"));
    }
    let idempotency_key = headers
        .get(IDEMPOTENCY_KEY_HEADER)
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            invalid_request(
                &request_id,
                "the team member removal repair requires an 'Idempotency-Key' header",
            )
        })?
        .to_string();
    let receipt = service
        .repair_remove_team_member(TeamManagerMemberRepair {
            service: verified,
            bot_id,
            team_id,
            user_id,
            idempotency_key,
        })
        .await
        .map_err(|error| {
            warn_team_sync_rejection(&request_id, &error);
            application_error_response(&request_id, error)
        })?;
    Ok(cache_control_no_store(
        (
            StatusCode::OK,
            Json(Envelope::success(20_000, "OK", TeamSyncReceiptDto::from(receipt), request_id.0)),
        )
            .into_response(),
    ))
}