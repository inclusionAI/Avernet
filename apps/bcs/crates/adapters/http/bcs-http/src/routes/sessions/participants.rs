use axum::{
    Json,
    extract::{Path, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use serde::Deserialize;

use bcs_domain::{ActorKind, MessageViewScope, SystemMessageEvent};

use crate::routes::group_messages::{GroupChatCaller, application_caller, resolve_group_chat_caller};
use crate::state::HttpAppState;

use super::{
    human_controls_bot_response, session_application_error_response, session_error_to_response,
    session_to_json,
};

// ---------------------------------------------------------------
// POST /sessions/{sid}/members
//
// Auth (legacy server.rs:13170-13218):
//   - Caller must be authenticated (Bot token or Human cookie); 401 otherwise.
//   - Role must be compatible with the parent group's `group_strategy`
//     (`Chat` accepts driver/consultant/observer; `ManagerWorker` accepts
//     manager/worker/observer); 400 otherwise.
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct AddParticipantRequest {
    pub bot_uuid: String,
    #[serde(default)]
    pub role: Option<String>,
    #[serde(default)]
    pub message_view_scope: Option<MessageViewScope>,
}

pub async fn add_session_participant(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<AddParticipantRequest>,
) -> impl IntoResponse {
    if resolve_group_chat_caller(&state, &headers, &uri)
        .await
        .is_err()
    {
        return (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"error": "unauthorized"})),
        )
            .into_response();
    }

    // Look up session and parent group's strategy.
    let sess = match state.services.session_management.get(&sid).await {
        Ok(Some(s)) => s,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({
                    "error": "not_found",
                    "message": format!("session {} not found", sid)
                })),
            )
                .into_response();
        }
        Err(e) => return session_error_to_response(&e),
    };
    let group = state.services.group.get(&sess.group_id).await;
    let strategy = group
        .as_ref()
        .map_or(bcs_service_api::GroupStrategy::Chat, |group| {
            group.group_strategy
        });

    // Default role: Worker for ManagerWorker, Consultant for Chat (matches legacy).
    let role = match body.role.as_deref() {
        Some("driver") => bcs_service_api::ParticipantRole::Driver,
        Some("manager") => bcs_service_api::ParticipantRole::Manager,
        Some("worker") => bcs_service_api::ParticipantRole::Worker,
        Some("observer") => bcs_service_api::ParticipantRole::Observer,
        Some("consultant") => bcs_service_api::ParticipantRole::Consultant,
        _ => match strategy {
            bcs_service_api::GroupStrategy::ManagerWorker => {
                bcs_service_api::ParticipantRole::Worker
            }
            _ => bcs_service_api::ParticipantRole::Consultant,
        },
    };
    if !strategy.allows_role(role) {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "invalid_params",
                "message": format!("role {:?} is not allowed in {:?} group", role, strategy),
            })),
        )
            .into_response();
    }

    let bot = match state.services.registry.get(&body.bot_uuid).await {
        Some(b) => b,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({
                    "error": "not_found",
                    "message": format!("bot {} not found", body.bot_uuid)
                })),
            )
                .into_response();
        }
    };
    let mode = if bot.actor_kind == ActorKind::Human {
        Some(bcs_service_api::ParticipantMode::Present)
    } else {
        None
    };
    let message_view_scope = body
        .message_view_scope
        .or_else(|| {
            group.as_ref().and_then(|group| {
                group
                    .participants
                    .iter()
                    .find(|participant| participant.bot_uuid == body.bot_uuid)
                    .map(|participant| participant.message_view_scope)
            })
        })
        .unwrap_or(MessageViewScope::Full);
    if !message_view_scope.is_valid_for(bot.actor_kind) {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "invalid_message_view_scope",
                "message": "Bot participants must use full message_view_scope"
            })),
        )
            .into_response();
    }
    let participant = bcs_service_api::Participant {
        bot_uuid: body.bot_uuid,
        bot_name: bot.capabilities.name,
        kind: None,
        role,
        actor_kind: bot.actor_kind,
        mode,
        tags: Vec::new(),
        message_view_scope,
    };

    match state
        .services
        .session_management
        .add_participant(&sid, participant.clone())
        .await
    {
        Ok(s) => {
            let event = SystemMessageEvent::BotJoined {
                group_id: sess.group_id.clone(),
                actor: participant.into(),
                session_id: sid.clone(),
                session_input: s.input.clone(),
            };
            let _ = state
                .services
                .system_message
                .notify(&sess.group_id, event, &sid, &s.participants)
                .await;
            Json(session_to_json(&s)).into_response()
        }
        Err(e) => session_error_to_response(&e),
    }
}

