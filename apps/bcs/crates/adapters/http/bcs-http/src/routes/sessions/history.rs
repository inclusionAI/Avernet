use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use serde::Deserialize;
use serde_json::Value;

use bcs_domain::{ActorKind, HumanMessageView, MessageViewScope};
use bcs_service_api::GroupMessage;

use crate::routes::collaboration_runs::collaboration_error_to_response;
use crate::state::HttpAppState;

use super::{
    human_controls_bot_response, human_has_session_access, resolve_session_caller,
    session_error_to_response,
};

// ---------------------------------------------------------------
// GET /sessions/{sid}/messages
//
// Fetches session messages via the GroupMessageHistoryService,
// which queries source bots via chat.history and converts the
// raw bot responses into GroupMessage format with proper role
// resolution, [from:] prefix stripping, and queued-message expansion.
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct GetSessionMessagesQuery {
    #[serde(default)]
    pub view_bot_id: Option<String>,
    #[serde(default)]
    pub limit: Option<u64>,
    #[serde(default)]
    pub before: Option<u64>,
    #[serde(default)]
    pub include_pending: bool,
}

pub async fn get_session_messages(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    Query(query): Query<GetSessionMessagesQuery>,
    headers: HeaderMap,
    uri: Uri,
) -> impl IntoResponse {
    // 1. Look up session
    let sess = match state.services.session_management.get(&sid).await {
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

    // 2. Look up parent group
    let group = match state.services.group.get(&sess.group_id).await {
        Some(g) => g,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"error": "group not found"})),
            )
                .into_response();
        }
    };

    // 3. Resolve caller context before any session history read.
    let caller = match resolve_session_caller(&state, &group, &headers, &uri).await {
        Ok(c) => c,
        Err(response) => return response,
    };

    let resolved_view =
        match resolve_session_history_view(&state, &sess, &caller, query.view_bot_id.as_deref()).await {
            Ok(view) => view,
            Err(response) => return response,
        };
    let limit = query.limit.unwrap_or(u64::MAX);
    let participant_human_view = resolved_view
        .human_view
        .clone()
        .filter(|view| view.scope == MessageViewScope::Participant);
    if group.group_strategy == bcs_service_api::GroupStrategy::StateMachine {
        // Full keeps the pre-participant-view authorization and history path.
        // Only an explicitly participant-scoped Human uses the projected
        // state-machine history API.
        if participant_human_view.is_none()
            && let Err(response) =
                authorize_state_machine_session_history(&state, &sess, &caller).await
        {
            return response;
        }
        let human_view = resolved_view.human_view;
        let history = match human_view {
            Some(view) => {
                state
                    .services
                    .collaboration_runtime
                    .get_state_machine_session_history_for_view(&sid, limit, query.before, view)
                    .await
            }
            None => {
                state
                    .services
                    .collaboration_runtime
                    .get_state_machine_session_history(&sid, limit, query.before)
                    .await
            }
        };
        return match history {
            Ok(Some(result)) => (StatusCode::OK, Json(result.messages)).into_response(),
            Ok(None) => (StatusCode::OK, Json(Vec::<GroupMessage>::new())).into_response(),
            Err(error) => collaboration_error_to_response(error),
        };
    }

    // 4. Delegate to the history service for proper message conversion
    let cmd = bcs_service_api::SessionHistoryCommand {
        caller,
        group_id: sess.group_id.clone(),
        session_id: sid.clone(),
        session_participants: sess.participants.clone(),
        view_bot_id: resolved_view.view_actor_id,
        limit,
        before: query.before,
    };

    match state
        .services
        .group_message_history
        .get_session_history_with_options(
            cmd,
            bcs_service_api::MessageHistoryOptions {
                include_pending: query.include_pending,
            },
        )
        .await
    {
        Ok(result) => {
            let mut messages = result.messages;
            if let Some(human_view) = participant_human_view {
                let snapshot = match state
                    .services
                    .collaboration_runtime
                    .get_state_machine_session_history_for_view(
                        &sid,
                        limit,
                        query.before,
                        human_view,
                    )
                    .await
                {
                    Ok(snapshot) => snapshot,
                    Err(error) => return collaboration_error_to_response(error),
                };
                messages = merge_participant_state_machine_snapshot(
                    messages,
                    snapshot.map(|history| history.messages),
                    limit,
                );
            }
            (StatusCode::OK, Json(messages)).into_response()
        }
        Err(e) => {
            let (status, body) = session_history_error_to_response(&e);
            (status, Json(body)).into_response()
        }
    }
}

fn merge_participant_state_machine_snapshot(
    persisted: Vec<GroupMessage>,
    snapshot: Option<Vec<GroupMessage>>,
    limit: u64,
) -> Vec<GroupMessage> {
    bcs_domain::state_machine_history::merge_state_machine_history(
        persisted, snapshot.unwrap_or_default(), limit,
    )
}

struct ResolvedSessionHistoryView {
    view_actor_id: Option<String>,
    human_view: Option<HumanMessageView>,
}

