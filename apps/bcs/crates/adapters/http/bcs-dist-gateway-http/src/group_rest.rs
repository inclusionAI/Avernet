//! The REST/A2A group surface of published FSM groups (plan T8):
//! HTTP+JSON and SSE under `/api/v1/collaboration/a2a/groups/{group_id}`,
//! beside — never inside — the legacy JSON-RPC binding.
//!
//! Protocol rules owned by this adapter:
//!
//! - The action rides the dynamic path segment (`message:send`,
//!   `message:stream`, `tasks/{id}:cancel`, `tasks/{id}:subscribe`).
//! - The caller is the trusted `GatewayCaller` extension the front
//!   authentication layer installs; client-reported metadata is never read
//!   for identity or authorization.
//! - Errors answer `{"error": {"code", "message"}}` with stable machine
//!   codes; a session whose context was quarantined answers
//!   `400 CONTEXT_UNAVAILABLE`, never an anonymous live fallback.
//! - One public frame (`data:` or request body) is bounded by the public
//!   frame budget; the single external budget code is
//!   `FRAME_BUDGET_EXCEEDED` — the internal `frame_budget` and
//!   `projection_frame_budget` spellings both map to it.
//! - History rows default to 0 and never exceed 100; a page never exceeds
//!   100 rows.
//!
//! No SSE frame or protocol shape crosses into the Runner or store: this
//! module normalizes into application request types and maps results back.

use crate::mapping::{self, TaskShape};
use crate::{AppState, cursor};
use a2a::{AgentCapabilities, AgentCard, AgentInterface, SendMessageResponse, StreamResponse};
use axum::Json;
use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use bcs_dist_model::{
    GroupId, PageToken, PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES, RequestId, Sid, TaskStatus, Tid,
};
use bcs_service_api::application::dist_gateway::{
    CancelTask, CreationSource, GatewayError, GroupTaskList, SubscribeMode, SubscribeTask,
    SubmitInput, TaskStreamItem, WaitTask, WaitUntil,
};
use serde::Deserialize;
use serde_json::{Value, json};
use futures::StreamExt;
use std::num::NonZeroU32;
use std::time::Duration;

/// The fixed path prefix of the group surface.
pub const GROUPS_PREFIX: &str = "/api/v1/collaboration/a2a/groups";

/// Largest history a group answer carries (candidate profile bound).
const MAX_HISTORY: usize = 100;

const LAST_EVENT_ID: &str = "last-event-id";
const REQUEST_ID: &str = "x-request-id";

