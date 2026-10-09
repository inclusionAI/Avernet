//! Transport-independent engine execution and bounded typed event retention.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use bcs_protocol::now_ms;
use bcs_protocol::stream::{ChatState, StreamEvent};
use futures::stream::Stream;
use serde_json::json;
use tokio::sync::{broadcast, mpsc};
use tokio_util::sync::CancellationToken;

use crate::engine::{TurnError, TurnOutcome, TurnRequest};
use crate::engine::trace::TraceContext;
use crate::sse;
use crate::runtime::{AppState, DownstreamRequest};
use crate::config::BotConfig;

/// Grace TTL for a terminal run's buffered frames before lazy sweep removes the
/// entry (lets a late re-send replay the terminal state).
const TERMINAL_GRACE: Duration = Duration::from_secs(300);

/// Forward-loop poll interval: after the driver marks a run terminal, the
/// forwarder drains remaining broadcast messages and exits within this window.
/// Retained records are the source of truth even if broadcast wake-ups lag.
const TERMINAL_POLL: Duration = Duration::from_millis(25);

/// Result of attempting to push one frame into the run's buffer+broadcast.
enum PushOutcome {
    /// Frame accepted; loop continues.
    Ok,
    /// Broadcast had no subscribers: BCS disconnected → abort + close.
    Disconnect,
    /// Retention or typed serialization failed; a terminal error was retained.
    Terminate,
}

/// An engine event with immutable correlation and replay metadata.
#[derive(Clone, Debug)]
pub struct RunEvent {
    pub run_id: String,
    pub seq: u64,
    pub ts: u64,
    pub event: StreamEvent,
}

const MAX_RETAINED_BYTES: usize = 32 * 1024 * 1024;
const MAX_RETAINED_EVENTS: usize = 8192;

/// One active or terminal run's shared state: abort token, broadcast sender,
/// replay buffer, terminal flag, and the idempotency fingerprint.
///
/// `buffer` is a `std::sync::Mutex` (not `tokio::RwLock`) so that a push and a
/// forwarder's `(subscribe, snapshot)` can be made mutually atomic without
/// holding the lock across an `.await` — neither path awaits while holding it.
/// This is what keeps the replay-buffer-then-follow-broadcast forward path free
/// of duplicate or lost frames.
///
/// `abort_requested` distinguishes an explicit abort (chat.abort or graceful
/// shutdown) from a passive BCS disconnect. The run loop emits a terminal
/// `chat_aborted` frame only when it is set, so a disconnect closes the stream
/// silently while chat.abort surfaces a final `state=aborted` frame to the BCS
/// SSE consumer (spec §5.3).
///
/// All fields are `Arc`/clone-cheap so a [`RunHandle`] is cheaply cloneable for
/// the driver task and each re-attach forwarder.
#[derive(Clone)]
pub struct RunHandle {
    pub run_id: String,
    pub abort: CancellationToken,
    upstream: Arc<AtomicBool>,
    /// Whether the plugin transport this run reports through can uplink V3
    /// interaction events. Only read together with `upstream` (gateway runs
    /// fall back to the gateway SSE encoder, which supports interactions).
    interaction_uplink: Arc<AtomicBool>,
    delivery_pinned: Arc<AtomicBool>,
    delivery_released_at: Arc<Mutex<Option<Instant>>>,
    retained_bytes: Arc<AtomicUsize>,
    pub tx: broadcast::Sender<RunEvent>,
    pub buffer: Arc<Mutex<Vec<RunEvent>>>,
    pub terminal: Arc<AtomicBool>,
    abort_requested: Arc<AtomicBool>,
    /// `stopReason` to surface in the terminal `chat_aborted` SSE frame. Set by
    /// the abort requester via [`Self::request_abort`]; stays at the default
    /// `"user_cancelled"` until then. `std::sync::Mutex` (short critical section,
    /// never held across an `.await`) so a poison is recoverable.
    abort_reason: Arc<Mutex<String>>,
    fp: Arc<String>,
}

impl RunHandle {
    pub fn snapshot_after(&self, seq: u64) -> Vec<RunEvent> {
        self.buffer.lock().unwrap_or_else(|p| p.into_inner()).iter()
            .filter(|event| event.seq > seq).cloned().collect()
    }

    /// Clone only the next retained record for a delivery cursor.
    pub fn next_after(&self, seq: u64) -> Option<RunEvent> {
        self.buffer.lock().unwrap_or_else(|p| p.into_inner()).iter()
            .find(|event| event.seq > seq).cloned()
    }

    pub fn release_delivery(&self) {
        if !self.upstream.load(Ordering::SeqCst) || !self.is_terminal() { return; }
        let mut released_at = self.delivery_released_at.lock().unwrap_or_else(|p| p.into_inner());
        if released_at.is_some() { return; }
        let mut buffer = self.buffer.lock().unwrap_or_else(|p| p.into_inner());
        buffer.clear();
        buffer.shrink_to_fit();
        self.retained_bytes.store(0, Ordering::SeqCst);
        // A long offline run gets a full tombstone grace period after delivery.
        // Repeated releases must not renew that period indefinitely.
        *released_at = Some(Instant::now());
        self.delivery_pinned.store(false, Ordering::SeqCst);
    }

    fn cancel_if_gateway_disconnected(&self) {
        if !self.upstream.load(Ordering::SeqCst) && self.tx.receiver_count() == 0 {
            self.abort.cancel();
        }
    }

    pub(crate) fn set_upstream(&self, upstream: bool) {
        self.upstream.store(upstream, Ordering::SeqCst);
        self.delivery_pinned.store(upstream, Ordering::SeqCst);
    }

    /// Whether the plugin transport this run reports through can uplink V3
    /// interaction events. Gateway runs keep the default (true for behavior):
    /// the gateway encoder always supports interaction frames, so the flag is
    /// only meaningful together with `set_upstream(true)`.
    pub(crate) fn set_interaction_uplink(&self, supported: bool) {
        self.interaction_uplink.store(supported, Ordering::SeqCst);
    }

