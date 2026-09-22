//! Route-boundary error translation and caller resolution helpers.

use super::*;

pub(crate) fn group_application_error_response(error: ApplicationError) -> Response {
    match error {
        ApplicationError::InvalidInput { code, message } => {
            legacy_group_error_response(StatusCode::BAD_REQUEST, &code, message)
        }
        ApplicationError::Unauthenticated => legacy_group_error_response(
            StatusCode::UNAUTHORIZED,
            "unauthenticated",
            "authentication is required",
        ),
        ApplicationError::Forbidden(message) => {
            legacy_group_error_response(StatusCode::FORBIDDEN, "forbidden", message)
        }
        ApplicationError::ForbiddenCode { code, message } => {
            legacy_group_error_response(StatusCode::FORBIDDEN, &code, message)
        }
        ApplicationError::NotFound { code, message } => {
            legacy_group_error_response(StatusCode::NOT_FOUND, &code, message)
        }
        ApplicationError::Conflict { code, message } => {
            legacy_group_error_response(StatusCode::CONFLICT, &code, message)
        }
        ApplicationError::Gone { code, message } => {
            legacy_group_error_response(StatusCode::GONE, &code, message)
        }
        ApplicationError::QuotaExceeded { code, message } => {
            legacy_group_error_response(StatusCode::TOO_MANY_REQUESTS, &code, message)
        }
        ApplicationError::PayloadTooLarge { code, message } => {
            legacy_group_error_response(StatusCode::PAYLOAD_TOO_LARGE, &code, message)
        }
        ApplicationError::Unprocessable { code, message } => {
            legacy_group_error_response(StatusCode::UNPROCESSABLE_ENTITY, &code, message)
        }
        ApplicationError::BadGateway { code, message } => {
            legacy_group_error_response(StatusCode::BAD_GATEWAY, &code, message)
        }
        // COSEC: keep persistence and infrastructure details out of the legacy response.
        ApplicationError::Internal(error) => {
            tracing::error!(request_id = %bcs_observability::CurrentRequestId, error = %error, "legacy Group application request failed");
            legacy_group_error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal_error",
                "internal server error",
            )
        }
    }
}

pub(crate) fn legacy_group_error_response(
    status: StatusCode,
    code: &str,
    message: impl Into<String>,
) -> Response {
    let message = message.into();
    (
        status,
        Json(serde_json::json!({
            "status": status.as_u16(),
            "code": code,
            "message": message,
            "error": message,
        })),
    )
        .into_response()
}


pub(crate) fn group_use_case_error_to_http(error: GroupUseCaseError) -> HttpAdapterError {
    match error {
        GroupUseCaseError::Unauthorized(message) => HttpAdapterError::Unauthorized(message),
        GroupUseCaseError::Forbidden(message) => HttpAdapterError::Forbidden(message),
        GroupUseCaseError::InvalidProposal(message)
            if message.starts_with("invalid_opening_message:") =>
        {
            HttpAdapterError::InvalidOpeningMessage(
                message
                    .strip_prefix("invalid_opening_message:")
                    .unwrap_or(&message)
                    .trim()
                    .to_string(),
            )
        }
        GroupUseCaseError::InvalidGroupId(message)
        | GroupUseCaseError::InvalidGroupStatus(message)
        | GroupUseCaseError::InvalidProposal(message) => HttpAdapterError::BadRequest(message),
        GroupUseCaseError::InvalidHistoryLimit(limit) => {
            HttpAdapterError::BadRequest(format!("Invalid history limit: {}", limit))
        }
        GroupUseCaseError::ActorNotFound(actor_id) => {
            HttpAdapterError::NotFound(format!("Actor '{}' not found", actor_id))
        }
        GroupUseCaseError::ProposalNotFound(proposal_id)
        | GroupUseCaseError::ProposalExpired(proposal_id) => {
            HttpAdapterError::NotFound(format!("Proposal '{}' not found or expired", proposal_id))
        }
        GroupUseCaseError::InvalidParticipantMode { mode, actor_kind } => {
            HttpAdapterError::BadRequest(format!(
                "mode '{:?}' is not valid for actor_kind '{:?}'",
                mode, actor_kind
            ))
        }
        GroupUseCaseError::Service(ServiceError::ExistNonPublicBots { bots }) => {
            let bot_list: Vec<Value> = bots
                .iter()
                .map(|(uuid, name)| {
                    serde_json::json!({
                        "bot_uuid": uuid,
                        "bot_name": name.as_deref().unwrap_or(uuid),
                    })
                })
                .collect();
            HttpAdapterError::BadRequestStructured {
                message: "Group contains non-public bots preventing visibility change".into(),
                params: serde_json::json!({
                    "code": "exist_none_public_bots",
                    "bots": bot_list,
                }),
            }
        }
        GroupUseCaseError::Conflict(message) => HttpAdapterError::Conflict(message),
        GroupUseCaseError::Service(error) => HttpAdapterError::Service(error),
    }
}