// ---- wire DTOs (owned here; never in the application or store) ----

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct GroupSendConfiguration {
    /// Explicit blocking choice; wins over `returnImmediately` when both ride
    /// the request.
    blocking: Option<bool>,
    return_immediately: Option<bool>,
    #[serde(alias = "wait_timeout_millis")]
    wait_timeout_millis: Option<u32>,
    #[serde(alias = "history_length")]
    history_length: Option<i32>,
    #[serde(alias = "accepted_output_modes")]
    accepted_output_modes: Option<Vec<String>>,
    #[serde(rename = "taskPushNotificationConfig", alias = "pushNotificationConfig", alias = "push_notification_config")]
    push_notification_config: Option<Value>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct GroupSendRequest {
    message: a2a::Message,
    #[serde(default)]
    configuration: Option<GroupSendConfiguration>,
}

#[derive(Debug, Default, Deserialize)]
pub(crate) struct TaskQuery {
    #[serde(default, alias = "pageSize")]
    page_size: Option<u32>,
    #[serde(default, rename = "pageCursor")]
    page_cursor: Option<String>,
    #[serde(default)]
    status: Option<String>,
    #[serde(default, rename = "includeArtifacts")]
    include_artifacts: Option<bool>,
    #[serde(default, rename = "historyLength")]
    history_length: Option<i32>,
}

#[derive(Debug, Default, Deserialize)]
pub(crate) struct CancelRequest {
    #[serde(default)]
    reason: Option<String>,
}

fn error_body(status: StatusCode, code: &str, message: &str) -> Response {
    let body = json!({ "error": { "code": code, "message": message } });
    (status, Json(body)).into_response()
}

/// The one external budget code of the public surface.
const FRAME_BUDGET_EXCEEDED: &str = "FRAME_BUDGET_EXCEEDED";

/// `GatewayError` as HTTP status + stable machine code.
fn rest_error(error: &GatewayError) -> Response {
    match error {
        GatewayError::NotFound => error_body(StatusCode::NOT_FOUND, "NOT_FOUND", "task not found"),
        GatewayError::AccessDenied => {
            error_body(StatusCode::FORBIDDEN, "PERMISSION_DENIED", "access denied")
        }
        GatewayError::Conflict => error_body(
            StatusCode::CONFLICT,
            "CONFLICT",
            "the same message key was already used with different content",
        ),
        GatewayError::NotCancelable => error_body(
            StatusCode::CONFLICT,
            "TASK_NOT_CANCELABLE",
            "task is finished and cannot be canceled",
        ),
        GatewayError::Quarantined => error_body(
            StatusCode::BAD_REQUEST,
            "CONTEXT_UNAVAILABLE",
            "session unavailable",
        ),
        GatewayError::Invalid { code } => match code.as_str() {
            "group_round_expired" => error_body(StatusCode::GONE, "TASK_EXPIRED", "original task expired"),
            "cursor_format" => rest_invalid(StatusCode::BAD_REQUEST, "CURSOR_FORMAT_INVALID"),
            "cursor_signature" => rest_invalid(StatusCode::BAD_REQUEST, "CURSOR_SIGNATURE_INVALID"),
            "cursor_subject_mismatch" => rest_invalid(StatusCode::BAD_REQUEST, "CURSOR_SUBJECT_MISMATCH"),
            "cursor_binding" => rest_invalid(StatusCode::BAD_REQUEST, "CURSOR_BINDING_INVALID"),
            "cursor_last_key" => rest_invalid(StatusCode::BAD_REQUEST, "CURSOR_LAST_KEY_INVALID"),
            "cursor_expired" | "cut_expired" => rest_invalid(StatusCode::BAD_REQUEST, "CURSOR_EXPIRED"),
            "page_size_budget" => rest_invalid(StatusCode::BAD_REQUEST, "INVALID_ARGUMENT"),
            "frame_budget" | "projection_frame_budget" => {
                error_body(StatusCode::PAYLOAD_TOO_LARGE, FRAME_BUDGET_EXCEEDED, "public frame budget exceeded")
            }
            _ => rest_invalid(StatusCode::BAD_REQUEST, "INVALID_ARGUMENT"),
        },
        GatewayError::Unavailable { code } => match code.as_str() {
            "group_round_busy" => error_body(
                StatusCode::CONFLICT,
                "CONTEXT_BUSY",
                "another round is active in this context",
            ),
            "frame_budget" | "projection_frame_budget" => error_body(
                StatusCode::PAYLOAD_TOO_LARGE,
                FRAME_BUDGET_EXCEEDED,
                "public frame budget exceeded",
            ),
            "cut_released" => rest_invalid(StatusCode::BAD_REQUEST, "CURSOR_RELEASED"),
            _ => error_body(StatusCode::SERVICE_UNAVAILABLE, "UNAVAILABLE", "dependency unavailable"),
        },
        GatewayError::OutcomeUnknown { .. } => error_body(
            StatusCode::SERVICE_UNAVAILABLE,
            "UNAVAILABLE",
            "admission outcome unknown; retry the same request",
        ),
    }
}

fn rest_invalid(status: StatusCode, code: &str) -> Response {
    error_body(status, code, "invalid argument")
}

fn bad_json(rejection: JsonRejection) -> Response {
    // A body rejected by the frame budget keeps its 413 and the budget code;
    // every other malformed request is an invalid argument.
    match rejection {
        JsonRejection::BytesRejection(bytes) => {
            let status = bytes.status();
            let code = if status == StatusCode::PAYLOAD_TOO_LARGE {
                FRAME_BUDGET_EXCEEDED
            } else {
                "INVALID_ARGUMENT"
            };
            error_body(status, &code.to_owned(), &bytes.body_text())
        }
        other => error_body(
            StatusCode::BAD_REQUEST,
            "INVALID_ARGUMENT",
            &format!("malformed request: {}", other.body_text()),
        ),
    }
}

fn unauthenticated() -> Response {
    error_body(StatusCode::UNAUTHORIZED, "UNAUTHENTICATED", "missing resolved caller")
}

/// The history shape of one group answer: default 0, never above 100.
fn clamped_history(requested: Option<i32>) -> Option<i32> {
    Some(requested.map_or(0, |n| n.clamp(0, MAX_HISTORY as i32)))
}

// ---- handlers ----

/// POST `<prefix>/{group}/{action}`: `message:send` or `message:stream`.
pub(crate) async fn message_post(
    State(state): State<AppState>,
    Path((group, action)): Path<(String, String)>,
    caller: Option<Extension<bcs_service_api::application::dist_gateway::GatewayCaller>>,
    body: Result<Json<GroupSendRequest>, JsonRejection>,
) -> Response {
    let Some(Extension(caller)) = caller else {
        return unauthenticated();
    };
    let group = GroupId(group);
    match action.as_str() {
        "message:send" => send(state, group, caller, body).await,
        "message:stream" => send_streaming(state, group, caller, body).await,
        other => error_body(
            StatusCode::METHOD_NOT_ALLOWED,
            "UNKNOWN_ACTION",
            &format!("unknown action: {other}"),
        ),
    }
}

fn submit_input(group: GroupId, request: &GroupSendRequest) -> Result<SubmitInput, GatewayError> {
    let message = &request.message;
    if message.message_id.trim().is_empty() {
        return Err(GatewayError::Invalid {
            code: "message_id_required".to_owned(),
        });
    }
    if let Some(configuration) = &request.configuration {
        if configuration.push_notification_config.is_some() {
            return Err(GatewayError::Invalid {
                code: "push_notifications_unsupported".to_owned(),
            });
        }
        // Input/output modes are validated, not assumed: only the plain-text
        // profile this surface implements is accepted, and a request may ask
        // for less.
        if let Some(modes) = &configuration.accepted_output_modes
            && !modes.iter().all(|mode| mode == "text/plain")
        {
            return Err(GatewayError::Invalid {
                code: "output_mode_unsupported".to_owned(),
            });
        }
    }
    // The business content of the message: everything but its routing ids.
    let content = serde_json::to_value(message).map_err(|_| GatewayError::Invalid {
        code: "message_not_encodable".to_owned(),
    })?;
    let mut content = content;
    if let Some(object) = content.as_object_mut() {
        for routing in ["messageId", "contextId", "taskId"] {
            object.remove(routing);
        }
        // Self-reported metadata never rides the normalized request.
        object.remove("metadata");
    }
    Ok(SubmitInput {
        source: CreationSource::GroupA2a,
        group,
        message_id: RequestId(message.message_id.clone()),
        session: message.context_id.clone().map(Sid),
        task: message.task_id.clone().map(Tid),
        interaction: None,
        content: bcs_dist_model::Payload::Inline(content),
    })
}

async fn send(
    state: AppState,
    group: GroupId,
    caller: bcs_service_api::application::dist_gateway::GatewayCaller,
    body: Result<Json<GroupSendRequest>, JsonRejection>,
) -> Response {
    let request = match body {
        Ok(Json(request)) => request,
        Err(rejection) => return bad_json(rejection),
    };
    let history = request
        .configuration
        .as_ref()
        .and_then(|c| c.history_length);
    // Default is the A2A convention (blocking), with `blocking` as the
    // explicit override of the same budgeted wait.
    let blocking = match request.configuration.as_ref() {
        None => true,
        Some(configuration) => match configuration.blocking {
            Some(blocking) => blocking,
            None => !configuration.return_immediately.unwrap_or(false),
        },
    };
    // The caller's own budget, never above the deployment's blocking limit.
    let budget = request
        .configuration
        .as_ref()
        .and_then(|c| c.wait_timeout_millis)
        .map_or(state.config.blocking_send_wait, |millis| {
            Duration::from_millis(u64::from(millis)).min(state.config.blocking_send_wait)
        });

    let input = match submit_input(group.clone(), &request) {
        Ok(input) => input,
        Err(error) => return rest_error(&error),
    };
    let gateway = &state.gateway;
    let receipt = match gateway.group_send(&caller, input).await {
        Ok(receipt) => receipt,
        Err(error) => return rest_error(&error),
    };
    if !blocking {
        return match gateway
            .get_task(&caller, &group, &receipt.tid, CreationSource::GroupA2a)
            .await
        {
            Ok(view) => framed_final(view, history, false),
            Err(error) => rest_error(&error),
        };
    }
    let wait = WaitTask {
        group: group.clone(),
        tid: receipt.tid.clone(),
        until: WaitUntil::InputOrFinal,
        after: None,
        timeout: budget,
        source: CreationSource::GroupA2a,
    };
    match gateway.group_wait_task(&caller, wait).await {
        Ok((view, waited_out)) => framed_final(view, history, waited_out),
        Err(error) => rest_error(&error),
    }
}

/// 200 or the 504 budget expiry: the current task rides either answer, and
/// the task keeps running.
fn framed_final(view: bcs_dist_model::TaskView, history: Option<i32>, waited_out: bool) -> Response {
    let task = mapping::task(&view, TaskShape::history(history.map(|n| n.min(MAX_HISTORY as i32))));
    let status = if waited_out {
        StatusCode::GATEWAY_TIMEOUT
    } else {
        StatusCode::OK
    };
    (status, Json(SendMessageResponse::Task(task))).into_response()
}

async fn send_streaming(
    state: AppState,
    group: GroupId,
    caller: bcs_service_api::application::dist_gateway::GatewayCaller,
    body: Result<Json<GroupSendRequest>, JsonRejection>,
) -> Response {
    let request = match body {
        Ok(Json(request)) => request,
        Err(rejection) => return bad_json(rejection),
    };
    let input = match submit_input(group.clone(), &request) {
        Ok(input) => input,
        Err(error) => return rest_error(&error),
    };
    let gateway = &state.gateway;
    let receipt = match gateway.group_send(&caller, input).await {
        Ok(receipt) => receipt,
        Err(error) => return rest_error(&error),
    };
    let subscribe = SubscribeTask {
        group,
        tid: receipt.tid,
        mode: SubscribeMode::Snapshot,
        source: CreationSource::GroupA2a,
    };
    let items = match gateway.subscribe(&caller, subscribe).await {
        Ok(items) => items,
        Err(error) => return rest_error(&error),
    };
    group_stream(items, &state.config)
}

/// GET `<prefix>/{group}/tasks`: the public listing, signed cursors, the
/// filtered actual total and the effective page size of the materialized cut.
pub(crate) async fn tasks_list(
    State(state): State<AppState>,
    Path(group): Path<String>,
    caller: Option<Extension<bcs_service_api::application::dist_gateway::GatewayCaller>>,
    Query(query): Query<TaskQuery>,
) -> Response {
    let Some(Extension(caller)) = caller else {
        return unauthenticated();
    };
    let group = GroupId(group);
    let statuses = match statuses_of(query.status.as_deref()) {
        Ok(statuses) => statuses,
        Err(error) => return rest_error(&error),
    };
    let requested = query
        .page_size
        .and_then(|size| NonZeroU32::try_from(size.max(1)).ok())
        .or_else(|| NonZeroU32::new(state.config.default_page_size))
        .unwrap_or(NonZeroU32::MIN);
    let list = GroupTaskList {
        group: group.clone(),
        statuses,
        limit: requested,
        cursor: query.page_cursor.filter(|token| !token.is_empty()).map(PageToken),
        include_artifacts: query.include_artifacts.unwrap_or(false),
    };
    let page = match state.gateway.group_list_tasks(&caller, list).await {
        Ok(page) => page,
        Err(error) => return rest_error(&error),
    };
    let shape = TaskShape::history(query.history_length.map(|n| n.clamp(0, i32::try_from(MAX_HISTORY).unwrap_or(100))));
    let tasks: Vec<Value> = page
        .rows
        .iter()
        .map(|row| {
            if let Some(view) = &row.view {
                serde_json::to_value(mapping::task(view, shape)).unwrap_or_else(|_| row_json(row))
            } else {
                row_json(row)
            }
        })
        .collect();
    let body = json!({
        "tasks": tasks,
        "totalSize": page.total,
        "pageSize": page.rows.len(),
        "nextPageCursor": page.next.map(|token| token.0),
        "cut": page.cut,
    });
    Json(body).into_response()
}

/// The light row of a cut: identity and the cut's status channel only.
fn row_json(row: &bcs_service_api::port::repo::distributed::group_listing::GroupIndexRow) -> Value {
    json!({
        "id": row.tid.0,
        "contextId": row.sid.0,
        "status": { "state": mapping::state(row.status) },
        "statusUpdatedAt": row.status_updated_at.0,
    })
}

fn statuses_of(filter: Option<&str>) -> Result<Vec<TaskStatus>, GatewayError> {
    let Some(filter) = filter else {
        return Ok(Vec::new());
    };
    let mut statuses = Vec::new();
    for name in filter.split(',') {
        let name = name.trim();
        if name.is_empty() {
            continue;
        }
        let state = match name.to_ascii_lowercase().as_str() {
            "submitted" => a2a::TaskState::Submitted,
            "working" => a2a::TaskState::Working,
            "input_required" | "inputrequired" => a2a::TaskState::InputRequired,
            "auth_required" | "authrequired" => a2a::TaskState::AuthRequired,
            "completed" => a2a::TaskState::Completed,
            "failed" => a2a::TaskState::Failed,
            "canceled" => a2a::TaskState::Canceled,
            "rejected" => a2a::TaskState::Rejected,
            other => {
                return Err(GatewayError::Invalid {
                    code: format!("status_filter_unknown:{other}"),
                })
            }
        };
        if let Some(status) = mapping::status_filter(&state) {
            statuses.push(status);
        }
    }
    Ok(statuses)
}

/// GET/POST `<prefix>/{group}/tasks/{action}`: one task, its subscription or
/// its cancel — the action rides the dynamic tail.
pub(crate) async fn task_action(
    State(state): State<AppState>,
    Path((group, action)): Path<(String, String)>,
    caller: Option<Extension<bcs_service_api::application::dist_gateway::GatewayCaller>>,
    headers: HeaderMap,
    method: axum::http::Method,
    Query(query): Query<TaskQuery>,
    Query(cancel_query): Query<CancelRequest>,
) -> Response {
    let Some(Extension(caller)) = caller else {
        return unauthenticated();
    };
    let group = GroupId(group);
    let (tid, tail) = match action.split_once(':') {
        Some((tid, tail)) => (tid.to_owned(), tail.to_owned()),
        None => (action.clone(), String::new()),
    };
    let tid = Tid(tid);
    match (method.as_str(), tail.as_str()) {
        ("GET", "") => {
            match state
                .gateway
                .get_task(&caller, &group, &tid, CreationSource::GroupA2a)
                .await
            {
                Ok(view) => {
                    Json(mapping::task(&view, TaskShape::history(clamped_history(query.history_length))))
                        .into_response()
                }
                Err(error) => rest_error(&error),
            }
        }
        ("GET" | "POST", "subscribe") => {
            let mode = headers
                .get(LAST_EVENT_ID)
                .and_then(|value| value.to_str().ok())
                .and_then(cursor::decode_group)
                .filter(|cursor| cursor.tid == tid)
                .map_or(SubscribeMode::Snapshot, SubscribeMode::Resume);
            let subscribe = SubscribeTask {
                group: group.clone(),
                tid: tid.clone(),
                mode,
                source: CreationSource::GroupA2a,
            };
            let items = match state.gateway.subscribe(&caller, subscribe).await {
                Ok(items) => items,
                Err(error) => return rest_error(&error),
            };
            group_stream(items, &state.config)
        }
        ("POST", "cancel") => {
            let reason = cancel_query.reason;
            // Idempotent recovery (plan T8): when the round already reached
            // Canceled, a repeated cancel replays the same final answer
            // instead of refusing, whatever happened to the first response.
            if let Ok(view) = state
                .gateway
                .get_task(&caller, &group, &tid, CreationSource::GroupA2a)
                .await
                && view.content.status == TaskStatus::Canceled
            {
                let wait = WaitTask {
                    group: group.clone(),
                    tid: tid.clone(),
                    until: WaitUntil::Final,
                    after: None,
                    timeout: state.config.cancel_wait,
                    source: CreationSource::GroupA2a,
                };
                return match state.gateway.group_wait_task(&caller, wait).await {
                    Ok((view, _)) => Json(mapping::task(&view, TaskShape::FULL)).into_response(),
                    Err(error) => rest_error(&error),
                };
            }
            let request_id = headers
                .get(REQUEST_ID)
                .and_then(|value| value.to_str().ok())
                .map_or_else(|| "cancel".to_owned(), str::to_owned);
            let cancel = CancelTask {
                group: group.clone(),
                tid: tid.clone(),
                request_id: RequestId(request_id),
                reason,
                source: CreationSource::GroupA2a,
            };
            if let Err(error) = state.gateway.cancel(&caller, cancel).await {
                return rest_error(&error);
            }
            let wait = WaitTask {
                group: group.clone(),
                tid,
                until: WaitUntil::Final,
                after: None,
                timeout: state.config.cancel_wait,
                source: CreationSource::GroupA2a,
            };
            match state.gateway.group_wait_task(&caller, wait).await {
                // The cancel obligation is durable: the current view answers,
                // whether or not the round reached its final state yet.
                Ok((view, _)) => Json(mapping::task(&view, TaskShape::FULL)).into_response(),
                Err(error) => rest_error(&error),
            }
        }
        (_, _) => error_body(
            StatusCode::METHOD_NOT_ALLOWED,
            "UNKNOWN_ACTION",
            "unknown task action",
        ),
    }
}

/// The SSE response of a group stream. Normal frames carry the A2A
/// `StreamResponse` union (without a JSON-RPC envelope); resume acknowledgement
/// and terminal error are separate control payloads. A frame's `id:` carries
/// the cursor a client sends back as `Last-Event-ID`. A final error closes the
/// stream, so no subscription hangs on a stopped feed.
fn group_stream(
    items: bcs_service_api::application::dist_gateway::TaskSubscription,
    config: &std::sync::Arc<crate::GatewayHttpConfig>,
) -> Response {
    let owned = config.clone();
    let events = futures::stream::unfold(
        GroupStream {
            pending: std::collections::VecDeque::new(),
            items: Some(items),
        },
        group_step,
    )
    .map(Ok::<_, std::convert::Infallible>);
    Sse::new(events)
        .keep_alive(KeepAlive::new().interval(owned.keep_alive))
        .into_response()
}

/// One frame of a group stream: `None` when the serialized payload exceeds
/// the public frame budget (the caller answers one `FRAME_BUDGET_EXCEEDED`
/// frame and closes).
fn data_frame(value: &impl serde::Serialize, id: Option<String>) -> Option<Event> {
    let payload = serde_json::to_string(value).ok()?;
    if payload.len() > PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES {
        return None;
    }
    Some(match id {
        Some(id) => Event::default().data(payload).id(id),
        None => Event::default().data(payload),
    })
}

fn budget_error_frame() -> Event {
    Event::default().data(
        json!({ "error": { "code": FRAME_BUDGET_EXCEEDED, "message": "public frame budget exceeded" } }).to_string(),
    )
}

/// The stable machine code an SSE error frame carries.
fn stream_error_code(error: &GatewayError) -> &'static str {
    match error {
        GatewayError::NotFound => "NOT_FOUND",
        GatewayError::AccessDenied => "PERMISSION_DENIED",
        GatewayError::Conflict => "CONFLICT",
        GatewayError::NotCancelable => "TASK_NOT_CANCELABLE",
        GatewayError::Quarantined => "CONTEXT_UNAVAILABLE",
        GatewayError::Invalid { code } => match code.as_str() {
            "group_round_expired" => "TASK_EXPIRED",
            "cursor_format" => "CURSOR_FORMAT_INVALID",
            "cursor_signature" => "CURSOR_SIGNATURE_INVALID",
            "cursor_subject_mismatch" => "CURSOR_SUBJECT_MISMATCH",
            "cursor_binding" => "CURSOR_BINDING_INVALID",
            "cursor_last_key" => "CURSOR_LAST_KEY_INVALID",
            "cursor_expired" | "cut_expired" => "CURSOR_EXPIRED",
            "frame_budget" | "projection_frame_budget" => FRAME_BUDGET_EXCEEDED,
            _ => "INVALID_ARGUMENT",
        },
        GatewayError::Unavailable { code } => match code.as_str() {
            "group_round_busy" => "CONTEXT_BUSY",
            "frame_budget" | "projection_frame_budget" => FRAME_BUDGET_EXCEEDED,
            "cut_released" => "CURSOR_RELEASED",
            _ => "UNAVAILABLE",
        },
        GatewayError::OutcomeUnknown { .. } => "UNAVAILABLE",
    }
}