    pub(crate) fn interaction_uplink(&self) -> bool {
        self.interaction_uplink.load(Ordering::SeqCst)
    }

    /// Idempotency fingerprint match (same id + same body).
    pub fn matches(&self, fp: &str) -> bool {
        self.fp.as_ref() == fp
    }

    /// Whether the run has reached a terminal state.
    pub fn is_terminal(&self) -> bool {
        self.terminal.load(Ordering::SeqCst)
    }

    /// True iff an explicit abort has been requested via [`Self::request_abort`]
    /// (chat.abort or graceful shutdown). The run loop emits a terminal
    /// `chat_aborted` frame only when this is set; a passive BCS disconnect
    /// (broadcast send returns no subscribers) does not set it, so its run
    /// closes silently — the stream just ends.
    pub fn is_abort_requested(&self) -> bool {
        self.abort_requested.load(Ordering::SeqCst)
    }

    /// The `stopReason` to surface in the terminal `chat_aborted` SSE frame —
    /// whatever the most recent [`Self::request_abort`] caller set, defaulting
    /// to `"user_cancelled"` until then. Mutex poison is recovered (consistent
    /// with the rest of this crate) so a panicking holder never wedges the run.
    pub fn abort_stop_reason(&self) -> String {
        self.abort_reason.lock().unwrap_or_else(|p| p.into_inner()).clone()
    }

    /// Mark this run explicitly aborted: set the requested flag (so the run
    /// loop emits a `chat_aborted` terminal frame when the engine returns
    /// `TurnError::Aborted`), record `reason` as the SSE frame's `stopReason`,
    /// and cancel the token (the driver's `select!` arm fires, killing the
    /// engine). Idempotent — safe to call repeatedly (a chat.abort racing a
    /// graceful shutdown, or a duplicate abort, all collapse to one abort).
    pub fn request_abort(&self, reason: &str) {
        self.abort_requested.store(true, Ordering::SeqCst);
        *self.abort_reason.lock().unwrap_or_else(|p| p.into_inner()) = reason.to_string();
        self.abort.cancel();
    }
}

struct RunEntry {
    handle: RunHandle,
    finished_at: Option<Instant>,
}

/// Inner state guarded by the registry's single mutex: the forward run map
/// plus the `run_session` reverse index (run_id → (provider_bot_ref,
/// bcs_session_id)). Both are mutated under one lock so the lazy grace-TTL
/// sweep reclaims them atomically — `chat.abort`'s `find_terminal_run` never
/// observes a run_session entry whose run was already swept (and vice versa).
#[derive(Default)]
struct RunRegistryInner {
    map: HashMap<String, RunEntry>,
    run_session: HashMap<String, (String, String)>,
}

/// Registry of in-flight and recently-terminal runs, keyed by downstream body id.
///
/// `begin` is the create-or-get entry point: it atomically inserts a new run or
/// returns the existing handle for the same id (`is_new == false`). `get` reads
/// an existing handle without inserting. `finish` marks a run terminal and stamps
/// `finished_at` so the lazy sweep can reclaim it after [`TERMINAL_GRACE`].
///
/// `chat.abort` (Task 14) drives off two lookup paths:
/// - [`Self::get`] returns the active run's handle (so the abort handler can
///   cancel its token + invalidate its interactions).
/// - [`Self::find_terminal_run`] reverse-looks-up via the `run_session` index:
///   given `(provider_bot_ref, session_id)` it answers "is there a terminal run
///   recorded for this pair?" — the second leg of the abort response matrix
///   (terminal run → 410 `run_terminated`; no record → 200 `{"aborted": false}`).
#[derive(Default)]
pub struct RunRegistry {
    inner: Mutex<RunRegistryInner>,
}

