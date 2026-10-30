//! A2A v1.0 server (JSON-RPC binding) on the gateway listener.
//!
//! Routes, per Provider and bot (a listener may host several Providers):
//! - `GET  /a2a/{provider_id}/{bot_ref}/.well-known/agent-card.json`
//! - `POST /a2a/{provider_id}/{bot_ref}` — JSON-RPC; streaming methods answer SSE
//!
//! Every engine turn still goes through [`runtime::dispatch`]; this module only
//! owns the A2A wire contract, task lifecycle and delivery modes (stream,
//! poll via `GetTask`, push notifications).
pub mod projection;
pub mod push;
pub mod task;

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use axum::{body::{Body, Bytes}, extract::{Path, Query, State}, http::{HeaderMap, HeaderValue, StatusCode},
    response::{IntoResponse, Response}, routing::{get, post}, Json, Router};
use bcs_a2a_protocol::{self as a2a, methods, A2aErrorCode, AgentCapabilities, AgentCard, AgentExtension, AgentInterface,
    AgentSkill, JsonRpcError, JsonRpcRequest, JsonRpcResponse, Message, PartContent, PushConfigRequest, Role,
    SendMessageRequest, SendMessageResponse, StreamResponse, Task, TaskPushNotificationConfig, TaskState};
use bcs_protocol::stream::InteractionKind;
use futures::{Stream, StreamExt};
use serde_json::{json, Value};

use crate::config::BotConfig;
use crate::runtime::{self, AppState, DownstreamRequest, RunDelivery, RuntimeReply, ToBot};
use projection::{with_history_length, Projection};
use task::{A2aTask, Cursor};

pub use task::A2aRegistry;

type Providers = Arc<BTreeMap<String, Arc<AppState>>>;

const HEARTBEAT: Duration = Duration::from_secs(20);
/// Upper bound for a blocking `CancelTask` to observe the terminal state.
const CANCEL_WAIT: Duration = Duration::from_secs(10);
/// Upper bound for an interaction answer to be acknowledged by the engine.
const RESOLVE_WAIT: Duration = Duration::from_secs(10);
/// Grace for the previous run of a context to release its slot.
const BUSY_WAIT: Duration = Duration::from_secs(3);

pub fn routes() -> Router<Providers> {
    Router::new()
        .route("/a2a/{provider_id}/{bot_ref}", post(handle_rpc))
        .route("/a2a/{provider_id}/{bot_ref}/.well-known/agent-card.json", get(handle_card))
}

/// Recover persisted tasks for Providers that expose A2A bots.
pub async fn recover(state: &AppState) {
    if state.config.bots.iter().any(BotConfig::a2a_enabled) {
        let ttl = state.config.a2a.as_ref().map_or(24 * 3600, |a2a| a2a.task_ttl_secs);
        task::recover(state, Duration::from_secs(ttl)).await;
    }
}

fn resolve<'a>(providers: &'a Providers, provider_id: &str, bot_ref: &str) -> Option<(&'a Arc<AppState>, BotConfig)> {
    let state = providers.get(provider_id)?;
    let bot = state.config.bot(bot_ref).filter(|bot| bot.a2a_enabled())?.clone();
    Some((state, bot))
}

async fn handle_card(State(providers): State<Providers>, Path((provider_id, bot_ref)): Path<(String, String)>, headers: HeaderMap) -> Response {
    let Some((state, bot)) = resolve(&providers, &provider_id, &bot_ref) else { return StatusCode::NOT_FOUND.into_response() };
    let mut response = Json(agent_card(state, &bot, &headers)).into_response();
    response.headers_mut().insert(axum::http::header::CACHE_CONTROL, HeaderValue::from_static("max-age=60"));
    response
}

