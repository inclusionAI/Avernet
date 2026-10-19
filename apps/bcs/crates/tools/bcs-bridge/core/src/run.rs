//! Transport-independent engine execution and bounded typed event retention.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
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
use crate::run_buffer::{RunEventBuffer, retained_event_size, MAX_RETAINED_BYTES, MAX_RETAINED_EVENTS};
pub use crate::run_buffer::{DeliveryCursorError, RetentionMode, RetentionStats};

/// Grace TTL for a terminal run's buffered frames before lazy sweep removes the
/// entry (lets a late re-send replay the terminal state).
const TERMINAL_GRACE: Duration = Duration::from_secs(300);

/// Forward-loop poll interval: after the driver marks a run terminal, the
/// forwarder drains remaining broadcast messages and exits within this window.
/// Retained records are the source of truth even if broadcast wake-ups lag.
const TERMINAL_POLL: Duration = Duration::from_millis(25);

/// Result of attempting to push one frame into the run's buffer+broadcast.
pub(crate) enum PushOutcome {
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
    retention_mode: RetentionMode,
    /// Whether the plugin transport this run reports through can uplink V3
    /// interaction events. Only read together with the retention mode (gateway runs
    /// fall back to the gateway SSE encoder, which supports interactions).
    interaction_uplink: Arc<AtomicBool>,
    delivery_pinned: Arc<AtomicBool>,
    delivery_released_at: Arc<Mutex<Option<Instant>>>,
    /// Set when the retention byte/event budget first fires inside this run:
    /// guards a one-shot WARN so overflow no longer vanishes silently.
    overflow_warned: Arc<AtomicBool>,
    pub tx: broadcast::Sender<RunEvent>,
    buffer: Arc<Mutex<RunEventBuffer>>,
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
    /// Snapshot of currently retained records; Plugin consumption uses the checked cursor API.
    pub fn snapshot_after(&self, seq: u64) -> Vec<RunEvent> {
        self.buffer.lock().unwrap_or_else(|p| p.into_inner()).snapshot_after(seq)
    }

    pub fn retention_mode(&self) -> RetentionMode { self.retention_mode }

    pub fn retention_stats(&self) -> RetentionStats {
        self.buffer.lock().unwrap_or_else(|p| p.into_inner()).stats()
    }

    /// Clone only the next retained record; never silently skip a reclaimed prefix.
    pub fn next_after(&self, seq: u64) -> Result<Option<RunEvent>, DeliveryCursorError> {
        let buffer = self.buffer.lock().unwrap_or_else(|p| p.into_inner());
        let committed_through = buffer.stats().delivery_cursor;
        if self.is_upstream() && seq > committed_through {
            return Err(DeliveryCursorError::Uncommitted {cursor:seq, committed_through});
        }
        buffer.next_after(seq)
    }

    /// Commit one locally consumed Plugin record, not a server-persistence ACK.
    pub fn commit_delivery_through(&self, seq: u64) -> Result<(), DeliveryCursorError> {
        if self.retention_mode != RetentionMode::PluginPending {
            return Err(DeliveryCursorError::WrongMode);
        }
        self.buffer.lock().unwrap_or_else(|p| p.into_inner()).commit_delivery_through(seq)
    }

    fn is_upstream(&self) -> bool { self.retention_mode == RetentionMode::PluginPending }

    pub fn release_delivery(&self) {
        if !self.is_upstream() || !self.is_terminal() { return; }
        let mut released_at = self.delivery_released_at.lock().unwrap_or_else(|p| p.into_inner());
        if released_at.is_some() { return; }
        let mut buffer = self.buffer.lock().unwrap_or_else(|p| p.into_inner());
        let stats = buffer.stats();
        tracing::debug!(run = %self.run_id, retention_mode = self.retention_mode.label(),
            delivery_cursor = stats.delivery_cursor, last_emitted_seq = stats.last_emitted_seq,
            retained_events = stats.retained_events, retained_bytes = stats.retained_bytes,
            peak_retained_events = stats.peak_retained_events, peak_retained_bytes = stats.peak_retained_bytes,
            "releasing terminal run delivery; discarding any remaining records");
        buffer.clear();
        // A long offline run gets a full tombstone grace period after delivery.
        // Repeated releases must not renew that period indefinitely.
        *released_at = Some(Instant::now());
        self.delivery_pinned.store(false, Ordering::SeqCst);
    }

    fn cancel_if_gateway_disconnected(&self) {
        if !self.is_upstream() && self.tx.receiver_count() == 0 {
            self.abort.cancel();
        }
    }

