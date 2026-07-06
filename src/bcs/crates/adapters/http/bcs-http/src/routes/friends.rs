use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use bcs_protocol::{CreateFriendRequestBody, ListFriendRequestsQuery};
use bcs_service_api::{
    BotDetailCommand, BotUseCaseError, CreateFriendRequestCommand, FriendRequest,
    FriendRequestDecisionCommand, FriendRequestDirection, FriendRequestStatus, FriendUseCaseError,
    ListFriendRequestsCommand, ListFriendsCommand, ServiceError,
};
use serde_json::Value;

use crate::state::HttpAppState;


pub async fn create_friend_request(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<CreateFriendRequestBody>,
) -> Response {
    let caller_actor_id =
        match resolve_caller(&state, &headers, &uri, req.from_bot.as_deref()).await {
            Ok(caller) => caller,
            Err(ResolveCallerError::NoCaller) => {
                return error_response(
                    StatusCode::UNAUTHORIZED,
                    "Unauthorized: no valid token or from_bot provided",
                );
            }
            Err(ResolveCallerError::NoUserIdentity) => {
                return error_response(
                    StatusCode::UNAUTHORIZED,
                    "Unauthorized: no valid token or login session",
                );
            }
            Err(ResolveCallerError::BotNotFound(actor_id)) => {
                return error_response(
                    StatusCode::NOT_FOUND,
                    format!("Bot '{}' not found", actor_id),
                );
            }
            Err(ResolveCallerError::OwnershipDenied(actor_id)) => {
                return error_response(
                    StatusCode::FORBIDDEN,
                    format!("Forbidden: not authorized to act as bot '{}'", actor_id),
                );
            }
        };

    match state
        .services
        .friend_use_cases
        .create_friend_request(CreateFriendRequestCommand {
            caller_actor_id,
            to_bot: req.to_bot,
        })
        .await
    {
        Ok(request) => friend_request_created_response(request),
        Err(FriendUseCaseError::Service(ServiceError::PendingRequestExists {
            request_id,
            from_bot,
            to_bot,
        })) => (
            StatusCode::OK,
            Json(serde_json::json!({
                "success": true,
                "data": {
                    "id": request_id,
                    "from_bot": from_bot,
                    "to_bot": to_bot,
                    "status": "pending",
                    "message": "Friend request already pending"
                }
            })),
        )
            .into_response(),
        Err(error) => friend_use_case_error_response(error),
    }
}

pub async fn list_friend_requests(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Query(query): Query<ListFriendRequestsQuery>,
) -> Response {
    let direction = match query.direction.as_deref() {
        Some("sent") => FriendRequestDirection::Sent,
        Some("all") => FriendRequestDirection::All,
        _ => FriendRequestDirection::Received,
    };
    let status_filter = match query.status.as_deref() {
        Some("pending") => Some(FriendRequestStatus::Pending),
        Some("accepted") => Some(FriendRequestStatus::Accepted),
        Some("rejected") => Some(FriendRequestStatus::Rejected),
        _ => None,
    };

    let caller_actor_id =
        match resolve_caller(&state, &headers, &uri, query.bot_uuid.as_deref()).await {
            Ok(caller) => caller,
            Err(ResolveCallerError::NoCaller) => {
                return error_response(
                    StatusCode::UNAUTHORIZED,
                    "Unauthorized: no valid token or bot_uuid provided",
                );
            }
            Err(ResolveCallerError::NoUserIdentity) => {
                return error_response(
                    StatusCode::UNAUTHORIZED,
                    "Unauthorized: no valid token or login session",
                );
            }
            Err(ResolveCallerError::BotNotFound(actor_id)) => {
                return error_response(
                    StatusCode::NOT_FOUND,
                    format!("Bot '{}' not found", actor_id),
                );
            }
            Err(ResolveCallerError::OwnershipDenied(actor_id)) => {
                return error_response(
                    StatusCode::FORBIDDEN,
                    format!("Forbidden: not authorized to act as bot '{}'", actor_id),
                );
            }
        };

    let requests = match state
        .services
        .friend_use_cases
        .list_friend_requests(ListFriendRequestsCommand {
            caller_actor_id,
            direction,
            status_filter,
        })
        .await
    {
        Ok(requests) => requests,
        Err(error) => {
            return friend_use_case_error_response(error);
        }
    };

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "success": true,
            "data": requests.into_iter().map(friend_request_to_json).collect::<Vec<_>>()
        })),
    )
        .into_response()
}

