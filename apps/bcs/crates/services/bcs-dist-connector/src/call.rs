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
//!                    └─ on Stop::Invalid ──▶ park (stay held, re-read until lost/ended)
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
//! - **two backoffs**: [`Backoff`] sleeps and retries in place (a store
//!   write the actor must complete before anything else, see `held_write!`);
//!   the tick backoff (`retry_after`) only says "not before time X" for a
//!   repeated remote action, so the actor keeps working meanwhile.

use crate::config::{Backoff, ConnectorConfig, ConnectorPorts};
use crate::delayed::{self, UnresolvedKind};
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::{ReportCursor, ResumeStep};
use bcs_service_api::port::dist_agent::{CallAuthority, CallOperation, QueryOutcome, RemoteEventStream, RemoteQuery};
use crate::send::RemoteRead;
use bcs_service_api::port::repo::distributed::dispatch::{
    HeldDispatchPort, HolderError, VerificationHandoffDraft,
};
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
    /// The tick backoff: a repeated remote action (cancel retry, verification
    /// query, or read of an already-finished task) is not attempted before
    /// this time. Unlike [`Backoff`] it never sleeps; the tick skips it.
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
    /// A Completed stop fact is recorded but the product result is not
    /// verified complete yet: it must be recovered through a task read
    /// before any Final may be admitted (plan T6).
    pub completed_stop_seen: bool,
    /// The recovery task read answered with a definite absence: asking
    /// again cannot help, so the stop fact waits for the deadline close.
    pub recovery_gone: bool,
    /// Verification mode (plan T7): this actor holds a restricted handle
    /// fenced by the independent verification claim epoch — approved query
    /// and cancel, persistent reports, quarantine at the deadline, finish on
    /// verified stop evidence — and never a start, resend or continuation.
    pub verification: Option<VerificationHandoff>,
    /// The call was finished in `disp` by this actor: `follow` returns.
    pub ended: bool,
}