    /// Whether the plugin transport this run reports through can uplink V3
    /// interaction events. Gateway runs keep the default (true for behavior):
    /// the gateway encoder always supports interaction frames, so the flag is
    /// only meaningful for `RetentionMode::PluginPending`.
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
        self.begin_with_mode(run_id, fingerprint, RetentionMode::GatewayReplay)
    }

    /// Fix retention policy before publishing the handle or spawning the driver.
    pub fn begin_with_mode(&self, run_id: &str, fingerprint: String, retention_mode: RetentionMode) -> (RunHandle, bool) {
        let mut inner = self.inner.lock().unwrap_or_else(|p| p.into_inner());
        Self::sweep_locked(&mut inner);
        if let Some(entry) = inner.map.get(run_id) {
            return (entry.handle.clone(), false);
        }
        let (tx, _rx) = broadcast::channel::<RunEvent>(256);
        let handle = RunHandle {
            run_id: run_id.to_string(),
            retention_mode,
            interaction_uplink: Arc::new(AtomicBool::new(false)),
            delivery_pinned: Arc::new(AtomicBool::new(retention_mode == RetentionMode::PluginPending)),
            delivery_released_at: Arc::new(Mutex::new(None)),
            overflow_warned: Arc::new(AtomicBool::new(false)),
            abort: CancellationToken::new(),
            tx,
            buffer: Arc::new(Mutex::new(RunEventBuffer::default())),
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
            let stats = entry.handle.retention_stats();
            tracing::debug!(run = %run_id, retention_mode = entry.handle.retention_mode.label(),
                last_emitted_seq = stats.last_emitted_seq, delivery_cursor = stats.delivery_cursor,
                retained_events = stats.retained_events, retained_bytes = stats.retained_bytes,
                peak_retained_events = stats.peak_retained_events, peak_retained_bytes = stats.peak_retained_bytes,
                "run cleanup complete; pending delivery may continue");
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
                        if handle.is_upstream()
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

/// Retain a typed event; reserve one bounded terminal error beyond the budget.
pub(crate) fn push_frame(
    handle: &RunHandle,
    seq: &mut u64,
    run_id: &str,
    ev: &StreamEvent,
    trace: Option<&TraceContext>,
) -> PushOutcome {
    let size = retained_event_size(ev).map(|size| size.saturating_add(run_id.len()));
    let mut buffer = handle.buffer.lock().unwrap_or_else(|p| p.into_inner());
    // A failed retention attempt reserves exactly one terminal record, even if a
    // caller accidentally tries to push again while engine cancellation propagates.
    if buffer.failure_retained() { return PushOutcome::Terminate; }
    *seq += 1;
    let ts = now_ms();
    let stats = buffer.stats();
    let mut accounted_bytes = 0;
    let failure: Option<(String, &'static str)> = match size {
        Ok(size) => match buffer.limit_reason(size) {
            Some(reason) => {
                let overdue_bytes = stats.retained_bytes.saturating_add(size).saturating_sub(MAX_RETAINED_BYTES);
                if !handle.overflow_warned.swap(true, Ordering::SeqCst) {
                    tracing::warn!(target: "bcs_bridge_core::run",
                        run = %run_id, retention_mode = handle.retention_mode.label(), limit_reason = reason,
                        buffer_events = stats.retained_events, retained_bytes = stats.retained_bytes,
                        delivery_cursor = stats.delivery_cursor, last_emitted_seq = stats.last_emitted_seq,
                        next_event_bytes = size,
                        "run event retention budget exceeded before terminal error; aborting the run");
                }
                Some((format!(
                    "run event retention limit exceeded (limits: MAX_RETAINED_BYTES={MAX_RETAINED_BYTES}, \
                     MAX_RETAINED_EVENTS={MAX_RETAINED_EVENTS}; before this event: {bytes} bytes in \
                     {events} records, next event {size} bytes, projected overage {overdue_bytes} bytes; \
                     retention_mode={mode}, limit_reason={reason}, delivery_cursor={cursor}, last_emitted_seq={last})",
                    bytes = stats.retained_bytes, events = stats.retained_events,
                    mode = handle.retention_mode.label(), cursor = stats.delivery_cursor, last = stats.last_emitted_seq), "buffer_overflow"))
            }
            None => { accounted_bytes = size; None }
        },
        Err(_) => Some(("event serialization failed".to_string(), "runtime_error")),
    };
    let event = match &failure {
        Some((message, kind)) if *kind == "buffer_overflow" => {
            let mut failed = sse::chat_error(run_id, message, Some(kind));
            if let StreamEvent::Chat(chat) = &mut failed { chat.stop_reason = Some("buffer_overflow".into()); }
            failed
        }
        Some((message, kind)) => sse::chat_error(run_id, message, Some(kind)),
        None => ev.clone(),
    };
    if failure.is_some() {
        // The bounded replacement must be accounted too: otherwise reclaiming it
        // could underflow after normal records have all been consumed.
        match retained_event_size(&event) {
            Ok(size) => accounted_bytes = size.saturating_add(run_id.len()),
            Err(error) => {
                tracing::error!(%run_id, %error, "cannot account terminal retention error");
                handle.abort.cancel();
                return PushOutcome::Terminate;
            }
        }
    }
    if let Some(trace) = trace { trace.record_converted(&event, *seq); }
    let record = RunEvent { run_id: run_id.to_string(), seq: *seq, ts, event };
    buffer.append(record.clone(), accounted_bytes, failure.is_some());
    let sent = handle.tx.send(record).is_ok();
    if failure.is_some() {
        handle.abort.cancel();
        PushOutcome::Terminate
    } else if sent || handle.is_upstream() {
        PushOutcome::Ok
    } else {
        PushOutcome::Disconnect
    }
}

/// Subscribe synchronously before spawning an engine. Replay also repairs a
/// lagging broadcast receiver from the retained records without sequence gaps.
pub fn forward_stream(handle: RunHandle) -> Result<impl Stream<Item = RunEvent> + Send + 'static, DeliveryCursorError> {
    if handle.is_upstream() { return Err(DeliveryCursorError::WrongMode); }
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
    Ok(tokio_stream::wrappers::ReceiverStream::new(rx))
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
#[path = "run_tests.rs"]
mod tests;