pub fn agent_card(state: &AppState, bot: &BotConfig, headers: &HeaderMap) -> AgentCard {
    let origin = state.config.a2a.as_ref().and_then(|a2a| a2a.public_url.clone())
        .or_else(|| headers.get(axum::http::header::HOST).and_then(|h| h.to_str().ok()).map(|host| format!("http://{host}")))
        .unwrap_or_default();
    let url = format!("{}/a2a/{}/{}", origin.trim_end_matches('/'), state.config.provider_id, bot.provider_bot_ref);
    let card = bot.a2a.clone().unwrap_or_default();
    let skills = if card.skills.is_empty() {
        vec![AgentSkill { id: "conversation".into(), name: "Conversation".into(),
            description: "Send a task in natural language and receive the agent's result".into(),
            tags: vec!["conversation".into(), bot.engine.clone()], examples: Vec::new(), input_modes: Vec::new(), output_modes: Vec::new() }]
    } else {
        card.skills.iter().map(|skill| AgentSkill { id: skill.id.clone(), name: skill.name.clone(), description: skill.description.clone(),
            tags: skill.tags.clone(), examples: skill.examples.clone(), input_modes: Vec::new(), output_modes: Vec::new() }).collect()
    };
    let mut security_schemes = serde_json::Map::new();
    security_schemes.insert("bearer".into(), json!({"httpAuthSecurityScheme": {"scheme": "Bearer"}}));
    AgentCard {
        name: card.name.unwrap_or_else(|| bot.provider_bot_ref.clone()),
        description: card.description.unwrap_or_else(|| format!("{} coding agent served by bcs-bridge", bot.engine)),
        supported_interfaces: vec![AgentInterface::jsonrpc(url)],
        provider: None,
        version: card.version.unwrap_or_else(|| env!("CARGO_PKG_VERSION").into()),
        documentation_url: None,
        capabilities: AgentCapabilities {
            streaming: Some(true),
            push_notifications: Some(true),
            extended_agent_card: Some(false),
            extensions: vec![
                AgentExtension::optional(a2a::extensions::PROGRESS, "WORKING status metadata.kind: tool, thinking, lifecycle, commentary, commentary.delta, interaction.*"),
                AgentExtension::optional(a2a::extensions::REPLAY, "SubscribeToTask resumes after the SSE Last-Event-ID (metadata.seq)"),
                AgentExtension::optional(a2a::extensions::INJECT, "SendMessage with contextId queues an observation without starting a task"),
                AgentExtension::optional(a2a::extensions::CANCEL, "CancelTask aborts the running engine turn"),
            ],
        },
        security_schemes,
        security_requirements: vec![json!({"schemes": {"bearer": {"list": []}}})],
        default_input_modes: vec!["text/plain".into(), "application/json".into()],
        default_output_modes: vec!["text/plain".into()],
        skills,
        signatures: Vec::new(),
        icon_url: None,
    }
}

/// Request-scoped context after authentication.
struct Ctx {
    state: Arc<AppState>,
    bot: BotConfig,
    owner: String,
    headers: HeaderMap,
}

async fn handle_rpc(
    State(providers): State<Providers>,
    Path((provider_id, bot_ref)): Path<(String, String)>,
    Query(query): Query<BTreeMap<String, String>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let Some((state, bot)) = resolve(&providers, &provider_id, &bot_ref) else { return StatusCode::NOT_FOUND.into_response() };
    let Some(owner) = authenticate(state, &headers) else {
        return (StatusCode::UNAUTHORIZED, [(axum::http::header::WWW_AUTHENTICATE, "Bearer")]).into_response();
    };
    let request: JsonRpcRequest = match serde_json::from_slice(&body) {
        Ok(request) => request,
        Err(error) => return rpc_error(Value::Null, JsonRpcError::new(A2aErrorCode::ParseError, error.to_string())),
    };
    let id = request.id.clone();
    if request.jsonrpc != a2a::JSONRPC_VERSION {
        return rpc_error(id, JsonRpcError::new(A2aErrorCode::InvalidRequest, "jsonrpc must be \"2.0\""));
    }
    let version = headers.get(a2a::A2A_VERSION_HEADER).and_then(|v| v.to_str().ok()).map(str::to_owned)
        .or_else(|| query.get(a2a::A2A_VERSION_HEADER).cloned()).unwrap_or_default();
    if !version.split('.').take(2).eq(a2a::A2A_PROTOCOL_VERSION.split('.')) {
        let shown = if version.is_empty() { "0.3 (no A2A-Version)" } else { version.as_str() };
        return rpc_error(id, JsonRpcError::new(A2aErrorCode::VersionNotSupported, format!("A2A version {shown} is not supported; use 1.0")));
    }
    let ctx = Ctx { state: state.clone(), bot, owner, headers };
    match dispatch(ctx, request).await {
        Ok(Reply::Json(result)) => rpc_ok(id, result),
        Ok(Reply::Stream(events)) => sse_response(id, events),
        Err(error) => rpc_error(id, error),
    }
}

