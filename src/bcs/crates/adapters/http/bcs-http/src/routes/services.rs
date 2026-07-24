//! Service-invocation HTTP handlers (对外服务化入口).

use crate::service_key::{ResolvedCaller, caller_principal_for, sha256_hex};
use axum::{
    Json,
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
};
use bcs_domain::SystemMessageEvent;
use bcs_service_api::{StartStateMachineRunCommand, StartStateMachineRunOutcome};
use serde::Deserialize;
use serde_json::Value;

use super::collaboration_runs::collaboration_error_to_response;
use super::sessions::session_error_to_response;
use super::{bot_token_from_headers, validate_container_header};
use crate::state::HttpAppState;

#[derive(Debug, Deserialize)]
pub struct InvocationRequest {
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub caller_id: Option<String>,
    #[serde(default)]
    pub input: Option<Value>,
    #[serde(default)]
    pub session_title: Option<String>,
    #[serde(default)]
    pub meta: Option<Value>,
}

/// POST /services/{group_id}/sessions
pub async fn post_invocation(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    headers: HeaderMap,
    Json(body): Json<InvocationRequest>,
) -> impl IntoResponse {
    let caller = match resolve_service_caller(&state, &headers, &group_id).await {
        Ok(caller) => caller,
        Err(response) => return response,
    };
    if let Some(session_id) = &body.session_id {
        // Use the SessionManagementService::belongs_to_group contract here
        // (rather than `s.group_id == group_id`) so future stores with env
        // scoping or soft-delete filtering can layer rules without callers
        // silently bypassing them. Bug fix #13.
        let belongs = state
            .services
            .session_management
            .belongs_to_group(session_id, &group_id)
            .await
            .unwrap_or(false);
        if !belongs {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({
                    "error": "not_found",
                    "message": format!("invocation {} not found in group {}", session_id, group_id),
                })),
            )
                .into_response();
        }
    }
    let mut group = match state.services.group.get(&group_id).await {
        Some(group) => group,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({
                    "error": "not_found",
                    "message": format!("group {} not found", group_id),
                })),
            )
                .into_response();
        }
    };
    state.services.backfill_bot_names(&mut group).await;
    if group.service_spec.is_none() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "invalid_params",
                "message": format!("group {} is not a service group (no service_spec)", group_id),
            })),
        )
            .into_response();
    }
    if body.session_id.is_none() {
        if let Some(max) = group
            .service_spec
            .as_ref()
            .and_then(|spec| spec.max_concurrency)
        {
            let current = state
                .services
                .session_management
                .count_running_service(&group_id)
                .await
                .unwrap_or(0);
            if current as i32 >= max {
                return (
                    StatusCode::TOO_MANY_REQUESTS,
                    Json(serde_json::json!({
                        "error": "max_concurrency_exceeded",
                        "max": max,
                        "current_running": current,
                        "retry_after_seconds": 10,
                    })),
                )
                    .into_response();
            }
        }
    }
    let session_input = body.input.clone();
    let cmd = bcs_service_api::CreateOrReactivateCommand {
        group_id: group_id.clone(),
        session_id: body.session_id,
        params: bcs_service_api::NewSessionParams {
            session_kind: bcs_service_api::SessionKind::ServiceInvocation,
            participants: group.participants.clone(),
            group_version: Some(group.version),
            caller_id: body.caller_id,
            caller_principal: Some(caller.caller_principal.clone()),
            input: body.input,
            created_by: Some(caller.caller_principal),
            session_title: body.session_title,
            meta: body.meta,
            ..Default::default()
        },
    };

    match state
        .services
        .session_management
        .create_or_reactivate(cmd)
        .await
    {
        Ok(outcome) => {
            let reused = !outcome.created;
            let run = if group.group_strategy == bcs_service_api::GroupStrategy::StateMachine {
                match state
                    .services
                    .collaboration_runtime
                    .start_state_machine_run(StartStateMachineRunCommand {
                        group_id: group_id.clone(),
                        session_id: Some(outcome.session.id.clone()),
                        definition_yaml: None,
                        definition: None,
                        definition_ref: None,
                        input: outcome.session.input.clone().unwrap_or(Value::Null),
                        caller_id: outcome.session.caller_id.clone(),
                        authenticated_human: None,
                    })
                    .await
                {
                    Ok(outcome) => Some(outcome),
                    Err(error) => return collaboration_error_to_response(error),
                }
            } else {
                None
            };
            if outcome.created && run.is_none() {
                let notify = state.services.system_message.clone();
                let gid = group_id.clone();
                let sid = outcome.session.id.clone();
                let session_participants = outcome.session.participants.clone();
                let reason = group
                    .label
                    .clone()
                    .unwrap_or_else(|| "协作任务".to_string());
                let _task = tokio::spawn(async move {
                    let _ = notify
                        .notify(
                            &gid,
                            SystemMessageEvent::SessionContext {
                                group_id: gid.clone(),
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
            // Per spec, successful service invocations return 202 Accepted
            (
                StatusCode::ACCEPTED,
                Json(service_session_to_json_with_state_machine_run(
                    &outcome.session,
                    reused,
                    run.as_ref(),
                )),
            )
                .into_response()
        }
        Err(e) => session_error_to_response(&e),
    }
}

async fn resolve_service_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    group_id: &str,
) -> Result<ResolvedCaller, axum::response::Response> {
    if let Some(raw_key) = headers
        .get("X-BCS-Service-Key")
        .and_then(|header| header.to_str().ok())
        .filter(|value| !value.is_empty())
    {
        let registry = &state.service_api_keys;
        if registry.is_empty() {
            let sha256 = sha256_hex(raw_key);
            return Ok(ResolvedCaller {
                key_name: "service-key".to_string(),
                caller_principal: caller_principal_for(&sha256),
            });
        }
        let entry = match registry.resolve(raw_key) {
            Some(entry) => entry,
            None => {
                return Err((
                    StatusCode::UNAUTHORIZED,
                    Json(serde_json::json!({"error": "invalid_key"})),
                )
                    .into_response());
            }
        };
        if !entry.bound_groups.is_empty()
            && !entry.bound_groups.iter().any(|bound| bound == group_id)
        {
            return Err((
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({"error": "key_not_bound_to_group", "group_id": group_id})),
            )
                .into_response());
        }
        return Ok(ResolvedCaller {
            key_name: entry.name.clone(),
            caller_principal: caller_principal_for(&entry.sha256),
        });
    }

    match bot_token_from_headers(headers) {
        Some(token) if !token.is_empty() => {}
        _ => {
            return Err((
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({
                    "error": "missing_bot_identity",
                    "message": "valid bot token is required when X-BCS-Service-Key is absent"
                })),
            )
                .into_response());
        }
    };

    let bot_id = match state.bot_uuid_from_headers(headers).await {
        Some(bot_id) => bot_id,
        None => {
            return Err((
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"error": "invalid_bot_token"})),
            )
                .into_response());
        }
    };
    if validate_container_header(state, headers, &bot_id).is_err() {
        return Err((
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"error": "invalid_bot_token"})),
        )
            .into_response());
    }
    Ok(ResolvedCaller {
        key_name: format!("bot:{}", bot_id),
        caller_principal: format!("bot:{}", bot_id),
    })
}