pub(crate) fn collaboration_runtime_error_to_http(error: CollaborationRuntimeError) -> HttpAdapterError {
    match error {
        CollaborationRuntimeError::InvalidDefinition(message)
        | CollaborationRuntimeError::InvalidParticipantBinding(message)
        | CollaborationRuntimeError::InvalidRequest(message) => {
            HttpAdapterError::BadRequest(message)
        }
        CollaborationRuntimeError::DefinitionNotFound(id, version) => HttpAdapterError::NotFound(
            format!("CollaborationDefinition '{}@{}' not found", id, version),
        ),
        CollaborationRuntimeError::RunNotFound(run_id) => {
            HttpAdapterError::NotFound(format!("StateMachineRun '{}' not found", run_id))
        }
        CollaborationRuntimeError::NodeNotFound { run_id, node_id } => HttpAdapterError::NotFound(
            format!("StateMachineNodeRun '{run_id}/{node_id}' not found"),
        ),
        CollaborationRuntimeError::Unauthenticated => {
            HttpAdapterError::Unauthorized("authentication is required".to_string())
        }
        CollaborationRuntimeError::Forbidden(message) => HttpAdapterError::Forbidden(message),
        CollaborationRuntimeError::JudgeUnavailable(message) => {
            HttpAdapterError::Service(ServiceError::InternalError(message))
        }
        CollaborationRuntimeError::Conflict(message) => HttpAdapterError::Conflict(message),
        CollaborationRuntimeError::Internal(error) => HttpAdapterError::Service(error),
    }
}

pub(crate) async fn resolve_put_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<String, Response> {
    if let Some(caller) = state.bot_uuid_from_headers(headers).await {
        return Ok(caller);
    }

    if let Some(staff_no) = state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|identity| identity.staff_no)
        .filter(|staff_no| !staff_no.is_empty())
    {
        return Ok(format!("human_{}", staff_no));
    }

    Err((
        StatusCode::UNAUTHORIZED,
        Json(serde_json::json!({
            "success": false,
            "error": "Unauthorized: no valid token or login session"
        })),
    )
        .into_response())
}

pub(crate) async fn extract_human_actor_id(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Option<String> {
    state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|identity| identity.staff_no)
        .filter(|staff_no| !staff_no.is_empty())
        .map(|staff_no| format!("human_{}", staff_no))
}

/// Resolve the caller bot identity for group member management endpoints.
///
/// Tries bot token first; falls back to human identity (cookie or
/// X-Mock-User-Id in local/dev mode) and looks up a bot owned by
/// the human that has coordinator access to the group.
pub(crate) async fn resolve_group_member_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
    group_id: &str,
) -> Result<String, HttpAdapterError> {
    if let Ok(bot_id) = authenticated_bot_from_headers(state, headers).await {
        return Ok(bot_id);
    }

    let human_id = extract_human_actor_id(state, headers, uri)
        .await
        .ok_or_else(|| HttpAdapterError::Unauthorized("valid bot token is required".to_string()))?;

    let group = state
        .services
        .group_query
        .get_group(GroupDetailCommand {
            group_id: group_id.to_string(),
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    let staff_no = human_id.strip_prefix("human_").unwrap_or(&human_id);

    let owned = state
        .services
        .bot_query
        .list_bots_by_creator(staff_no)
        .await
        .map_err(bot_use_case_error_to_http)?;
    let coordinator = owned.iter().find(|b| {
        b.bot_uuid == group.driver_bot_id || group.originator.as_deref() == Some(&b.bot_uuid)
    });

    coordinator.map(|b| b.bot_uuid.clone()).ok_or_else(|| {
        HttpAdapterError::Forbidden(
            "human caller does not own a coordinator bot in this group".to_string(),
        )
    })
}

pub(crate) async fn resolve_actor_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<String, HttpAdapterError> {
    let explicit_bot_token = headers.contains_key("X-BCS-Bot-Token");
    match authenticated_bot_from_headers(state, headers).await {
        Ok(bot_id) if !bot_id.trim().is_empty() => return Ok(bot_id),
        Ok(_) if explicit_bot_token => {
            return Err(HttpAdapterError::Unauthorized(
                "valid bot token is required".to_string(),
            ));
        }
        Err(error) if explicit_bot_token => return Err(error),
        _ => {}
    }

    extract_human_actor_id(state, headers, uri)
        .await
        .ok_or_else(|| {
            HttpAdapterError::Unauthorized(
                "valid bot token or human cookie is required".to_string(),
            )
        })
}


pub(crate) fn group_use_case_error_to_mode_response(error: GroupUseCaseError) -> (StatusCode, Json<Value>) {
    match error {
        GroupUseCaseError::Unauthorized(message) => (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({
                "success": false,
                "error": message
            })),
        ),
        GroupUseCaseError::Forbidden(message) => (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "success": false,
                "error": message
            })),
        ),
        GroupUseCaseError::ActorNotFound(actor_id) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "success": false,
                "error": format!("Actor '{}' not found", actor_id)
            })),
        ),
        GroupUseCaseError::ProposalNotFound(proposal_id)
        | GroupUseCaseError::ProposalExpired(proposal_id) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "success": false,
                "error": format!("Proposal '{}' not found or expired", proposal_id)
            })),
        ),
        GroupUseCaseError::InvalidParticipantMode { mode, actor_kind } => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "success": false,
                "error": format!(
                    "mode '{:?}' is not valid for actor_kind '{:?}'",
                    mode, actor_kind
                )
            })),
        ),
        GroupUseCaseError::InvalidGroupId(message)
        | GroupUseCaseError::InvalidGroupStatus(message)
        | GroupUseCaseError::InvalidProposal(message) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "success": false,
                "error": message
            })),
        ),
        GroupUseCaseError::InvalidHistoryLimit(limit) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "success": false,
                "error": format!("Invalid history limit: {}", limit)
            })),
        ),
        GroupUseCaseError::Conflict(message) => (
            StatusCode::CONFLICT,
            Json(serde_json::json!({
                "success": false,
                "error": message
            })),
        ),
        GroupUseCaseError::Service(error) => service_error_to_mode_response(error),
    }
}

