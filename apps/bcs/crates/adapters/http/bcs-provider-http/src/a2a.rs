//! A2A v1.0 downlink: BCS calls an HTTP Provider bot as a standard A2A agent.
//!
//! Selected by the Provider downlink `protocol_version = "a2a/1.0"`. A2A
//! events are translated into BCN stream frames and fed through the same
//! [`crate::drive_sse_frame`] pipeline as Provider 2.0 SSE, so dedupe,
//! deadlines, HITL registration and message flow are shared.
//!
//! Delivery: streaming (`SendStreamingMessage`) when the AgentCard declares
//! it, otherwise `SendMessage{returnImmediately}` + `GetTask` polling. A
//! dropped stream resumes with `SubscribeToTask` + `Last-Event-ID`, then falls
//! back to polling. A request whose outcome is unknown is never re-sent.
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use bcs_a2a_protocol::{self as a2a, meta, methods, AgentCard, JsonRpcRequest, JsonRpcResponse, Message, Part, Role,
    SendMessageConfiguration, SendMessageRequest, SendMessageResponse, StreamResponse, Task, TaskState};
use bcs_domain::BotDeliveryTarget;
use bcs_service_api::{BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand, BotDeliveryResult,
    InteractionProviderAck, InteractionProviderCommand, ProviderRunTransport, ServiceError, ServiceResult};
use futures::StreamExt;
use serde_json::{json, Map, Value};
use tracing::{info, warn};

use crate::sse::SeqDedup;
use crate::{HttpProviderTransport, LagTracker, ProviderClientPolicy, SseInteractionContext};

const CARD_TTL: Duration = Duration::from_secs(300);
const MAX_RESUMES: u32 = 3;
const POLL_MIN: Duration = Duration::from_secs(1);
const POLL_MAX: Duration = Duration::from_secs(10);
/// Synthesized interaction ids for agents without the Avernet progress extension.
const SYNTHETIC_INTERACTION_PREFIX: &str = "a2a-input-";
const SYNTHETIC_QUESTION_ID: &str = "answer";

/// Per-transport A2A state: AgentCard cache and live run bindings.
#[derive(Default)]
pub(crate) struct A2aDownlink {
    cards: Mutex<HashMap<String, (Instant, Arc<AgentCard>)>>,
    runs: Mutex<HashMap<String, Arc<RunBinding>>>,
    /// `(bot_id, bcs session)` → agent-assigned contextId, for multi-turn.
    contexts: Mutex<HashMap<(String, String), String>>,
}

struct RunBinding {
    bot_id: String,
    session_id: String,
    rpc_url: String,
    task_id: Mutex<Option<String>>,
    context_id: Mutex<String>,
}

