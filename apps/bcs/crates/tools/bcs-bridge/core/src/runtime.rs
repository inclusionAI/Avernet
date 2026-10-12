//! Shared request execution; adapters own authentication and wire responses.

use std::{collections::HashMap, sync::Arc, pin::Pin};
use futures::Stream;
use serde::Deserialize;
use serde_json::{json, Value};
use crate::{config::ProviderConfig, error::BridgeError, idempotency::IdemDecision, interaction::ResolveOutcome, run::{RunRegistry, RunHandle, RunEvent}};

#[derive(Debug, Deserialize)]
pub struct ToBot { pub provider_id: String, pub provider_bot_ref: String }

#[derive(Debug, Deserialize)]
pub struct DownstreamRequest {
    pub id: String,
    pub method: String,
    pub to_bot: ToBot,
    pub session_id: Option<String>,
    pub message: Option<Value>,
    pub from: Option<Value>,   // {"kind","name","actor_id"}；inject 前置注入用 name
    pub timeout_ms: Option<u64>,
    pub params: Option<Value>,
}

pub struct AppState {
    pub config: ProviderConfig,
    pub idem: crate::idempotency::IdempotencyLedger,
    pub sessions: crate::session::SessionStore,
    pub runs: RunRegistry,
    pub interactions: crate::interaction::InteractionRegistry,
    pub trace: Option<Arc<crate::engine::trace::TraceStore>>,
    /// Engines built at startup, keyed by `provider_bot_ref`.
    engines: HashMap<String, Arc<dyn crate::engine::Engine>>,
}
impl AppState {
    /// Build state for `config`, creating every bot's engine from `engines`.
    /// Unknown engine ids or invalid engine options fail startup.
    pub fn new(config: ProviderConfig, engines: &crate::engine::EngineRegistry) -> anyhow::Result<Self> {
        let mut built = HashMap::new();
        for bot in &config.bots {
            let engine = engines.build(bot).map_err(|error| anyhow::anyhow!("invalid bot engine configuration: {error}"))?;
            built.insert(bot.provider_bot_ref.clone(), engine);
        }
        let sessions = crate::session::SessionStore::open(&config.state_path, &config.provider_id, &config.bots)?;
        let trace = config.trace_dir.as_deref().and_then(|dir| {
            match crate::engine::trace::TraceStore::open(dir) {
                Ok(trace) => Some(trace),
                Err(error) => {
                    tracing::warn!(
                        target: "bcs_bridge_core::trace",
                        path = %dir.display(),
                        error = %error,
                        "failed to open bridge trace directory; tracing disabled"
                    );
                    None
                }
            }
        });
        Ok(Self {
            config,
            idem: crate::idempotency::IdempotencyLedger::new(),
            sessions,
            runs: RunRegistry::new(),
            interactions: crate::interaction::InteractionRegistry::new(),
            trace,
            engines: built,
        })
    }

    /// The engine configured for a bot of this provider.
    pub fn engine(&self, provider_bot_ref: &str) -> Option<Arc<dyn crate::engine::Engine>> {
        self.engines.get(provider_bot_ref).cloned()
    }
}

pub enum RuntimeReply {
    Json { status: u16, body: Value },
    Run { handle: RunHandle, stream: Pin<Box<dyn Stream<Item = RunEvent> + Send>> },
}

fn json_reply(body: Value) -> RuntimeReply { RuntimeReply::Json { status: 200, body } }

pub async fn dispatch(
    state: Arc<AppState>,
    req: DownstreamRequest,
) -> Result<RuntimeReply, BridgeError> {
    // 2. provider_id
    if req.to_bot.provider_id != state.config.provider_id {
        return Err(BridgeError::provider_id_mismatch());
    }
    // 3. method
    match req.method.as_str() {
        "bot.ping" => Ok(json_reply(json!({"ok": true}))),
        "chat.send" => handle_chat_send(state, req).await,
        // `interaction.resolve` carries the BCS HITL decision back to a parked
        // cc control request. Its ACK error shape is a STRING error (spec
        // §5.1), distinct from `BridgeError`'s object shape — handled in its
        // own branch so it never reaches the shared error renderer.
        "interaction.resolve" => Ok(handle_interaction_resolve(state, req).await),
        "chat.inject" => handle_chat_inject(state, req).await,
        "chat.abort" => handle_chat_abort(state, req).await,
        other => Err(BridgeError::unsupported_method(other)),
    }
}