impl RunRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    fn sweep_locked(inner: &mut RunRegistryInner) {
        let now = Instant::now();
        // Retain terminal entries within grace; always retain active.
        inner.map.retain(|_, e| {
            match e.finished_at {
                Some(t) => {
                    if e.handle.delivery_pinned.load(Ordering::SeqCst) { return true; }
                    let released_at = *e.handle.delivery_released_at.lock().unwrap_or_else(|p| p.into_inner());
                    let retained_since = released_at.map_or(t, |released| released.max(t));
                    now.saturating_duration_since(retained_since) < TERMINAL_GRACE
                }
                None => true,
            }
        });
        // Drop run_session entries whose runs were swept (the run no longer
        // lives in the map — the (bot, session) pair is no longer resolvable
        // by run id, so `find_terminal_run` must stop reporting it).
        inner.run_session.retain(|run_id, _| inner.map.contains_key(run_id));
    }

    /// Returns the existing handle for `run_id` (active or terminal), if any.
    /// Performs a lazy grace-TTL sweep of terminal entries.
    pub fn get(&self, run_id: &str) -> Option<RunHandle> {
        let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        Self::sweep_locked(&mut inner);
        inner.map.get(run_id).map(|e| e.handle.clone())
    }

    /// Create a new run, or — if `run_id` already exists — return the existing
    /// handle. The second return is `true` iff a fresh run was created; `false`
    /// marks "same id already present" (re-attach / terminal-replay / conflict
    /// decision belongs to the caller, which compares [`RunHandle::matches`]).
    pub fn begin(&self, run_id: &str, fingerprint: String) -> (RunHandle, bool) {
        let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        Self::sweep_locked(&mut inner);
        if let Some(entry) = inner.map.get(run_id) {
            return (entry.handle.clone(), false);
        }
        let (tx, _rx) = broadcast::channel::<RunEvent>(256);
        let handle = RunHandle {
            run_id: run_id.to_string(),
            upstream: Arc::new(AtomicBool::new(false)),
            interaction_uplink: Arc::new(AtomicBool::new(false)),
            delivery_pinned: Arc::new(AtomicBool::new(false)),
            delivery_released_at: Arc::new(Mutex::new(None)),
            retained_bytes: Arc::new(AtomicUsize::new(0)),
            abort: CancellationToken::new(),
            tx,
            buffer: Arc::new(Mutex::new(Vec::new())),
            terminal: Arc::new(AtomicBool::new(false)),
            abort_requested: Arc::new(AtomicBool::new(false)),
            abort_reason: Arc::new(Mutex::new("user_cancelled".to_string())),
            fp: Arc::new(fingerprint),
        };
        inner.map.insert(
            run_id.to_string(),
            RunEntry { handle: handle.clone(), finished_at: None },
        );
        (handle, true)
    }

    /// Mark `run_id` terminal. Buffered frames are retained for [`TERMINAL_GRACE`]
    /// so a late re-send can replay the terminal state; lazy sweep reclaims them.
    pub fn finish(&self, run_id: &str) {
        let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        if let Some(entry) = inner.map.get_mut(run_id) {
            entry.handle.terminal.store(true, Ordering::SeqCst);
            entry.finished_at = Some(Instant::now());
        }
    }

    /// Delete a run entry (and its `run_session` association), bypassing the
    /// grace-TTL retention [`Self::finish`] relies on. Rollback-only: the
    /// chat.send handler creates a placeholder entry via [`Self::begin`]
    /// before claiming the session slot (`try_start_run`), so it can roll
    /// back the placeholder on 429 (the session slot is held by a different
    /// run_id) instead of leaving a dangling never-spawned entry pinned in
    /// the registry for [`TERMINAL_GRACE`]. Removes from the main `map` then
    /// `run_session.retain` (one upsert), so a stale session association is
    /// never left pointing at a gone run_id.
    pub fn remove(&self, run_id: &str) {
        let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        inner.map.remove(run_id);
        inner.run_session.retain(|rid, _| rid != run_id);
    }

    /// Record the `(provider_bot_ref, session_id)` association for `run_id`,
    /// enabling `chat.abort`'s [`Self::find_terminal_run`] reverse lookup.
    /// Called by `handle_chat_send` for a freshly-created run (right after
    /// [`Self::begin`] returns `is_new == true`). Idempotent on the same
    /// run_id — overwrites any stale association; stale entries are pruned
    /// by the grace-TTL sweep ([`Self::sweep_locked`]) once the run itself is
    /// swept.
    pub fn record_session(&self, run_id: &str, bot: &str, session: &str) {
        let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        inner
            .run_session
            .insert(run_id.to_string(), (bot.to_string(), session.to_string()));
    }

    pub fn belongs_to(&self, run_id: &str, bot: &str, session: &str) -> bool {
        let inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        inner.run_session.get(run_id).map_or(false, |(b, s)| b == bot && s == session)
    }

    /// Find a terminal run for `(bot, session)`, returning its run_id. Used by
    /// `chat.abort` to distinguish "no active run, but a terminal run was
    /// recorded for this session" (return 410 `run_terminated`) from "no record
    /// at all" (return 200 `{"aborted": false}`). Iterates the run_session
    /// reverse index and checks each candidate's `terminal` flag in the run
    /// map. There is at most one terminal run per session in practice: a fresh
    /// run cannot start while another is active (the session slot's 429 guard
    /// excludes it), so successive terminal runs for one session never overlap.
    pub fn find_terminal_run(&self, bot: &str, session: &str) -> Option<String> {
        let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        Self::sweep_locked(&mut inner);
        inner
            .run_session
            .iter()
            .find(|(run_id, (b, s))| {
                b == bot && s == session
                    && inner.map.get(*run_id).map_or(false, |e| e.handle.is_terminal())
            })
            .map(|(run_id, _)| run_id.clone())
    }

    /// Abort every still-active run by calling [`RunHandle::request_abort`] with
    /// `reason` on each. Used by the provider's graceful shutdown path (Task 15):
    /// iterating in-flight runs and cancelling them lets each run loop finalize
    /// (engine killed, interactions invalidated, session slot released) instead
    /// of leaving orphaned drivers when the process exits. Terminal runs are
    /// skipped — they are already closing. The mutex is released before calling
    /// `request_abort` so the per-run cancellation (which writes the engine's
    /// abort token, not this registry) proceeds without holding the registry
    /// lock; cancellation itself is non-blocking.
    pub async fn abort_all(&self, reason: &str) {
        let handles: Vec<RunHandle> = {
            let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
            Self::sweep_locked(&mut inner);
            inner
                .map
                .values()
                .filter(|e| !e.handle.is_terminal())
                .map(|e| e.handle.clone())
                .collect()
        };
        for handle in handles {
            handle.request_abort(reason);
        }
    }
}

/// Fixed-size SHA-256 fingerprint of the serialized message, session ID and
/// local Bot reference. Length-delimited inputs preserve field boundaries;
/// sorted JSON object keys make structurally-equal messages compare equally.
/// The serialized request text is temporary and is not retained in the run.
pub fn body_fingerprint(req: &DownstreamRequest, session_id: &str) -> String {
    let msg = serde_json::to_string(&req.message)
        .unwrap_or_else(|_| String::new());
    crate::idempotency::fingerprint(&[&msg, session_id, &req.to_bot.provider_bot_ref])
}

/// Reserve a durable inject batch and prepend it FIFO to this turn's input.
/// Reservation does not delete messages: only successful engine completion
/// acknowledges them; failures and restarts leave them eligible for retry.
async fn assemble_prompt(
    state: &AppState,
    bot: &BotConfig,
    session_id: &str,
    req: &DownstreamRequest,
) -> Result<String, crate::session::SessionError> {
    let injects = state
        .sessions
        .claim_injects(&bot.provider_bot_ref, session_id, &req.id)
        .await?;
    let mut prefix = String::new();
    for inj in &injects {
        if !prefix.is_empty() {
            prefix.push('\n');
        }
        match &inj.from_name {
            Some(name) => prefix.push_str(&format!("[from:{name}] {}", inj.text)),
            None => prefix.push_str(&inj.text),
        }
    }
    let body = extract_message_text(req.message.as_ref());
    Ok(if prefix.is_empty() {
        body
    } else {
        format!("{prefix}\n\n{body}")
    })
}