impl RunBinding {
    fn task_id(&self) -> Option<String> { self.task_id.lock().unwrap_or_else(|p| p.into_inner()).clone() }
    fn context_id(&self) -> String { self.context_id.lock().unwrap_or_else(|p| p.into_inner()).clone() }
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> { mutex.lock().unwrap_or_else(|p| p.into_inner()) }

fn token(target: &BotDeliveryTarget) -> Option<&str> {
    match target { BotDeliveryTarget::HttpProvider { bcs_to_provider_token, .. } => Some(bcs_to_provider_token.expose_secret()), _ => None }
}

fn not_sent(code: &'static str, retryable: bool) -> ServiceError { ServiceError::DeliveryNotSent { code, retryable } }

fn rejected(bot_id: String, message: impl Into<String>) -> BotDeliveryResult {
    BotDeliveryResult { target_bot_id: bot_id, delivered: false, error: Some(ServiceError::InternalError(message.into())) }
}

/// Card URL for a configured agent base URL (or a direct card URL).
pub(crate) fn card_url(base: &str) -> String {
    if base.ends_with(".json") { return base.to_owned(); }
    format!("{}/{}", base.trim_end_matches('/'), a2a::AGENT_CARD_WELL_KNOWN_PATH)
}

impl HttpProviderTransport {
    /// One guarded A2A HTTP exchange (URL policy, DNS pinning, no redirects).
    async fn a2a_http(&self, target: &BotDeliveryTarget, url: &str, body: Option<&Value>, accept_sse: bool,
        extra: &[(&str, String)], bypass: &[(String, String)]) -> ServiceResult<reqwest::Response> {
        let guarded = self.url_guard.resolve_request_http_url(url).await.map_err(|error| {
            warn!(url = %crate::webhook_url_for_log(url), reason = %error, "a2a downlink: URL blocked by outbound policy");
            not_sent("provider_url_preflight_failed", matches!(error, bcs_route_security::OutboundUrlError::ResolveFailed(_)))
        })?;
        let policy = ProviderClientPolicy::for_request(accept_sse);
        let pinned = crate::provider_client_for_url(&guarded, policy).map_err(|_| not_sent("provider_client_build_failed", false))?;
        let fallback = if accept_sse { &self.sse_client } else { &self.client };
        let client = pinned.as_ref().unwrap_or(fallback);
        let mut request = match body {
            Some(body) => client.post(guarded.as_str()).json(body),
            None => client.get(guarded.as_str()),
        };
        request = request.header(a2a::A2A_VERSION_HEADER, a2a::A2A_PROTOCOL_VERSION)
            .header("Accept", if accept_sse { "text/event-stream, application/json" } else { "application/json" });
        if let Some(token) = token(target) { request = request.bearer_auth(token); }
        for (name, value) in extra { request = request.header(*name, value); }
        for (name, value) in bypass {
            if crate::is_safe_provider_bypass_header(name) { request = request.header(name.as_str(), value.as_str()); }
        }
        let send = request.send();
        let response = match policy.response_header_timeout {
            Some(timeout) => tokio::time::timeout(timeout, send).await
                .map_err(|_| ServiceError::InternalError(format!("a2a response header timeout after {}ms", timeout.as_millis())))?,
            None => send.await,
        };
        response.map_err(|error| ServiceError::InternalError(format!("a2a request failed: {error}")))
    }

    async fn a2a_rpc(&self, target: &BotDeliveryTarget, url: &str, method: &str, params: impl serde::Serialize,
        accept_sse: bool, extra: &[(&str, String)], bypass: &[(String, String)]) -> ServiceResult<reqwest::Response> {
        let request = JsonRpcRequest::new(uuid::Uuid::new_v4().to_string(), method, params)
            .map_err(|_| not_sent("provider_request_build_failed", false))?;
        let body = serde_json::to_value(&request).map_err(|_| not_sent("provider_request_build_failed", false))?;
        self.a2a_http(target, url, Some(&body), accept_sse, extra, bypass).await
    }

    /// Non-streaming JSON-RPC call decoded into `T`.
    async fn a2a_call<T: serde::de::DeserializeOwned>(&self, target: &BotDeliveryTarget, url: &str, method: &str,
        params: impl serde::Serialize, extra: &[(&str, String)], bypass: &[(String, String)]) -> ServiceResult<Result<T, a2a::JsonRpcError>> {
        let response = self.a2a_rpc(target, url, method, params, false, extra, bypass).await?;
        let status = response.status();
        let body = response.bytes().await.map_err(|error| ServiceError::InternalError(format!("read a2a response: {error}")))?;
        let envelope: JsonRpcResponse = serde_json::from_slice(&body)
            .map_err(|error| ServiceError::InternalError(format!("a2a {method} returned HTTP {status} without JSON-RPC body: {error}")))?;
        Ok(envelope.into_result())
    }

    pub(crate) async fn a2a_card(&self, target: &BotDeliveryTarget, bypass: &[(String, String)]) -> ServiceResult<Arc<AgentCard>> {
        let BotDeliveryTarget::HttpProvider { webhook_url, .. } = target else {
            return Err(ServiceError::InternalError("not http provider target".into()));
        };
        if let Some((at, card)) = lock(&self.a2a.cards).get(webhook_url) && at.elapsed() < CARD_TTL {
            return Ok(card.clone());
        }
        let response = self.a2a_http(target, &card_url(webhook_url), None, false, &[], bypass).await?;
        if !response.status().is_success() {
            return Err(not_sent("a2a_agent_card_unavailable", true));
        }
        let card: AgentCard = response.json().await.map_err(|error| {
            warn!(%error, "a2a downlink: invalid AgentCard");
            not_sent("a2a_agent_card_invalid", false)
        })?;
        let card = Arc::new(card);
        lock(&self.a2a.cards).insert(webhook_url.clone(), (Instant::now(), card.clone()));
        Ok(card)
    }

    pub(crate) async fn deliver_a2a(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let bot_id = cmd.target_bot_id().to_string();
        let mut body = crate::provider_request_from_frame(&cmd.target, &cmd.frame, self.chat_run_timeout_ms)
            .map_err(|_| not_sent("provider_request_invalid", false))?;
        if body.method == "chat.send" { body.id = cmd.run_id.clone(); }
        let card = self.a2a_card(&cmd.target, &cmd.provider_bypass_headers).await?;
        let Some(interface) = card.jsonrpc_interface() else {
            return Ok(rejected(bot_id, "A2A agent exposes no JSONRPC 1.0 interface"));
        };
        let rpc_url = interface.url.clone();
        let session_key = (bot_id.clone(), body.session_id.clone());
        let context_id = lock(&self.a2a.contexts).get(&session_key).cloned().unwrap_or_else(|| body.session_id.clone());
        let mut message = Message::new(if body.method == "chat.send" { cmd.run_id.clone() } else { body.id.clone() },
            Role::User, message_parts(&body));
        message.context_id = Some(context_id.clone());
        message.metadata = Some(bcs_metadata(&body));
        match body.method.as_str() {
            "chat.send" => {}
            "chat.inject" if card.supports_extension(a2a::extensions::INJECT) => {
                let request = SendMessageRequest { tenant: None, message, configuration: None, metadata: None };
                let ext = [(a2a::A2A_EXTENSIONS_HEADER, a2a::extensions::INJECT.to_owned())];
                return Ok(match self.a2a_call::<SendMessageResponse>(&cmd.target, &rpc_url, methods::SEND_MESSAGE, request, &ext, &cmd.provider_bypass_headers).await? {
                    Ok(_) => BotDeliveryResult { target_bot_id: bot_id, delivered: true, error: None },
                    Err(error) => rejected(bot_id, error.to_string()),
                });
            }
            other => return Ok(rejected(bot_id, format!("{other} is not supported by this A2A agent"))),
        }
        if cmd.run_id.is_empty() { return Err(not_sent("provider_canonical_run_id_required", false)); }
        let run_id = cmd.run_id.clone();
        let run_context = lock_rw(&self.bot_run_context);
        if let Some(context) = run_context.as_ref()
            && !context.begin_provider_transport(&run_id, bcs_protocol::now_ms().saturating_add(body.timeout_ms)).await {
            return Err(ServiceError::InvalidOperation { message: "provider run transport is already registered".into(), request_id: Some(run_id) });
        }
        let clear = || async { if let Some(context) = run_context.as_ref() { context.clear_provider_transport(&run_id).await; } };
        let streaming = card.capabilities.streaming == Some(true);
        let configuration = (!streaming).then(|| SendMessageConfiguration { return_immediately: true, ..Default::default() });
        let request = SendMessageRequest { tenant: None, message, configuration, metadata: None };
        let method = if streaming { methods::SEND_STREAMING_MESSAGE } else { methods::SEND_MESSAGE };
        info!(target_bot_id = %bot_id, run_id = %run_id, %method, rpc_url = %crate::webhook_url_for_log(&rpc_url), "a2a downlink: send");
        let response = match self.a2a_rpc(&cmd.target, &rpc_url, method, &request, streaming, &[], &cmd.provider_bypass_headers).await {
            Ok(response) => response,
            Err(error) => { clear().await; return Err(error); }
        };
        if !response.status().is_success() {
            clear().await;
            return Ok(crate::provider_delivery_rejection(bot_id, response.status()));
        }
        let is_sse = response.headers().get(reqwest::header::CONTENT_TYPE).and_then(|v| v.to_str().ok())
            .is_some_and(|v| v.starts_with("text/event-stream"));
        let source = if is_sse {
            Source::Stream(response)
        } else {
            let decoded: Result<JsonRpcResponse, _> = response.json().await;
            match decoded.map_err(|e| a2a::JsonRpcError::new(a2a::A2aErrorCode::InvalidAgentResponse, e.to_string())).and_then(JsonRpcResponse::into_result::<SendMessageResponse>) {
                Ok(SendMessageResponse::Task(task)) => Source::Event(StreamResponse::Task(task)),
                Ok(SendMessageResponse::Message(message)) => Source::Event(StreamResponse::Message(message)),
                Err(error) => {
                    clear().await;
                    warn!(target_bot_id = %bot_id, run_id = %run_id, %error, "a2a downlink: agent rejected message");
                    return Ok(rejected(bot_id, error.to_string()));
                }
            }
        };
        let transport = if is_sse { ProviderRunTransport::Sse } else { ProviderRunTransport::Callback };
        if let Some(context) = run_context.as_ref() && !context.bind_provider_transport(&run_id, transport).await {
            return Err(ServiceError::InvalidOperation { message: "provider run is already bound to another transport".into(), request_id: Some(run_id) });
        }
        let (Some(flow), Some(ctx)) = (lock_rw(&self.event_ingest), run_context) else {
            return Err(ServiceError::InternalError("a2a ingest deps not wired".into()));
        };
        let binding = Arc::new(RunBinding { bot_id: bot_id.clone(), session_id: body.session_id.clone(), rpc_url,
            task_id: Mutex::new(None), context_id: Mutex::new(context_id) });
        lock(&self.a2a.runs).insert(run_id.clone(), binding.clone());
        let interactions = lock_rw(&self.interactions).and_then(|service| service.upgrade()).map(|service| SseInteractionContext {
            service, provider_target: cmd.target.clone(), provider_bypass_headers: cmd.provider_bypass_headers.clone() });
        let driver = Driver { transport: self.clone_handle(), target: cmd.target.clone(), bypass: cmd.provider_bypass_headers.clone(),
            run_id, bot_id: bot_id.clone(), binding, streaming, replay: card.supports_extension(a2a::extensions::REPLAY) };
        tokio::spawn(driver.run(source, flow, ctx, interactions));
        Ok(BotDeliveryResult { target_bot_id: bot_id, delivered: true, error: None })
    }

    /// Bot/session-scoped abort: cancel every live A2A task of that session.
    pub(crate) async fn abort_a2a(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        let bot_id = cmd.target_bot_id().to_string();
        let targets: Vec<(String, Arc<RunBinding>)> = lock(&self.a2a.runs).iter()
            .filter(|(_, b)| b.bot_id == bot_id && b.session_id == cmd.session_id).map(|(r, b)| (r.clone(), b.clone())).collect();
        let mut aborted = Vec::new();
        for (run_id, binding) in targets {
            let Some(task_id) = binding.task_id() else { continue };
            match self.a2a_call::<Task>(&cmd.target, &binding.rpc_url, methods::CANCEL_TASK, a2a::CancelTaskRequest { tenant: None, id: task_id, metadata: None }, &[], &cmd.provider_bypass_headers).await? {
                Ok(_) => aborted.push(run_id),
                Err(error) if error.is(a2a::A2aErrorCode::TaskNotCancelable) => {}
                Err(error) => return Err(ServiceError::InvalidOperation { message: error.to_string(), request_id: Some(cmd.command_id) }),
            }
        }
        Ok(BotAbortDeliveryResult { target_bot_id: bot_id, aborted_run_ids: aborted })
    }

    /// HITL answer: a follow-up message on the task waiting for input.
    pub(crate) async fn resolve_a2a_interaction(&self, command: InteractionProviderCommand) -> ServiceResult<InteractionProviderAck> {
        let binding = lock(&self.a2a.runs).get(&command.bcs_run_id).cloned();
        let Some((binding, task_id)) = binding.and_then(|b| b.task_id().map(|t| (b, t))) else {
            return Ok(InteractionProviderAck { ok: false, retryable: Some(false), error: Some("A2A task is no longer active".into()) });
        };
        let mut message = Message::new(command.idempotency_key.clone(), Role::User, resolution_parts(&command.interaction_id, &command.resolution));
        message.task_id = Some(task_id);
        message.context_id = Some(binding.context_id());
        if !command.interaction_id.starts_with(SYNTHETIC_INTERACTION_PREFIX) {
            message.metadata = Some([(meta::INTERACTION_ID.to_owned(), json!(command.interaction_id))].into_iter().collect());
        }
        let request = SendMessageRequest { tenant: None, message,
            configuration: Some(SendMessageConfiguration { return_immediately: true, history_length: Some(0), ..Default::default() }), metadata: None };
        Ok(match self.a2a_call::<SendMessageResponse>(&command.target, &binding.rpc_url, methods::SEND_MESSAGE, request, &[], &command.provider_bypass_headers).await? {
            Ok(_) => InteractionProviderAck { ok: true, retryable: None, error: None },
            Err(error) => InteractionProviderAck { ok: false, retryable: Some(false), error: Some(error.message) },
        })
    }
}

fn lock_rw<T: Clone>(lock: &std::sync::RwLock<Option<T>>) -> Option<T> { lock.read().unwrap_or_else(|p| p.into_inner()).clone() }

/// BCN message text and attachments as A2A parts.
fn message_parts(body: &bcs_protocol::ProviderWebhookRequest) -> Vec<Part> {
    let text = body.message.as_ref().map(bcn_text).unwrap_or_default();
    let mut parts = vec![Part::text(text)];
    for attachment in &body.attachments {
        parts.push(Part::url(attachment.url.clone(), Some(attachment.file_name.clone()), attachment.mime_type.clone()));
    }
    parts
}

fn bcn_text(message: &Value) -> String {
    match message.get("content") {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Array(blocks)) => blocks.iter().filter_map(|b| b.get("text").and_then(Value::as_str)).collect::<Vec<_>>().join("\n"),
        _ => message.get("text").and_then(Value::as_str).unwrap_or_default().to_owned(),
    }
}

fn bcs_metadata(body: &bcs_protocol::ProviderWebhookRequest) -> Map<String, Value> {
    let mut bcs = json!({"sessionId": body.session_id, "groupId": body.bcn_group_id});
    if let Some(from) = &body.from { bcs["from"] = json!({"kind": from.kind, "name": from.name}); }
    [("bcs".to_owned(), bcs)].into_iter().collect()
}

/// Structured resolution as a `data` part; synthesized questions answer with text.
fn resolution_parts(interaction_id: &str, resolution: &Value) -> Vec<Part> {
    if interaction_id.starts_with(SYNTHETIC_INTERACTION_PREFIX) {
        let answer = resolution.pointer(&format!("/answers/{SYNTHETIC_QUESTION_ID}"));
        let values: Vec<&str> = ["values", "customValues"].iter()
            .flat_map(|key| answer.and_then(|a| a.get(*key)).and_then(Value::as_array).into_iter().flatten())
            .filter_map(Value::as_str).collect();
        let text = if values.is_empty() {
            resolution.get("action").and_then(Value::as_str).unwrap_or("cancel").to_owned()
        } else { values.join("\n") };
        return vec![Part::text(text)];
    }
    vec![Part::data(resolution.clone())]
}

enum Source {
    Stream(reqwest::Response),
    Event(StreamResponse),
}

/// Owns one run's A2A lifecycle until a terminal frame is ingested.
struct Driver {
    transport: TransportHandle,
    target: BotDeliveryTarget,
    bypass: Vec<(String, String)>,
    run_id: String,
    bot_id: String,
    binding: Arc<RunBinding>,
    streaming: bool,
    replay: bool,
}

/// `HttpProviderTransport` is not `Clone`; drivers share its clients and state.
pub(crate) type TransportHandle = Arc<HttpProviderTransport>;

impl HttpProviderTransport {
    fn clone_handle(&self) -> TransportHandle {
        Arc::new(HttpProviderTransport {
            client: self.client.clone(),
            sse_client: self.sse_client.clone(),
            url_guard: self.url_guard.clone(),
            chat_run_timeout_ms: self.chat_run_timeout_ms,
            event_ingest: std::sync::RwLock::new(lock_rw(&self.event_ingest)),
            bot_run_context: std::sync::RwLock::new(lock_rw(&self.bot_run_context)),
            interactions: std::sync::RwLock::new(lock_rw(&self.interactions)),
            a2a: self.a2a.clone(),
        })
    }
}

struct Sink<'a> {
    run_id: &'a str,
    group_id: String,
    bot_id: &'a str,
    session_id: Option<String>,
    deadline_ms: u64,
    dedup: SeqDedup,
    lag: LagTracker,
    flow: &'a Arc<dyn bcs_service_api::ProviderEventIngestService>,
    interactions: Option<&'a SseInteractionContext>,
}