/// The BCS token and configured client tokens are separate owners: a task
/// is visible only to the identity that created it (spec §13.1).
fn authenticate(state: &AppState, headers: &HeaderMap) -> Option<String> {
    let token = headers.get(axum::http::header::AUTHORIZATION)?.to_str().ok()?.strip_prefix("Bearer ")?;
    if state.config.bcs_to_provider_token.as_deref() == Some(token) { return Some("bcs".into()); }
    state.config.a2a.as_ref()?.client_tokens.iter().any(|candidate| candidate == token)
        .then(|| format!("client:{}", &crate::idempotency::fingerprint(&[token])[..16]))
}

enum Reply {
    Json(Value),
    Stream(std::pin::Pin<Box<dyn Stream<Item = (u64, StreamResponse)> + Send>>),
}

fn invalid_params(message: impl Into<String>) -> JsonRpcError { JsonRpcError::new(A2aErrorCode::InvalidParams, message) }
fn unsupported(message: impl Into<String>) -> JsonRpcError { JsonRpcError::new(A2aErrorCode::UnsupportedOperation, message) }
fn task_not_found(id: &str) -> JsonRpcError { JsonRpcError::new(A2aErrorCode::TaskNotFound, format!("task {id} not found")) }

fn params<T: serde::de::DeserializeOwned>(value: Value) -> Result<T, JsonRpcError> {
    serde_json::from_value(value).map_err(|error| invalid_params(error.to_string()))
}

fn to_value(value: impl serde::Serialize) -> Result<Value, JsonRpcError> {
    serde_json::to_value(value).map_err(|error| JsonRpcError::new(A2aErrorCode::InternalError, error.to_string()))
}

async fn dispatch(ctx: Ctx, request: JsonRpcRequest) -> Result<Reply, JsonRpcError> {
    match request.method.as_str() {
        methods::SEND_MESSAGE => send_message(ctx, params(request.params)?, false).await,
        methods::SEND_STREAMING_MESSAGE => send_message(ctx, params(request.params)?, true).await,
        methods::GET_TASK => {
            let req: a2a::GetTaskRequest = params(request.params)?;
            Ok(Reply::Json(to_value(with_history_length(load_task(&ctx, &req.id).await?, req.history_length))?))
        }
        methods::CANCEL_TASK => cancel_task(ctx, params(request.params)?).await,
        methods::SUBSCRIBE_TO_TASK => {
            let req: a2a::SubscribeToTaskRequest = params(request.params)?;
            let task = live_task(&ctx, &req.id).await?;
            let cursor = last_event_id(&ctx.headers).map(Cursor::After);
            if task.state().is_terminal() && cursor.is_none() {
                return Err(unsupported("task is in a terminal state; use GetTask"));
            }
            Ok(Reply::Stream(Box::pin(task.subscribe(cursor.unwrap_or(Cursor::Snapshot)))))
        }
        methods::CREATE_PUSH_CONFIG => {
            let mut config: TaskPushNotificationConfig = params(request.params)?;
            let task_id = config.task_id.clone().ok_or_else(|| invalid_params("taskId required"))?;
            let task = live_task(&ctx, &task_id).await?;
            if task.state().is_terminal() { return Err(unsupported("task is in a terminal state")); }
            config.id.get_or_insert_with(|| uuid::Uuid::new_v4().to_string());
            start_push(&ctx, &task, config.clone(), Cursor::After(task.latest_seq()))?;
            Ok(Reply::Json(to_value(config)?))
        }
        methods::GET_PUSH_CONFIG => {
            let req: PushConfigRequest = params(request.params)?;
            let task = live_task(&ctx, &req.task_id).await?;
            let id = req.id.ok_or_else(|| invalid_params("id required"))?;
            let config = task.push_config(&id).ok_or_else(|| task_not_found(&id))?;
            Ok(Reply::Json(to_value(config)?))
        }
        methods::LIST_PUSH_CONFIGS => {
            let req: PushConfigRequest = params(request.params)?;
            let task = live_task(&ctx, &req.task_id).await?;
            Ok(Reply::Json(to_value(a2a::ListTaskPushNotificationConfigsResponse { configs: task.push_configs(), next_page_token: None })?))
        }
        methods::DELETE_PUSH_CONFIG => {
            let req: PushConfigRequest = params(request.params)?;
            let task = live_task(&ctx, &req.task_id).await?;
            task.remove_push(&req.id.ok_or_else(|| invalid_params("id required"))?);
            Ok(Reply::Json(json!({})))
        }
        methods::LIST_TASKS => Err(unsupported("ListTasks is not supported")),
        methods::GET_EXTENDED_AGENT_CARD => Err(JsonRpcError::new(A2aErrorCode::ExtendedAgentCardNotConfigured, "no extended agent card")),
        other => Err(JsonRpcError::new(A2aErrorCode::MethodNotFound, format!("method {other} not found"))),
    }
}

