use super::*;

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