impl Sink<'_> {
    /// Feed translated BCN frames; true once a terminal frame was ingested.
    async fn feed(&mut self, frames: Vec<String>) -> bool {
        for frame in frames {
            if crate::drive_sse_frame(&frame, self.run_id, &self.group_id, self.bot_id, &self.session_id, self.deadline_ms,
                &mut self.dedup, &mut self.lag, self.flow, self.interactions).await == Some(true) {
                return true;
            }
        }
        false
    }
}

impl Driver {
    async fn run(self, source: Source, flow: Arc<dyn bcs_service_api::ProviderEventIngestService>,
        ctx: Arc<dyn bcs_service_api::BotRunContextPort>, interactions: Option<SseInteractionContext>) {
        let Some(run_ctx) = crate::resolve_run_context(&ctx, &self.run_id).await else {
            warn!(run_id = %self.run_id, "a2a: run context never became available; closing");
            lock(&self.transport.a2a.runs).remove(&self.run_id);
            return;
        };
        let mut sink = Sink { run_id: &self.run_id, group_id: run_ctx.group_id.clone(), bot_id: &self.bot_id,
            session_id: run_ctx.bcs_session_id.clone(), deadline_ms: run_ctx.deadline_ms, dedup: SeqDedup::default(),
            lag: LagTracker::default(), flow: &flow, interactions: interactions.as_ref() };
        let mut translator = Translator::new(&self.run_id);
        let mut last_event_id = None;
        let mut terminal = match source {
            Source::Stream(response) => self.read_stream(response, &mut translator, &mut sink, &mut last_event_id).await,
            Source::Event(event) => { let frames = self.translate(&mut translator, &event); sink.feed(frames).await }
        };
        let mut resumes = 0;
        let mut poll = POLL_MIN;
        while !terminal && bcs_protocol::now_ms() < sink.deadline_ms {
            let Some(task_id) = translator.task_id.clone() else { break };
            if self.streaming && resumes < MAX_RESUMES {
                resumes += 1;
                tokio::time::sleep(Duration::from_millis(250 * u64::from(resumes))).await;
                let cursor = if self.replay { last_event_id } else { None };
                let extra: Vec<(&str, String)> = cursor.map(|id: u64| ("Last-Event-ID", id.to_string())).into_iter().collect();
                match self.transport.a2a_rpc(&self.target, &self.binding.rpc_url, methods::SUBSCRIBE_TO_TASK,
                    a2a::SubscribeToTaskRequest { tenant: None, id: task_id.clone(), metadata: None }, true, &extra, &self.bypass).await {
                    Ok(response) if response.status().is_success() && response.headers().get(reqwest::header::CONTENT_TYPE)
                        .and_then(|v| v.to_str().ok()).is_some_and(|v| v.starts_with("text/event-stream")) => {
                        info!(run_id = %self.run_id, resumes, ?cursor, "a2a: stream resumed");
                        terminal = self.read_stream(response, &mut translator, &mut sink, &mut last_event_id).await;
                        continue;
                    }
                    Ok(_) | Err(_) => { resumes = MAX_RESUMES; info!(run_id = %self.run_id, "a2a: resume refused; polling"); }
                }
            }
            let before = translator.fingerprint();
            match self.transport.a2a_call::<Task>(&self.target, &self.binding.rpc_url, methods::GET_TASK,
                a2a::GetTaskRequest { tenant: None, id: task_id, history_length: Some(0) }, &[], &self.bypass).await {
                Ok(Ok(task)) => {
                    let frames = self.translate(&mut translator, &StreamResponse::Task(task));
                    terminal = sink.feed(frames).await;
                }
                Ok(Err(error)) if error.is(a2a::A2aErrorCode::TaskNotFound) => {
                    warn!(run_id = %self.run_id, %error, "a2a: task vanished; closing run");
                    break;
                }
                Ok(Err(error)) => warn!(run_id = %self.run_id, %error, "a2a: GetTask failed"),
                Err(error) => warn!(run_id = %self.run_id, %error, "a2a: GetTask transport error"),
            }
            if terminal { break; }
            poll = if translator.fingerprint() == before { (poll.mul_f32(1.5)).min(POLL_MAX) } else { POLL_MIN };
            tokio::time::sleep(poll).await;
        }
        if !terminal {
            warn!(run_id = %self.run_id, "a2a: run closed without terminal; synthesizing error");
            crate::ingest_synthesized_error(&self.run_id, &sink.group_id, &self.bot_id, &sink.session_id, &flow).await;
        }
        ctx.mark_terminal(&self.run_id).await;
        ctx.mark_provider_transport_terminal(&self.run_id).await;
        if let Some(interactions) = &interactions
            && let Err(error) = interactions.service.invalidate_run(&self.run_id, "run_terminal", bcs_protocol::now_ms()).await {
            warn!(run_id = %self.run_id, %error, "failed to invalidate run interactions");
        }
        lock(&self.transport.a2a.runs).remove(&self.run_id);
    }