pub(crate) fn service_error_to_mode_response(error: ServiceError) -> (StatusCode, Json<Value>) {
    let (status, message) = match &error {
        ServiceError::CannotAddSelf => (StatusCode::BAD_REQUEST, error.to_string()),
        ServiceError::PendingRequestExists { .. } => (StatusCode::CONFLICT, error.to_string()),
        ServiceError::BotNotFound(_) => (StatusCode::NOT_FOUND, error.to_string()),
        ServiceError::BotNotRegistered(_) => (StatusCode::NOT_FOUND, error.to_string()),
        ServiceError::FriendRequestNotFound(_) => (StatusCode::NOT_FOUND, error.to_string()),
        ServiceError::NotFriends(_) => (StatusCode::FORBIDDEN, error.to_string()),
        ServiceError::Conflict(_) => (StatusCode::CONFLICT, error.to_string()),
        ServiceError::InvalidOperation { .. } => (StatusCode::CONFLICT, error.to_string()),
        ServiceError::CannotAcceptRejected => (StatusCode::CONFLICT, error.to_string()),
        ServiceError::CannotRejectAccepted => (StatusCode::CONFLICT, error.to_string()),
        ServiceError::PrivateBotCannotCollaborate => (StatusCode::FORBIDDEN, error.to_string()),
        _ => (StatusCode::INTERNAL_SERVER_ERROR, error.to_string()),
    };
    (
        status,
        Json(serde_json::json!({
            "success": false,
            "error": message
        })),
    )
}

pub(crate) fn routing_policy_from_protocol_value(
    routing_policy: Option<Value>,
) -> Result<Option<RoutingPolicy>, HttpAdapterError> {
    match routing_policy {
        Some(value) => serde_json::from_value(value)
            .map(Some)
            .map_err(|e| HttpAdapterError::BadRequest(format!("invalid routing_policy: {}", e))),
        None => Ok(None),
    }
}

pub(crate) fn group_kind_from_str(value: &str) -> Result<GroupKind, HttpAdapterError> {
    match value {
        "normal" => Ok(GroupKind::Normal),
        "dm" => Ok(GroupKind::Dm),
        other => Err(HttpAdapterError::BadRequest(format!(
            "invalid group_kind: '{}'",
            other
        ))),
    }
}

pub(crate) fn group_kind_filter(filter: GroupKindFilter) -> Option<GroupKind> {
    match filter {
        GroupKindFilter::Normal => Some(GroupKind::Normal),
        GroupKindFilter::Dm => Some(GroupKind::Dm),
        GroupKindFilter::All => None,
    }
}


pub(crate) fn validate_service_spec_callback_urls(
    guard: &OutboundUrlGuard,
    service_spec: Option<&ServiceSpec>,
) -> Result<(), String> {
    let Some(callback_config) = service_spec.and_then(|spec| spec.callback_config.as_ref()) else {
        return Ok(());
    };
    for (index, channel) in callback_config.channels.iter().enumerate() {
        if let CallbackChannelConfig::Baas { base_url, .. } = channel {
            guard
                .validate_configured_http_url(base_url)
                .map_err(|error| {
                    format!(
                        "service_spec.callback_config.channels[{index}].base_url is not allowed: {error}"
                    )
                })?;
        }
    }
    Ok(())
}

// ---------------------------------------------------------------
// PATCH /groups/{id}/settings
// ---------------------------------------------------------------