/// GET /services/{group_id}/sessions/{session_id}
///
/// Auth:
///   - service-key callers are validated against `bound_groups`;
///   - bot-token callers are resolved as `bot:<bot_id>`;
///   - the session's `caller_principal` MUST match the resolved caller.
pub async fn get_service_session(
    State(state): State<HttpAppState>,
    Path((_group_id, session_id)): Path<(String, String)>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let group_id = _group_id;
    let caller = match resolve_service_caller(&state, &headers, &group_id).await {
        Ok(c) => c,
        Err(response) => return response,
    };
    // Bug fix #13: prefer the contract method over `session.group_id` field
    // comparison. Cross-group probes should also be 404, not leak.
    let belongs = state
        .services
        .session_management
        .belongs_to_group(&session_id, &group_id)
        .await
        .unwrap_or(false);
    if !belongs {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "session not found"})),
        )
            .into_response();
    }
    match state.services.session_management.get(&session_id).await {
        Ok(Some(s)) => {
            // Enforce caller_principal isolation: bot callers and service-key
            // callers can only read sessions they created.
            if s.caller_principal.as_deref() != Some(caller.caller_principal.as_str()) {
                return (
                    StatusCode::FORBIDDEN,
                    Json(serde_json::json!({
                        "error": "forbidden",
                        "message": "caller_principal mismatch",
                    })),
                )
                    .into_response();
            }
            Json(service_session_to_json(&s, false)).into_response()
        }
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "session not found"})),
        )
            .into_response(),
        Err(e) => session_error_to_response(&e),
    }
}

/// Convert a Session to the service-invocation wire format.
/// Matches legacy `session_to_json_with_reused`:
/// - `session_id` key (not `id`)
/// - default-filled participant modes
/// - optional `reused` flag
fn service_session_to_json(sess: &bcs_service_api::Session, reused: bool) -> Value {
    let participants_json: Vec<Value> = sess
        .participants
        .iter()
        .map(|p| {
            let mut filled = p.clone();
            if filled.mode.is_none() {
                filled.mode = Some(bcs_service_api::ParticipantMode::default_for(
                    filled.actor_kind,
                ));
            }
            serde_json::to_value(filled).unwrap_or_else(|_| Value::Null)
        })
        .collect();
    serde_json::json!({
        "session_id": sess.id,
        "group_id": sess.group_id,
        "session_title": sess.session_title,
        "status": sess.status,
        "session_kind": sess.session_kind,
        "activation_count": sess.activation_count,
        "participants": participants_json,
        "input": sess.input,
        "output": sess.output,
        "error_message": sess.error_message,
        "callback_status": sess.callback_status,
        "meta": sess.meta,
        "reused": reused,
        "created_at": sess.created_at,
        "updated_at": sess.updated_at,
        "completed_at": sess.completed_at,
    })
}

fn service_session_to_json_with_state_machine_run(
    sess: &bcs_service_api::Session,
    reused: bool,
    run: Option<&StartStateMachineRunOutcome>,
) -> Value {
    let mut v = service_session_to_json(sess, reused);
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