    fn translate(&self, translator: &mut Translator, event: &StreamResponse) -> Vec<String> {
        let frames = translator.translate(event);
        if let Some(task_id) = &translator.task_id { lock(&self.binding.task_id).get_or_insert_with(|| task_id.clone()); }
        if let Some(context_id) = &translator.context_id {
            *lock(&self.binding.context_id) = context_id.clone();
            lock(&self.transport.a2a.contexts).insert((self.bot_id.clone(), self.binding.session_id.clone()), context_id.clone());
        }
        frames
    }

    /// Consume one JSON-RPC SSE response; true once a terminal frame landed.
    async fn read_stream(&self, response: reqwest::Response, translator: &mut Translator, sink: &mut Sink<'_>, last_event_id: &mut Option<u64>) -> bool {
        let mut body = response.bytes_stream();
        let mut buffer: Vec<u8> = Vec::new();
        loop {
            let (wait, _) = crate::sse_next_read_timeout(bcs_protocol::now_ms(), sink.deadline_ms);
            if wait.is_zero() { return false; }
            let chunk = match tokio::time::timeout(wait, body.next()).await {
                Ok(Some(Ok(chunk))) => chunk,
                Ok(Some(Err(error))) => { warn!(run_id = %self.run_id, %error, "a2a: stream read error"); return false; }
                Ok(None) | Err(_) => return false,
            };
            buffer.extend_from_slice(&chunk);
            while let Some((index, separator)) = crate::find_frame_sep(&buffer) {
                let frame: Vec<u8> = buffer.drain(..index + separator).collect();
                let text = String::from_utf8_lossy(&frame);
                let mut id = None;
                let mut data = String::new();
                for line in text.lines() {
                    if let Some(value) = line.strip_prefix("id:") { id = value.trim().parse::<u64>().ok(); }
                    if let Some(value) = line.strip_prefix("data:") { data.push_str(value.trim_start()); }
                }
                if data.is_empty() { continue; }
                if id.is_some() { *last_event_id = id; }
                let frames = match serde_json::from_str::<JsonRpcResponse>(&data).map_err(|e| a2a::JsonRpcError::new(a2a::A2aErrorCode::InvalidAgentResponse, e.to_string()))
                    .and_then(JsonRpcResponse::into_result::<StreamResponse>) {
                    Ok(event) => self.translate(translator, &event),
                    Err(error) => translator.fail(&error.message, "a2a_error"),
                };
                if sink.feed(frames).await { return true; }
            }
            if buffer.len() > crate::SSE_MAX_FRAME_BYTES { warn!(run_id = %self.run_id, "a2a: oversized frame"); return false; }
        }
    }
}