/// Extract `message.content[].text` and join multiple parts with `\n`. Missing
/// fields yield an empty string (validated upstream). Reused by the chat.inject
/// handler to flatten the inject body into the [`crate::session::InjectedMessage`]
/// text field used by the durable inject queue.
pub(crate) fn extract_message_text(message: Option<&serde_json::Value>) -> String {
    let Some(msg) = message else { return String::new() };
    let Some(content) = msg.get("content").and_then(|c| c.as_array()) else {
        return String::new();
    };
    let texts: Vec<&str> = content
        .iter()
        .filter_map(|item| item.get("text").and_then(|t| t.as_str()))
        .collect();
    texts.join("\n")
}

/// Drive the engine turn and push frames into the run's buffer+broadcast. Runs
/// until a terminal condition (final / engine-EOF / deadline / disconnect),
/// then marks the run terminal, releases the session slot, and notifies the
/// registry. The forward stream to the client is consumed separately via
/// [`forward_stream`].
async fn run_driver(
    state: Arc<AppState>,
    handle: RunHandle,
    req: DownstreamRequest,
    bot: BotConfig,
    session_id: String,
) {
    let run_id = req.id.clone();
    let timeout_ms = req.timeout_ms.unwrap_or(3_600_000);

    let prepared = async {
        let engine_session_id = state.sessions.mapping(&bot.provider_bot_ref, &session_id).await?.engine_session_id;
        let prompt = assemble_prompt(&state, &bot, &session_id, &req).await?;
        Ok::<_, crate::session::SessionError>((engine_session_id, prompt))
    }.await;
    let (engine_session_id, prompt) = match prepared {
        Ok(turn) => turn,
        Err(error) => {
            tracing::error!(%error, %run_id, bcs_session_id = %session_id, "cannot prepare durable session");
            let mut seq = 0;
            let _ = push_frame(&handle, &mut seq, &run_id,
                &sse::chat_error(&run_id, &error.to_string(), Some("session_store_error")), None);
            state.sessions.finish_run(&bot.provider_bot_ref, &session_id, &run_id).await;
            state.runs.finish(&run_id);
            return;
        }
    };
    tracing::info!(provider_id = %state.config.provider_id, provider_bot_ref = %bot.provider_bot_ref,
        bcs_session_id = %session_id, %run_id, engine_session_id = ?engine_session_id,
        resume = engine_session_id.is_some(), "starting engine turn");

    let Some(engine) = state.engine(&bot.provider_bot_ref) else {
        // AppState builds an engine for every configured bot, so this is unreachable
        // for bots resolved from the same configuration.
        let mut seq = 0;
        let _ = push_frame(&handle, &mut seq, &run_id,
            &sse::chat_error(&run_id, "bot has no configured engine", Some("engine_unavailable")), None);
        state.sessions.finish_run(&bot.provider_bot_ref, &session_id, &run_id).await;
        state.runs.finish(&run_id);
        return;
    };
    let mut turn_req = TurnRequest::new(run_id.clone(), prompt, bot.cwd.clone(),
        state.sessions.observer(&bot.provider_bot_ref, &session_id, &run_id));
    turn_req.engine_session_id = engine_session_id;
    turn_req.model = bot.model.clone();
    turn_req.permission_mode = bot.permission_mode.clone();
    turn_req.interactions = state.interactions.clone();
    turn_req.trace = state.trace.as_ref()
        .map(|store| TraceContext::new(store.clone(), bot.engine.clone(), run_id.clone()));

    let (ev_tx, mut ev_rx) = mpsc::channel::<StreamEvent>(64);
    let trace = turn_req.trace.clone();
    let abort_token = handle.abort.clone();
    let mut engine_handle: Option<tokio::task::JoinHandle<Result<TurnOutcome, TurnError>>> =
        Some(tokio::spawn(async move {
            engine.run_turn(turn_req, ev_tx, abort_token).await
        }));

    let mut seq: u64 = 0;
    let mut injects_completed = false;
    // Self-terminate ~30s ahead of the hard deadline so a terminal chat_error can
    // still flush before the client times out.
    let deadline_ms = timeout_ms.saturating_sub(30_000);
    let deadline = tokio::time::sleep(Duration::from_millis(deadline_ms));
    tokio::pin!(deadline);

    loop {
        tokio::select! {
            _ = &mut deadline => {
                // Deadline: emit terminal chat_error(deadline) and close.
                let _ = push_frame(&handle, &mut seq, &run_id,
                    &sse::chat_error(&run_id, "run deadline exceeded", Some("deadline")),
                    trace.as_ref());
                break;
            }
            ev = ev_rx.recv() => {
                match ev {
                    Some(StreamEvent::Chat(c)) if c.state == ChatState::Final => {
                        if let Err(error) = state.sessions.complete_injects(&bot.provider_bot_ref, &session_id, &run_id).await {
                            let _ = push_frame(&handle, &mut seq, &run_id,
                                &sse::chat_error(&run_id, &error.to_string(), Some("session_store_error")), trace.as_ref());
                            break;
                        }
                        injects_completed = true;
                        let _ = push_frame(
                            &handle,
                            &mut seq,
                            &run_id,
                            &StreamEvent::Chat(c),
                            trace.as_ref(),
                        );
                        break;
                    }
                    Some(StreamEvent::Interaction(interaction))
                        if handle.upstream.load(Ordering::SeqCst)
                            && !handle.interaction_uplink()
                            && interaction.phase == bcs_protocol::stream::InteractionPhase::Requested => {
                        state.interactions.invalidate_run(&run_id, json!({ "decision": "deny" }));
                        let _ = push_frame(&handle, &mut seq, &run_id,
                            &sse::chat_error(&run_id, "interactive authorization requires BCS protocol v3; this connection negotiated v2", Some("unsupported_interaction")), trace.as_ref());
                        break;
                    }
                    Some(event) => {
                        match push_frame(&handle, &mut seq, &run_id, &event, trace.as_ref()) {
                            PushOutcome::Ok => {}
                            PushOutcome::Disconnect | PushOutcome::Terminate => break,
                        }
                    }
                    None => {
                        // Engine task ended without emitting a Chat(Final) event
                        // (the cc/codex drivers return the final text via
                        // TurnOutcome). Resolve the outcome and emit the terminal
                        // frame here. `Aborted` is silent (handled by the
                        // post-loop cancel).
                        let outcome: Result<TurnOutcome, TurnError> = match engine_handle.take() {
                            Some(h) => match h.await {
                                Ok(r) => r,
                                Err(join_err) => Err(TurnError::EngineExited(format!("task join: {join_err}"))),
                            },
                            None => Err(TurnError::EngineExited("engine task missing".into())),
                        };
                        match outcome {
                            Ok(o) => {
                                if let Some(sid) = o.engine_session_id {
                                    if let Err(error) = state.sessions
                                        .set_engine_session_id(&bot.provider_bot_ref, &session_id, &sid).await {
                                        let _ = push_frame(&handle, &mut seq, &run_id,
                                            &sse::chat_error(&run_id, &error.to_string(), Some("session_store_error")), trace.as_ref());
                                        break;
                                    }
                                }
                                match o.final_text {
                                    Some(text) => {
                                        if let Err(error) = state.sessions.complete_injects(&bot.provider_bot_ref, &session_id, &run_id).await {
                                            let _ = push_frame(&handle, &mut seq, &run_id,
                                                &sse::chat_error(&run_id, &error.to_string(), Some("session_store_error")), trace.as_ref());
                                            break;
                                        }
                                        injects_completed = true;
                                        let _ = push_frame(&handle, &mut seq, &run_id,
                                            &sse::chat_final(&run_id, text),
                                            trace.as_ref());
                                    }
                                    None => {
                                        let _ = push_frame(&handle, &mut seq, &run_id,
                                            &sse::chat_error(&run_id,
                                                "engine exited without final text",
                                                Some("runtime_error")),
                                            trace.as_ref());
                                    }
                                }
                            }
                            Err(TurnError::Aborted) => {
                                // Explicit abort (chat.abort or graceful
                                // shutdown) → emit a terminal `chat_aborted`
                                // frame so the BCS SSE consumer sees the final
                                // `state=aborted` (spec §5.3). A passive BCS
                                // disconnect (push_raw's broadcast send returns
                                // no subscribers) does NOT set the abort flag —
                                // its run closes silently, the stream just ends.
                                if handle.is_abort_requested() {
                                    let _ = push_frame(
                                        &handle,
                                        &mut seq,
                                        &run_id,
                                        &sse::chat_aborted(&run_id, &handle.abort_stop_reason()),
                                        trace.as_ref(),
                                    );
                                }
                            }
                            Err(e) => {
                                let _ = push_frame(&handle, &mut seq, &run_id,
                                    &sse::chat_error(&run_id, &e.to_string(),
                                        Some("runtime_error")),
                                    trace.as_ref());
                            }
                        }
                        break;
                    }
                }
            }
        }
    }

    // Finalize: release any parked HITL interactions with a deny fallback
    // (spec §6.3: deadline → safe fallback; abort → deny) so the driver's
    // resolution_rx never blocks on a dead receiver. Done BEFORE cancelling
    // the engine so the fallback is delivered through the resolution channel
    // rather than lost to a dropped receiver; entries are retained (marked
    // resolved) so a late BCS resolve replays as Duplicate instead of Unknown.
    state
        .interactions
        .invalidate_run(&run_id, json!({ "decision": "deny" }));

    // Cancel the engine (idempotent), await its task if we did not already,
    // then mark terminal, release the session slot, notify registry.
    handle.abort.cancel();
    if let Some(h) = engine_handle.take() {
        let _ = h.await;
    }
    if !injects_completed {
        if let Err(error) = state.sessions.release_injects(&bot.provider_bot_ref, &session_id, &run_id).await {
            // This run already failed or disconnected. Preserve the original
            // terminal event; durable inflight rows are reclaimed next time.
            tracing::error!(%error, %run_id, bcs_session_id = %session_id, "failed to release inject batch");
        }
    }
    state
        .sessions
        .finish_run(&bot.provider_bot_ref, &session_id, &run_id)
        .await;
    // Publish terminal only after all driver cleanup and persistence work.
    state.runs.finish(&run_id);
}