pub async fn accept_friend_request(
    State(state): State<HttpAppState>,
    Path(request_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let caller = match resolve_request_receiver_caller(
        &state,
        &headers,
        &uri,
        &request_id,
        "accept",
    )
    .await
    {
        Ok(caller) => caller,
        Err(response) => return response,
    };

    match state
        .services
        .friend_use_cases
        .accept_friend_request(FriendRequestDecisionCommand {
            caller_actor_id: caller.actor_id,
            request_id,
            request_to_bot: caller.request_to_bot,
        })
        .await
    {
        Ok(()) => success_empty_response(),
        Err(error) => friend_use_case_error_response(error),
    }
}

pub async fn reject_friend_request(
    State(state): State<HttpAppState>,
    Path(request_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let caller = match resolve_request_receiver_caller(
        &state,
        &headers,
        &uri,
        &request_id,
        "reject",
    )
    .await
    {
        Ok(caller) => caller,
        Err(response) => return response,
    };

    match state
        .services
        .friend_use_cases
        .reject_friend_request(FriendRequestDecisionCommand {
            caller_actor_id: caller.actor_id,
            request_id,
            request_to_bot: caller.request_to_bot,
        })
        .await
    {
        Ok(()) => success_empty_response(),
        Err(error) => friend_use_case_error_response(error),
    }
}

pub async fn list_friends(
    State(state): State<HttpAppState>,
    Path(bot_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let caller_actor_id = match resolve_friends_list_caller(&state, &headers, &uri, &bot_id).await {
        Ok(caller) => caller,
        Err(response) => return response,
    };

    let friends = match state
        .services
        .friend_use_cases
        .list_friends(ListFriendsCommand {
            caller_actor_id,
            target_actor_id: bot_id,
        })
        .await
    {
        Ok(friends) => friends,
        Err(error) => {
            return friend_use_case_error_response(error);
        }
    };

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "success": true,
            "data": friends
        })),
    )
        .into_response()
}

#[derive(Debug)]
enum ResolveCallerError {
    NoCaller,
    NoUserIdentity,
    BotNotFound(String),
    OwnershipDenied(String),
}

#[derive(Debug)]
enum ActorOwnershipError {
    NoUserIdentity,
    BotNotFound,
    Denied,
}

struct ResolvedRequestReceiverCaller {
    actor_id: String,
    request_to_bot: Option<String>,
}

async fn resolve_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
    requested_actor_id: Option<&str>,
) -> Result<String, ResolveCallerError> {
    if let Some(actor_id) = optional_caller_from_token(state, headers).await {
        return Ok(actor_id);
    }

    if let Some(actor_id) = requested_actor_id.filter(|id| !id.is_empty()) {
        match check_actor_ownership(state, headers, uri, actor_id).await {
            Ok(()) => return Ok(actor_id.to_string()),
            Err(ActorOwnershipError::NoUserIdentity) => {
                return Err(ResolveCallerError::NoUserIdentity);
            }
            Err(ActorOwnershipError::BotNotFound) => {
                return Err(ResolveCallerError::BotNotFound(actor_id.to_string()));
            }
            Err(ActorOwnershipError::Denied) => {}
        }
        return Err(ResolveCallerError::OwnershipDenied(actor_id.to_string()));
    }

    Err(ResolveCallerError::NoCaller)
}

async fn resolve_request_receiver_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
    request_id: &str,
    action: &str,
) -> Result<ResolvedRequestReceiverCaller, Response> {
    if let Some(actor_id) = optional_caller_from_token(state, headers).await {
        return Ok(ResolvedRequestReceiverCaller {
            actor_id,
            request_to_bot: None,
        });
    }

    let request_to_bot = match state
        .services
        .friend_use_cases
        .friend_request_receiver(request_id)
        .await
    {
        Ok(to_bot) => to_bot,
        Err(error) => return Err(friend_use_case_error_response(error)),
    };

    match check_actor_ownership(state, headers, uri, &request_to_bot).await {
        Ok(()) => Ok(ResolvedRequestReceiverCaller {
            actor_id: request_to_bot.clone(),
            request_to_bot: Some(request_to_bot),
        }),
        Err(ActorOwnershipError::NoUserIdentity) => Err(error_response(
            StatusCode::UNAUTHORIZED,
            "Unauthorized: no valid token or login session",
        )),
        Err(ActorOwnershipError::BotNotFound) => Err(error_response(
            StatusCode::NOT_FOUND,
            format!("Bot '{}' not found", request_to_bot),
        )),
        Err(ActorOwnershipError::Denied) => Err(error_response(
            StatusCode::FORBIDDEN,
            format!(
                "Not authorized to {} request for bot '{}'",
                action, request_to_bot
            ),
        )),
    }
}