/// Pure A2A → BCN translation, deterministic for tests.
pub(crate) struct Translator {
    run_id: String,
    seq: u64,
    artifacts: Vec<(String, String)>,
    open: HashMap<String, String>,
    requested: HashSet<String>,
    status_text: String,
    state: Option<TaskState>,
    done: bool,
    pub task_id: Option<String>,
    pub context_id: Option<String>,
}

impl Translator {
    pub fn new(run_id: &str) -> Self {
        Self { run_id: run_id.into(), seq: 0, artifacts: Vec::new(), open: HashMap::new(), requested: HashSet::new(),
            status_text: String::new(), state: None, done: false, task_id: None, context_id: None }
    }

    /// Changes whenever visible progress happens (poll backoff reset).
    fn fingerprint(&self) -> (u64, Option<TaskState>) { (self.seq, self.state) }

    fn frame(&mut self, event: &str, mut data: Map<String, Value>) -> String {
        self.seq += 1;
        data.insert("runId".into(), json!(self.run_id));
        data.insert("seq".into(), json!(self.seq));
        data.insert("ts".into(), json!(bcs_protocol::now_ms()));
        format!("event: {event}\nid: {}\ndata: {}\n\n", self.seq, Value::Object(data))
    }

    fn chat(&mut self, fields: Value) -> String {
        let data = fields.as_object().cloned().unwrap_or_default();
        self.frame("chat", data)
    }