/// Count serialized bytes without allocating another copy of a large payload.
fn serialized_size<T: serde::Serialize>(value: &T) -> Result<usize, serde_json::Error> {
    struct Counter(usize);
    impl std::io::Write for Counter {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.0 = self.0.saturating_add(bytes.len());
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
    }
    let mut counter = Counter(0);
    serde_json::to_writer(&mut counter, value)?;
    Ok(counter.0)
}

/// Account for all typed fields and raw snapshots, independent of any wire format.
fn retained_event_size(event: &StreamEvent) -> Result<usize, serde_json::Error> {
    use bcs_protocol::stream::AgentData;
    let size = match event {
        StreamEvent::Chat(c) => serialized_size(&(&c.run_id, c.seq, c.ts, &c.state, &c.session_id,
            &c.content, &c.delta_text, &c.stop_reason, &c.error_message, &c.error_kind, &c.error_code, &c.message, &c.raw))?,
        StreamEvent::Agent(a) => {
            let metadata = serialized_size(&(&a.run_id, a.seq, a.ts, &a.session_id, &a.raw))?;
            metadata + match &a.data {
                AgentData::Tool(data) => serialized_size(data)?,
                AgentData::Thinking(data) => serialized_size(data)?,
                AgentData::Approval(data) => serialized_size(data)?,
                AgentData::Lifecycle(data) => serialized_size(data)?,
                AgentData::Phase(data) => serialized_size(data)?,
                AgentData::Unknown { stream, raw } => serialized_size(&(stream, raw))?,
                AgentData::Assistant { raw } | AgentData::Error { raw } => serialized_size(raw)?,
            }
        }
        StreamEvent::Interaction(i) => serialized_size(&(&i.run_id, i.seq, i.ts, &i.session_id,
            &i.phase, &i.interaction_id, &i.kind, &i.raw))?,
        StreamEvent::Unknown { event, raw } => serialized_size(&(event, raw))?,
        StreamEvent::Ping { ts } => serialized_size(ts)?,
    };
    Ok(size + size_of::<RunEvent>())
}

