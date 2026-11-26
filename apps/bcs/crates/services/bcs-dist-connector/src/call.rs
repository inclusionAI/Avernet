//! One held call: the actor of one holder generation.
//!
//! Order (the core contract): persist send intent → call the agent → record
//! acceptance → report events → on every poll forward replies and carry out
//! cancels → admit the terminal event → finish. A lost holder stops at once
//! and writes nothing more. The actor enforces no deadline: that is the
//! Worker's, and reaches the call as a cancel (B13).
//!
//! Life of an actor:
//!
//! ```text
//! engine spawns ──▶ run ──▶ drive: first step by phase (send / resolve start /
//!                    │              re-attach / end canceled / nothing)
//!                    │       └──▶ follow: loop until ended
//!                    │              select! { next agent event | timer tick | poke }
//!                    │
//!                    ├─ on Stop::Invalid ──▶ park (stay held, re-read until lost/ended)
//!                    └─ on Stop::Yielded ──▶ return (drain: the call stays held for
//!                                             the partition's next owner)
//! ```
//!
//! `run` and `drive` run once per actor; everything repeated happens in
//! `follow`'s loop. A new actor (and a new `run`) exists only after a claim:
//! a first claim, a resume by this process, or a takeover by another one.
//!
//! Glossary:
//! - **holder generation** (holder epoch): one claim of the call. Every
//!   `disp` write of this actor is fenced by it; a newer claim makes the
//!   writes fail with `Lost`, and the actor stops (B1, B18).
//! - **tick**: one look at the `disp` document (see [`Call::tick`]), on a
//!   timer (`poll_interval`, about 1.5 s, B14) or at once on a poke.
//! - **poke**: the engine calls `Notify::notify_one()` on [`Call::poke`] when
//!   a hint for this call arrives while this actor is alive (`OwnedByMe`). A
//!   `Notify` stores at most one permit, so several pokes before the next
//!   wait collapse into one tick.
//! - **unresolved**: the START (first message) may or may not have reached
//!   the agent and cannot be checked or resent (`Uncertain`), or definitely
//!   did not (`NotSent`). Only a cancel is acted on then (B17).
//! - **blind**: the remote task is known but cannot be followed; replies
//!   and cancels still work.
//! - **safe point**: where a draining process may stop the actor without
//!   cutting anything off: the top of `follow`'s loop (no send, reply or
//!   cancel in flight) while no event stream is open, or a parked actor.
//!   The drain sets the shared `yielding` flag and pokes every actor.
//! - **two backoffs**: [`Backoff`] sleeps and retries in place (a store
//!   write the actor must complete before anything else, see `held_write!`);
//!   the tick backoff (`retry_after`) only says "not before time X" for a
//!   repeated remote action, so the actor keeps working meanwhile.

use crate::config::{Backoff, ConnectorConfig, ConnectorPorts};
use crate::delayed::{self, UnresolvedKind};
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::{ReportCursor, ResumeStep};
use bcs_service_api::port::dist_agent::RemoteEventStream;
use bcs_service_api::port::repo::distributed::dispatch::{HeldDispatchPort, HolderError};
use futures::StreamExt;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use tokio::sync::Notify;
use tokio::time::Instant;

/// What the engine can see of a running actor. Shared between the actor's
/// task (writer) and the engine (reader) through an `Arc`.
#[derive(Default)]
pub(crate) struct CallStatus {
    /// The actor returned normally through `run` (ended, lost, or parked and
    /// then lost or ended). A task that finished with `done == false`
    /// panicked or was aborted; the engine's `reap` then resumes the call
    /// under a new holder generation.
    pub done: AtomicBool,
    /// Something is unsettled: an unresolved send or input, a cancel whose
    /// outcome is not known yet, a blind or conflicted call, or a parked
    /// actor. The engine's `snapshot()` sums it into `pending_effects`.
    pub pending_effect: AtomicBool,
}