    fn delta(&mut self, text: &str) -> Vec<String> {
        if text.is_empty() { return Vec::new(); }
        vec![self.chat(json!({"state": "delta", "deltaText": text}))]
    }

    fn result_text(&self) -> String {
        let artifacts: Vec<&str> = self.artifacts.iter().map(|(_, text)| text.as_str()).filter(|t| !t.is_empty()).collect();
        if artifacts.is_empty() { self.status_text.clone() } else { artifacts.join("\n") }
    }

    pub fn fail(&mut self, message: &str, kind: &str) -> Vec<String> {
        if self.done { return Vec::new(); }
        self.done = true;
        vec![self.chat(json!({"state": "error", "errorMessage": message, "errorKind": kind}))]
    }

    fn finish(&mut self, state: TaskState, metadata: Option<&Map<String, Value>>) -> Vec<String> {
        if self.done { return Vec::new(); }
        let text_of = |key: &str| metadata.and_then(|m| m.get(key)).and_then(Value::as_str).map(str::to_owned);
        let frame = match state {
            TaskState::Completed => {
                let message = json!({"role": "assistant", "content": [{"type": "text", "text": self.result_text()}], "timestamp": bcs_protocol::now_ms()});
                self.chat(json!({"state": "final", "message": message, "stopReason": "completed"}))
            }
            TaskState::Canceled => {
                let reason = text_of("stopReason").unwrap_or_else(|| "user_cancelled".into());
                self.chat(json!({"state": "aborted", "stopReason": reason}))
            }
            TaskState::AuthRequired => self.chat(json!({"state": "error", "errorMessage": "A2A agent requires authorization", "errorKind": "a2a_auth_required"})),
            _ => {
                let message = text_of(meta::ERROR_MESSAGE).filter(|m| !m.is_empty())
                    .unwrap_or_else(|| if self.status_text.is_empty() { "A2A task failed".into() } else { self.status_text.clone() });
                let kind = text_of(meta::ERROR_CODE).unwrap_or_else(|| if state == TaskState::Rejected { "a2a_task_rejected".into() } else { "a2a_task_failed".into() });
                self.chat(json!({"state": "error", "errorMessage": message, "errorKind": kind}))
            }
        };
        self.done = true;
        vec![frame]
    }

