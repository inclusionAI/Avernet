use axum::{
    Json,
    extract::{Path, State},
    http::{HeaderMap, StatusCode, Uri},
    response::IntoResponse,
};
use serde::Deserialize;

use bcs_domain::SystemMessageEvent;
use bcs_service_api::GroupChatCommand;

use crate::routes::collaboration_runs::collaboration_error_to_response;
use crate::routes::group_messages::{
    GroupChatCaller, delivery_results_json, group_chat_caller_context, resolve_group_chat_caller,
};
use crate::state::HttpAppState;

use super::session_error_to_response;

// ---------------------------------------------------------------
// POST /sessions/{sid}/chat
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct SessionChatRequest {
    pub message: String,
    #[serde(default)]
    pub from: Option<String>,
}

pub async fn session_chat(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<SessionChatRequest>,
) -> impl IntoResponse {
    // 1. Look up session
    let mut sess = match state.services.session_management.get(&sid).await {
        Ok(Some(s)) => s,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"error": "session not found"})),
            )
                .into_response();
        }
        Err(e) => return session_error_to_response(&e),
    };

    // 2. Resolve caller
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

    // 3. Caller must be a session participant
    let caller_id = match &caller {
        GroupChatCaller::Bot { bot_uuid } => bot_uuid.clone(),
        GroupChatCaller::Human(h) => h.actor_id.clone(),
    };
    if let Err(error) = state.services.session_management.authorize_collaboration(&sid, &caller_id).await {
        return session_error_to_response(&error);
    }
    let is_participant = sess.participants.iter().any(|p| p.bot_uuid == caller_id);
    if !is_participant {
        // COSEC: only the authenticated Human may self-enroll. Bot callers
        // remain fail-closed so a valid Bot token cannot expand membership.
        let GroupChatCaller::Human(human) = &caller else {
            return (
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({"error": "caller is not a session participant"})),
            )
                .into_response();
        };
        let mut participant = bcs_service_api::Participant::human(
            &human.actor_id,
            bcs_service_api::ParticipantRole::Observer,
        );
        participant.mode = Some(bcs_service_api::ParticipantMode::Present);
        participant.bot_name = human
            .nick_name
            .clone()
            .or_else(|| Some(human.staff_no.clone()));
        sess = match state
            .services
            .session_management
            .add_participant(&sid, participant.clone())
            .await
        {
            Ok(updated) => updated,
            Err(error) => return session_error_to_response(&error),
        };
        let event = SystemMessageEvent::HumanJoined {
            group_id: sess.group_id.clone(),
            actor: participant.into(),
        };
        let _ = state
            .services
            .system_message
            .notify(&sess.group_id, event, &sid, &sess.participants)
            .await;
    }

    // COSEC: caller_id was resolved from the authenticated caller above. The
    // optional request body `from` field must never select a Human responder.
    match state
        .services
        .collaboration_runtime
        .handle_session_human_input(bcs_service_api::HandleSessionHumanInputCommand {
            group_id: sess.group_id.clone(),
            session_id: Some(sid.clone()),
            caller_actor_id: caller_id.to_string(),
            content: body.message.clone(),
            source: bcs_service_api::HumanResponseSource::Http,
            // The HTTP session_chat path stays caller-closed for now; the
            // design (spec 2026-10-22) scopes entry B to the WS channel.
            // Route the same ingress here later if one-shot parity is
            // ever needed.
            caller_ingress: None,
        })
        .await
    {
        Ok(bcs_service_api::HandleSessionHumanInputOutcome::NotStateMachine) => {}
        Ok(bcs_service_api::HandleSessionHumanInputOutcome::Consumed { response }) => {
            return Json(serde_json::json!({
                "delivered": true,
                "handled_as": "human_input",
                "session_id": sid,
                "group_id": sess.group_id,
                "state_machine_run_id": response.run.run_id,
                "node_id": response.node.node_id,
                "outcome": response.node.outcome,
            }))
            .into_response();
        }
        Err(error) => {
            return collaboration_error_to_response(error);
        }
    }

    // 4. Route via MessageFlowService with session_id pinned from path
    // COSEC: Human messages are always bound to the authenticated actor, even
    // when the request supplies `from`; Bot callers retain legacy validation.
    let requested_sender_id = match &caller {
        GroupChatCaller::Human(human) => Some(human.actor_id.clone()),
        GroupChatCaller::Bot { .. } => body.from,
    };
    // Strict same-authentication caller material (spec §10.1.1/§7.5.1):
    // only attached when BOTH the concrete extractor is configured and the
    // authentication chain yields a Human with a trusted session window.
    // Extraction failure never fails the request here — the ordinary path
    // continues and the seam makes the fail-closed judgment; the DM-only
    // branch of the flow decides whether caller semantics apply.
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or_default();
    let caller_ingress = match (&state.caller_ingress_source, &caller) {
        (Some(source), GroupChatCaller::Human(_)) => {
            match source.extract_caller_request(&headers, now_ms).await {
                Ok(request) => Some(request.ingress),
                Err(error) => {
                    // Same rationale as group_chat: the ordinary path
                    // continues, but the relay capture silently starves
                    // without the refusal kind in the log.
                    tracing::warn!(
                        target: "bcs_http::routes::sessions",
                        request_id = %bcs_observability::CurrentRequestId,
                        session_id = %sid,
                        reason = ?error,
                        "caller ingress extraction failed; the session relay capture stays closed for this message"
                    );
                    None
                }
            }
        }
        _ => None,
    };
    let cmd = GroupChatCommand {
        caller_ingress,
        caller: group_chat_caller_context(&caller),
        group_id: sess.group_id.clone(),
        requested_sender_id,
        message: body.message,
        session_id: Some(sid.clone()),
        provider_bypass_headers: state.provider_bypass_headers_from(&headers),
    };

    match state.services.message_flow.handle_group_chat(cmd).await {
        Ok(outcome) => Json(serde_json::json!({
            "delivered": outcome.delivered_count > 0,
            "session_id": sid,
            "group_id": outcome.group_id,
            "driver_bot": outcome.driver_bot_id,
            "delivered_count": outcome.delivered_count,
            "queue_admission": outcome.queue_admission,
            "failed_count": outcome.failed_count,
            "delivery_results": delivery_results_json(&outcome.delivery_results),
            "mentions": outcome.mentions,
        }))
        .into_response(),
        Err(e) => {
            let (code, msg) = match &e {
                bcs_service_api::ServiceError::GroupNotFound(gid) => {
                    (StatusCode::NOT_FOUND, format!("group not found: {gid}"))
                }
                bcs_service_api::ServiceError::Unauthorized(m) => {
                    (StatusCode::FORBIDDEN, m.clone())
                }
                bcs_service_api::ServiceError::Forbidden(m) => (StatusCode::FORBIDDEN, m.clone()),
                bcs_service_api::ServiceError::InvalidOperation { .. } => {
                    (StatusCode::BAD_REQUEST, e.to_string())
                }
                bcs_service_api::ServiceError::SessionNotFound(_) => {
                    (StatusCode::NOT_FOUND, e.to_string())
                }
                _ => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()),
            };
            if code.is_server_error() {
                tracing::error!(
                    target: "bcs_http::routes::sessions",
                    request_id = %bcs_observability::CurrentRequestId,
                    route = "/sessions/{sid}/chat",
                    session_id = %sid,
                    group_id = %sess.group_id,
                    error_kind = e.as_ref(),
                    error = %e,
                    "session_chat failed"
                );
            }
            (code, Json(serde_json::json!({"error": msg}))).into_response()
        }
    }
}