/// Why an actor stops before the call ended. Carried as the `Err` of
/// [`Flow`], so a `?` anywhere in the actor unwinds straight to `run`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Stop {
    /// Another holder generation owns the call now. The actor stops at once
    /// and writes nothing more.
    Lost,
    /// The document is in a state this actor cannot act on: the store refused
    /// a transition, or answered with a permanent error (`Invalid`,
    /// `UnsupportedFormat`). `run` then parks the actor.
    Invalid,
    /// The process is draining and the actor reached a safe point. The call
    /// stays held; the partition's next owner takes it over.
    Yielded,
}

/// Result of an actor step: `Ok` to go on, `Err(Stop)` to stop the actor.
pub(crate) type Flow<T = ()> = Result<T, Stop>;

/// A store error that no retry can cure.
pub(crate) fn permanent(error: &bcs_service_api::port::repo::distributed::common::StoreError) -> bool {
    use bcs_service_api::port::repo::distributed::common::StoreError;
    matches!(error, StoreError::Invalid { .. } | StoreError::UnsupportedFormat { .. })
}

/// The actor of one held call for one holder generation. All its state is
/// in memory and private to the actor's task; what must survive a crash is
/// in the `disp` document (phase, remote task, confirmed inputs, cancel
/// observation), so a later holder can rebuild from there.
pub(crate) struct Call {
    pub ports: ConnectorPorts,
    pub config: Arc<ConnectorConfig>,
    /// The handle on the claimed dispatch: proves ownership, fences every
    /// `disp` read and write, and keeps the last view read.
    pub held: Box<dyn HeldDispatchPort>,
    /// What the engine sees of this actor.
    pub status: Arc<CallStatus>,
    /// Signalled by the engine to make the actor tick at once (a poke).
    pub poke: Arc<Notify>,
    /// Set once by a drain, shared by every actor of the process: stop at
    /// the next safe point.
    pub yielding: Arc<AtomicBool>,
    /// Holder epoch and last seq of the events reported by this actor; seq
    /// restarts at 1 for every new holder generation (B7).
    pub cursor: ReportCursor,
    /// The open stream of agent events, if any. Set when a start or a reply
    /// returned a stream (`send.rs::accepted`, `control.rs::send_input`) or by
    /// a re-subscribe (`send.rs::reattach`); cleared when the stream fails or
    /// ends, when the call finishes, and in `park`. `None` = the actor runs on
    /// ticks and pokes only.
    pub events: Option<RemoteEventStream>,
    /// The event stream broke or was never opened: re-attach on the next tick.
    pub reattach_due: bool,
    /// Follow the remote task by polling instead of a stream (the agent can
    /// be queried by task but not subscribed to).
    pub polling: bool,
    /// The task state and snapshot reported last from a query, so an
    /// unchanged read is not reported again on every tick.
    pub last_polled: Option<(TaskStatus, Payload)>,
    /// Reads of a task whose cancel answered "already finished" that did not
    /// give its terminal state.
    pub finished_reads: u32,
    /// The agent asked a question and its stream ended: wait for a reply.
    pub awaiting_input: bool,
    /// A human reply could not be delivered or its delivery is unknown:
    /// later inputs wait behind it. The call is still followed and a cancel
    /// is still carried out. Never cleared by this actor; only a new holder
    /// generation tries that input again.
    pub input_blocked: bool,
    /// Consecutive definite "not sent" answers for the current input; above
    /// `not_sent_retries` the input is blocked.
    pub input_not_sent: u32,
    /// Resends of an idempotent input whose outcome stayed unknown.
    pub input_resends: u32,
    /// The tick backoff: a repeated remote action (cancel retry, idempotent
    /// input resend, read of an already-finished task) is not attempted
    /// before this time. Unlike [`Backoff`] it never sleeps; a tick that finds
    /// it in the future simply skips that action. One value serves all three.
    pub retry_after: Option<Instant>,
    /// The current tick-backoff step: zero, then `backoff_min` doubling up to
    /// `backoff_max`. With ticks of about 1.5 s the first steps have already
    /// expired by the next tick.
    pub retry_backoff: std::time::Duration,
    /// The START's outcome is unknown (`Uncertain`) or it was never sent
    /// (`NotSent`), and nothing more can be tried: nothing is done with the
    /// agent until the Worker acts, and then only a cancel is carried out.
    /// Also set when the call was accepted but no remote task id is stored.
    /// A failed reply uses `input_blocked` instead.
    pub unresolved: Option<UnresolvedKind>,
    /// The remote task is known but the agent offers no way to follow it.
    /// Replies and cancels are still carried out.
    pub blind: bool,
    /// A key event conflicted with one admitted earlier: the call is not
    /// ended on it and is no longer followed.
    pub conflicted: bool,
    /// Ticks since a waiting call last looked at its remote task.
    pub waiting_ticks: u32,
    /// The call was finished in `disp` by this actor: `follow` returns.
    pub ended: bool,
}