    fn interaction(&mut self, phase: &str, id: &str, kind: &str, frame: &Value) -> String {
        let mut data = frame.as_object().cloned().unwrap_or_default();
        data.insert("phase".into(), json!(phase));
        data.insert("interactionId".into(), json!(id));
        data.insert("kind".into(), json!(kind));
        self.frame("interaction", data)
    }

    fn request(&mut self, id: &str, kind: &str, frame: &Value) -> Vec<String> {
        if !self.requested.insert(id.to_owned()) { return Vec::new(); }
        self.open.insert(id.to_owned(), kind.to_owned());
        vec![self.interaction("requested", id, kind, frame)]
    }

    fn resolve(&mut self, id: &str, frame: &Value) -> Vec<String> {
        let Some(kind) = self.open.remove(id) else { return Vec::new() };
        vec![self.interaction("resolved", id, &kind, frame)]
    }

    /// Third-party input request without the Avernet extension: a free-text question.
    fn synthesize_input(&mut self, prompt: &str) -> Vec<String> {
        if self.open.keys().any(|id| id.starts_with(SYNTHETIC_INTERACTION_PREFIX)) { return Vec::new(); }
        let id = format!("{SYNTHETIC_INTERACTION_PREFIX}{}", self.seq + 1);
        let question = if prompt.trim().is_empty() { "The agent needs more input" } else { prompt };
        self.request(&id, "ask_user", &json!({"questions": [{"questionId": SYNTHETIC_QUESTION_ID, "question": question}]}))
    }

    fn resolve_synthesized(&mut self) -> Vec<String> {
        let ids: Vec<String> = self.open.keys().filter(|id| id.starts_with(SYNTHETIC_INTERACTION_PREFIX)).cloned().collect();
        ids.iter().flat_map(|id| self.resolve(id, &json!({"action": "submit"}))).collect()
    }

    fn set_artifact(&mut self, id: &str, text: &str, append: bool) -> Vec<String> {
        let index = match self.artifacts.iter().position(|(existing, _)| existing == id) {
            Some(index) => index,
            None => { self.artifacts.push((id.to_owned(), String::new())); self.artifacts.len() - 1 }
        };
        let current = &mut self.artifacts[index].1;
        let delta = if append {
            current.push_str(text);
            text.to_owned()
        } else if let Some(suffix) = text.strip_prefix(current.as_str()) {
            let suffix = suffix.to_owned();
            *current = text.to_owned();
            suffix
        } else {
            // A replacement (e.g. the authoritative final answer): the final
            // frame carries it; no misleading delta.
            *current = text.to_owned();
            String::new()
        };
        self.delta(&delta)
    }