async fn handle_chat_send(
    state: Arc<AppState>,
    req: DownstreamRequest,
) -> Result<RuntimeReply, BridgeError> {
    // 2. session_id present
    let session_id = req
        .session_id
        .as_ref()
        .ok_or_else(|| BridgeError::invalid_request("session_id required"))?
        .clone();
    // 3. message present
    if req.message.is_none() {
        return Err(BridgeError::invalid_request("message required"));
    }
    // 4. bot exists
    let bot = state
        .config
        .bot(&req.to_bot.provider_bot_ref)
        .ok_or_else(|| BridgeError::bot_not_found(&req.to_bot.provider_bot_ref))?
        .clone();

    let run_id = req.id.clone();
    let fp = crate::run::body_fingerprint(&req, &session_id);

    // 5. Atomic begin FIRST — get-or-create the run entry BEFORE claiming the
    //    session slot. This closes the startup TOCTOU between
    //    `sessions.try_start_run` (sets `active_run`) and `runs.begin` (creates
    //    the registry entry): a chat.abort landing in that window used to see
    //    `active_run=Some` + `runs.get(None)` and wrongly return
    //    `{"aborted":false}`. With begin-first, the registry entry exists the
    //    moment `active_run` becomes `Some`, so the abort handler always reads
    //    a consistent pair.
    //
    // Re-attach ordering (Task 11): a same-id retry MUST NOT 429 and MUST NOT
    // touch the session slot. `begin` is the get-or-create atomic — it returns
    // `is_new == false` for an existing id, so the re-attach / terminal-replay
    // / conflict decision below runs entirely before `try_start_run` (which is
    // new-id only). Same-id retries with a different body still 409.
    let (handle, is_new) = state.runs.begin(&run_id, fp.clone());
    if !is_new {
        if !handle.matches(&fp) {
            // same id, different body → conflict (409)
            return Err(BridgeError::conflict());
        }
        let stream = Box::pin(crate::run::forward_stream(handle.clone()));
        return Ok(RuntimeReply::Run { handle, stream });
    }

    // 6. New run: claim the session slot (429 if a different run is busy).
    //    On contention, roll back the placeholder entry we just created via
    //    `remove` (not `finish` — finishing would pin a never-spawned entry
    //    for TERMINAL_GRACE and stall same-id retries behind a fake terminal
    //    replay). The run_id is unique at this point (a colliding id would
    //    have returned `is_new == false` above), so removing it cannot touch
    //    another run's entry.
    if state
        .sessions
        .try_start_run(&bot.provider_bot_ref, &session_id, &run_id)
        .await
        .is_err()
    {
        state.runs.remove(&run_id);
        return Err(BridgeError::rate_limited());
    }

    // 7. Record the (provider_bot_ref, session_id) association BEFORE spawning
    //    the driver so a chat.abort landing in the (small) window between
    //    claiming the slot and the driver taking flight can still resolve the
    //    run — via the active leg if mid-flight, or via `find_terminal_run`
    //    (410 leg) once the driver self-terminates.
    state
        .runs
        .record_session(&run_id, &bot.provider_bot_ref, &session_id);
    handle.set_upstream(state.config.mode == crate::config::ConnectionMode::Plugin);
    let stream = Box::pin(crate::run::forward_stream(handle.clone()));
    crate::run::spawn_run(state, req, bot, session_id, handle.clone());
    Ok(RuntimeReply::Run { handle, stream })
}

/// `interaction.resolve` ACK with a STRING error (spec §5.1 — this method does
/// not share `BridgeError`'s `{code,message,retryable}` object shape). 200 OK
/// carries the protocol-level ok/false so the BCS retry layer reads `error`.
fn resolve_err_ack(message: &str) -> RuntimeReply {
    json_reply(json!({ "ok": false, "retryable": false, "error": message }))
}

/// Handle `interaction.resolve`: deliver the BCS decision (exec `decision` or
/// ask_user `action`+`answers`) to the parked driver via the registry.
///
/// ACK semantics (spec §5.1):
/// - `Delivered` (first resolve) and `Duplicate` (idempotent replay of an
///   already-delivered interaction) → `{"ok":true}` — `Duplicate` does not
///   re-write the engine control channel.
/// - `Unknown` (no such `interactionId`) → `{"ok":false,"retryable":false,
///   "error":"unknown interaction"}`.
/// - Malformed params (missing interactionId/idempotencyKey or
///   decision|action) → `{"ok":false,"retryable":false,"error":"<reason>"}`.
async fn handle_interaction_resolve(state: Arc<AppState>, req: DownstreamRequest) -> RuntimeReply {
    let params = req.params.clone().unwrap_or(Value::Null);

    let Some(interaction_id) = params.get("interactionId").and_then(|v| v.as_str()) else {
        return resolve_err_ack("missing interactionId");
    };
    let Some(idempotency_key) = params.get("idempotencyKey").and_then(|v| v.as_str()) else {
        return resolve_err_ack("missing idempotencyKey");
    };

    // Build the resolution payload consumed by the driver's behavior mapping.
    // exec: {"decision": <decision>}. ask_user: {"action": <action>, "answers":
    // [...]}. The driver collapses ask_user to allow/deny (cc v1 has no answers
    // channel); `action:"cancel"` and missing answers map to deny.
    let resolution = if let Some(decision) = params.get("decision").cloned() {
        json!({ "decision": decision })
    } else if let Some(action) = params.get("action").cloned() {
        let answers = params.get("answers").cloned().unwrap_or_else(|| json!([]));
        json!({ "action": action, "answers": answers })
    } else {
        return resolve_err_ack("decision or action required");
    };

    match state.interactions.resolve(interaction_id, idempotency_key, resolution) {
        ResolveOutcome::Delivered | ResolveOutcome::Duplicate => {
            json_reply(json!({ "ok": true }))
        }
        ResolveOutcome::Unknown => resolve_err_ack("unknown interaction"),
    }
}