/// Runs a holder write until it is applied. A lost holder and an impossible
/// transition stop the actor; a store error is retried with backoff.
///
/// Evaluates to the write's `Ok` value. On `Lost` / a refused transition /
/// a permanent store error it executes `return Err(Stop::…)` in the
/// *calling* function, which is why it is a macro and not a function. A
/// temporary store error sleeps ([`Backoff`]) and retries in place: nothing
/// else of this call happens until the write lands, because the next step
/// (e.g. the network send after `begin_send`) depends on it.
macro_rules! held_write {
    ($call:expr, $what:literal, $op:expr) => {{
        let mut backoff = $crate::config::Backoff::new(&$call.config);
        loop {
            match $op.await {
                Ok(value) => break value,
                Err(bcs_service_api::port::repo::distributed::dispatch::HolderError::Lost) => {
                    return Err($crate::call::Stop::Lost);
                }
                Err(bcs_service_api::port::repo::distributed::dispatch::HolderError::InvalidTransition { code }) => {
                    tracing::error!(did = %$call.did().0, code, concat!($what, " refused; leaving the call as it is"));
                    return Err($crate::call::Stop::Invalid);
                }
                Err(bcs_service_api::port::repo::distributed::dispatch::HolderError::Store(error)) => {
                    if $crate::call::permanent(&error) {
                        tracing::error!(did = %$call.did().0, ?error, concat!($what, " cannot succeed; leaving the call as it is"));
                        return Err($crate::call::Stop::Invalid);
                    }
                    tracing::warn!(did = %$call.did().0, ?error, concat!($what, " failed; retrying"));
                    backoff.wait().await;
                }
            }
        }
    }};
}
pub(crate) use held_write;

impl Call {
    pub fn new(
        ports: ConnectorPorts,
        config: Arc<ConnectorConfig>,
        held: Box<dyn HeldDispatchPort>,
        status: Arc<CallStatus>,
        poke: Arc<Notify>,
        yielding: Arc<AtomicBool>,
    ) -> Self {
        // A new holder generation reports from sequence 1.
        let cursor = ReportCursor {
            holder_epoch: held.holder().epoch,
            last_seq: InputSeq(0),
        };
        Self {
            ports,
            config,
            held,
            status,
            poke,
            yielding,
            cursor,
            events: None,
            reattach_due: false,
            polling: false,
            last_polled: None,
            finished_reads: 0,
            awaiting_input: false,
            input_blocked: false,
            input_not_sent: 0,
            input_resends: 0,
            retry_after: None,
            retry_backoff: std::time::Duration::ZERO,
            unresolved: None,
            blind: false,
            conflicted: false,
            waiting_ticks: 0,
            ended: false,
        }
    }

    pub fn did(&self) -> &Did {
        &self.held.view().spec.did
    }

    pub fn spec(&self) -> &DispatchSpec {
        &self.held.view().spec
    }

