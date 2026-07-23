//! Session HTTP handlers.
//!
//! Phase 2a: 9 endpoints mounted at /groups/{id}/sessions and /sessions/{sid}/...
//! Full auth (caller resolution + 6eb4b6384 permission check) for create/complete
//! is deferred to Task 9.

use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use serde::Deserialize;
use serde_json::Value;

use bcs_domain::{ActorKind, SystemMessageEvent};
use bcs_service_api::{GroupChatCommand, StartStateMachineRunCommand, StartStateMachineRunOutcome};

use crate::routes::collaboration_runs::collaboration_error_to_response;
use crate::routes::group_messages::{
    delivery_results_json, group_chat_caller_context, resolve_group_chat_caller, GroupChatCaller,
};
use crate::state::HttpAppState;

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
    run: Option<&StartStateMachineRunOutcome>,
) -> Value {
    let mut v = session_to_json(sess);
    if let (Some(obj), Some(run)) = (v.as_object_mut(), run) {
        obj.insert(
            "state_machine_run_id".into(),
            Value::String(run.view.run.run_id.clone()),
        );
        obj.insert(
            "state_machine_run".into(),
            serde_json::to_value(&run.view).unwrap_or(Value::Null),
        );
    }
    v
}

async fn start_state_machine_run_for_session(
    state: &HttpAppState,
    group: &bcs_service_api::Group,
    session: &bcs_service_api::Session,
    caller_id: Option<String>,
) -> Result<Option<StartStateMachineRunOutcome>, Response> {
    if group.group_strategy != bcs_service_api::GroupStrategy::StateMachine
        || session.session_kind != bcs_service_api::SessionKind::ServiceInvocation
    {
        return Ok(None);
    }
    state
        .services
        .collaboration_runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: group.id.clone(),
            session_id: Some(session.id.clone()),
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            input: session.input.clone().unwrap_or(Value::Null),
            caller_id,
        })
        .await
        .map(Some)
        .map_err(collaboration_error_to_response)
}

/// Check whether the Human identified by `actor_id` / `staff_no` has access
/// to the given group. The Human has access if any of:
///   1. The Human's actor_id is a participant in the group.
///   2. The Human owns at least one bot that is a participant in the group.
///
/// Mirrors legacy `crate::ws::human_has_group_access` semantics
/// (server.rs:12300-12320 + ws/mod.rs).
pub(crate) async fn human_has_group_access(
    state: &HttpAppState,
    group: &bcs_service_api::Group,
    actor_id: &str,
    staff_no: &str,
) -> bool {
    if group
        .participants
        .iter()
        .any(|p| p.bot_uuid == actor_id)
    {
        return true;
    }
    let owned = state.services.registry.list_bots_by_creator(staff_no).await;
    owned
        .iter()
        .any(|b| group.participants.iter().any(|p| p.bot_uuid == b.bot_uuid))
}