/// The durable reason recorded when the stop-verification deadline passed
/// with the outcome still unknown (plan T7).
pub(crate) const QUARANTINE_REASON: &str = "stop_verification_deadline";

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
            cursor,
            events: None,
            reattach_due: false,
            polling: false,
            last_polled: None,
            finished_reads: 0,
            awaiting_input: false,
            input_blocked: false,
            input_not_sent: 0,
            retry_after: None,
            retry_backoff: std::time::Duration::ZERO,
            unresolved: None,
            blind: false,
            conflicted: false,
            waiting_ticks: 0,
            completed_stop_seen: false,
            recovery_gone: false,
            verification: None,
            ended: false,
        }
    }

    /// Turns this actor into the verifier of the handed-off call it holds
    /// (plan T7). Called only by the engine after a bounded verification
    /// claim; the handle fences every write by the claim epoch.
    pub(crate) fn into_verifier(mut self) -> Self {
        self.verification = self
            .held
            .view()
            .execution
            .verification
            .clone();
        self
    }

    pub fn did(&self) -> &Did {
        &self.held.view().spec.did
    }

    /// The trusted authority of one downstream operation: the FSM round,
    /// node, attempt and binding of the dispatch this holder serves (plan
    /// T6). Built from the held dispatch only, never from client metadata.
    pub fn authority_now(
        &self,
        operation: CallOperation,
        remote: Option<RemoteTaskRef>,
        spec: &DispatchSpec,
    ) -> CallAuthority {
        CallAuthority {
            round: crate::identity::round_of(&spec.did).unwrap_or_default(),
            node: spec.node.clone(),
            attempt: spec.attempt,
            binding: spec.target.clone(),
            operation,
            remote_ref: remote,
        }
    }

    /// Whether a remote task reference is the one this call accepted. A
    /// `None` known reference accepts the first task an answer binds (a
    /// start resolution); after acceptance, evidence of any other task is
    /// fenced out.
    pub fn owns(&self, remote: &RemoteTaskRef, known: &RemoteTaskRef) -> bool {
        remote.task_id == known.task_id
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
    /// or phase `ended`. It does not carry out cancels and ignores pokes.
    /// Another process can take the call over only once membership proves
    /// this one dead, so a parked call usually stays parked until `drain`
    /// stops it (or the process exits).
    async fn park(&mut self) {
        self.events = None;
        self.status.pending_effect.store(true, Ordering::SeqCst);
        let interval = self.config.poll_interval_of(self.did());
        loop {
            tokio::time::sleep(interval).await;
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
        if self.verification.is_some() {
            return self.verify_follow().await;
        }
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
    async fn follow(&mut self) -> Flow {
        let interval = self.config.poll_interval_of(self.did());
        let mut next_tick = Instant::now() + interval;
        while !self.ended {
            let poke = self.poke.clone();
            tokio::select! {
                event = next_event(&mut self.events) => match event {
                    Some(Ok(event)) => {
                        let remote = event.remote.clone();
                        self.on_event(event.kind, Some(remote), event.event_id, event.payload).await?;
                    }
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
        // A Completed stop fact stands but its product is not verified:
        // the complete result is recovered through the task read — the
        // only evidence channel for a product Final (plan T6) — until the
        // dispatch deadline bounds the effort with a clean, evidence-
        // preserving failure.
        if self.completed_stop_seen && !self.ended && self.unresolved.is_none() {
            if self.spec().capabilities.query_by_task && !self.recovery_gone {
                // A definite absence stops the recovery reads: the stop fact
                // stays durable and the deadline bounds the open obligation.
                if self.read_remote().await? == RemoteRead::Gone {
                    self.recovery_gone = true;
                }
            }
            if !self.ended && self.config.clock.now() >= self.spec().deadline.0 {
                tracing::warn!(did = %self.did().0, "result recovery deadline exhausted");
                return self
                    .end_with_error("failed", "result_incomplete", FinalDispatchOutcome::Failed, "result_incomplete")
                    .await;
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

    /// The verifier's loop (plan T7): a low-frequency bounded check of a
    /// handed-off call, waken early by pokes. Ends when the call finished,
    /// the claim was superseded (Lost), or the state became unactionable.
    async fn verify_follow(&mut self) -> Flow {
        while !self.ended {
            let floor = self
                .config
                .verification_interval
                .max(self.config.verification_backoff_min);
            let next_tick = Instant::now() + self.retry_backoff.max(floor);
            let poke = self.poke.clone();
            tokio::select! {
                _ = tokio::time::sleep_until(next_tick) => {
                    self.verify_tick().await?;
                }
                _ = poke.notified() => {
                    self.verify_tick().await?;
                }
            }
        }
        Ok(())
    }

    /// One look at a handed-off dispatch: the deadline may quarantine the
    /// call (durable, never terminal), an approved cancel is carried out,
    /// and the stable key is asked once when due.
    async fn verify_tick(&mut self) -> Flow {
        let view = match self.held.refresh().await {
            Ok(view) => view,
            // A later verifier owns the epoch: this actor is done.
            Err(HolderError::Lost) => return Err(Stop::Lost),
            Err(error) => {
                tracing::warn!(did = %self.did().0, ?error, "verification poll failed; next tick");
                return Ok(());
            }
        };
        self.publish_status(&view);
        // The call was ended by someone else (a worker close, an approved
        // cancel seen by another verifier): nothing left to verify.
        let Some(handoff) = view.execution.verification.clone() else {
            self.ended = true;
            return Ok(());
        };
        let now = self.config.clock.now();
        // 超过停止核实期限：保存独立 Quarantined，不越过终态门槛。
        if now >= self.spec().deadline.0 && view.execution.quarantined.is_none() {
            held_write!(
                self,
                "quarantine",
                self.held.record_quarantine(TimestampMillis(now), QUARANTINE_REASON)
            );
        }
        // An approved cancel is still carried out: request/cancel are the
        // only actions this mode grants.
        if view.control.cancel.is_some() {
            if view.execution.remote.is_some() {
                self.carry_out_cancel(&view).await?;
            } else if view.execution.cancellation
                == CancelObservation::NotSent
            {
                held_write!(
                    self,
                    "cancel observation",
                    self.held.record_cancel(CancelObservation::OutcomeUnknown)
                );
            }
        }
        if self.ended {
            return Ok(());
        }
        // The query cadence lives in this actor's retry backoff: the index
        // entry keeps Sitzfleisch in the queue for crash recovery, while the
        // running verifier owns the pacing (bounded doubling, ceiling).
        if self.backing_off() {
            return Ok(());
        }
        // Index repair before each attempt: a crash between the claim and the
        // index entry never loses the obligation.
        let re_due = TimestampMillis(now + self.config.verification_backoff_min.as_millis().min(i64::MAX as u128) as i64);
        if let Err(error) = self.ports.queue.index_verification(self.did(), re_due).await {
            tracing::warn!(did = %self.did().0, ?error, "verification index re-arm failed");
        }
        self.back_off();
        match &handoff.key {
            VerificationKey::ByRequest { request_id } => {
                let request_id = request_id.clone();
                return self
                    .verify_by_request(request_id, handoff.method)
                    .await;
            }
            VerificationKey::ByTask { .. } => {
                match handoff.method {
                    VerificationMethod::Query => {
                        if self.spec().capabilities.query_by_task {
                            self.read_remote().await?;
                        } else {
                            tracing::warn!(
                                did = %self.did().0,
                                "by-task verification without the query capability; obligation kept"
                            );
                        }
                    }
                    _ => tracing::warn!(
                        did = %self.did().0,
                        "verification without an approved method; obligation kept"
                    ),
                }
                Ok(())
            }
            VerificationKey::Retained => {
                // 无能力或权限则保留并告警：never fabricated, never dropped.
                tracing::warn!(
                    did = %self.did().0,
                    "handed-off call has no stable query key; obligation retained"
                );
                Ok(())
            }
        }
    }

    /// Asks by the stable request key — the T6-requested extension profile —
    /// and reports what it finds. A foreign task is never evidence; a found
    /// task is recorded, its state reported, and verified stop evidence
    /// finishes the call through the ordinary report path.
    async fn verify_by_request(
        &mut self,
        request_id: RequestId,
        method: VerificationMethod,
    ) -> Flow {
        if method != VerificationMethod::Query {
            tracing::warn!(
                did = %self.did().0,
                "by-request verification without the approved query method; obligation kept"
            );
            return Ok(());
        }
        let authority = self.authority_now(
            CallOperation::Query,
            self.held.view().execution.remote.clone(),
            self.spec(),
        );
        match self
            .ports
            .agent
            .query(&authority, RemoteQuery::ByRequest(request_id.clone()))
            .await
        {
            QueryOutcome::Found { remote, status, snapshot } => {
                let known = self.held.view().execution.remote.clone();
                if let Some(known) = &known {
                    if !self.owns(&remote, known) {
                        tracing::warn!(
                            did = %self.did().0, task = %remote.task_id,
                            "by-request answer of a foreign task ignored"
                        );
                        return Ok(());
                    }
                }
                // The original task binds under the verification claim
                // fence when no earlier acceptance did.
                if known.is_none() {
                    let evidence = bcs_service_api::port::repo::distributed::dispatch::AcceptanceEvidence {
                        operation: bcs_service_api::port::repo::distributed::dispatch::SendOperation::Start {
                            request_id: request_id.clone(),
                        },
                        remote: remote.clone(),
                    };
                    held_write!(self, "acceptance", self.held.record_acceptance(evidence.clone()));
                }
                self.report_task_state(status, snapshot).await
            }
            // NotFound stays unknown: never a licence to resend (plan T6).
            QueryOutcome::NotFound => {
                tracing::debug!(did = %self.did().0, "request index still has no entry");
                Ok(())
            }
            QueryOutcome::Unavailable { code } => {
                tracing::debug!(did = %self.did().0, code, "by-request verification unavailable");
                Ok(())
            }
            QueryOutcome::Unsupported => {
                // The standard profile: the obligation is kept and warned.
                tracing::warn!(
                    did = %self.did().0,
                    "by-request lookup unsupported by this profile; obligation retained"
                );
                Ok(())
            }
        }
    }

    /// Hands this call off to the verification lane (plan T7): the one
    /// claim-fenced CAS registers the mode, the attempt, the stable query
    /// key, the approved method, the next due time, the backoff and the
    /// pending scheduling obligation; the read-back confirmed, the actor
    /// exits and frees its normal slot. No unsupported lookup is armed as
    /// a Query, even when a different verification capability was declared.
    pub(crate) async fn hand_off(&mut self, code: &str, key: VerificationKey) -> Flow {
        let capabilities = &self.spec().capabilities;
        let method = match &key {
            VerificationKey::ByRequest { .. } if capabilities.query_by_request => VerificationMethod::Query,
            VerificationKey::ByTask { .. } if !self.blind && capabilities.query_by_task => VerificationMethod::Query,
            VerificationKey::ByTask { .. } if capabilities.cancel => VerificationMethod::CancelOnly,
            _ => VerificationMethod::Retain,
        };
        let now = self.config.clock.now();
        let backoff = self.config.verification_backoff_min;
        let draft = VerificationHandoffDraft {
            attempt: self.spec().attempt,
            key,
            method,
            next_due: TimestampMillis(now + backoff.as_millis().min(i64::MAX as u128) as i64),
            backoff_ms: backoff.as_millis().min(u64::MAX as u128) as u64,
        };
        held_write!(self, "verification handoff", self.held.hand_off_verification(draft.clone()));
        // 停摆窗口补齐: the CAS is durable; the index is written after it,
        // and a crash in between is repaired by the Supervisor's scan of the
        // authoritative state (`due`), or by the next claim-side repair.
        if let Err(error) = self
            .ports
            .queue
            .index_verification(self.did(), draft.next_due)
            .await
        {
            tracing::warn!(
                did = %self.did().0, ?error,
                "verification index write failed; the Supervisor repair path owns the entry"
            );
        }
        tracing::warn!(
            did = %self.did().0, code,
            "uncertain call handed off to the verification lane"
        );
        // Only after the confirmation/read-back: exit the actor cleanly.
        self.ended = true;
        Ok(())
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
        let stuck = self.unresolved.is_some()
            || self.input_blocked
            || self.completed_stop_seen
            || self.blind
            || self.conflicted
            || self.verification.is_some();
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