// ---------------------------------------------------------------
// DELETE /sessions/{sid}/members/{bot_uuid}
//
// Auth (legacy server.rs:13221-13234): caller must be authenticated.
// ---------------------------------------------------------------

pub async fn remove_session_participant(
    State(state): State<HttpAppState>,
    Path((sid, bot_uuid)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
) -> impl IntoResponse {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"error": "unauthorized"})),
            )
                .into_response();
        }
    };

    let caller_id = match &caller {
        GroupChatCaller::Bot { bot_uuid } => bot_uuid.clone(),
        GroupChatCaller::Human(h) => h.actor_id.clone(),
    };

    // Pre-query session to capture group_id and created_by for notification and authorization.
    let (group_id, session_created_by, session_caller_principal) = state
        .services
        .session_management
        .get(&sid)
        .await
        .ok()
        .flatten()
        .map(|sess| {
            (
                Some(sess.group_id.clone()),
                sess.created_by.clone(),
                sess.caller_principal.clone(),
            )
        })
        .unwrap_or((None, None, None));

    // Authorization: self, controller, session creator/caller_principal, or
    // coordinator.
    let is_self = caller_id == bot_uuid;
    // COSEC: Human authority includes only Bots the authenticated staff
    // identity CONTROLS (bot-authority plan Task 6.5): the live authority
    // hook decides per actor; the legacy `list_bots_by_creator` set is gone.
    // This lets a Human act as a Bot-valued Session manager without trusting
    // any caller-supplied actor id.
    let human_staff_no = match &caller {
        GroupChatCaller::Human(h) => Some(h.staff_no.clone()),
        GroupChatCaller::Bot { .. } => None,
    };
    async fn human_controls_actor(
        state: &HttpAppState,
        staff_no: &Option<String>,
        actor_id: &str,
    ) -> Result<bool, Response> {
        match staff_no {
            Some(staff_no) => human_controls_bot_response(state, staff_no, actor_id).await,
            None => Ok(false),
        }
    }
    // A Human caller may remove a bot they control (mirrors delete_session
    // authz).
    let is_bot_owner =
        match human_controls_actor(&state, &human_staff_no, &bot_uuid).await {
            Ok(v) => v,
            Err(response) => return response,
        };
    let is_session_creator = match session_created_by.as_deref() {
        Some(c) => {
            caller_id == format!("human_{}", c)
                || caller_id == c
                || match human_controls_actor(&state, &human_staff_no, c).await {
                    Ok(v) => v,
                    Err(response) => return response,
                }
        }
        None => false,
    };
    let is_session_principal = match session_caller_principal.as_deref() {
        Some(p) => {
            caller_id == p
                || match human_controls_actor(&state, &human_staff_no, p).await {
                    Ok(v) => v,
                    Err(response) => return response,
                }
        }
        None => false,
    };
    let (is_direct_coordinator, is_coordinator) = if let Some(ref gid) = group_id {
        if let Some(group) = state.services.group.get(gid).await {
            let direct = caller_id == group.driver_bot || caller_id == group.originator();
            let controls_driver =
                match human_controls_actor(&state, &human_staff_no, &group.driver_bot).await {
                    Ok(v) => v,
                    Err(response) => return response,
                };
            let controls_originator = match human_controls_actor(
                &state,
                &human_staff_no,
                group.originator(),
            ).await {
                Ok(v) => v,
                Err(response) => return response,
            };
            (direct, direct || controls_driver || controls_originator)
        } else {
            (false, false)
        }
    } else {
        (false, false)
    };
    if !is_self && !is_bot_owner && !is_session_creator && !is_session_principal && !is_coordinator
    {
        return (
            StatusCode::FORBIDDEN,
            Json(
                serde_json::json!({"error": "Caller is not authorized to remove this participant"}),
            ),
        )
            .into_response();
    }

    // Session creator/principal/owner cannot remove the driver bot.
    if (is_session_creator || is_session_principal || is_bot_owner)
        && !is_self
        && !is_direct_coordinator
    {
        if let Some(ref gid) = group_id {
            if let Some(group) = state.services.group.get(gid).await {
                if bot_uuid == group.driver_bot {
                    return (
                        StatusCode::FORBIDDEN,
                        Json(serde_json::json!({"error": "cannot remove driver bot"})),
                    )
                        .into_response();
                }
            }
        }
    }

    // Look up actor name from registry for the notification.
    let actor_name = state
        .services
        .registry
        .get(&bot_uuid)
        .await
        .and_then(|b| b.capabilities.name)
        .unwrap_or_else(|| bot_uuid.clone());
    let kind = if bot_uuid.starts_with("human_") {
        ActorKind::Human
    } else {
        ActorKind::Bot
    };

    match state
        .services
        .session_management
        .remove_participant(&sid, &bot_uuid)
        .await
    {
        Ok(s) => {
            if let Some(ref gid) = group_id {
                let event = SystemMessageEvent::BotLeft {
                    group_id: gid.clone(),
                    actor: bcs_domain::Participant {
                        bot_uuid: bot_uuid.clone(),
                        bot_name: Some(actor_name.clone()),
                        kind: None,
                        role: bcs_domain::ParticipantRole::Observer,
                        actor_kind: kind,
                        mode: None,
                        tags: Vec::new(),
                        message_view_scope: MessageViewScope::Full,
                    },
                };
                let _ = state
                    .services
                    .system_message
                    .notify(gid, event, &sid, &s.participants)
                    .await;
            }
            Json(session_to_json(&s)).into_response()
        }
        Err(e) => session_error_to_response(&e),
    }
}

