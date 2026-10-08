use axum::{
    Json,
    extract::{Path, Query, State},
    http::HeaderMap,
};
use bcs_protocol as wire;
use bcs_service_api as app;
use bcs_service_api::{
    A2aRunStatus, BotActor, CallerContext, ChatRunCancelCommand, ChatRunQueryCommand,
    GroupFusionCommand,
};
use serde::Deserialize;
use serde_json::Value;

use crate::error::HttpAdapterError;
use crate::state::HttpAppState;

use super::require_bot_id_from_headers;

#[derive(Debug, Deserialize)]
pub struct GetChatRunQuery {
    #[serde(default)]
    pub wait_ms: Option<u64>,
    #[serde(default)]
    pub since_version: Option<u64>,
}

pub async fn get_chat_run(
    State(state): State<HttpAppState>,
    Path(run_id): Path<String>,
    Query(query): Query<GetChatRunQuery>,
    headers: HeaderMap,
) -> Result<Json<Value>, HttpAdapterError> {
    let caller = resolve_bot_caller(&state, &headers).await?;
    let wait_ms = query
        .wait_ms
        .unwrap_or(0)
        .min(state.async_chat_poll_wait_max_ms);
    let since_version = query.since_version.unwrap_or(0);
    let caller_ctx = CallerContext::Bot(BotActor { bot_uuid: caller });

    let status = state
        .services
        .a2a_chat_runs
        .get_run(ChatRunQueryCommand {
            caller: caller_ctx,
            run_id: run_id.clone(),
            wait_ms,
            since_version,
        })
        .await?;

    Ok(Json(chat_run_status_to_json(
        status,
        chat_version_supports_submitted(&headers),
    )))
}

pub async fn cancel_chat_run(
    State(state): State<HttpAppState>,
    Path(run_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, HttpAdapterError> {
    let caller = resolve_bot_caller(&state, &headers).await?;
    let status = state
        .services
        .a2a_chat_runs
        .cancel_run(ChatRunCancelCommand {
            caller: CallerContext::Bot(BotActor { bot_uuid: caller }),
            run_id: run_id.clone(),
        })
        .await?;

    Ok(Json(chat_run_cancel_to_json(status)))
}

pub async fn fuse_context(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    Json(req): Json<wire::FusionRequest>,
) -> Result<Json<wire::FusionResponse>, HttpAdapterError> {
    let response = state
        .services
        .group_fusion
        .fuse_for_group(GroupFusionCommand {
            group_id,
            request: to_app_fusion_request(req),
        })
        .await?;
    Ok(Json(to_wire_fusion_response(response)))
}

async fn resolve_bot_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
) -> Result<String, HttpAdapterError> {
    require_bot_id_from_headers(state, headers).await
}

fn chat_run_status_to_json(status: A2aRunStatus, expose_submitted: bool) -> Value {
    let details = status
        .response
        .clone()
        .unwrap_or_else(|| serde_json::json!({}));
    let mut state = details
        .get("state")
        .and_then(|v| v.as_str())
        .unwrap_or(&status.status)
        .to_string();
    if state == "submitted" && !expose_submitted {
        state = "running".to_string();
    }
    let content = details
        .get("content")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let is_terminal = details
        .get("is_terminal")
        .and_then(|v| v.as_bool())
        .unwrap_or(matches!(
            state.as_str(),
            "completed" | "failed" | "cancelled"
        ));

    serde_json::json!({
        "run_id": status.run_id,
        "bot_uuid": details.get("bot_uuid").cloned().unwrap_or(Value::Null),
        "from_bot_id": details.get("from_bot_id").cloned().unwrap_or(Value::Null),
        "session_id": details.get("session_id").cloned().unwrap_or(Value::Null),
        "state": state,
        "response": {"content": content},
        "error_message": details.get("error_message").cloned().unwrap_or(Value::Null),
        "created_at_ms": details.get("created_at_ms").cloned().unwrap_or(Value::Null),
        "updated_at_ms": details.get("updated_at_ms").cloned().unwrap_or(Value::Null),
        "completed_at_ms": details.get("completed_at_ms").cloned().unwrap_or(Value::Null),
        "expires_at_ms": details.get("expires_at_ms").cloned().unwrap_or(Value::Null),
        "version": details.get("version").cloned().unwrap_or(Value::Null),
        "content_truncated": details.get("content_truncated").cloned().unwrap_or(Value::Bool(false)),
        "is_terminal": is_terminal,
    })
}

fn chat_version_supports_submitted(headers: &HeaderMap) -> bool {
    headers
        .get(wire::BCS_CHAT_VERSION_HEADER)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.trim().parse::<u32>().ok())
        .is_some_and(|version| version >= 2)
}

fn chat_run_cancel_to_json(status: A2aRunStatus) -> Value {
    let details = status
        .response
        .clone()
        .unwrap_or_else(|| serde_json::json!({}));
    let state = details
        .get("state")
        .and_then(|v| v.as_str())
        .unwrap_or(&status.status)
        .to_string();
    let content = details
        .get("content")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let cancelled = details
        .get("cancelled")
        .and_then(|v| v.as_bool())
        .unwrap_or(state == "cancelled");

    serde_json::json!({
        "run_id": status.run_id,
        "cancelled": cancelled,
        "state": state,
        "response": {"content": content},
        "error_message": details.get("error_message").cloned().unwrap_or(Value::Null),
        "version": details.get("version").cloned().unwrap_or(Value::Null),
        "content_truncated": details.get("content_truncated").cloned().unwrap_or(Value::Bool(false)),
    })
}

fn to_app_fusion_request(request: wire::FusionRequest) -> app::FusionRequest {
    app::FusionRequest {
        question: request.question,
        participants: request.participants,
        focus: request.focus,
        session_id: request.session_id,
        fusion_mode: request.fusion_mode,
    }
}

fn to_wire_fusion_response(response: app::FusionResponse) -> wire::FusionResponse {
    wire::FusionResponse {
        perspectives: response
            .perspectives
            .into_iter()
            .map(to_wire_participant_perspective)
            .collect(),
        conflicts: response.conflicts.into_iter().map(to_wire_conflict).collect(),
        alignment_points: response.alignment_points,
        recommendation: response.recommendation,
        key_insights: response.key_insights,
        extra: response.extra,
    }
}

fn to_wire_participant_perspective(
    perspective: app::ParticipantPerspective,
) -> wire::ParticipantPerspective {
    wire::ParticipantPerspective {
        bot_uuid: perspective.bot_uuid,
        name: perspective.name,
        emoji: perspective.emoji,
        summary: perspective.summary,
        key_points: perspective.key_points,
        concerns: perspective.concerns,
        role: perspective.role,
        confidence: perspective.confidence,
        status: perspective.status,
        participant_type: perspective.participant_type,
        evidence: perspective.evidence,
    }
}

fn to_wire_conflict(conflict: app::Conflict) -> wire::Conflict {
    wire::Conflict {
        parties: conflict.parties,
        issue: conflict.issue,
        positions: conflict
            .positions
            .into_iter()
            .map(|position| wire::ConflictPosition {
                bot_uuid: position.bot_uuid,
                view: position.view,
            })
            .collect(),
        severity: conflict.severity,
    }
}
