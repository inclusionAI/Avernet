//! The unified `/api/v1/collaboration/` authority
//! route set (spec §5.1/§6.1): exactly the twelve frozen
//! method/path combos, each served through the
//! `BotAuthorityService` application facade.
//!
//! Route composition: these routes share the Gateway
//! signature verification boundary but NOT the
//! always-Human invite-code gate (spec §6.1 — a trusted
//! service must not be treated as an un-invite-coded
//! Human). Human admission, including the invite gate,
//! runs inside the application; there is no anonymous
//! router and no URL whitelist bypass.

use std::sync::Arc;

use axum::extract::{Path, Query, RawQuery, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Extension, Json, Router};
use serde::Deserialize;

use bcs_service_api::application::v1::bot_authority::BotAuthorityService;
use bcs_service_api::application::v1::error::ApplicationError;
use bcs_service_api::application::v1::identity::AuthenticatedCaller;
use bcs_service_api::application::v1::bot_authority::{
    AuthorityActor, AuthorityMutation, ManagerEdit, ManagerEditAction,
    ManagerListQuery, ManagerSync, OwnershipSync, TransferAction,
    TransferChange, TransferCommand, TransferDirection, TransferKey,
    TransferListQuery, TransferOutcome, DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
};

use crate::v1::common::ApiState;
use crate::v1::common::{
    Envelope, ErrorResponse, RequestId, application_error_response,
    invalid_request,
};

use super::dto;
use super::dto::{
    BotOwnershipDto, ManagerPageDto, ManagerSetChangeDto,
    OwnershipChangeDto, SyncManagersBody, SyncOwnershipBody,
    TransferPageDto, TransferViewDto,
};

pub fn router() -> Router<ApiState> {
    Router::new()
        // Manager single-item / collection surface
        // (spec §5.1). The single-item PUT/DELETE coexist
        // with the collection GET/PUT; there is NO
        // `/managers/sync` sub-path so the legal user id
        // `sync` stays a manager edit.
        .route(
            "/bots/{bot_id}/managers",
            get(list_bot_managers).put(sync_bot_managers),
        )
        .route(
            "/bots/{bot_id}/managers/{user_id}",
            put(add_bot_manager).delete(remove_bot_manager),
        )
        // Ownership surface (spec §6; the trusted sync is the
        // 2026-10-29 ownership-sync design).
        .route(
            "/bots/{bot_id}/ownership",
            get(get_bot_ownership).put(sync_bot_ownership),
        )
        .route(
            "/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}",
            put(request_ownership_transfer).get(get_ownership_transfer),
        )
        .route(
            "/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/accept",
            post(accept_ownership_transfer),
        )
        .route(
            "/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/reject",
            post(reject_ownership_transfer),
        )
        .route(
            "/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/cancel",
            post(cancel_ownership_transfer),
        )
        .route("/ownership-transfers", get(list_ownership_transfers))
}

/// The authority facade is fail-closed: an unmounted
/// service is an internal misconfiguration (500), never an
/// empty result.
fn authority_service(
    state: &ApiState,
    request_id: &RequestId,
) -> Result<Arc<dyn BotAuthorityService>, ErrorResponse> {
    state.bot_authority_service.clone().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::internal(
                "V1 BotAuthority service is not configured",
            ),
        )
    })
}

/// `offset=0 / limit=50 / max 100` (spec §3 command
/// grammar).
fn validate_page(
    request_id: &RequestId,
    _offset: u64,
    limit: u64,
) -> Result<(), ErrorResponse> {
    if limit == 0 || limit > MAX_PAGE_LIMIT {
        return Err(invalid_request(
            request_id,
            format!("limit must be in 1..={MAX_PAGE_LIMIT}"),
        ));
    }
    Ok(())
}

#[derive(Debug, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct PageParams {
    #[serde(default)]
    pub offset: u64,
    #[serde(default)]
    pub limit: Option<u64>,
}

/// `GET /bots/{bot_id}/managers` — the Human owner/manager
/// list read (owner/manager rule in the application).
async fn list_bot_managers(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Path(bot_id): Path<String>,
    Query(params): Query<PageParams>,
) -> Result<Response, ErrorResponse> {
    let limit =
        params.limit.unwrap_or(DEFAULT_PAGE_LIMIT);
    validate_page(&request_id, params.offset, limit)?;
    let service = authority_service(&state, &request_id)?;
    let page = service
        .managers(
            &caller,
            ManagerListQuery {
                bot_id,
                offset: params.offset,
                limit,
            },
        )
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    let body: ManagerPageDto = page.into();
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", body, request_id.0)),
    )
        .into_response())
}