// ---------------------------------------------------------------
// PATCH /sessions/{sid}/members/{bot_uuid}
//
// Auth (legacy server.rs:13260+): caller must be authenticated. Bot token
// or Human cookie/mock both accepted.
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct UpdateParticipantModeRequest {
    #[serde(default)]
    pub mode: Option<String>,
    #[serde(default)]
    pub message_view_scope: Option<MessageViewScope>,
}

pub async fn update_session_participant_mode(
    State(state): State<HttpAppState>,
    Path((sid, bot_uuid)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<UpdateParticipantModeRequest>,
) -> impl IntoResponse {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(caller) => caller,
        Err(_) => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"error": "unauthorized"})),
            )
                .into_response();
        }
    };

    if body.mode.is_none() && body.message_view_scope.is_none() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "At least one of mode or message_view_scope must be provided"
            })),
        )
            .into_response();
    }

    let mode = match body.mode.as_deref() {
        Some("auto") => Some(bcs_service_api::ParticipantMode::Auto),
        Some("muted") => Some(bcs_service_api::ParticipantMode::Muted),
        Some("present") => Some(bcs_service_api::ParticipantMode::Present),
        Some("absent") => Some(bcs_service_api::ParticipantMode::Absent),
        Some(unknown) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": format!("unknown mode: {unknown}")
                })),
            )
                .into_response();
        }
        None => None,
    };

    if let Some(message_view_scope) = body.message_view_scope {
        let GroupChatCaller::Human(human) = &caller else {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": "invalid_message_view_scope",
                    "message": "Bot participants must use full message_view_scope"
                })),
            )
                .into_response();
        };

        if let Some(application) = state.session_application.as_ref() {
            let update = application
                .update_participant(bcs_service_api::application::v1::UpdateSessionParticipant {
                    caller: application_caller(&caller),
                    session_id: sid.clone(),
                    bot_uuid: bot_uuid.clone(),
                    mode,
                    message_view_scope: Some(message_view_scope),
                })
                .await;
            match update {
                Ok(_) => {
                    return match state.services.session_management.get(&sid).await {
                        Ok(Some(session)) => Json(session_to_json(&session)).into_response(),
                        Ok(None) => session_application_error_response(
                            bcs_service_api::application::v1::ApplicationError::not_found(
                                "session_not_found",
                                format!("Session '{sid}' was not found after update"),
                            ),
                        ),
                        Err(error) => session_error_to_response(&error),
                    };
                }
                Err(bcs_service_api::application::v1::ApplicationError::NotFound {
                    code, ..
                }) if code == "participant_not_found" && human.actor_id == bot_uuid => {
                    let mut participant = bcs_service_api::Participant::human(
                        &bot_uuid,
                        bcs_service_api::ParticipantRole::Observer,
                    );
                    participant.bot_name = human.nick_name.clone();
                    participant.mode = mode.or(Some(bcs_service_api::ParticipantMode::Present));
                    participant.message_view_scope = message_view_scope;
                    return match state
                        .services
                        .session_management
                        .add_participant(&sid, participant)
                        .await
                    {
                        Ok(session) => Json(session_to_json(&session)).into_response(),
                        Err(error) => session_error_to_response(&error),
                    };
                }
                Err(error) => return session_application_error_response(error),
            }
        }

        if human.actor_id != bot_uuid {
            return (
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({
                    "error": "message_view_scope_forbidden",
                    "message": "A Human may only update its own scope unless it manages the Session"
                })),
            )
                .into_response();
        }

        match state
            .services
            .session_management
            .update_participant_mode_and_message_view_scope(
                &sid,
                &bot_uuid,
                mode,
                message_view_scope,
            )
            .await
        {
            Ok(session) => return Json(session_to_json(&session)).into_response(),
            Err(
                bcs_service_api::SessionUseCaseError::NotFound(_)
                | bcs_service_api::SessionUseCaseError::InvalidParams(_),
            ) => {
                let mut participant = bcs_service_api::Participant::human(
                    &bot_uuid,
                    bcs_service_api::ParticipantRole::Observer,
                );
                participant.bot_name = human.nick_name.clone();
                participant.mode = mode.or(Some(bcs_service_api::ParticipantMode::Present));
                participant.message_view_scope = message_view_scope;
                return match state
                    .services
                    .session_management
                    .add_participant(&sid, participant)
                    .await
                {
                    Ok(session) => Json(session_to_json(&session)).into_response(),
                    Err(error) => session_error_to_response(&error),
                };
            }
            Err(error) => return session_error_to_response(&error),
        }
    }

    let mode = mode.expect("mode is required when message_view_scope is absent");

    // Capture old mode before update so we can notify on change.
    let old_mode = state
        .services
        .session_management
        .get(&sid)
        .await
        .ok()
        .flatten()
        .and_then(|sess| {
            sess.participants
                .iter()
                .find(|p| p.bot_uuid == bot_uuid)
                .and_then(|p| p.mode)
        });

    let bot_info = state.services.registry.get(&bot_uuid).await;
    let actor_name = bot_info
        .as_ref()
        .and_then(|b| b.capabilities.name.clone())
        .unwrap_or_else(|| bot_uuid.clone());
    let actor_kind = bot_info.map(|b| b.actor_kind).unwrap_or_else(|| {
        if bot_uuid.starts_with("human_") {
            ActorKind::Human
        } else {
            ActorKind::Bot
        }
    });

    let result = state
        .services
        .session_management
        .update_participant_mode(&sid, &bot_uuid, mode)
        .await;

    match result {
        Ok(s) => {
            if old_mode != Some(mode) {
                let event = SystemMessageEvent::ParticipantModeChanged {
                    group_id: s.group_id.clone(),
                    actor_id: bot_uuid.clone(),
                    actor_name: actor_name.clone(),
                    actor_kind,
                    from: old_mode,
                    to: mode,
                };
                if let Err(e) = state
                    .services
                    .system_message
                    .notify(&s.group_id, event, &sid, &s.participants)
                    .await
                {
                    tracing::warn!(
                        target: "bcs_http::routes::sessions",
                        request_id = %bcs_observability::CurrentRequestId,
                        session_id = %sid,
                        error = %e,
                        "notify participant mode changed failed"
                    );
                }
            }
            Json(session_to_json(&s)).into_response()
        }
        Err(e) => {
            // Human first-insert: if the participant is not in the session yet
            // and the target is a human actor, auto-create as Observer then
            // apply the requested mode. Bots must be added via POST first.
            let is_not_found = matches!(
                e,
                bcs_service_api::SessionUseCaseError::NotFound(_)
                    | bcs_service_api::SessionUseCaseError::InvalidParams(_)
            );
            if is_not_found && bot_uuid.starts_with("human_") {
                let mut participant = bcs_service_api::Participant::human(
                    &bot_uuid,
                    bcs_service_api::ParticipantRole::Observer,
                );
                participant.bot_name = Some(actor_name.clone());
                match state
                    .services
                    .session_management
                    .add_participant(&sid, participant)
                    .await
                {
                    Ok(_) => {
                        match state
                            .services
                            .session_management
                            .update_participant_mode(&sid, &bot_uuid, mode)
                            .await
                        {
                            Ok(s) => {
                                if old_mode != Some(mode) {
                                    let event = SystemMessageEvent::ParticipantModeChanged {
                                        group_id: s.group_id.clone(),
                                        actor_id: bot_uuid.clone(),
                                        actor_name: actor_name.clone(),
                                        actor_kind,
                                        from: old_mode,
                                        to: mode,
                                    };
                                    if let Err(e) = state
                                        .services
                                        .system_message
                                        .notify(&s.group_id, event, &sid, &s.participants)
                                        .await
                                    {
                                        tracing::warn!(
                                            target: "bcs_http::routes::sessions",
                                            request_id = %bcs_observability::CurrentRequestId,
                                            session_id = %sid,
                                            error = %e,
                                            "notify participant mode changed failed"
                                        );
                                    }
                                }
                                Json(session_to_json(&s)).into_response()
                            }
                            Err(e2) => session_error_to_response(&e2),
                        }
                    }
                    Err(e2) => session_error_to_response(&e2),
                }
            } else {
                session_error_to_response(&e)
            }
        }
    }
}