fn last_event_id(headers: &HeaderMap) -> Option<u64> {
    headers.get("last-event-id")?.to_str().ok()?.trim().parse().ok()
}

fn requested_extension(headers: &HeaderMap, uri: &str) -> bool {
    headers.get_all(a2a::A2A_EXTENSIONS_HEADER).iter().filter_map(|v| v.to_str().ok())
        .flat_map(|v| v.split(',')).any(|candidate| candidate.trim() == uri)
}

/// An in-memory task owned by the caller.
async fn live_task(ctx: &Ctx, id: &str) -> Result<Arc<A2aTask>, JsonRpcError> {
    match ctx.state.a2a.get(id) {
        Some(task) if task.owner == ctx.owner && task.bot == ctx.bot.provider_bot_ref => Ok(task),
        Some(_) => Err(task_not_found(id)),
        None => match load_task(ctx, id).await {
            Ok(_) => Err(unsupported("task is in a terminal state; use GetTask")),
            Err(error) => Err(error),
        },
    }
}

/// Live snapshot, else the persisted one (after memory grace or a restart).
async fn load_task(ctx: &Ctx, id: &str) -> Result<Task, JsonRpcError> {
    if let Some(task) = ctx.state.a2a.get(id) {
        return if task.owner == ctx.owner && task.bot == ctx.bot.provider_bot_ref { Ok(task.snapshot()) } else { Err(task_not_found(id)) };
    }
    let row = ctx.state.sessions.load_a2a_task(id).await
        .map_err(|error| JsonRpcError::new(A2aErrorCode::InternalError, error.to_string()))?;
    match row {
        Some(row) if row.owner == ctx.owner && row.bot == ctx.bot.provider_bot_ref => serde_json::from_str(&row.snapshot)
            .map_err(|error| JsonRpcError::new(A2aErrorCode::InternalError, error.to_string())),
        _ => Err(task_not_found(id)),
    }
}

fn map_bridge_error(error: crate::error::BridgeError) -> JsonRpcError {
    match error.status {
        429 => unsupported("this contextId already has a running task; wait for it or use another contextId"),
        409 => invalid_params("messageId was already used with a different message"),
        400 | 404 => invalid_params(error.message),
        _ => JsonRpcError::new(A2aErrorCode::InternalError, error.message),
    }
}

/// Flatten A2A parts into the BCN message the engines consume.
fn bcn_message(message: &Message) -> Value {
    let lines: Vec<String> = message.parts.iter().map(|part| {
        let name = part.filename.as_deref().unwrap_or("attachment");
        match &part.content {
            PartContent::Text(text) => text.clone(),
            PartContent::Url(url) => format!("[{name}] {url}"),
            PartContent::Data(value) => value.to_string(),
            PartContent::Raw(raw) => format!("[{name}: {} base64 bytes omitted, {}]", raw.len(),
                part.media_type.as_deref().unwrap_or("application/octet-stream")),
        }
    }).collect();
    json!({"role": "user", "content": [{"type": "text", "text": lines.join("\n")}], "timestamp": bcs_protocol::now_ms()})
}

fn to_bot(ctx: &Ctx) -> ToBot {
    ToBot { provider_id: ctx.state.config.provider_id.clone(), provider_bot_ref: ctx.bot.provider_bot_ref.clone() }
}

fn session_for(context_id: &str) -> String { format!("a2a:{context_id}") }