/// Retain a typed event; reserve one bounded terminal error beyond the budget.
fn push_frame(
    handle: &RunHandle,
    seq: &mut u64,
    run_id: &str,
    ev: &StreamEvent,
    trace: Option<&TraceContext>,
) -> PushOutcome {
    *seq += 1;
    let ts = now_ms();
    let size = retained_event_size(ev).map(|size| size.saturating_add(run_id.len()));
    let mut buffer = handle.buffer.lock().unwrap_or_else(|p| p.into_inner());
    let failure = match size {
        Ok(size) if buffer.len() >= MAX_RETAINED_EVENTS || handle.retained_bytes.load(Ordering::SeqCst).saturating_add(size) > MAX_RETAINED_BYTES => Some(("run event retention limit exceeded", "buffer_overflow")),
        Ok(size) => { handle.retained_bytes.fetch_add(size, Ordering::SeqCst); None }
        Err(_) => Some(("event serialization failed", "runtime_error")),
    };
    let event = match failure {
        Some((message, kind)) => sse::chat_error(run_id, message, Some(kind)),
        None => ev.clone(),
    };
    if let Some(trace) = trace { trace.record_converted(&event, *seq); }
    let record = RunEvent { run_id: run_id.to_string(), seq: *seq, ts, event };
    buffer.push(record.clone());
    let sent = handle.tx.send(record).is_ok();
    if failure.is_some() {
        handle.abort.cancel();
        PushOutcome::Terminate
    } else if sent || handle.upstream.load(Ordering::SeqCst) {
        PushOutcome::Ok
    } else {
        PushOutcome::Disconnect
    }
}

/// Subscribe synchronously before spawning an engine. Replay also repairs a
/// lagging broadcast receiver from the retained records without sequence gaps.
pub fn forward_stream(handle: RunHandle) -> impl Stream<Item = RunEvent> + Send + 'static {
    let (tx, rx) = mpsc::channel::<RunEvent>(64);
    let mut subscriber = {
        let _buffer = handle.buffer.lock().unwrap_or_else(|p| p.into_inner());
        handle.tx.subscribe()
    };
    tokio::spawn(async move {
        let mut cursor = 0;
        loop {
            for event in handle.snapshot_after(cursor) {
                cursor = event.seq;
                if tx.send(event).await.is_err() { drop(subscriber); handle.cancel_if_gateway_disconnected(); return; }
            }
            if handle.is_terminal() && handle.snapshot_after(cursor).is_empty() { return; }
            tokio::select! {
                _ = tx.closed() => { drop(subscriber); handle.cancel_if_gateway_disconnected(); return; },
                event = subscriber.recv() => match event {
                    Ok(_) | Err(broadcast::error::RecvError::Lagged(_)) => {},
                    Err(broadcast::error::RecvError::Closed) => return,
                },
                _ = tokio::time::sleep(TERMINAL_POLL) => {},
            }
        }
    });
    tokio_stream::wrappers::ReceiverStream::new(rx)
}