    pub fn backoff(&self) -> Backoff {
        Backoff::new(&self.config)
    }

    /// Whether a repeated remote attempt (cancel, input resend, finished-task
    /// read) must still wait (tick backoff).
    pub fn backing_off(&self) -> bool {
        self.retry_after.is_some_and(|at| Instant::now() < at)
    }

    /// Schedules the next repeated attempt, doubling the wait up to the limit.
    pub fn back_off(&mut self) {
        self.retry_backoff = if self.retry_backoff.is_zero() {
            self.config.backoff_min
        } else {
            self.retry_backoff.saturating_mul(2).min(self.config.backoff_max)
        };
        self.retry_after = Some(Instant::now() + self.retry_backoff);
    }

    pub fn reset_backoff(&mut self) {
        self.retry_after = None;
        self.retry_backoff = std::time::Duration::ZERO;
    }

    /// The whole life of the actor; the engine spawns it as one task and it
    /// runs once. `pending_effect` is cleared before `done` is set, both
    /// `SeqCst`, so whoever sees `done == true` also sees the cleared flag:
    /// a finished actor never looks unsettled.
    pub async fn run(mut self) {
        let did = self.did().clone();
        match self.drive().await {
            Ok(()) => tracing::debug!(did = %did.0, "call ended"),
            Err(Stop::Lost) => tracing::info!(did = %did.0, "holder lost; actor stopped"),
            Err(Stop::Yielded) => tracing::info!(did = %did.0, "draining; stopped at a safe point"),
            Err(Stop::Invalid) => {
                tracing::error!(did = %did.0, "unexpected dispatch state; the call stays held and visible");
                self.park().await;
            }
        }
        self.status.pending_effect.store(false, Ordering::SeqCst);
        self.status.done.store(true, Ordering::SeqCst);
    }

    /// A call this actor cannot act on any more: it stays held and counted as
    /// a pending effect until another holder takes it or it is ended.
    ///
    /// Parking re-reads `disp` every poll interval and returns only on `Lost`
    /// or phase `ended`. It does not carry out cancels; a poke only re-reads early.
    /// A drain ends it at once (parking is a safe point): the call stays
    /// held for the partition's next owner.
    async fn park(&mut self) {
        self.events = None;
        self.status.pending_effect.store(true, Ordering::SeqCst);
        let interval = self.config.poll_interval_of(self.did());
        loop {
            if self.yielding.load(Ordering::SeqCst) {
                return;
            }
            let poke = self.poke.clone();
            tokio::select! {
                () = tokio::time::sleep(interval) => {}
                // Only a drain's poke matters here; others re-read early.
                () = poke.notified() => {}
            }
            if self.yielding.load(Ordering::SeqCst) {
                return;
            }
            match self.held.refresh().await {
                Err(HolderError::Lost) => return,
                Ok(view) if view.execution.phase == DispatchPhase::Ended => return,
                _ => {}
            }
        }
    }

    /// The first step, chosen by the stored phase (B15), then `follow`.
    /// `start` and `reattach` return `Ok` also when they leave the call
    /// unresolved or blind; `follow` then keeps watching for a cancel.
    async fn drive(&mut self) -> Flow {
        match self.ports.rules.resume_step(self.held.view()) {
            ResumeStep::Nothing => return Ok(()),
            ResumeStep::EndCanceledUnsent => return self.end_canceled_unsent().await,
            ResumeStep::Send => self.start(false).await?,
            ResumeStep::ResolveStart => self.start(true).await?,
            ResumeStep::Reattach => self.reattach().await?,
        }
        self.follow().await
    }