fn error_frame(error: &GatewayError) -> Event {
    Event::default().data(
        json!({ "error": { "code": stream_error_code(error), "message": error.to_string() } }).to_string(),
    )
}

/// The wire frames of one application stream item and whether the item is
/// terminal. Terminal items — a refusal from the application surface, or a
/// frame above the public frame budget — emit exactly one final error frame
/// and close the stream: the subscription is never polled again and the
/// server-side side of it drops with this state.
fn group_frames(item: Result<TaskStreamItem, GatewayError>) -> (Vec<Event>, bool) {
    match item {
        Ok(TaskStreamItem::Snapshot { view, cursor }) => {
            let task = StreamResponse::Task(mapping::task(&view, TaskShape::FULL));
            match data_frame(&task, cursor.as_ref().and_then(cursor::encode_group)) {
                Some(event) => (vec![event], false),
                None => (vec![budget_error_frame()], true),
            }
        }
        Ok(TaskStreamItem::ResumeAccepted { cursor }) => {
            let value = json!({ "resumeAccepted": true });
            match data_frame(&value, cursor::encode_group(&cursor)) {
                Some(event) => (vec![event], false),
                None => (vec![budget_error_frame()], true),
            }
        }
        Ok(TaskStreamItem::Events { events, cursor }) => {
            let last = events.len().saturating_sub(1);
            let mut frames = Vec::new();
            for (n, event) in events.iter().enumerate() {
                let Some(result) = mapping::event(&cursor.sid, event) else {
                    continue;
                };
                let id = (n == last).then(|| cursor::encode_group(&cursor)).flatten();
                match data_frame(&result, id) {
                    Some(frame) => frames.push(frame),
                    None => return (vec![budget_error_frame()], true),
                }
            }
            (frames, false)
        }
        // The application surface refused this subscription late (a revoked
        // grant, a quarantined context, a position gone for good): one
        // terminal error frame, then close.
        Err(error) => (vec![error_frame(&error)], true),
    }
}