/// `PUT /bots/{bot_id}/managers` — the collection replace
/// (spec §5.2). Trusted-App rule; the application rejects a
/// Human identity with no App fallback.
async fn sync_bot_managers(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Path(bot_id): Path<String>,
    body: Result<Json<SyncManagersBody>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(body) = body
        .map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let service = authority_service(&state, &request_id)?;
    let change = service
        .sync_managers(
            &caller,
            ManagerSync {
                bot_id,
                user_ids: body.manager_user_ids,
                // The application builds the real mutation from
                // the verified identity; the adapter never
                // constructs one.
                mutation: placeholder_mutation(),
            },
        )
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    let body = ManagerSetChangeDto::from(change);
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", body, request_id.0)),
    )
        .into_response())
}

/// `PUT /bots/{bot_id}/managers/{user_id}` — add/restore.
/// The legal user id `sync` is exactly a single-item add
/// here (spec §5.2).
async fn add_bot_manager(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Path((bot_id, user_id)): Path<(String, String)>,
) -> Result<Response, ErrorResponse> {
    edit_single_item(
        state,
        caller,
        request_id,
        bot_id,
        user_id,
        ManagerEditAction::Grant,
    )
    .await
}

/// `DELETE /bots/{bot_id}/managers/{user_id}` — remove
/// (spec §5.1: absents stay successful).
async fn remove_bot_manager(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Path((bot_id, user_id)): Path<(String, String)>,
) -> Result<Response, ErrorResponse> {
    edit_single_item(
        state,
        caller,
        request_id,
        bot_id,
        user_id,
        ManagerEditAction::Revoke,
    )
    .await
}

async fn edit_single_item(
    state: ApiState,
    caller: AuthenticatedCaller,
    request_id: RequestId,
    bot_id: String,
    user_id: String,
    action: ManagerEditAction,
) -> Result<Response, ErrorResponse> {
    let service = authority_service(&state, &request_id)?;
    let change = service
        .edit_manager(
            &caller,
            ManagerEdit {
                bot_id,
                user_id,
                action,
                mutation: placeholder_mutation(),
            },
        )
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    let body = ManagerSetChangeDto::from(change);
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", body, request_id.0)),
    )
        .into_response())
}

/// `GET /bots/{bot_id}/ownership` — the owner/manager read.
async fn get_bot_ownership(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Path(bot_id): Path<String>,
) -> Result<Response, ErrorResponse> {
    let service = authority_service(&state, &request_id)?;
    let view = service
        .ownership(&caller, &bot_id)
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    let body = BotOwnershipDto::from(view);
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", body, request_id.0)),
    )
        .into_response())
}

/// `PUT /bots/{bot_id}/ownership` — the trusted owner replace
/// (设计 §3). Trusted-App rule (`ownership:sync`); the
/// application rejects a Human identity with no App fallback.
/// Missing bots are tolerated no-ops (200), never 404.
async fn sync_bot_ownership(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Path(bot_id): Path<String>,
    body: Result<Json<SyncOwnershipBody>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(body) = body
        .map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let service = authority_service(&state, &request_id)?;
    let change = service
        .sync_ownership(
            &caller,
            OwnershipSync {
                bot_id,
                owner_user_id: body.owner_user_id,
                // The application builds the real mutation from
                // the verified identity; the adapter never
                // constructs one.
                mutation: placeholder_mutation(),
            },
        )
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    let body = OwnershipChangeDto::from(change);
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", body, request_id.0)),
    )
        .into_response())
}

/// `PUT /bots/{bot_id}/ownership-transfers/{target}` —
/// initiate or refresh (spec §6.2; new row 201,
/// reuse/refresh 200). Human-rule; the Store re-verifies the
/// current owner inside the transaction.
async fn request_ownership_transfer(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Path((bot_id, target_owner_actor_id)): Path<(String, String)>,
) -> Result<Response, ErrorResponse> {
    let service = authority_service(&state, &request_id)?;
    let outcome = service
        .change_transfer(
            &caller,
            TransferCommand {
                key: TransferKey {
                    bot_id,
                    target_owner_actor_id,
                },
                action: TransferAction::RequestOrRefresh,
                mutation: placeholder_mutation(),
            },
        )
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    transfer_response(outcome, &request_id)
}

/// `GET /bots/{bot_id}/ownership-transfers/{target}` — the
/// DUAL-admission read (initiator/target Human or scoped
/// trusted App, spec §6.1); one handler serves both.
async fn get_ownership_transfer(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Path((bot_id, target_owner_actor_id)): Path<(String, String)>,
) -> Result<Response, ErrorResponse> {
    let service = authority_service(&state, &request_id)?;
    let view = service
        .transfer(
            &caller,
            &TransferKey {
                bot_id,
                target_owner_actor_id,
            },
        )
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    match view {
        None => Err(application_error_response(
            &request_id,
            ApplicationError::not_found(
                "bot_transfer_not_found",
                "no ownership transfer for this target",
            ),
        )),
        Some(view) => {
            let body = TransferViewDto::project(view);
            Ok((
                StatusCode::OK,
                Json(Envelope::success(20_000, "OK", body, request_id.0)),
            )
                .into_response())
        }
    }
}