    /// The select loop of a call that was sent: remote events, the poll tick
    /// and pokes from re-notifications, until the call ends or is lost.
    ///
    /// `select!` waits on three branches and runs the first that is ready;
    /// the other two futures are dropped. Dropping them loses nothing: the
    /// tick deadline is kept in `next_tick`, an unconsumed poke permit stays
    /// in the `Notify`, and a stream's `next()` takes no item unless it
    /// completes. A poke tick does not move `next_tick`.
    ///
    /// The top of the loop is the safe point: a draining process stops the
    /// actor there unless an event stream is open (the agent is mid-turn; the
    /// stream usually ends when the turn does).
    async fn follow(&mut self) -> Flow {
        let interval = self.config.poll_interval_of(self.did());
        let mut next_tick = Instant::now() + interval;
        while !self.ended {
            if self.yielding.load(Ordering::SeqCst) && self.events.is_none() {
                return Err(Stop::Yielded);
            }
            let poke = self.poke.clone();
            tokio::select! {
                event = next_event(&mut self.events) => match event {
                    Some(Ok(event)) => self.on_event(event.kind, event.event_id, event.payload).await?,
                    // A broken or finished stream is not an agent result.
                    // While a question is open the stream normally ends; the
                    // tick keeps observing the task instead.
                    Some(Err(_)) | None => {
                        self.events = None;
                        self.reattach_due = !self.awaiting_input;
                    }
                },
                _ = tokio::time::sleep_until(next_tick) => {
                    self.tick().await?;
                    next_tick = Instant::now() + interval;
                }
                _ = poke.notified() => self.tick().await?,
            }
        }
        Ok(())
    }

    /// One look at the dispatch document: replies and cancels the Worker
    /// wrote, and whatever is due on the remote side.
    ///
    /// Steps, in order:
    /// 1. Fenced re-read of `disp`. `Lost` stops the actor; another error
    ///    skips this tick.
    /// 2. Unresolved start: only watch for a cancel ([`Call::tick_unresolved`]).
    /// 3. A cancel in `/control/cancel`: carry it out (may end the call).
    /// 4. Follow the remote side, unless blind or conflicted: re-attach if
    ///    due, poll if polling, observe while waiting for a reply.
    /// 5. Send the next human reply, unless an input is blocked.
    /// 6. Publish `pending_effect` from what this tick wrote.
    async fn tick(&mut self) -> Flow {
        let view = match self.held.refresh().await {
            Ok(view) => view,
            Err(HolderError::Lost) => return Err(Stop::Lost),
            Err(error) => {
                tracing::warn!(did = %self.did().0, ?error, "dispatch poll failed; next tick");
                return Ok(());
            }
        };
        if let Some(kind) = self.unresolved {
            return self.tick_unresolved(kind, &view).await;
        }
        if view.control.cancel.is_some() {
            self.carry_out_cancel(&view).await?;
        }
        if self.ended {
            return Ok(());
        }
        if !self.blind && !self.conflicted {
            if self.reattach_due && !self.awaiting_input {
                self.reattach().await?;
            }
            if self.polling && !self.ended {
                self.poll_remote().await?;
            }
            if self.awaiting_input && self.events.is_none() && !self.polling && !self.ended {
                self.observe_while_waiting().await?;
            }
        }
        if !self.ended && !self.input_blocked && self.unresolved.is_none() {
            if let Some(input) = self.ports.rules.next_input(&view).cloned() {
                self.send_input(input).await?;
            }
        }
        // The flag reflects what this tick wrote, not what it read.
        let now = self.held.view().clone();
        self.publish_status(&now);
        Ok(())
    }

    /// A call waiting on a question has no stream, but the agent may move on
    /// without a reply through the Connector (out-of-band authorization, an
    /// expired or canceled task, an input whose delivery is unknown). Keep
    /// looking: by task query when the agent has one, otherwise by a
    /// re-subscribe every few ticks.
    async fn observe_while_waiting(&mut self) -> Flow {
        const RESUBSCRIBE_EVERY: u32 = 8;
        if self.spec().capabilities.query_by_task {
            return self.poll_remote().await;
        }
        self.waiting_ticks += 1;
        if self.waiting_ticks >= RESUBSCRIBE_EVERY && self.spec().capabilities.subscribe {
            self.waiting_ticks = 0;
            self.reattach().await?;
        }
        Ok(())
    }