async fn resolve_friends_list_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
    target_actor_id: &str,
) -> Result<String, Response> {
    if let Some(actor_id) = optional_caller_from_token(state, headers).await {
        return Ok(actor_id);
    }

    match check_actor_ownership(state, headers, uri, target_actor_id).await {
        Ok(()) => Ok(target_actor_id.to_string()),
        Err(ActorOwnershipError::NoUserIdentity) => Err(error_response(
            StatusCode::UNAUTHORIZED,
            "Unauthorized: no valid token or login session",
        )),
        Err(ActorOwnershipError::BotNotFound) => Err(error_response(
            StatusCode::NOT_FOUND,
            format!("Bot '{}' not found", target_actor_id),
        )),
        Err(ActorOwnershipError::Denied) => Err(error_response(
            StatusCode::FORBIDDEN,
            format!("Not authorized to access bot '{}'", target_actor_id),
        )),
    }
}

async fn check_actor_ownership(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
    actor_id: &str,
) -> Result<(), ActorOwnershipError> {
    let staff_no = state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|identity| identity.staff_no)
        .filter(|staff_no| !staff_no.is_empty());
    let Some(staff_no) = staff_no else {
        return Err(ActorOwnershipError::NoUserIdentity);
    };

    match state
        .services
        .bot_query
        .get_bot(BotDetailCommand {
            caller_actor_id: None,
            bot_id: actor_id.to_string(),
        })
        .await
    {
        Ok(bot) => {
            if bot
                .created_by
                .as_deref()
                .map(|owner| owner == staff_no)
                .unwrap_or(true)
            {
                Ok(())
            } else {
                Err(ActorOwnershipError::Denied)
            }
        }
        Err(BotUseCaseError::Service(
            ServiceError::BotNotFound(_) | ServiceError::BotNotRegistered(_),
        )) => Err(ActorOwnershipError::BotNotFound),
        Err(_) => Err(ActorOwnershipError::Denied),
    }
}

async fn optional_caller_from_token(state: &HttpAppState, headers: &HeaderMap) -> Option<String> {
    state.bot_uuid_from_headers(headers).await
}

fn friend_request_created_response(request: FriendRequest) -> Response {
    if request.id.is_empty() {
        return (
            StatusCode::OK,
            Json(serde_json::json!({
                "success": true,
                "message": "Already friends"
            })),
        )
            .into_response();
    }

    let status_code = match request.status {
        FriendRequestStatus::Accepted => StatusCode::OK,
        _ => StatusCode::CREATED,
    };

    (
        status_code,
        Json(serde_json::json!({
            "success": true,
            "data": friend_request_to_json(request)
        })),
    )
        .into_response()
}

fn friend_request_to_json(request: FriendRequest) -> Value {
    serde_json::json!({
        "id": request.id,
        "from_bot": request.from_bot,
        "to_bot": request.to_bot,
        "status": request.status,
        "created_at": request.created_at,
        "updated_at": request.updated_at
    })
}

fn success_empty_response() -> Response {
    (
        StatusCode::OK,
        Json(serde_json::json!({
            "success": true
        })),
    )
        .into_response()
}

fn service_error_response(error: ServiceError) -> Response {
    let status = match error {
        ServiceError::CannotAddSelf => StatusCode::BAD_REQUEST,
        ServiceError::PendingRequestExists { .. } => StatusCode::CONFLICT,
        ServiceError::BotNotFound(_) | ServiceError::BotNotRegistered(_) => StatusCode::NOT_FOUND,
        ServiceError::FriendRequestNotFound(_) => StatusCode::NOT_FOUND,
        ServiceError::NotFriends(_) => StatusCode::FORBIDDEN,
        ServiceError::Conflict(_) => StatusCode::CONFLICT,
        ServiceError::InvalidOperation { .. }
        | ServiceError::CannotAcceptRejected
        | ServiceError::CannotRejectAccepted => StatusCode::CONFLICT,
        ServiceError::PrivateBotCannotCollaborate => StatusCode::FORBIDDEN,
        _ => StatusCode::INTERNAL_SERVER_ERROR,
    };
    error_response(status, error.to_string())
}

fn friend_use_case_error_response(error: FriendUseCaseError) -> Response {
    match error {
        FriendUseCaseError::Forbidden(message) => error_response(StatusCode::FORBIDDEN, message),
        FriendUseCaseError::Service(error) => service_error_response(error),
    }
}

fn error_response(status: StatusCode, message: impl Into<String>) -> Response {
    (
        status,
        Json(serde_json::json!({
            "success": false,
            "error": message.into()
        })),
    )
        .into_response()
}