async fn send_message(ctx: Ctx, req: SendMessageRequest, streaming: bool) -> Result<Reply, JsonRpcError> {
    let message = req.message;
    if message.role != Role::User { return Err(invalid_params("message.role must be ROLE_USER")); }
    if message.parts.is_empty() { return Err(invalid_params("message.parts must not be empty")); }
    if message.message_id.trim().is_empty() { return Err(invalid_params("messageId required")); }
    let configuration = req.configuration.unwrap_or_default();
    if let Some(task_id) = message.task_id.clone() {
        return continue_task(ctx, &task_id, message, &configuration, streaming).await;
    }
    if requested_extension(&ctx.headers, a2a::extensions::INJECT) {
        return inject(ctx, message).await;
    }
    if let Some(task) = ctx.state.a2a.by_message(&ctx.owner, &ctx.bot.provider_bot_ref, &message.message_id) {
        return respond(&task, &configuration, streaming, Cursor::Start).await;
    }

    let context_id = message.context_id.clone().unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    let task_id = uuid::Uuid::new_v4().to_string();
    let session_id = session_for(&context_id);
    let timeout_ms = ctx.state.config.a2a.as_ref().map_or(3_600_000, |a2a| a2a.task_timeout_ms);
    let push = configuration.task_push_notification_config.clone();
    if let Some(config) = &push && !push::url_allowed(&ctx.state.config, &config.url) {
        return Err(invalid_params("push notification URL is not allowed by this agent"));
    }
    let downstream = DownstreamRequest {
        id: task_id.clone(), method: "chat.send".into(), to_bot: to_bot(&ctx), session_id: Some(session_id.clone()),
        message: Some(bcn_message(&message)), from: Some(json!({"kind": "a2a", "name": ctx.owner})),
        timeout_ms: Some(timeout_ms), params: None,
    };
    let (handle, stream) = match start_run(&ctx, downstream).await? {
        RuntimeReply::Run { handle, delivery: RunDelivery::Gateway { stream } } => (handle, stream),
        _ => return Err(JsonRpcError::new(A2aErrorCode::InternalError, "engine run did not start")),
    };
    let mut recorded = message.clone();
    recorded.task_id = Some(task_id.clone());
    recorded.context_id = Some(context_id.clone());
    let projection = Projection::new(&task_id, &context_id, recorded);
    let task = A2aTask::new(task_id, ctx.owner.clone(), ctx.bot.provider_bot_ref.clone(), context_id, session_id, handle, projection);
    ctx.state.a2a.insert(&message.message_id, task.clone());
    task::spawn_projection(ctx.state.clone(), task.clone(), stream);
    if let Some(mut config) = push {
        config.id.get_or_insert_with(|| uuid::Uuid::new_v4().to_string());
        config.task_id = Some(task.id.clone());
        start_push(&ctx, &task, config, Cursor::Start)?;
    }
    respond(&task, &configuration, streaming, Cursor::Start).await
}

