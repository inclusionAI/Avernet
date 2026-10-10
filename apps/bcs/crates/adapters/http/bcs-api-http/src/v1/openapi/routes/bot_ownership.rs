//! Human-only ownership view/transfer routes (plan Task 14, spec
//! §9-§11): the ownership query, the idempotent transfer create, the
//! both-parties inbox/outbox read, and the bodyless
//! accept/reject/cancel decisions.
//!
//! Delivery-adapter responsibilities ONLY (the routes never query the
//! database): parse the request, forward the VERIFIED Human Principal
//! and exact path identities into the application use cases, and
//! project their results into the shared envelope. HumanOnly gating,
//! the current-owner/manager read role, concealment 404s, the typed
//! conflict codes and the committed-outcome mapping live in the
//! application lane.
//!
//! Status-code contract (spec §11.1): the FIRST create answers 201
//! Created; a same-payload idempotent replay answers 200 with the
//! ORIGINAL historical receipt. Every other success is 200.

use std::sync::Arc;

use axum::Router;
use axum::body::Bytes;
use axum::Json;
use axum::extract::rejection::{PathRejection, QueryRejection};
use axum::extract::{Extension, Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, BotOwnershipTransferReceipt, CreateBotOwnershipTransfer,
    DecideBotOwnershipTransfer, GetBotOwnership, GetBotOwnershipTransfer, ListBotOwnershipTransfers,
    OwnershipTransferService,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::bot_ownership::{
    BotOwnershipDto, BotOwnershipTransferPageDto, BotOwnershipTransferReceiptDto,
    CreateBotOwnershipTransferRequest, ListBotOwnershipTransfersQuery,
};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/bots/{bot_id}/ownership", get(get_ownership))
        .route(
            "/bots/{bot_id}/ownership-transfers",
            post(create_transfer),
        )
        .route("/ownership-transfers", get(list_transfers))
        .route("/ownership-transfers/{transfer_id}", get(get_transfer))
        .route(
            "/ownership-transfers/{transfer_id}/accept",
            post(accept_transfer),
        )
        .route(
            "/ownership-transfers/{transfer_id}/reject",
            post(reject_transfer),
        )
        .route(
            "/ownership-transfers/{transfer_id}/cancel",
            post(cancel_transfer),
        )
}

fn service(
    state: &ApiState,
    request_id: &RequestId,
) -> Result<Arc<dyn OwnershipTransferService>, ErrorResponse> {
    state.ownership_transfer_service.clone().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::internal("Ownership transfer V1 service is not configured"),
        )
    })
}

async fn get_ownership(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(bot_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let ownership = service(&state, &request_id)?
        .get_ownership(GetBotOwnership { caller, bot_id })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(
            20_000,
            "OK",
            BotOwnershipDto::from(ownership),
            request_id.0,
        )),
    )
        .into_response())
}

async fn create_transfer(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    bytes: Bytes,
) -> Result<Response, ErrorResponse> {
    let Path(bot_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let body: CreateBotOwnershipTransferRequest = serde_json::from_slice(&bytes)
        .map_err(|error| invalid_request(&request_id, format!("invalid create body: {error}")))?;
    body.validate()
        .map_err(|message| invalid_request(&request_id, message))?;
    let creation = service(&state, &request_id)?
        .create_transfer(CreateBotOwnershipTransfer {
            caller,
            bot_id,
            to_user_id: body.to_user_id,
            expected_owner_version: body.expected_owner_version,
            client_request_id: body.client_request_id,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    // The FIRST committed creation answers 201; a same-payload replay
    // keeps the original receipt and answers 200 (spec §11.1).
    let status = if creation.created {
        StatusCode::CREATED
    } else {
        StatusCode::OK
    };
    Ok((
        status,
        Json(Envelope::success(
            20_000,
            "OK",
            BotOwnershipTransferReceiptDto::from(creation.receipt),
            request_id.0,
        )),
    )
        .into_response())
}

async fn list_transfers(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ListBotOwnershipTransfersQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Query(query) =
        query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    query
        .validate()
        .map_err(|message| invalid_request(&request_id, message))?;
    let page = service(&state, &request_id)?
        .list_transfers(ListBotOwnershipTransfers {
            caller,
            direction: query.direction.into(),
            status: query.status.map(Into::into),
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
            BotOwnershipTransferPageDto {
                items: page.items.into_iter().map(Into::into).collect(),
                total: page.total,
                offset: page.offset,
                limit: page.limit,
            },
            request_id.0,
        )),
    )
        .into_response())
}

async fn get_transfer(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(transfer_id) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let receipt = service(&state, &request_id)?
        .get_transfer(GetBotOwnershipTransfer { caller, transfer_id })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    receipt_response(receipt, &request_id)
}

/// Accept/reject/cancel carry NO business body (spec §11.1): an absent
/// body or an empty JSON object is the request contract; any other
/// content is rejected 400 before any application call.
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
            "the transfer decision carries no business body; unknown fields \
             or any other content are rejected",
        ))
    }
}

async fn accept_transfer(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    bytes: Bytes,
) -> Result<Response, ErrorResponse> {
    decide_route(state, caller, request_id, path, bytes, TransferDecision::Accept).await
}

async fn reject_transfer(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    bytes: Bytes,
) -> Result<Response, ErrorResponse> {
    decide_route(state, caller, request_id, path, bytes, TransferDecision::Reject).await
}

async fn cancel_transfer(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    bytes: Bytes,
) -> Result<Response, ErrorResponse> {
    decide_route(state, caller, request_id, path, bytes, TransferDecision::Cancel).await
}

enum TransferDecision {
    Accept,
    Reject,
    Cancel,
}

async fn decide_route(
    state: ApiState,
    caller: AuthenticatedCaller,
    request_id: RequestId,
    path: Result<Path<String>, PathRejection>,
    bytes: Bytes,
    decision: TransferDecision,
) -> Result<Response, ErrorResponse> {
    let Path(transfer_id) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    reject_business_body(&request_id, &bytes)?;
    let command = DecideBotOwnershipTransfer { caller, transfer_id };
    let outcome = match decision {
        TransferDecision::Accept => service(&state, &request_id)?.accept_transfer(command).await,
        TransferDecision::Reject => service(&state, &request_id)?.reject_transfer(command).await,
        TransferDecision::Cancel => service(&state, &request_id)?.cancel_transfer(command).await,
    };
    match outcome {
        Ok(receipt) => receipt_response(receipt, &request_id),
        Err(error) => Err(application_error_response(&request_id, error)),
    }
}

fn receipt_response(
    receipt: BotOwnershipTransferReceipt,
    request_id: &RequestId,
) -> Result<Response, ErrorResponse> {
    Ok((
        StatusCode::OK,
        Json(Envelope::success(
            20_000,
            "OK",
            BotOwnershipTransferReceiptDto::from(receipt),
            request_id.0.clone(),
        )),
    )
        .into_response())
}