/// Handle `chat.inject`: queue an observation message into the session without
/// driving an engine turn (spec §5.1: inject never triggers a run).
///
/// The SQLite receipt and queued message commit together before ACK. Both
/// engines prepend queued observations on the next send; no native transcript
/// files are read or modified, and inject never starts an engine.
async fn handle_chat_inject(
    state: Arc<AppState>,
    req: DownstreamRequest,
) -> Result<RuntimeReply, BridgeError> {
    // 1. session_id required
    let session_id = req
        .session_id
        .as_ref()
        .ok_or_else(|| BridgeError::invalid_request("session_id required"))?
        .clone();
    // 2. message required
    let message = req
        .message
        .clone()
        .ok_or_else(|| BridgeError::invalid_request("message required"))?;
    // 3. bot exists
    let bot = state
        .config
        .bot(&req.to_bot.provider_bot_ref)
        .ok_or_else(|| BridgeError::bot_not_found(&req.to_bot.provider_bot_ref))?
        .clone();

    // Include all sender identity and message fields in the durable receipt.
    // Structured serialization avoids delimiter ambiguity in arbitrary text.
    let fingerprint = serde_json::to_string(&json!([message, req.from]))
        .map_err(|_| BridgeError::invalid_request("cannot encode inject payload"))?;
    let run_id = req.id.clone();

    // Flatten sender name and text for the durable queue and next-turn prompt.
    let from_name = req
        .from
        .as_ref()
        .and_then(|f| f.get("name"))
        .and_then(|n| n.as_str())
        .map(str::to_string);
    let text = crate::run::extract_message_text(Some(&message));

    let injected = crate::session::InjectedMessage {
        run_id, from_name, text,
    };
    state.sessions.enqueue_inject(&bot.provider_bot_ref, &session_id, injected, fingerprint)
        .await.map_err(|error| {
            if matches!(error, crate::session::SessionError::Conflict) {
                BridgeError::conflict()
            } else {
                tracing::error!(%error, provider_bot_ref = %bot.provider_bot_ref,
                    bcs_session_id = %session_id, "inject persistence failed");
                BridgeError::unavailable("inject persistence failed")
            }
        })?;
    Ok(json_reply(json!({ "ok": true })))
}