pub fn session_error_to_response(err: &bcs_service_api::SessionUseCaseError) -> Response {
    let (code, msg) = match err {
        bcs_service_api::SessionUseCaseError::NotFound(s) => (StatusCode::NOT_FOUND, s.clone()),
        bcs_service_api::SessionUseCaseError::InvalidParams(s)
        | bcs_service_api::SessionUseCaseError::CallbackPending(s) => {
            (StatusCode::BAD_REQUEST, s.clone())
        }
        bcs_service_api::SessionUseCaseError::Conflict(s) => (StatusCode::CONFLICT, s.clone()),
        bcs_service_api::SessionUseCaseError::Internal(e) => {
            (StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
        }
    };
    (code, Json(serde_json::json!({"error": msg}))).into_response()
}

// ---------------------------------------------------------------
// POST /groups/{id}/sessions
//
// Ports 6eb4b6384: resolve caller identity, verify created_by
// ownership (human must be self, bot must be self or owner),
// and enforce participant access to the group.
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct CreateSessionRequest {
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub session_title: Option<String>,
    #[serde(default)]
    pub input: Option<Value>,
    #[serde(default)]
    pub meta: Option<Value>,
    #[serde(default)]
    pub session_kind: Option<String>,
    #[serde(default)]
    pub created_by: Option<String>,
    #[serde(default)]
    pub caller_role: Option<String>,
}

pub async fn create_session_for_group(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<CreateSessionRequest>,
) -> impl IntoResponse {
    // Step 1: resolve caller (bot token or human cookie)
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => {
            return (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"error": "unauthorized"}))).into_response()
        }
    };

    // Step 2: verify group exists and caller has access
    let mut group = match state.services.group.get(&group_id).await {
        Some(g) => g,
        None => return (StatusCode::NOT_FOUND, Json(serde_json::json!({"error": "not_found", "message": format!("group {} not found", group_id)}))).into_response(),
    };
    state.services.backfill_bot_names(&mut group).await;

    let is_public_group = group.visibility == "public";

    let has_access = if is_public_group {
        true
    } else {
        match &caller {
            GroupChatCaller::Bot { bot_uuid } => {
                group.participants.iter().any(|p| p.bot_uuid == *bot_uuid)
            }
            GroupChatCaller::Human(h) => {
                human_has_group_access(&state, &group, &h.actor_id, &h.staff_no).await
            }
        }
    };
    if !has_access {
        return (StatusCode::FORBIDDEN, Json(serde_json::json!({"error": "forbidden", "message": "caller is not a participant"}))).into_response();
    }

    // Validate caller_role for public group non-member callers
    if is_public_group && body.caller_role.is_some() {
        let role = body.caller_role.as_deref().unwrap();
        if role == "driver" {
            return (StatusCode::BAD_REQUEST, Json(serde_json::json!({
                "error": "invalid_role",
                "message": "Non-member actors cannot use the driver role"
            }))).into_response();
        }
        let valid_roles = ["consultant", "manager", "worker", "observer"];
        if !valid_roles.contains(&role) {
            return (StatusCode::BAD_REQUEST, Json(serde_json::json!({
                "error": "invalid_role",
                "message": format!("Invalid caller_role '{}': must be one of consultant, manager, worker, observer", role)
            }))).into_response();
        }
    }

    let caller_nick_name = if let GroupChatCaller::Human(h) = &caller { h.nick_name.clone() } else { None };

    // Ensure human actor exists in DB for public group access
    if is_public_group {
        if let GroupChatCaller::Human(h) = &caller {
            let display = h.nick_name.as_deref().unwrap_or(&h.staff_no);
            let _ = state.services.registry
                .ensure_human_actor(&h.staff_no, display)
                .await;
        }
    }

    // Step 3: resolve the actual session creator
    let requester = match &caller {
        GroupChatCaller::Bot { bot_uuid } => bot_uuid.clone(),
        GroupChatCaller::Human(h) => h.actor_id.clone(),
    };

    let creator = match body.created_by.as_deref() {
        None => requester.clone(),
        Some(target) if target == requester => requester.clone(),
        Some(target) if target.starts_with("human_") => {
            match &caller {
                GroupChatCaller::Human(h) if h.actor_id == target => target.to_string(),
                _ => return (
                    StatusCode::FORBIDDEN,
                    Json(serde_json::json!({
                        "error": "forbidden",
                        "message": format!("caller is not authorized to create session as {}", target)
                    })),
                ).into_response(),
            }
        }
        Some(target) => {
            // bot_xxx: caller must be that bot, or own that bot
            let owns_bot = match &caller {
                GroupChatCaller::Bot { bot_uuid } => bot_uuid == target,
                GroupChatCaller::Human(h) => state
                    .services
                    .registry
                    .get(target)
                    .await
                    .and_then(|b| b.created_by)
                    .map_or(false, |owner| owner == h.staff_no),
            };
            if !owns_bot {
                return (
                    StatusCode::FORBIDDEN,
                    Json(serde_json::json!({
                        "error": "forbidden",
                        "message": format!("caller does not own bot {}", target)
                    })),
                ).into_response();
            }
            target.to_string()
        }
    };

    // Step 4: verify creator has access to this group.
    // - Human creator must own a participant bot or be a participant (legacy server.rs:12300-12320).
    // - Bot creator must itself be a participant in the group.
    if !is_public_group {
        if creator.starts_with("human_") {
            let staff_no = creator.trim_start_matches("human_").to_string();
            if !human_has_group_access(&state, &group, &creator, &staff_no).await {
                return (
                    StatusCode::FORBIDDEN,
                    Json(serde_json::json!({
                        "error": "forbidden",
                        "message": format!(
                            "human {} does not own any bot in this group",
                            creator
                        ),
                    })),
                )
                    .into_response();
            }
        } else if !group.participants.iter().any(|p| p.bot_uuid == creator) {
            return (
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({
                    "error": "forbidden",
                    "message": format!(
                        "bot {} is not a participant in this group",
                        creator
                    ),
                })),
            )
                .into_response();
        }
    }

    // Only auto-add human creator to session participants when created_by
    // was explicitly passed in the request body. When the creator was
    // inferred from the cookie/header (not in the body), do NOT auto-add
    // — the frontend is just proxying a bot action.
    let explicit_human_creator = body.created_by.as_ref()
        .map_or(false, |cb| cb.starts_with("human_"));

    // Step 5: create or reactivate the session
    let session_kind = match body.session_kind.as_deref() {
        Some("service_invocation") => bcs_service_api::SessionKind::ServiceInvocation,
        Some("chat") => bcs_service_api::SessionKind::Chat,
        None if group.group_strategy == bcs_service_api::GroupStrategy::StateMachine => {
            bcs_service_api::SessionKind::ServiceInvocation
        }
        _ => bcs_service_api::SessionKind::Chat,
    };

    // Copy group participants into session, filling in mode defaults.
    let mut session_participants: Vec<bcs_service_api::Participant> = group
        .participants
        .iter()
        .map(|p| {
            let mut p = p.clone();
            if p.mode.is_none() {
                p.mode = Some(bcs_service_api::ParticipantMode::default_for(p.actor_kind));
            }
            p
        })
        .collect();

    // For public groups, pre-add the creator (not the caller) to session
    // participants so the session is created with the creator already included.
    if is_public_group {
        if !session_participants.iter().any(|p| p.bot_uuid == creator) {
            let role_str = body.caller_role.as_deref().unwrap_or("consultant");
            let role = match role_str {
                "manager" => bcs_service_api::ParticipantRole::Manager,
                "worker" => bcs_service_api::ParticipantRole::Worker,
                "observer" => bcs_service_api::ParticipantRole::Observer,
                _ => bcs_service_api::ParticipantRole::Consultant,
            };
            let is_human = creator.starts_with("human_");
            let actor_kind = if is_human { ActorKind::Human } else { ActorKind::Bot };
            let mode = if is_human {
                Some(bcs_service_api::ParticipantMode::Present)
            } else {
                Some(bcs_service_api::ParticipantMode::Auto)
            };
            let bot_name = if is_human {
                caller_nick_name.clone()
            } else {
                state.services.registry.get(&creator).await
                    .and_then(|b| b.capabilities.name.clone())
            };
            session_participants.push(bcs_service_api::Participant {
                bot_uuid: creator.clone(),
                bot_name,
                kind: None,
                role,
                actor_kind,
                mode,
            });
        }
    }

    // Handle reactivation of existing session
    if let Some(ref _sid) = body.session_id {
        // Verify session belongs to this group
        // (belongs_to_group check via session_management)
        let cmd = bcs_service_api::CreateOrReactivateCommand {
            group_id,
            session_id: body.session_id,
            params: bcs_service_api::NewSessionParams {
                session_kind,
                input: body.input,
                meta: body.meta,
                created_by: Some(creator.clone()),
                session_title: body.session_title,
                caller_principal: Some(requester.clone()),
                group_version: Some(group.version),
                participants: session_participants,
                ..Default::default()
            },
        };
        match state.services.session_management.create_or_reactivate(cmd).await {
            Ok(outcome) => {
                let code = if outcome.created { StatusCode::CREATED } else { StatusCode::OK };
                let sess = outcome.session;

                let run = match start_state_machine_run_for_session(
                    &state,
                    &group,
                    &sess,
                    Some(requester.clone()),
                )
                .await
                {
                    Ok(run) => run,
                    Err(response) => return response,
                };
                return (
                    code,
                    Json(session_to_json_with_state_machine_run(&sess, run.as_ref())),
                ).into_response();
            }
            Err(e) => return session_error_to_response(&e),
        }
    }

    // New session
    let cmd = bcs_service_api::CreateOrReactivateCommand {
        group_id,
        session_id: None,
        params: bcs_service_api::NewSessionParams {
            session_kind,
            input: body.input,
            meta: body.meta,
            created_by: Some(creator.clone()),
            session_title: body.session_title,
            caller_principal: Some(requester.clone()),
            group_version: Some(group.version),
            participants: session_participants,
            ..Default::default()
        },
    };

    match state.services.session_management.create_or_reactivate(cmd).await {
        Ok(outcome) => {
            let code = if outcome.created { StatusCode::CREATED } else { StatusCode::OK };
            let mut sess = outcome.session;

            // Auto-add human creator to session participants when the
            // frontend explicitly sent created_by=human_xxx. Bot creators
            // are already covered by the group participant copy above.
            if explicit_human_creator && creator.starts_with("human_") {
                let already_in = sess.participants.iter().any(|p| p.bot_uuid == creator);
                if !already_in {
                    let p = bcs_service_api::Participant {
                        bot_uuid: creator.clone(),
                        bot_name: caller_nick_name.clone(),
                        kind: None,
                        role: bcs_service_api::ParticipantRole::Driver,
                        actor_kind: ActorKind::Human,
                        mode: Some(bcs_service_api::ParticipantMode::Present),
                    };
                    match state
                        .services
                        .session_management
                        .add_participant(&sess.id, p)
                        .await
                    {
                        Ok(updated) => sess = updated,
                        Err(e) => {
                            tracing::warn!(
                                session_id = %sess.id,
                                actor_id = %creator,
                                error = %e,
                                "failed to auto-add session creator (human) to session"
                            );
                        }
                    }
                }
            }

            let run = match start_state_machine_run_for_session(
                &state,
                &group,
                &sess,
                Some(requester.clone()),
            )
            .await
            {
                Ok(run) => run,
                Err(response) => return response,
            };

            if outcome.created && run.is_none() {
                let notify = state.services.system_message.clone();
                let group_id = group.id.clone();
                let sid = sess.id.clone();
                let session_input = sess.input.clone();
                let session_participants = sess.participants.clone();
                let reason = group
                    .label
                    .clone()
                    .unwrap_or_else(|| "协作任务".to_string());
                let _task = tokio::spawn(async move {
                    let _ = notify
                        .notify(
                            &group_id,
                            SystemMessageEvent::SessionContext {
                                group_id: group_id.clone(),
                                session_id: sid.clone(),
                                reason,
                                session_input,
                                task_ledger: None,
                            },
                            &sid,
                            &session_participants,
                        )
                        .await;
                });
            }

            (
                code,
                Json(session_to_json_with_state_machine_run(&sess, run.as_ref())),
            ).into_response()
        }
        Err(e) => session_error_to_response(&e),
    }
}