async fn resolve_session_history_view(
    state: &HttpAppState,
    session: &bcs_service_api::Session,
    caller: &bcs_service_api::CallerContext,
    requested_view_actor_id: Option<&str>,
) -> Result<ResolvedSessionHistoryView, Response> {
    let forbidden = || {
        (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "error": "forbidden",
                "message": "the selected View Actor is not an authorized Session participant"
            })),
        )
            .into_response()
    };

    match caller {
        bcs_service_api::CallerContext::Human(human) => {
            if let Some(requested) = requested_view_actor_id.filter(|id| *id != human.actor_id) {
                // The authenticated Human authorizes the selected Bot; its
                // own message scope applies only when viewing the Human tab.
                if !session.participants.iter().any(|participant| {
                    participant.bot_uuid == requested && participant.actor_kind == ActorKind::Bot
                }) {
                    return Err(forbidden());
                }
                let controlled =
                    match human_controls_bot_response(state, &human.staff_no, requested).await {
                        Ok(v) => v,
                        Err(response) => return Err(response),
                    };
                if !controlled {
                    return Err(forbidden());
                }
                return Ok(ResolvedSessionHistoryView {
                    view_actor_id: Some(requested.to_string()),
                    human_view: None,
                });
            }
            let participant_view = session
                .participants
                .iter()
                .find(|participant| {
                    participant.bot_uuid == human.actor_id
                        && participant.actor_kind == ActorKind::Human
                        && participant.message_view_scope == MessageViewScope::Participant
                });
            if let Some(participant) = participant_view {
                return Ok(ResolvedSessionHistoryView {
                    view_actor_id: Some(human.actor_id.clone()),
                    human_view: Some(HumanMessageView {
                        actor_id: human.actor_id.clone(),
                        scope: participant.message_view_scope,
                        allow_legacy_unclassified_chat: false,
                    }),
                });
            }

            // Full is the compatibility mode. Preserve the pre-feature
            // behavior: do not infer a Human View Actor for an omitted view.
            Ok(ResolvedSessionHistoryView {
                view_actor_id: requested_view_actor_id.map(str::to_string),
                human_view: None,
            })
        }
        bcs_service_api::CallerContext::Bot(_) => Ok(ResolvedSessionHistoryView {
            view_actor_id: requested_view_actor_id.map(str::to_string),
            human_view: None,
        }),
        bcs_service_api::CallerContext::Public => Err((
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({
                "error": "unauthorized",
                "message": "valid Human identity or Bot token is required for session history"
            })),
        )
            .into_response()),
        bcs_service_api::CallerContext::Integration(_)
        | bcs_service_api::CallerContext::Admin(_) => Ok(ResolvedSessionHistoryView {
            view_actor_id: requested_view_actor_id.map(str::to_string),
            human_view: None,
        }),
    }
}

async fn authorize_state_machine_session_history(
    state: &HttpAppState,
    session: &bcs_service_api::Session,
    caller: &bcs_service_api::CallerContext,
) -> Result<(), Response> {
    let authorized = match caller {
        bcs_service_api::CallerContext::Human(human) => {
            human_has_session_access(state, session, &human.actor_id, &human.staff_no)
                .await
                .map_err(IntoResponse::into_response)?
        }
        bcs_service_api::CallerContext::Bot(bot) => session
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == bot.bot_uuid),
        bcs_service_api::CallerContext::Public => {
            return Err((
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({
                    "error": "unauthorized",
                    "message": "valid Human identity or Bot token is required for session history"
                })),
            )
                .into_response());
        }
        bcs_service_api::CallerContext::Integration(_)
        | bcs_service_api::CallerContext::Admin(_) => true,
    };

    if authorized {
        Ok(())
    } else {
        Err((
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "error": "forbidden",
                "message": "caller is not a session participant and owns no Bot in this session"
            })),
        )
            .into_response())
    }
}

fn session_history_error_to_response(
    e: &bcs_service_api::GroupUseCaseError,
) -> (StatusCode, Value) {
    match e {
        bcs_service_api::GroupUseCaseError::Forbidden(msg) => (
            StatusCode::FORBIDDEN,
            serde_json::json!({"error": "forbidden", "message": msg}),
        ),
        bcs_service_api::GroupUseCaseError::Unauthorized(msg) => (
            StatusCode::UNAUTHORIZED,
            serde_json::json!({"error": "unauthorized", "message": msg}),
        ),
        bcs_service_api::GroupUseCaseError::InvalidHistoryLimit(limit) => (
            StatusCode::BAD_REQUEST,
            serde_json::json!({"error": "invalid_limit", "message": format!("limit must be > 0, got {}", limit)}),
        ),
        bcs_service_api::GroupUseCaseError::Service(err) => (
            StatusCode::BAD_REQUEST,
            serde_json::json!({"error": "bad_request", "message": err.to_string()}),
        ),
        _ => (
            StatusCode::INTERNAL_SERVER_ERROR,
            serde_json::json!({"error": "internal_error"}),
        ),
    }
}