/// Handle `chat.abort` (Task 14, spec §5.3): cancel the active run for the
/// session (if any), emit a terminal `chat_aborted` SSE frame on that run's
/// stream (driven by the run loop, which sees the abort flag and surfaces the
/// final state), and respond per the abort matrix. The 200 abort ACK does NOT
/// wait for the engine to die — the run loop's post-cancel finalize drains it
/// asynchronously (the BCS x-PC observes the `state=aborted` frame on the
/// chat.send SSE stream, not a flushed abort ACK).
///
/// Response matrix (spec §5.3):
/// - Active run for the session: invalidate its parked HITL interactions with
///   the deny fallback (so the driver never blocks on a dead receiver), call
///   its `request_abort()` (sets the abort flag + cancels the token + records
///   the `user_cancelled` stop_reason for the terminal `chat_aborted` SSE
///   frame), then 200 `{"ok":true,"aborted":true,"aborted_run_ids":[<run_id>]}`.
/// - No active run, but a terminal run recorded for this session in
///   `RunRegistry`'s `run_session` reverse index: 410 `run_terminated` —
///   repeating abort on the same terminal run stably returns 410.
/// - No record at all (and the edge case where the session store claims an
///   active run_id that the registry has already swept): 200
///   `{"ok":true,"aborted":false,"aborted_run_ids":[]}`.
///
/// Passes through the idempotency ledger (Task 5) with fingerprint
/// `method + provider_bot_ref + session_id` (no message body — abort has
/// none). Replays the prior status+body verbatim (via
/// [`IdempotencyLedger::complete_with_status`], including 410s) so a same-id
/// retry of any branch returns the exact same response — the brief's
/// "对同 terminal run 重复 abort 稳定同答；幂等台账保证同 id 重放".
async fn handle_chat_abort(
    state: Arc<AppState>,
    req: DownstreamRequest,
) -> Result<RuntimeReply, BridgeError> {
    // 1. session_id required
    let session_id = req
        .session_id
        .as_ref()
        .ok_or_else(|| BridgeError::invalid_request("session_id required"))?
        .clone();
    // 2. bot exists
    let bot = state
        .config
        .bot(&req.to_bot.provider_bot_ref)
        .ok_or_else(|| BridgeError::bot_not_found(&req.to_bot.provider_bot_ref))?
        .clone();

    let target = req.params.as_ref().and_then(|params| params.get("run_id")).and_then(Value::as_str);
    if req.params.as_ref().and_then(|params| params.get("run_id")).is_some() && target.map_or(true, str::is_empty) {
        return Err(BridgeError::invalid_request("run_id must be a nonempty string"));
    }
    if let Some(target) = target {
        if state.runs.get(target).is_some() && !state.runs.belongs_to(target, &bot.provider_bot_ref, &session_id) {
            return Err(BridgeError::invalid_request("run_id belongs to another bot or session"));
        }
    }

    // 3. Idempotency: fingerprint = method + provider_bot_ref + session_id
    //    (no message — abort carries none). Same shape as `chat.inject` minus
    //    the body.
    let fp = crate::idempotency::fingerprint(&[
        "chat.abort",
        &req.to_bot.provider_bot_ref,
        &session_id,
        target.unwrap_or(""),
    ]);
    let run_id = req.id.clone();
    match state.idem.begin(&run_id, &fp) {
        IdemDecision::Proceed => {}
        IdemDecision::Replay { status, body } => {
            return Ok(RuntimeReply::Json { status, body })
        }
        IdemDecision::Conflict => return Err(BridgeError::conflict()),
    }

    // 4. Matrix. Resolve the active run's handle up-front so the active branch
    //    is a single atomic invalidate+request_abort against the same handle
    //    (no TOCTOU between re-reading sessions.active_run and runs.get).
    let active_run_id = state
        .sessions
        .active_run(&bot.provider_bot_ref, &session_id)
        .await;
    let active_run_id = active_run_id.filter(|active| target.map_or(true, |target| target == active));
    let active_handle = active_run_id
        .as_deref()
        .and_then(|rid| state.runs.get(rid));

    // Branch 1 — active run: invalidate + request_abort → 200 aborted:true.
    if let (Some(run_id_active), Some(handle)) = (active_run_id.as_deref(), active_handle) {
        // Invalidate BEFORE the engine kill (spec §6.3): the resolution channel
        // delivers the deny fallback rather than a dropped-receiver error. The
        // run loop's finalize calls invalidate_run again — idempotent, so the
        // second call's resolver-clear is a no-op.
        state
            .interactions
            .invalidate_run(run_id_active, json!({ "decision": "deny" }));
        // request_abort: set abort_requested (→ run loop emits chat_aborted),
        // store "user_cancelled" as the SSE frame's stopReason, cancel the
        // engine's CancellationToken (the driver's select! arm fires, kills
        // the cli, returns TurnError::Aborted).
        handle.request_abort("user_cancelled");
        let body = json!({
            "ok": true,
            "aborted": true,
            "aborted_run_ids": [run_id_active],
        });
        state
            .idem
            .complete_with_status(&run_id, 200, body.clone());
        return Ok(RuntimeReply::Json { status: 200, body });
    }

    // Branch 2 — terminal run recorded for this session → 410 run_terminated.
    if target.map_or_else(|| state.runs.find_terminal_run(&bot.provider_bot_ref, &session_id).is_some(), |target| state.runs.get(target).map_or(false, |handle| handle.is_terminal()))
    {
        let (status, body) = BridgeError::run_terminated().into_parts();
        state
            .idem
            .complete_with_status(&run_id, status, body.clone());
        return Ok(RuntimeReply::Json { status, body });
    }

    // Branch 3 — no active run and no terminal record: 200 aborted:false.
    // The only sources of `active_run=Some` + `handle=None` used to be the
    // startup TOCTOU between try_start_run and begin (chat.send now does
    // begin-first, so the registry entry exists the moment active_run is
    // set) and a run swept past grace after finish (impossible: the driver
    // calls sessions.finish_run between runs.finish and the sweep's grace
    // expiry, so active_run is cleared before the entry is reclaimable).
    // Reaching here on a session the provider has never seen, or whose run
    // finished long ago past grace — BCS may send a fresh request normally.
    let body = json!({
        "ok": true,
        "aborted": false,
        "aborted_run_ids": [],
    });
    state
        .idem
        .complete_with_status(&run_id, 200, body.clone());
    Ok(RuntimeReply::Json { status: 200, body })
}