// ---------------------------------------------------------------
// GET /groups/{id}/sessions
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct ListSessionsQuery {
    #[serde(default)]
    pub status: Option<bcs_service_api::SessionStatus>,
    /// Optional search query: filters by session_title substring (case-insensitive).
    #[serde(default)]
    pub q: Option<String>,
    /// Filter sessions by participant (bot_uuid or human actor_id).
    #[serde(default)]
    pub participant: Option<String>,
    /// When true, return only sessions collected by `participant` (requires participant).
    #[serde(default)]
    pub collected: Option<bool>,
    #[serde(default)]
    pub offset: u64,
    #[serde(default = "default_limit")]
    pub limit: u64,
}

fn default_limit() -> u64 {
    20
}

pub async fn list_sessions_for_group(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    Query(params): Query<ListSessionsQuery>,
    headers: HeaderMap,
    uri: Uri,
) -> impl IntoResponse {
    // collected filter: requires participant, returns only sessions the bot
    // has collected in this group. Does NOT run the legacy auto-create path.
    if params.collected == Some(true) {
        let bot_uuid = match params.participant.as_deref() {
            Some(p) => p,
            None => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({
                        "error": "invalid_params",
                        "message": "collected filter requires participant"
                    })),
                )
                    .into_response();
            }
        };
        // Authorization: the caller must be authorized to act as (and therefore
        // view the collections of) the requested `participant` bot. A bot token
        // resolves to itself; a human caller must own the named bot. Without
        // this check any caller could enumerate any bot's collected sessions
        // in any group (BOLA).
        match resolve_collector_bot(&state, &headers, &uri, Some(bot_uuid)).await {
            Ok(authorized_bot) => {
                if authorized_bot != bot_uuid {
                    return (
                        StatusCode::FORBIDDEN,
                        Json(serde_json::json!({
                            "error": "forbidden",
                            "message": format!(
                                "caller is not authorized to view collections for bot {}",
                                bot_uuid
                            )
                        })),
                    )
                        .into_response();
                }
            }
            Err(resp) => return resp,
        }
        let collected_sessions = match state
            .services
            .session_management
            .list_collected_by_group(
                &group_id,
                bot_uuid,
                params.status,
                params.q.as_deref(),
                params.offset,
                params.limit,
            )
            .await
        {
            Ok(v) => v,
            Err(e) => return session_error_to_response(&e),
        };
        let items: Vec<Value> = collected_sessions
            .iter()
            .map(|s| {
                let mut v = session_to_json(s);
                if let Some(obj) = v.as_object_mut() {
                    // This branch only returns collected sessions, so every item
                    // is collected=true. collected_at was populated by the store.
                    obj.insert("collected".into(), Value::Bool(true));
                }
                v
            })
            .collect();
        return Json(serde_json::json!({
            "items": items,
            "group_id": group_id,
        }))
        .into_response();
    }

    let sessions = match state
        .services
        .session_management
        .list_by_group(
            &group_id,
            params.status,
            params.offset,
            params.limit,
            params.q.as_deref(),
            params.participant.as_deref(),
        )
        .await
    {
        Ok(v) => v,
        Err(e) => return session_error_to_response(&e),
    };

    // Resolve caller for visibility filtering. Temp participants (caller
    // added only to a session, not to group.participants) can only see
    // sessions they themselves are in. Formal group members see all.
    //
    // Bug fix #11: Human caller must be expanded to {actor_id, ...owned bots}
    // so a Human who owns a driver-bot in the group is treated as formal
    // (legacy server.rs:12767-12782).
    let caller_actor = resolve_group_chat_caller(&state, &headers, &uri).await.ok();
    let caller_ids: Vec<String> = match &caller_actor {
        Some(GroupChatCaller::Bot { bot_uuid }) => vec![bot_uuid.clone()],
        Some(GroupChatCaller::Human(h)) => {
            let mut ids = vec![h.actor_id.clone()];
            for b in state
                .services
                .registry
                .list_bots_by_creator(&h.staff_no)
                .await
            {
                ids.push(b.bot_uuid);
            }
            ids
        }
        None => Vec::new(),
    };
    let is_formal_member = if caller_ids.is_empty() {
        false
    } else if let Some(g) = state.services.group.get(&group_id).await {
        caller_ids
            .iter()
            .any(|id| g.participants.iter().any(|p| p.bot_uuid == *id))
    } else {
        false
    };

    // When participant filter is already applied by the service layer,
    // visibility is implicitly scoped. Otherwise, temp participants
    // only see sessions they belong to.
    let visible: Vec<_> = if is_formal_member || params.participant.is_some() {
        sessions
    } else if !caller_ids.is_empty() {
        sessions
            .into_iter()
            .filter(|s| {
                caller_ids
                    .iter()
                    .any(|id| s.participants.iter().any(|p| p.bot_uuid == *id))
            })
            .collect()
    } else {
        sessions
    };

    // Legacy session auto-create: only when the group has no sessions at all.
    // The response list may be empty because of caller visibility or a
    // participant filter, and that must not be treated as an empty group.
    let should_check_legacy_auto_create = visible.is_empty()
        && is_formal_member
        && params.q.is_none()
        && params.offset == 0
        && params.status != Some(bcs_service_api::SessionStatus::Completed);

    let group_has_any_session = if should_check_legacy_auto_create {
        match state
            .services
            .session_management
            .list_by_group(&group_id, None, 0, 1, None, None)
            .await
        {
            Ok(existing) => !existing.is_empty(),
            Err(e) => return session_error_to_response(&e),
        }
    } else {
        false
    };

    // If the group is truly sessionless, create the legacy session entry so
    // old groups (pre-session-split) still present a selectable session to the
    // frontend (matches legacy server.rs:12838-12871).
    let legacy_sid = format!("{group_id}:00000000");
    let items: Vec<Value> = if should_check_legacy_auto_create && !group_has_any_session {
        // First try to fetch — fast path when the legacy row already exists.
        match state.services.session_management.get(&legacy_sid).await {
            Ok(Some(sess)) => vec![session_to_json(&sess)],
            _ => {
                // Build a chat session pinned to the deterministic legacy id.
                let mut group = match state.services.group.get(&group_id).await {
                    Some(g) => g,
                    None => {
                        return (
                            StatusCode::NOT_FOUND,
                            Json(serde_json::json!({
                                "error": "not_found",
                                "message": format!("group {} not found", group_id)
                            })),
                        )
                            .into_response();
                    }
                };
                state.services.backfill_bot_names(&mut group).await;
                let mut session_participants = group.participants.clone();
                for p in session_participants.iter_mut() {
                    if p.mode.is_none() {
                        p.mode = Some(bcs_service_api::ParticipantMode::default_for(p.actor_kind));
                    }
                }
                let cmd = bcs_service_api::CreateOrReactivateCommand {
                    group_id: group_id.clone(),
                    session_id: None,
                    params: bcs_service_api::NewSessionParams {
                        session_kind: bcs_service_api::SessionKind::Chat,
                        participants: session_participants,
                        group_version: Some(group.version),
                        id: Some(legacy_sid.clone()),
                        ..Default::default()
                    },
                };
                match state.services.session_management.create_or_reactivate(cmd).await {
                    Ok(outcome) => vec![session_to_json(&outcome.session)],
                    Err(e) => {
                        tracing::warn!(
                            group_id = %group_id,
                            error = %e,
                            "auto-create legacy session failed"
                        );
                        Vec::new()
                    }
                }
            }
        }
    } else {
        visible.iter().map(|s| session_to_json(s)).collect()
    };

    // When the request explicitly specifies a participant, surface that
    // participant's per-session collected state on each item: collected (bool)
    // for every item, and collected_at (epoch ms) for the collected ones. When
    // no participant is given, neither field is added.
    let items: Vec<Value> = if let Some(p) = params.participant.as_deref() {
        let ids: Vec<&str> = items
            .iter()
            .filter_map(|v| v.get("id").and_then(|i| i.as_str()))
            .collect();
        let collected_map: std::collections::HashMap<String, u64> = state
            .services
            .session_management
            .collected_at_map(&ids, p)
            .await
            .unwrap_or_default()
            .into_iter()
            .collect();
        items
            .into_iter()
            .map(|mut v| {
                if let Some(obj) = v.as_object_mut() {
                    // Query the map by &str directly (String: Borrow<str>),
                    // avoiding a per-item String allocation.
                    if let Some(sid) = obj.get("id").and_then(|i| i.as_str()) {
                        if let Some(ts) = collected_map.get(sid) {
                            obj.insert("collected".into(), Value::Bool(true));
                            obj.insert("collected_at".into(), Value::from(*ts));
                        } else {
                            obj.insert("collected".into(), Value::Bool(false));
                        }
                    }
                }
                v
            })
            .collect()
    } else {
        items
    };

    Json(serde_json::json!({
        "items": items,
        "group_id": group_id,
    }))
    .into_response()
}

