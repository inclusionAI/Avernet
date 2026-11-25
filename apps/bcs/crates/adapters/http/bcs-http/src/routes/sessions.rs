//! Session HTTP handlers.
//!
//! Phase 2a: 9 endpoints mounted at /groups/{id}/sessions and /sessions/{sid}/...
//! Full auth (caller resolution + 6eb4b6384 permission check) for create/complete
//! is deferred to Task 9.

use axum::{
    Json,
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use serde_json::Value;

use bcs_domain::ActorKind;
use bcs_service_api::StateMachineRunView;

use crate::state::HttpAppState;

mod creation;
mod queries;
mod lifecycle;
mod participants;
mod chat;
mod history;
mod collections;

pub use creation::{CreateSessionRequest, create_session_for_group};
pub use queries::{ListSessionsQuery, get_session_by_id, list_sessions_for_group};
pub use lifecycle::{PatchSessionRequest, complete_session, delete_session, patch_session};
pub use participants::{AddParticipantRequest, UpdateParticipantModeRequest,
    add_session_participant, remove_session_participant, update_session_participant_mode};
pub use chat::{SessionChatRequest, session_chat};
pub use history::{GetSessionMessagesQuery, get_session_messages};
pub use collections::{CollectSessionRequest, collect_session, uncollect_session};
use collections::resolve_collector_bot;

// ---------------------------------------------------------------
// helpers
// ---------------------------------------------------------------

/// Serialize a Session with both `id` and `session_id` keys for backward
/// compatibility with test scripts and legacy clients that expect `session_id`.
fn session_to_json(sess: &bcs_service_api::Session) -> Value {
    let mut v = serde_json::to_value(sess).unwrap_or(Value::Null);
    if let Some(obj) = v.as_object_mut() {
        let sid = obj.get("id").and_then(|v| v.as_str().map(str::to_string));
        if let Some(sid) = sid {
            obj.insert("session_id".into(), Value::String(sid));
        }
    }
    v
}

fn session_to_json_with_state_machine_run(
    sess: &bcs_service_api::Session,
    run: Option<&StateMachineRunView>,
    initial_run: Option<&bcs_service_api::InitialSessionRun>,
) -> Value {
    let mut v = session_to_json(sess);
    if let (Some(obj), Some(run)) = (v.as_object_mut(), run) {
        obj.insert(
            "state_machine_run_id".into(),
            Value::String(run.run.run_id.clone()),
        );
        obj.insert(
            "state_machine_run".into(),
            serde_json::to_value(run).unwrap_or(Value::Null),
        );
    }
    if let (Some(obj), Some(initial_run)) = (v.as_object_mut(), initial_run) {
        obj.insert(
            "initial_run".into(),
            serde_json::to_value(initial_run).unwrap_or(Value::Null),
        );
    }
    v
}

/// Live Human→Bot control judgment for the Session routes
/// (bot-authority plan Task 6.5, spec §8): the legacy
/// `list_bots_by_creator` / `created_by` probes are gone — the
/// routes consult the container-injected authority hook. A
/// failing authority read is a 500, never a deny-as-forbidden.
pub(crate) async fn human_controls_bot(
    state: &HttpAppState,
    staff_no: &str,
    bot_id: &str,
) -> Result<bool, crate::error::HttpAdapterError> {
    match state.services.bot_authority_hook.can_manage(staff_no, bot_id).await {
        Ok(controlled) => Ok(controlled),
        Err(error) => Err(crate::error::HttpAdapterError::Service(
            bcs_service_api::ServiceError::InternalError(format!(
                "bot authority lookup failed: {error}"
            )),
        )),
    }
}

/// `human_controls_bot` for routes that return bare `Response`s.
pub(crate) async fn human_controls_bot_response(
    state: &HttpAppState,
    staff_no: &str,
    bot_id: &str,
) -> Result<bool, Response> {
    human_controls_bot(state, staff_no, bot_id)
        .await
        .map_err(IntoResponse::into_response)
}



/// Check whether the Human identified by `actor_id` / `staff_no` has access
/// to the given session. The Human has access if any of:
///   1. The Human's actor_id is a participant in the session.
///   2. The Human controls at least one bot that is a participant in the
///      session (live authority edge).
///
/// Session participants are the authoritative set for session-scoped access
/// (seeded from the group at creation, then evolving independently); this
/// mirrors `human_has_group_access` but judges membership against
/// `session.participants` rather than `group.participants`.
pub(crate) async fn human_has_session_access(
    state: &HttpAppState,
    session: &bcs_service_api::Session,
    actor_id: &str,
    staff_no: &str,
) -> Result<bool, crate::error::HttpAdapterError> {
    if session.participants.iter().any(|p| p.bot_uuid == actor_id) {
        return Ok(true);
    }
    for participant in session
        .participants
        .iter()
        .filter(|p| p.actor_kind == ActorKind::Bot)
    {
        if human_controls_bot(state, staff_no, &participant.bot_uuid).await? {
            return Ok(true);
        }
    }
    Ok(false)
}

pub fn session_error_to_response(err: &bcs_service_api::SessionUseCaseError) -> Response {
    let (code, msg) = match err {
        bcs_service_api::SessionUseCaseError::InvalidParams(s) => (StatusCode::BAD_REQUEST, s.clone()),
        bcs_service_api::SessionUseCaseError::NotFound(s) => (StatusCode::NOT_FOUND, s.clone()),
        bcs_service_api::SessionUseCaseError::Conflict(s) => (StatusCode::CONFLICT, s.clone()),
        bcs_service_api::SessionUseCaseError::Internal(bcs_service_api::ServiceError::Forbidden(message)) => {
            (StatusCode::FORBIDDEN, message.clone())
        }
        bcs_service_api::SessionUseCaseError::Internal(e) => {
            (StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
        }
    };
    (code, Json(serde_json::json!({"error": msg}))).into_response()
}

fn session_application_error_response(
    error: bcs_service_api::application::v1::ApplicationError,
) -> Response {
    use bcs_service_api::application::v1::ApplicationError;
    let (status, code, message) = match error {
        ApplicationError::InvalidInput { code, message } => {
            (StatusCode::BAD_REQUEST, code, message)
        }
        ApplicationError::Unauthenticated => (
            StatusCode::UNAUTHORIZED,
            "unauthenticated".to_string(),
            "authentication is required".to_string(),
        ),
        ApplicationError::Forbidden(message) => {
            (StatusCode::FORBIDDEN, "forbidden".to_string(), message)
        }
        ApplicationError::ForbiddenCode { code, message } => (StatusCode::FORBIDDEN, code, message),
        ApplicationError::NotFound { code, message } => (StatusCode::NOT_FOUND, code, message),
        ApplicationError::Conflict { code, message } => (StatusCode::CONFLICT, code, message),
        ApplicationError::Gone { code, message } => (StatusCode::GONE, code, message),
        ApplicationError::QuotaExceeded { code, message } => {
            (StatusCode::TOO_MANY_REQUESTS, code, message)
        }
        ApplicationError::PayloadTooLarge { code, message } => {
            (StatusCode::PAYLOAD_TOO_LARGE, code, message)
        }
        ApplicationError::Unprocessable { code, message } => {
            (StatusCode::UNPROCESSABLE_ENTITY, code, message)
        }
        ApplicationError::BadGateway { code, message } => (StatusCode::BAD_GATEWAY, code, message),
        ApplicationError::Unavailable { code, message } => {
            (StatusCode::SERVICE_UNAVAILABLE, code, message)
        }
        ApplicationError::Internal(message) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal_error".to_string(),
            message,
        ),
    };
    (
        status,
        Json(serde_json::json!({"error": code, "message": message})),
    )
        .into_response()
}

async fn resolve_session_caller(
    state: &HttpAppState,
    _group: &bcs_service_api::Group,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<bcs_service_api::CallerContext, Response> {
    // Try to extract a bot caller from token first so bearer Bot tokens are not
    // accidentally treated as an ambient Human identity in local/static auth setups.
    if let Some(bot_id) = state.bot_uuid_from_headers(headers).await? {
        return Ok(bcs_service_api::CallerContext::Bot(
            bcs_service_api::BotActor { bot_uuid: bot_id },
        ));
    }

    // Try to extract a human caller from headers.
    if let Some(identity) = state.user_identity.extract(headers, uri).await {
        if let Some(staff_no) = identity.staff_no.filter(|staff_no| !staff_no.is_empty()) {
            return Ok(bcs_service_api::CallerContext::Human(
                bcs_service_api::HumanActor {
                    actor_id: format!("human_{}", staff_no),
                    staff_no,
                },
            ));
        }
    }

    Err((
        StatusCode::UNAUTHORIZED,
        Json(serde_json::json!({
            "error": "unauthorized",
            "message": "valid Human identity or Bot token is required for this session history request"
        })),
    )
        .into_response())
}

#[cfg(test)]
mod tests {
    // Session message history tests are in bcs-message-flow/src/group_history.rs
    // alongside the session_history_request_params and resolve_session_history_source_bots
    // functions that power the service layer.
}