/// The caller must create its subscription before spawning the engine.
pub fn spawn_run(
    state: Arc<AppState>,
    req: DownstreamRequest,
    bot: BotConfig,
    session_id: String,
    handle: RunHandle,
) {
    tokio::spawn(async move {
        run_driver(state, handle, req, bot, session_id).await;
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delivered_large_request_keeps_only_fixed_size_fingerprint() {
        let req: DownstreamRequest = serde_json::from_value(json!({
            "id": "large-request", "method": "chat.send", "session_id": "session",
            "to_bot": {"provider_id": "provider", "provider_bot_ref": "worker"},
            "message": {"content": [{"type": "text", "text": "x".repeat(8 * 1024 * 1024)}]}
        })).unwrap();
        let fingerprint = body_fingerprint(&req, "session");
        let registry = RunRegistry::new();
        let (handle, _) = registry.begin(&req.id, fingerprint.clone());
        handle.set_upstream(true);
        registry.finish(&req.id);
        handle.release_delivery();
        assert_eq!(handle.fp.len(), 64, "tombstones must not retain request text");
        let (same, is_new) = registry.begin(&req.id, body_fingerprint(&req, "session"));
        assert!(!is_new);
        assert!(same.matches(&fingerprint));
        let mut changed = req;
        changed.message = Some(json!({"content": [{"type": "text", "text": "changed"}]}));
        assert!(!same.matches(&body_fingerprint(&changed, "session")));
    }

    #[test]
    fn delivered_upstream_keeps_tombstone_but_releases_payloads() {
        let registry = RunRegistry::new();
        let (handle, _) = registry.begin("delivered", "fp".into());
        handle.set_upstream(true);
        let mut seq = 0;
        push_frame(&handle, &mut seq, "delivered", &sse::chat_delta("delivered", "payload"), None);
        handle.release_delivery();
        assert!(handle.delivery_pinned.load(Ordering::SeqCst), "active delivery cannot be released");
        assert_eq!(handle.snapshot_after(0).len(), 1);
        registry.finish("delivered");
        handle.release_delivery();
        assert!(handle.snapshot_after(0).is_empty());
        assert_eq!(handle.retained_bytes.load(Ordering::SeqCst), 0);
        let (repeated, is_new) = registry.begin("delivered", "fp".into());
        assert!(!is_new, "a duplicate must not launch a new engine");
        assert!(repeated.is_terminal());
        assert!(repeated.matches("fp"));
    }

    #[tokio::test]
    async fn dropping_idle_gateway_subscription_cancels_promptly() {
        let registry = RunRegistry::new();
        let (handle, _) = registry.begin("idle", "fp".into());
        drop(forward_stream(handle.clone()));
        tokio::time::timeout(Duration::from_millis(200), handle.abort.cancelled()).await.unwrap();
    }

    #[test]
    fn runtime_retains_events_without_gateway_wire_support() {
        let registry = RunRegistry::new();
        let (handle, _) = registry.begin("opaque", "fp".into());
        handle.set_upstream(true);
        let mut seq = 0;
        let event = StreamEvent::Unknown { event: "opaque".into(), raw: json!({"x": 7}) };
        assert!(matches!(push_frame(&handle, &mut seq, "opaque", &event, None), PushOutcome::Ok));
        assert!(matches!(handle.next_after(0).unwrap().event, StreamEvent::Unknown { .. }));
    }

    #[test]
    fn retention_budget_counts_raw_event_payloads() {
        let registry = RunRegistry::new();
        let (handle, _) = registry.begin("raw-budget", "fp".into());
        handle.set_upstream(true);
        let mut seq = 0;
        let mut event = sse::chat_delta("raw-budget", "small wire delta");
        if let StreamEvent::Chat(chat) = &mut event { chat.raw = json!({"trace": "x".repeat(7 * 1024 * 1024)}); }
        for _ in 0..4 {
            assert!(matches!(push_frame(&handle, &mut seq, "raw-budget", &event, None), PushOutcome::Ok));
        }
        assert!(matches!(push_frame(&handle, &mut seq, "raw-budget", &event, None), PushOutcome::Terminate));
        assert!(matches!(&handle.snapshot_after(4)[0].event, StreamEvent::Chat(chat) if chat.error_kind.as_deref() == Some("buffer_overflow")));
    }

    #[test]
    fn upstream_without_receivers_retains_until_delivery_released() {
        let registry = RunRegistry::new();
        let (handle, _) = registry.begin("offline", "fp".into());
        handle.set_upstream(true);
        let mut seq = 0;
        assert!(matches!(push_frame(&handle, &mut seq, "offline", &sse::chat_final("offline", "done".into()), None), PushOutcome::Ok));
        registry.finish("offline");
        registry.inner.lock().unwrap().map.get_mut("offline").unwrap().finished_at = Some(Instant::now() - TERMINAL_GRACE - Duration::from_secs(1));
        assert!(registry.get("offline").is_some());
        assert_eq!(handle.snapshot_after(0).len(), 1);
        handle.release_delivery();
        assert!(registry.get("offline").is_some(), "delivery starts a fresh tombstone grace period");
        let (_, is_new) = registry.begin("offline", "fp".into());
        assert!(!is_new, "late offline delivery must not permit re-execution");
        let expired = Instant::now() - TERMINAL_GRACE - Duration::from_secs(1);
        *handle.delivery_released_at.lock().unwrap() = Some(expired);
        handle.release_delivery();
        assert_eq!(*handle.delivery_released_at.lock().unwrap(), Some(expired), "release is idempotent");
        assert!(registry.get("offline").is_none(), "delivered tombstone expires after its delivery grace");
    }

    #[test]
    fn retention_count_overflow_keeps_terminal_error_and_cancels() {
        let registry = RunRegistry::new();
        let (handle, _) = registry.begin("overflow", "fp".into());
        handle.set_upstream(true);
        let mut seq = 0;
        for _ in 0..MAX_RETAINED_EVENTS {
            assert!(matches!(push_frame(&handle, &mut seq, "overflow", &sse::chat_delta("overflow", "x"), None), PushOutcome::Ok));
        }
        assert!(matches!(push_frame(&handle, &mut seq, "overflow", &sse::chat_delta("overflow", "x"), None), PushOutcome::Terminate));
        assert!(handle.abort.is_cancelled());
        let events = handle.snapshot_after(MAX_RETAINED_EVENTS as u64);
        assert_eq!(events.len(), 1);
        assert!(matches!(&events[0].event, StreamEvent::Chat(chat) if chat.state == ChatState::Error && chat.error_kind.as_deref() == Some("buffer_overflow")));
    }

    #[tokio::test]
    async fn forwarder_repairs_broadcast_lag_without_gaps() {
        use futures::StreamExt;
        let registry = RunRegistry::new();
        let (handle, _) = registry.begin("lag", "fp".into());
        let mut stream = Box::pin(forward_stream(handle.clone()));
        let mut seq = 0;
        for _ in 0..600 {
            assert!(matches!(push_frame(&handle, &mut seq, "lag", &sse::chat_delta("lag", "x"), None), PushOutcome::Ok));
        }
        registry.finish("lag");
        for expected in 1..=600 {
            assert_eq!(stream.next().await.unwrap().seq, expected);
        }
        assert!(stream.next().await.is_none());
    }

    #[test]
    fn structured_retention_preserves_metadata() {
        let registry = RunRegistry::new();
        let (handle, _) = registry.begin("typed-run", "fp".into());
        let _receiver = handle.tx.subscribe();
        let mut seq = 0;
        push_frame(&handle, &mut seq, "typed-run", &sse::chat_final("typed-run", "done".into()), None);
        let events = handle.snapshot_after(0);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].run_id, "typed-run");
        assert_eq!(events[0].seq, 1);
        assert_eq!(events[0].ts, handle.snapshot_after(0)[0].ts);
        assert!(handle.snapshot_after(1).is_empty());
        push_frame(&handle, &mut seq, "typed-run", &sse::chat_final("typed-run", "second".into()), None);
        let first = handle.next_after(0).unwrap();
        assert_eq!(first.seq, 1);
        assert_eq!(first.ts, events[0].ts);
        assert_eq!(handle.next_after(1).unwrap().seq, 2);
        assert!(handle.next_after(2).is_none());
    }

    #[test]
    fn begin_new_then_existing_returns_same_handle() {
        let reg = RunRegistry::new();
        let (h1, is_new) = reg.begin("r-1", "fp-a".into());
        assert!(is_new);
        let (h2, is_new2) = reg.begin("r-1", "fp-a".into());
        assert!(!is_new2);
        assert!(h2.matches("fp-a"), "existing handle carries the same fingerprint");
        assert!(h1.matches("fp-a"));
        assert!(!h1.matches("fp-b"));
        assert!(reg.get("r-1").is_some());
        assert!(reg.get("r-2").is_none());
    }

    #[test]
    fn finish_marks_terminal_and_retains_for_replay() {
        let reg = RunRegistry::new();
        let (h, _) = reg.begin("r-9", "fp".into());
        assert!(!h.is_terminal());
        reg.finish("r-9");
        assert!(h.is_terminal());
        assert!(reg.get("r-9").is_some(), "terminal run retained within grace TTL");
    }

    #[test]
    fn body_fingerprint_stable_for_same_body_distinct_for_different() {
        let mk = |msg: serde_json::Value, sid: &str, ref_: &str| {
            let req = DownstreamRequest {
                id: "x".into(),
                method: "chat.send".into(),
                to_bot: crate::webhook::ToBot { provider_id: "p".into(), provider_bot_ref: ref_.into() },
                session_id: Some(sid.into()),
                message: Some(msg),
                from: None,
                timeout_ms: None,
                params: None,
            };
            body_fingerprint(&req, sid)
        };
        let m = serde_json::json!({"role":"user","content":[{"type":"text","text":"hi"}]});
        let a = mk(m.clone(), "s-1", "b-1");
        let b = mk(m.clone(), "s-1", "b-1");
        assert_eq!(a, b, "same body → same fingerprint");
        let c = mk(m.clone(), "s-1", "b-2");
        assert_ne!(a, c, "different ref → different fingerprint");
        let d = mk(serde_json::json!({"role":"user","content":[{"type":"text","text":"yo"}]}), "s-1", "b-1");
        assert_ne!(a, d, "different message → different fingerprint");
    }

    #[test]
    fn extract_message_text_joins_multiple_content_blocks() {
        let m = serde_json::json!({"content":[
            {"type":"text","text":"line1"},
            {"type":"image","text":"ignored"},  // non-text type but text present: still joined
            {"type":"text","text":"line2"},
            {"type":"text"},                     // no text: skipped
        ]});
        assert_eq!(extract_message_text(Some(&m)), "line1\nignored\nline2");
        assert_eq!(extract_message_text(None), "");
    }

    #[test]
    fn request_abort_sets_flag_overrides_reason_and_cancels_token() {
        // A fresh run handle's flag is false and stop_reason defaults to
        // "user_cancelled"; calling request_abort flips the flag, overrides the
        // stop_reason, and cancels the CancellationToken.
        let reg = RunRegistry::new();
        let (h, _) = reg.begin("r-a", "fp".into());
        assert!(!h.is_abort_requested(), "fresh run is not aborted");
        assert_eq!(h.abort_stop_reason(), "user_cancelled");

        let r2 = h.clone();
        h.request_abort("provider_shutdown");
        assert!(h.is_abort_requested(), "flag set after request_abort");
        assert_eq!(h.abort_stop_reason(), "provider_shutdown", "reason overridden");
        assert!(r2.is_abort_requested(), "shared flag visible to cloned handle");
        assert_eq!(r2.abort_stop_reason(), "provider_shutdown");
        // Cancellation propagates to all clones (CancellationToken is shared).
        assert!(h.abort.is_cancelled(), "token cancelled after request_abort");
    }

    #[test]
    fn find_terminal_run_returns_terminal_match_none_for_active_or_unknown() {
        // Active run (not terminal): find_terminal_run returns None — abort
        // goes through the sessions.active_run path instead.
        let reg = RunRegistry::new();
        let (_h_active, _) = reg.begin("r-active", "fp".into());
        reg.record_session("r-active", "bot-1", "s-1");
        assert_eq!(reg.find_terminal_run("bot-1", "s-1"), None, "active run is not terminal");

        // Mark it terminal: now find_terminal_run resolves to its run_id.
        reg.finish("r-active");
        assert_eq!(reg.find_terminal_run("bot-1", "s-1").as_deref(), Some("r-active"));

        // Unknown session and bot mismatch: no match.
        assert_eq!(reg.find_terminal_run("bot-1", "s-unknown"), None, "unknown session");
        assert_eq!(reg.find_terminal_run("bot-other", "s-1"), None, "bot mismatch");
    }

    #[test]
    fn remove_drops_entry_and_run_session_association_for_rollback() {
        // chat.send's 429 rollback path: the placeholder entry created by
        // `begin` (and its record_session association, if any) must be wiped
        // so the same run_id is not pinned in the registry and a same-id retry
        // is not later surprised by a stale terminal-replay entry. `remove`
        // bypasses the grace-TTL retention `finish` relies on.
        let reg = RunRegistry::new();
        let (_h, is_new) = reg.begin("r-roll", "fp".into());
        assert!(is_new);
        reg.record_session("r-roll", "bot-1", "s-1");
        assert!(reg.get("r-roll").is_some());

        reg.remove("r-roll");

        assert!(reg.get("r-roll").is_none(), "entry removed by rollback");
        assert_eq!(reg.find_terminal_run("bot-1", "s-1"), None,
            "run_session association pruned alongside the entry");
    }

    #[tokio::test]
    async fn abort_all_cancels_every_active_run_and_skips_terminal() {
        // Two active + one terminal: only the two active handles have their
        // abort tokens cancelled after abort_all; the terminal one stays as-is
        // (it was already cancelled when its run loop finalized).
        let reg = RunRegistry::new();
        let (h_a, _) = reg.begin("r-a", "fp".into());
        let (h_b, _) = reg.begin("r-b", "fp".into());
        let (h_t, _) = reg.begin("r-t", "fp".into());
        reg.finish("r-t");
        assert!(!h_a.abort.is_cancelled());
        assert!(!h_b.abort.is_cancelled());

        reg.abort_all("provider_shutdown").await;

        assert!(h_a.abort.is_cancelled(), "active run a cancelled");
        assert!(h_b.abort.is_cancelled(), "active run b cancelled");
        assert!(!h_t.abort.is_cancelled(), "terminal run skipped");
        assert!(h_a.is_abort_requested() && h_b.is_abort_requested(), "flag set on each");
        assert_eq!(h_a.abort_stop_reason(), "provider_shutdown");
    }
}