// ---------------------------------------------------------------
// GET /sessions/{sid}
// ---------------------------------------------------------------

pub async fn get_session_by_id(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
) -> impl IntoResponse {
    match state.services.session_management.get(&sid).await {
        Ok(Some(mut s)) => {
            bcs_service_api::backfill_participant_names(
                state.services.registry.as_ref(),
                &mut s.participants,
            )
            .await;
            Json(session_to_json(&s)).into_response()
        }
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "session not found"})),
        )
            .into_response(),
        Err(e) => session_error_to_response(&e),
    }
}

// ---------------------------------------------------------------
// PATCH /sessions/{sid}
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct PatchSessionRequest {
    #[serde(default)]
    pub session_title: Option<String>,
}

pub async fn patch_session(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    Json(body): Json<PatchSessionRequest>,
) -> impl IntoResponse {
    if let Some(title) = body.session_title {
        match state
            .services
            .session_management
            .update_title(&sid, Some(title))
            .await
        {
            Ok(s) => return Json(s).into_response(),
            Err(e) => return session_error_to_response(&e),
        }
    }
    // No fields to patch — return current session
    match state.services.session_management.get(&sid).await {
        Ok(Some(s)) => Json(session_to_json(&s)).into_response(),
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "session not found"})),
        )
            .into_response(),
        Err(e) => session_error_to_response(&e),
    }
}