/// The unfold state of one group SSE response.
struct GroupStream {
    pending: std::collections::VecDeque<Event>,
    items: Option<bcs_service_api::application::dist_gateway::TaskSubscription>,
}

async fn group_step(mut state: GroupStream) -> Option<(Event, GroupStream)> {
    loop {
        if let Some(frame) = state.pending.pop_front() {
            return Some((frame, state));
        }
        let mut items = std::mem::take(&mut state.items)?;
        match items.next().await {
            Some(item) => {
                let (frames, terminal) = group_frames(item);
                state.pending = frames.into();
                if !terminal {
                    // Keep polling the live subscription; a terminal item
                    // leaves `items` taken, so the stream ends after its one
                    // final error frame.
                    state.items = Some(items);
                }
            }
            None => return None,
        }
    }
}

/// GET `<prefix>/{group}/agent-card.json`: the Card of a published group,
/// served through the independent read-only discovery boundary. The card
/// declares only implemented capabilities of this surface.
pub(crate) async fn card_get(
    State(state): State<AppState>,
    Path(group): Path<String>,
    caller: Option<Extension<bcs_service_api::application::dist_gateway::GatewayCaller>>,
) -> Response {
    let Some(Extension(caller)) = caller else {
        return unauthenticated();
    };
    let card = match state.discovery.card(&caller, &GroupId(group)).await {
        Ok(card) => card,
        Err(error) => return rest_error(&error),
    };
    let base = state.config.public_url.trim_end_matches('/');
    let url = format!("{base}{}/{}", GROUPS_PREFIX, card.group.0);
    Json(AgentCard {
        name: card.group.0.clone(),
        description: format!("Published FSM group {}", card.group.0),
        version: "1".to_owned(),
        // The group surface speaks REST+JSON over HTTP with SSE streaming,
        // not the legacy JSON-RPC binding.
        supported_interfaces: vec![AgentInterface::new(url, a2a::TRANSPORT_PROTOCOL_HTTP_JSON)],
        capabilities: AgentCapabilities {
            streaming: Some(card.streaming),
            push_notifications: Some(card.push_notifications),
            // Enabled by-request extension URIs only; never a promise of an
            // unaudited capability.
            extensions: Some(
                card.extensions
                    .iter()
                    .map(|uri| a2a::AgentExtension {
                        uri: uri.clone(),
                        description: Some("enabled by request".to_owned()),
                        required: Some(false),
                        params: None,
                    })
                    .collect(),
            ),
            extended_agent_card: Some(false),
        },
        default_input_modes: card.input_modes,
        default_output_modes: card.output_modes,
        skills: Vec::new(),
        provider: None,
        documentation_url: None,
        icon_url: None,
        security_schemes: None,
        security_requirements: None,
        signatures: None,
    })
    .into_response()
}