    /// An unresolved call stays in its phase and is never finished on a
    /// guess; it is still polled so a cancel is seen (B17).
    ///
    /// `NotSent`: nothing reached the agent, so a cancel ends the call as
    /// canceled. `Uncertain`: the agent may run a task this actor does not
    /// know the id of, so no cancel request can be addressed to it; the
    /// actor only records the cancel observation `OutcomeUnknown` (read as
    /// "cannot tell whether the call is canceled") so `disp` shows the cancel
    /// was seen and not carried out. No cancel request is sent.
    async fn tick_unresolved(&mut self, kind: UnresolvedKind, view: &DispatchView) -> Flow {
        if view.control.cancel.is_none() {
            return Ok(());
        }
        match kind {
            // Nothing reached the agent: the cancel simply ends the call.
            UnresolvedKind::NotSent => self.end_canceled_unsent().await,
            UnresolvedKind::Uncertain => {
                if view.execution.cancellation != CancelObservation::OutcomeUnknown {
                    held_write!(self, "cancel observation", self.held.record_cancel(CancelObservation::OutcomeUnknown));
                }
                Ok(())
            }
        }
    }

    /// The remote task is known but cannot be followed: report it (D2) and
    /// keep serving replies and cancels. The call stays in `accepted` and
    /// counts as a pending effect; the tick no longer re-attaches or polls.
    pub async fn mark_blind(&mut self, code: &str) {
        self.blind = true;
        self.polling = false;
        self.status.pending_effect.store(true, Ordering::SeqCst);
        self.report_unresolved(UnresolvedKind::Uncertain, code).await;
    }

    /// The call cannot be resolved here: report it (D2), keep it held. The
    /// D2 report is a stub today, so the Worker is not told; `disp` keeps the
    /// truth and the Worker's deadline becomes a cancel.
    pub async fn mark_unresolved(&mut self, kind: UnresolvedKind, code: &str) {
        self.unresolved = Some(kind);
        self.status.pending_effect.store(true, Ordering::SeqCst);
        self.report_unresolved(kind, code).await;
    }

    /// Calls the D2 stub and logs its (expected) failure with `code`.
    pub async fn report_unresolved(&mut self, kind: UnresolvedKind, code: &str) {
        if let Err(error) = delayed::report_unresolved(self.did(), kind).await {
            tracing::warn!(
                did = %self.did().0, ?kind, code, ?error,
                "call unresolved; it stays in its phase until the Worker acts"
            );
        }
    }

    /// Sets `pending_effect` from the actor's flags and from `view`: a stuck
    /// call (unresolved, blocked input, blind, conflicted) or a cancel that is
    /// open and not settled (not sent, being sent, outcome unknown).
    fn publish_status(&self, view: &DispatchView) {
        let cancel_open = view.control.cancel.is_some()
            && matches!(
                view.execution.cancellation,
                CancelObservation::NotSent | CancelObservation::Sending | CancelObservation::OutcomeUnknown
            );
        let stuck = self.unresolved.is_some() || self.input_blocked || self.blind || self.conflicted;
        let pending = stuck || (cancel_open && !self.ended);
        self.status.pending_effect.store(pending, Ordering::SeqCst);
    }
}

/// The next remote event, or never when no stream is open.
///
/// With no stream it awaits `std::future::pending()`, a future that never
/// completes, so this `select!` branch simply never fires and the loop runs
/// on ticks and pokes only. `None` = the stream ended.
async fn next_event(
    events: &mut Option<RemoteEventStream>,
) -> Option<Result<bcs_service_api::port::dist_agent::RemoteEvent, bcs_service_api::port::dist_agent::AgentReadError>> {
    match events {
        Some(stream) => stream.next().await,
        None => std::future::pending().await,
    }
}