// ---------------------------------------------------------------
// POST /sessions/{sid}/complete
//
// Auth (legacy server.rs:13115-13157):
//   - Caller must be authenticated (Bot token or Human cookie); 401 otherwise.
//   - ServiceInvocation sessions are rejected here; 403 with hint to use the
//     /services/* endpoint.
//   - Caller must be the parent group's lead/driver (driver bot itself or
//     Human owner of the driver bot); 403 otherwise.
// ---------------------------------------------------------------

pub async fn complete_session(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    // 1. Authenticate
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

    // 2. Look up session
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

    // 3. Service-invocation sessions must use /services/*
    if matches!(
        sess.session_kind,
        bcs_service_api::SessionKind::ServiceInvocation
    ) {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "error": "forbidden",
                "message": "service sessions cannot be completed via this endpoint"
            })),
        )
            .into_response();
    }

    // 4. Look up parent group for driver check
    let group = match state.services.group.get(&sess.group_id).await {
        Some(g) => g,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({
                    "error": "not_found",
                    "message": "parent group not found"
                })),
            )
                .into_response();
        }
    };

    // 5. Caller must be driver (bot itself, or Human who owns the driver bot)
    let is_driver = match &caller {
        GroupChatCaller::Bot { bot_uuid } => group.driver_bot == *bot_uuid,
        GroupChatCaller::Human(h) => {
            h.actor_id == group.driver_bot
                || state
                    .services
                    .registry
                    .list_bots_by_creator(&h.staff_no)
                    .await
                    .iter()
                    .any(|b| b.bot_uuid == group.driver_bot)
        }
    };
    if !is_driver {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "error": "forbidden",
                "message": "only driver can complete a chat session"
            })),
        )
            .into_response();
    }

    // 6. Complete via CAS
    let output = body.get("output").cloned();
    let error = body
        .get("error")
        .and_then(|v| v.as_str().map(String::from));

    match state
        .services
        .session_management
        .complete_if_running(&sid, output, error)
        .await
    {
        Ok(Some(session)) => {
            bcs_callback::dispatch::maybe_dispatch_for_session_with_url_guard(
                session.clone(),
                state.services.group.clone(),
                state.services.session_management.clone(),
                state.outbound_url_guard.clone(),
            );
            Json(session).into_response()
        }
        Ok(None) => Json(serde_json::json!({"already_completed": true})).into_response(),
        Err(e) => session_error_to_response(&e),
    }
}

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
}