/// Start the turn. A client may see the previous task's terminal event just
/// before that run releases its context slot; wait briefly instead of
/// reporting a busy context for a sequential follow-up.
async fn start_run(ctx: &Ctx, downstream: DownstreamRequest) -> Result<RuntimeReply, JsonRpcError> {
    let deadline = tokio::time::Instant::now() + BUSY_WAIT;
    loop {
        let attempt = DownstreamRequest { id: downstream.id.clone(), method: downstream.method.clone(),
            to_bot: to_bot(ctx), session_id: downstream.session_id.clone(), message: downstream.message.clone(),
            from: downstream.from.clone(), timeout_ms: downstream.timeout_ms, params: downstream.params.clone() };
        match runtime::dispatch(ctx.state.clone(), attempt).await {
            Err(error) if error.status == 429 && tokio::time::Instant::now() < deadline => {
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
            result => return result.map_err(map_bridge_error),
        }
    }
}

fn start_push(ctx: &Ctx, task: &Arc<A2aTask>, config: TaskPushNotificationConfig, cursor: Cursor) -> Result<(), JsonRpcError> {
    if !push::url_allowed(&ctx.state.config, &config.url) {
        return Err(invalid_params("push notification URL is not allowed by this agent"));
    }
    let stop = task.add_push(config.clone());
    push::spawn_dispatcher(ctx.state.http.clone(), task.clone(), config, cursor, stop);
    Ok(())
}

async fn respond(task: &Arc<A2aTask>, configuration: &a2a::SendMessageConfiguration, streaming: bool, cursor: Cursor) -> Result<Reply, JsonRpcError> {
    if streaming { return Ok(Reply::Stream(Box::pin(task.subscribe(cursor)))); }
    let snapshot = if configuration.return_immediately { task.snapshot() } else { task.wait_settled(Duration::from_secs(24 * 3600)).await };
    to_value(SendMessageResponse::Task(with_history_length(snapshot, configuration.history_length))).map(Reply::Json)
}

/// A message addressed to a task in INPUT_REQUIRED answers its interaction.
async fn continue_task(ctx: Ctx, task_id: &str, message: Message, configuration: &a2a::SendMessageConfiguration, streaming: bool) -> Result<Reply, JsonRpcError> {
    let task = live_task(&ctx, task_id).await?;
    if task.state() != TaskState::InputRequired {
        return Err(unsupported("task is not waiting for input"));
    }
    let pending = task.pending_interactions();
    let interaction_id = message.metadata.as_ref().and_then(|m| m.get(a2a::meta::INTERACTION_ID)).and_then(Value::as_str).map(str::to_owned)
        .or_else(|| (pending.len() == 1).then(|| pending[0].clone()))
        .ok_or_else(|| invalid_params("metadata.interactionId required when several interactions are pending"))?;
    let (kind, raw) = task.pending_interaction(&interaction_id).ok_or_else(|| invalid_params(format!("interaction {interaction_id} is not pending")))?;
    let mut resolution = interaction_resolution(kind, &raw, &message)?;
    resolution["interactionId"] = json!(interaction_id);
    resolution["idempotencyKey"] = json!(message.message_id);
    let cursor = Cursor::After(task.latest_seq());
    let downstream = DownstreamRequest {
        id: message.message_id.clone(), method: "interaction.resolve".into(), to_bot: to_bot(&ctx),
        session_id: Some(task.session_id.clone()), message: None, from: None, timeout_ms: None, params: Some(resolution),
    };
    match runtime::dispatch(ctx.state.clone(), downstream).await.map_err(map_bridge_error)? {
        RuntimeReply::Json { body, .. } if body.get("ok") == Some(&json!(true)) => {}
        RuntimeReply::Json { body, .. } => return Err(invalid_params(body.get("error").and_then(Value::as_str).unwrap_or("interaction was not accepted").to_owned())),
        RuntimeReply::Run { .. } => return Err(JsonRpcError::new(A2aErrorCode::InternalError, "unexpected run reply")),
    }
    let mut recorded = message;
    recorded.context_id = Some(task.context_id.clone());
    task.update(|projection| { projection.push_history(recorded); Vec::new() });
    if streaming { return Ok(Reply::Stream(Box::pin(task.subscribe(cursor)))); }
    task.wait_for(RESOLVE_WAIT, |task| !task.pending_interactions().contains(&interaction_id) || task.state().is_terminal()).await;
    respond(&task, configuration, false, cursor).await
}

/// Structured answers pass through as a `data` part; plain text is accepted
/// when it is unambiguous (an exact exec decision, or a single question).
fn interaction_resolution(kind: InteractionKind, raw: &Value, message: &Message) -> Result<Value, JsonRpcError> {
    if let Some(data) = message.data().filter(|data| data.get("decision").is_some() || data.get("action").is_some()) {
        return Ok(data.clone());
    }
    let text = message.text().trim().to_owned();
    match kind {
        InteractionKind::Exec | InteractionKind::ModeSwitch => {
            let options: Vec<(&str, &str)> = raw.get("options").and_then(Value::as_array).into_iter().flatten()
                .filter_map(|o| Some((o.get("decision")?.as_str()?, o.get("label").and_then(Value::as_str).unwrap_or("")))).collect();
            options.iter().find(|(decision, label)| decision.eq_ignore_ascii_case(&text) || (!label.is_empty() && label.eq_ignore_ascii_case(&text)))
                .map(|(decision, _)| json!({"decision": decision}))
                .ok_or_else(|| invalid_params(format!("reply with one of: {}", options.iter().map(|(d, _)| *d).collect::<Vec<_>>().join(", "))))
        }
        InteractionKind::AskUser => {
            let questions = raw.get("questions").and_then(Value::as_array).cloned().unwrap_or_default();
            match questions.as_slice() {
                [question] if !text.is_empty() => {
                    let id = question.get("questionId").and_then(Value::as_str).unwrap_or("question_1");
                    Ok(json!({"action": "submit", "answers": {id: {"values": [text]}}}))
                }
                _ => Err(invalid_params("several questions are pending; answer with a data part {\"action\":\"submit\",\"answers\":{...}}")),
            }
        }
    }
}

async fn inject(ctx: Ctx, message: Message) -> Result<Reply, JsonRpcError> {
    let context_id = message.context_id.clone().ok_or_else(|| invalid_params("inject requires contextId"))?;
    let downstream = DownstreamRequest {
        id: message.message_id.clone(), method: "chat.inject".into(), to_bot: to_bot(&ctx), session_id: Some(session_for(&context_id)),
        message: Some(bcn_message(&message)), from: Some(json!({"kind": "a2a", "name": ctx.owner})), timeout_ms: None, params: None,
    };
    runtime::dispatch(ctx.state.clone(), downstream).await.map_err(map_bridge_error)?;
    let mut ack = Message::new(format!("{}-ack", message.message_id), Role::Agent, vec![a2a::Part::text("injected")]);
    ack.context_id = Some(context_id);
    ack.metadata = Some([("injected".to_owned(), json!(true))].into_iter().collect());
    to_value(SendMessageResponse::Message(ack)).map(Reply::Json)
}

async fn cancel_task(ctx: Ctx, req: a2a::CancelTaskRequest) -> Result<Reply, JsonRpcError> {
    let task = live_task(&ctx, &req.id).await.map_err(|error| if error.is(A2aErrorCode::UnsupportedOperation) {
        JsonRpcError::new(A2aErrorCode::TaskNotCancelable, "task is in a terminal state")
    } else { error })?;
    if task.state().is_terminal() { return Err(JsonRpcError::new(A2aErrorCode::TaskNotCancelable, "task is in a terminal state")); }
    let downstream = DownstreamRequest {
        id: format!("cancel-{}", uuid::Uuid::new_v4()), method: "chat.abort".into(), to_bot: to_bot(&ctx),
        session_id: Some(task.session_id.clone()), message: None, from: None, timeout_ms: None,
        params: Some(json!({"run_id": task.id})),
    };
    runtime::dispatch(ctx.state.clone(), downstream).await.map_err(map_bridge_error)?;
    task.wait_for(CANCEL_WAIT, |task| task.state().is_terminal()).await;
    to_value(task.snapshot()).map(Reply::Json)
}

fn rpc_ok(id: Value, result: Value) -> Response {
    Json(JsonRpcResponse { jsonrpc: a2a::JSONRPC_VERSION.into(), id, result: Some(result), error: None }).into_response()
}

fn rpc_error(id: Value, error: JsonRpcError) -> Response {
    Json(JsonRpcResponse::failure(id, error)).into_response()
}

/// JSON-RPC over SSE: every `data:` is a full JSON-RPC response; `id:` is the
/// task event seq so `Last-Event-ID` can resume (replay extension).
fn sse_response(id: Value, events: std::pin::Pin<Box<dyn Stream<Item = (u64, StreamResponse)> + Send>>) -> Response {
    let mut heartbeat = tokio::time::interval(HEARTBEAT);
    heartbeat.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    heartbeat.reset();
    let frames = futures::stream::unfold((events, heartbeat, id), |(mut events, mut heartbeat, id)| async move {
        let frame = tokio::select! {
            biased;
            event = events.next() => {
                let (seq, event) = event?;
                let envelope = JsonRpcResponse { jsonrpc: a2a::JSONRPC_VERSION.into(), id: id.clone(),
                    result: serde_json::to_value(&event).ok(), error: None };
                let data = serde_json::to_string(&envelope).unwrap_or_default();
                format!("id: {seq}\ndata: {data}\n\n")
            }
            _ = heartbeat.tick() => crate::sse::HEARTBEAT.to_owned(),
        };
        Some((Ok::<_, std::io::Error>(Bytes::from(frame)), (events, heartbeat, id)))
    });
    let mut response = Response::new(Body::from_stream(frames));
    response.headers_mut().insert(axum::http::header::CONTENT_TYPE, HeaderValue::from_static("text/event-stream; charset=utf-8"));
    response.headers_mut().insert(axum::http::header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    response
}