    pub fn translate(&mut self, event: &StreamResponse) -> Vec<String> {
        if self.done { return Vec::new(); }
        match event {
            StreamResponse::Message(message) => {
                self.status_text = message.text();
                if self.context_id.is_none() { self.context_id = message.context_id.clone(); }
                self.finish(TaskState::Completed, message.metadata.as_ref())
            }
            StreamResponse::Task(task) => self.snapshot(task),
            StreamResponse::ArtifactUpdate(update) => {
                self.track_ids(&update.task_id, Some(&update.context_id));
                self.set_artifact(&update.artifact.artifact_id, &update.artifact.text(), update.append)
            }
            StreamResponse::StatusUpdate(update) => {
                self.track_ids(&update.task_id, Some(&update.context_id));
                self.state = Some(update.status.state);
                let metadata = update.metadata.as_ref();
                let kind = metadata.and_then(|m| m.get(meta::KIND)).and_then(Value::as_str);
                let frame = metadata.and_then(|m| m.get(meta::ENGINE_FRAME)).cloned().unwrap_or(Value::Null);
                let interaction_id = metadata.and_then(|m| m.get(meta::INTERACTION_ID)).and_then(Value::as_str).map(str::to_owned);
                let interaction_kind = metadata.and_then(|m| m.get(meta::INTERACTION_KIND)).and_then(Value::as_str).unwrap_or("ask_user").to_owned();
                if let Some(text) = update.status.message.as_ref().map(Message::text).filter(|t| !t.is_empty()) {
                    if !matches!(kind, Some("commentary")) { self.status_text = text; }
                }
                let mut frames = match (kind, interaction_id) {
                    (Some("tool" | "thinking" | "lifecycle"), _) => {
                        let mut data = frame.as_object().cloned().unwrap_or_default();
                        data.insert("stream".into(), json!(kind));
                        vec![self.frame("agent", data)]
                    }
                    (Some("commentary.delta"), _) => {
                        let delta = metadata.and_then(|m| m.get(meta::DELTA)).and_then(Value::as_str).unwrap_or_default().to_owned();
                        self.delta(&delta)
                    }
                    (Some("interaction.requested"), Some(id)) => self.request(&id, &interaction_kind, &frame),
                    (Some("interaction.resolved"), Some(id)) => self.resolve(&id, &frame),
                    (None, _) if update.status.state == TaskState::InputRequired => {
                        let prompt = self.status_text.clone();
                        self.synthesize_input(&prompt)
                    }
                    _ => Vec::new(),
                };
                if update.status.state != TaskState::InputRequired { frames.extend(self.resolve_synthesized()); }
                if update.status.state.is_terminal() || update.status.state == TaskState::AuthRequired {
                    frames.extend(self.finish(update.status.state, metadata));
                }
                frames
            }
        }
    }

    fn track_ids(&mut self, task_id: &str, context_id: Option<&String>) {
        if self.task_id.is_none() && !task_id.is_empty() { self.task_id = Some(task_id.to_owned()); }
        if let Some(context_id) = context_id.filter(|c| !c.is_empty()) { self.context_id = Some(context_id.clone()); }
    }

    /// Diff a full snapshot (initial task, poll result, resume preface).
    fn snapshot(&mut self, task: &Task) -> Vec<String> {
        self.track_ids(&task.id, task.context_id.as_ref());
        self.state = Some(task.status.state);
        if let Some(text) = task.status.message.as_ref().map(Message::text).filter(|t| !t.is_empty()) { self.status_text = text; }
        let mut frames = Vec::new();
        for artifact in &task.artifacts {
            frames.extend(self.set_artifact(&artifact.artifact_id, &artifact.text(), false));
        }
        let pending: Vec<(String, String, Value)> = task.metadata.as_ref().and_then(|m| m.get(meta::PENDING_INTERACTIONS))
            .and_then(Value::as_array).into_iter().flatten().filter_map(|entry| Some((
                entry.get(meta::INTERACTION_ID)?.as_str()?.to_owned(),
                entry.get(meta::INTERACTION_KIND).and_then(Value::as_str).unwrap_or("ask_user").to_owned(),
                entry.get(meta::ENGINE_FRAME).cloned().unwrap_or(Value::Null)))).collect();
        let still_open: HashSet<&str> = pending.iter().map(|(id, _, _)| id.as_str()).collect();
        let gone: Vec<String> = self.open.keys().filter(|id| !id.starts_with(SYNTHETIC_INTERACTION_PREFIX) && !still_open.contains(id.as_str())).cloned().collect();
        for id in gone { frames.extend(self.resolve(&id, &Value::Null)); }
        for (id, kind, frame) in &pending { frames.extend(self.request(id, kind, frame)); }
        // Leaving INPUT_REQUIRED (to work or straight to a terminal state)
        // answers a synthesized question.
        if task.status.state != TaskState::InputRequired { frames.extend(self.resolve_synthesized()); }
        match task.status.state {
            TaskState::InputRequired if pending.is_empty() => { let prompt = self.status_text.clone(); frames.extend(self.synthesize_input(&prompt)); }
            state if state.is_terminal() || state == TaskState::AuthRequired => frames.extend(self.finish(state, task.metadata.as_ref())),
            _ => {}
        }
        frames
    }
}

#[cfg(test)]
#[path = "a2a_tests.rs"]
mod tests;