pub async fn add_session_participant(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<AddParticipantRequest>,
) -> impl IntoResponse {
    if resolve_group_chat_caller(&state, &headers, &uri).await.is_err() {
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
    let strategy = match state.services.group.get(&sess.group_id).await {
        Some(g) => g.group_strategy,
        None => bcs_service_api::GroupStrategy::Chat,
    };

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
    let participant = bcs_service_api::Participant {
        bot_uuid: body.bot_uuid,
        bot_name: bot.capabilities.name,
        kind: None,
        role,
        actor_kind: bot.actor_kind,
        mode,
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
        .map(|sess| (Some(sess.group_id.clone()), sess.created_by.clone(), sess.caller_principal.clone()))
        .unwrap_or((None, None, None));

    // Authorization: self, session creator/caller_principal, or coordinator
    let is_self = caller_id == bot_uuid;
    let is_session_creator = session_created_by
        .as_deref()
        .map(|c| caller_id == format!("human_{}", c) || caller_id == c)
        .unwrap_or(false);
    let is_session_principal = session_caller_principal
        .as_deref()
        .map(|p| caller_id == p)
        .unwrap_or(false);
    let is_coordinator = if let Some(ref gid) = group_id {
        if let Some(group) = state.services.group.get(gid).await {
            caller_id == group.driver_bot || caller_id == group.originator()
        } else {
            false
        }
    } else {
        false
    };
    if !is_self && !is_session_creator && !is_session_principal && !is_coordinator {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({"error": "Caller is not authorized to remove this participant"})),
        )
            .into_response();
    }

    // Session creator/principal cannot remove the driver bot.
    if (is_session_creator || is_session_principal) && !is_self && !is_coordinator {
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
    pub mode: String,
}

pub async fn update_session_participant_mode(
    State(state): State<HttpAppState>,
    Path((sid, bot_uuid)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<UpdateParticipantModeRequest>,
) -> impl IntoResponse {
    if resolve_group_chat_caller(&state, &headers, &uri).await.is_err() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"error": "unauthorized"})),
        )
            .into_response();
    }

    let mode = match body.mode.as_str() {
        "auto" => bcs_service_api::ParticipantMode::Auto,
        "muted" => bcs_service_api::ParticipantMode::Muted,
        "present" => bcs_service_api::ParticipantMode::Present,
        "absent" => bcs_service_api::ParticipantMode::Absent,
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": format!("unknown mode: {}", body.mode)
                })),
            )
                .into_response()
        }
    };

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
                .into_response()
        }
        Err(e) => return session_error_to_response(&e),
    };

    // 2. Session must be running
    if !matches!(sess.status, bcs_service_api::SessionStatus::Running) {
        return (
            StatusCode::CONFLICT,
            Json(serde_json::json!({"error": "session is not running"})),
        )
            .into_response();
    }

    // 3. Resolve caller
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"error": "unauthorized"})),
            )
                .into_response()
        }
    };

    // 4. Caller must be a session participant
    let caller_id = match &caller {
        GroupChatCaller::Bot { bot_uuid } => bot_uuid.clone(),
        GroupChatCaller::Human(h) => h.actor_id.clone(),
    };
    let is_participant = sess
        .participants
        .iter()
        .any(|p| p.bot_uuid == caller_id);
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

    // 5. Route via MessageFlowService with session_id pinned from path
    // COSEC: Human messages are always bound to the authenticated actor, even
    // when the request supplies `from`; Bot callers retain legacy validation.
    let requested_sender_id = match &caller {
        GroupChatCaller::Human(human) => Some(human.actor_id.clone()),
        GroupChatCaller::Bot { .. } => body.from,
    };
    let cmd = GroupChatCommand {
        caller: group_chat_caller_context(&caller),
        group_id: sess.group_id.clone(),
        requested_sender_id,
        message: body.message,
        session_id: Some(sid.clone()),
    };

    match state.services.message_flow.handle_group_chat(cmd).await {
        Ok(outcome) => {
            Json(serde_json::json!({
                "delivered": outcome.delivered_count > 0,
                "session_id": sid,
                "group_id": outcome.group_id,
                "driver_bot": outcome.driver_bot_id,
                "delivered_count": outcome.delivered_count,
                "failed_count": outcome.failed_count,
                "delivery_results": delivery_results_json(&outcome.delivery_results),
                "mentions": outcome.mentions,
            }))
            .into_response()
        }
        Err(e) => {
            let (code, msg) = match &e {
                bcs_service_api::ServiceError::GroupNotFound(gid) => {
                    (StatusCode::NOT_FOUND, format!("group not found: {gid}"))
                }
                bcs_service_api::ServiceError::Unauthorized(m) => (StatusCode::FORBIDDEN, m.clone()),
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
                .into_response()
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
                .into_response()
        }
    };

    // 3. Resolve caller context (only when view_bot_id is provided, matching old behavior)
    let caller = if query.view_bot_id.is_some() {
        match resolve_session_caller(&state, &group, &headers, &uri).await {
            Ok(c) => c,
            Err(response) => return response,
        }
    } else {
        bcs_service_api::CallerContext::Public
    };

    let limit = query.limit.unwrap_or(u64::MAX);
    if group.group_strategy == bcs_service_api::GroupStrategy::StateMachine {
        return match state
            .services
            .collaboration_runtime
            .get_state_machine_session_history(&sid, limit, query.before)
            .await
        {
            Ok(Some(result)) => (StatusCode::OK, Json(result.messages)).into_response(),
            Ok(None) => (StatusCode::OK, Json(Vec::<bcs_service_api::GroupMessage>::new()))
                .into_response(),
            Err(error) => collaboration_error_to_response(error),
        };
    }

    // 4. Delegate to the history service for proper message conversion
    let cmd = bcs_service_api::SessionHistoryCommand {
        caller,
        group_id: sess.group_id.clone(),
        session_id: sid.clone(),
        session_participants: sess.participants.clone(),
        view_bot_id: query.view_bot_id.clone(),
        limit,
        before: query.before,
    };

    match state.services.group_message_history.get_session_history(cmd).await {
        Ok(result) => (StatusCode::OK, Json(result.messages)).into_response(),
        Err(e) => {
            let (status, body) = session_history_error_to_response(&e);
            (status, Json(body)).into_response()
        }
    }
}