/// `POST .../accept` — dual admission (target Human or
/// scoped trusted App); one handler serves both.
async fn accept_ownership_transfer(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Path((bot_id, target_owner_actor_id)): Path<(String, String)>,
) -> Result<Response, ErrorResponse> {
    decide_transfer(
        state,
        caller,
        request_id,
        bot_id,
        target_owner_actor_id,
        TransferAction::Accept,
    )
    .await
}

/// `POST .../reject` — dual admission; one handler serves
/// both identities.
async fn reject_ownership_transfer(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Path((bot_id, target_owner_actor_id)): Path<(String, String)>,
) -> Result<Response, ErrorResponse> {
    decide_transfer(
        state,
        caller,
        request_id,
        bot_id,
        target_owner_actor_id,
        TransferAction::Reject,
    )
    .await
}

/// `POST .../cancel` — Human-rule (the initiator).
async fn cancel_ownership_transfer(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    Path((bot_id, target_owner_actor_id)): Path<(String, String)>,
) -> Result<Response, ErrorResponse> {
    decide_transfer(
        state,
        caller,
        request_id,
        bot_id,
        target_owner_actor_id,
        TransferAction::Cancel,
    )
    .await
}

async fn decide_transfer(
    state: ApiState,
    caller: AuthenticatedCaller,
    request_id: RequestId,
    bot_id: String,
    target_owner_actor_id: String,
    action: TransferAction,
) -> Result<Response, ErrorResponse> {
    let service = authority_service(&state, &request_id)?;
    let outcome = service
        .change_transfer(
            &caller,
            TransferCommand {
                key: TransferKey {
                    bot_id,
                    target_owner_actor_id,
                },
                action,
                mutation: placeholder_mutation(),
            },
        )
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    transfer_response(outcome, &request_id)
}

/// `GET /ownership-transfers?direction=received|sent&status=`
/// — the Human inbox/outbox (spec §6.1); the list owner is
/// the authenticated Human. The query is parsed from the
/// RAW string (`TransferListParams::parse_query`): the
/// documented repeatable `status` filter cannot cross a
/// native `Query` extractor, which would 400 every legal
/// sequence as "invalid type: expected a sequence".
async fn list_ownership_transfers(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    RawQuery(raw): RawQuery,
) -> Result<Response, ErrorResponse> {
    let params = dto::TransferListParams::parse_query(raw.as_deref())
        .map_err(|message| invalid_request(&request_id, message))?;
    let received = dto::parse_direction(params.direction.as_deref())
        .map_err(|message| invalid_request(&request_id, message))?;
    let statuses = params
        .statuses()
        .map_err(|message| invalid_request(&request_id, message))?;
    let limit = params.limit.unwrap_or(DEFAULT_PAGE_LIMIT);
    validate_page(&request_id, params.offset, limit)?;
    let service = authority_service(&state, &request_id)?;
    let page = service
        .transfers(
            &caller,
            TransferListQuery {
                // Filled by the application from the
                // authenticated identity.
                user_id: String::new(),
                direction: if received {
                    TransferDirection::Received
                } else {
                    TransferDirection::Sent
                },
                statuses,
                offset: params.offset,
                limit,
            },
        )
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    let body = TransferPageDto {
        items: page
            .items
            .into_iter()
            .map(TransferViewDto::project)
            .collect(),
        total: page.total,
    };
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", body, request_id.0)),
    )
        .into_response())
}

/// New transfer row → 201; reuse/refresh → 200; committed
/// business conflicts → 409 with the pinned code (spec
/// §6.2/§6.4).
fn transfer_response(
    outcome: TransferOutcome,
    request_id: &RequestId,
) -> Result<Response, ErrorResponse> {
    match outcome {
        TransferOutcome::Applied { transfer, change } => {
            let (status, code) = if change == TransferChange::Created {
                (StatusCode::CREATED, 20_100)
            } else {
                (StatusCode::OK, 20_000)
            };
            let body = TransferViewDto::project(transfer);
            Ok((
                status,
                Json(Envelope::success(
                    code,
                    "OK",
                    body,
                    request_id.0.clone(),
                )),
            )
                .into_response())
        }
        TransferOutcome::Conflict { transfer, code } => Err(
            application_error_response(
                request_id,
                ApplicationError::conflict(
                    code.as_str(),
                    format!(
                        "ownership transfer for bot '{}' target '{}' \
                         conflicts: {}",
                        transfer.bot_id,
                        transfer.target_owner_actor_id,
                        code.as_str()
                    ),
                ),
            ),
        ),
    }
}

/// The adapter never supplies a mutation: the application
/// builds it from the verified identity and overrides this
/// placeholder value.
fn placeholder_mutation() -> AuthorityMutation {
    AuthorityMutation {
        mutation_id: String::new(),
        actor: AuthorityActor::System {
            name: "unused-by-adapter".to_string(),
        },
    }
}