async fn resolve_session_caller(
    state: &HttpAppState,
    _group: &bcs_service_api::Group,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<bcs_service_api::CallerContext, Response> {
    // Try to extract a human caller from headers
    if let Some(identity) = state.user_identity.extract(headers, uri).await {
        if let Some(staff_no) = &identity.staff_no {
            return Ok(bcs_service_api::CallerContext::Human(
                bcs_service_api::HumanActor {
                    actor_id: format!("human_{}", staff_no),
                    staff_no: staff_no.clone(),
                },
            ));
        }
    }

    // Try to extract a bot caller from token
    if let Some(bot_id) = state.bot_uuid_from_headers(headers).await {
        return Ok(bcs_service_api::CallerContext::Bot(
            bcs_service_api::BotActor { bot_uuid: bot_id },
        ));
    }

    // Default: public caller — allow through but with no ownership verification
    Ok(bcs_service_api::CallerContext::Public)
}

fn session_history_error_to_response(e: &bcs_service_api::GroupUseCaseError) -> (StatusCode, Value) {
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

// ---------------------------------------------------------------
// DELETE /sessions/{sid}
//
// Auth: bot_id query param must be the session creator, or if
// bot_id is a human, any bot owned by that human must be the
// session creator.
// ---------------------------------------------------------------

pub async fn delete_session(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    Query(query): Query<super::groups::DeleteSessionQuery>,
) -> impl IntoResponse {
    let sess = match state.services.session_management.get(&sid).await {
        Ok(Some(s)) => s,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"error": "not_found", "message": format!("session {} not found", sid)})),
            ).into_response();
        }
        Err(e) => return session_error_to_response(&e),
    };

    let caller_id = &query.bot_id;
    let session_creator = sess.created_by.as_deref().unwrap_or("");

    let authorized = if session_creator == caller_id {
        true
    } else if caller_id.starts_with("human_") {
        let staff_no = caller_id.trim_start_matches("human_");
        let owned_bots = state.services.registry.list_bots_by_creator(staff_no).await;
        owned_bots.iter().any(|b| b.bot_uuid == session_creator)
    } else {
        false
    };

    if !authorized {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "error": "forbidden",
                "message": format!("caller {} is not authorized to delete this session", caller_id)
            })),
        ).into_response();
    }

    match state.services.session_management.delete(&sid).await {
        Ok(true) => {
            // Best-effort session-file cleanup: count logged on success, error
            // logged but not fatal — orphan sweep reconciles later. MUST NOT
            // fail the session-delete response.
            match state.services.session_files.delete_all_for_session(&sid).await {
                Ok(n) => tracing::info!(
                    session_id = %sid,
                    deleted = n,
                    "cleaned up session files after session delete"
                ),
                Err(e) => tracing::warn!(
                    error = ?e,
                    session_id = %sid,
                    "session file cleanup partial failure (orphan sweep will reconcile)"
                ),
            }
            Json(serde_json::json!({"deleted": true, "session_id": sid})).into_response()
        }
        Ok(false) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "not_found", "message": "session not found"})),
        ).into_response(),
        Err(e) => session_error_to_response(&e),
    }
}

// ---------------------------------------------------------------
// POST /sessions/{sid}/collect
// DELETE /sessions/{sid}/collect
//
// Mark / unmark a session as collected by a bot. Caller resolves via
// resolve_group_chat_caller (bot token -> that bot; human cookie -> must
// supply an owned bot via the `participant` body/query field, ownership
// checked via registry.list_bots_by_creator).
// ---------------------------------------------------------------

#[derive(Debug, Deserialize, Default)]
pub struct CollectSessionRequest {
    /// Target bot that performs the collect. Required for human callers;
    /// ignored (defaults to the calling bot) when a bot token is used.
    #[serde(default)]
    pub participant: Option<String>,
}

async fn resolve_collector_bot(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
    participant: Option<&str>,
) -> Result<String, Response> {
    let caller = match resolve_group_chat_caller(state, headers, uri).await {
        Ok(c) => c,
        Err(_) => {
            return Err(
                (
                    StatusCode::UNAUTHORIZED,
                    Json(serde_json::json!({"error": "unauthorized"})),
                )
                    .into_response(),
            )
        }
    };
    match caller {
        GroupChatCaller::Bot { bot_uuid } => Ok(bot_uuid),
        GroupChatCaller::Human(h) => {
            let bot_uuid = participant.ok_or_else(|| {
                (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({
                        "error": "invalid_params",
                        "message": "human caller must supply participant"
                    })),
                )
                    .into_response()
            })?;
            let owns = state
                .services
                .registry
                .list_bots_by_creator(&h.staff_no)
                .await
                .iter()
                .any(|b| b.bot_uuid == bot_uuid);
            if !owns {
                return Err(
                    (
                        StatusCode::FORBIDDEN,
                        Json(serde_json::json!({
                            "error": "forbidden",
                            "message": format!("caller does not own bot {}", bot_uuid)
                        })),
                    )
                        .into_response(),
                );
            }
            Ok(bot_uuid.to_string())
        }
    }
}

pub async fn collect_session(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<CollectSessionRequest>,
) -> impl IntoResponse {
    let collector = match resolve_collector_bot(&state, &headers, &uri, body.participant.as_deref()).await {
        Ok(b) => b,
        Err(resp) => return resp,
    };
    match state.services.session_management.collect(&sid, &collector).await {
        Ok(()) => Json(serde_json::json!({"collected": true, "session_id": sid})).into_response(),
        Err(e) => session_error_to_response(&e),
    }
}

pub async fn uncollect_session(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Query(body): Query<CollectSessionRequest>,
) -> impl IntoResponse {
    let collector = match resolve_collector_bot(&state, &headers, &uri, body.participant.as_deref()).await {
        Ok(b) => b,
        Err(resp) => return resp,
    };
    match state.services.session_management.uncollect(&sid, &collector).await {
        Ok(()) => Json(serde_json::json!({"collected": false, "session_id": sid})).into_response(),
        Err(e) => session_error_to_response(&e),
    }
}

#[cfg(test)]
mod tests {
    // Session message history tests are in bcs-message-flow/src/group_history.rs
    // alongside the session_history_request_params and resolve_session_history_source_bots
    // functions that power the service layer.
}
