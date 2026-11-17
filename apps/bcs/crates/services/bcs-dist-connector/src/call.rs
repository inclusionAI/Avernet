//! One held call: the actor of one holder generation.
//!
//! Order (the core contract): persist send intent → call the agent → record
//! acceptance → report events → on every poll forward replies and carry out
//! cancels → admit the terminal event → finish. A lost holder stops at once
//! and writes nothing more. The actor enforces no deadline: that is the
//! Worker's, and reaches the call as a cancel.

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

/// What the engine can see of a running actor.
#[derive(Default)]
pub(crate) struct CallStatus {
    /// The actor returned normally (ended, lost, or stopped on purpose).
    pub done: AtomicBool,
    /// Something is unsettled: an unresolved send or input, or a cancel whose
    /// outcome is not known yet.
    pub pending_effect: AtomicBool,
}

/// Why an actor stops before the call ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Stop {
    /// Another holder generation owns the call now.
    Lost,
    /// The document is in a state this actor cannot act on.
    Invalid,
}

pub(crate) type Flow<T = ()> = Result<T, Stop>;

pub(crate) struct Call {
    pub ports: ConnectorPorts,
    pub config: Arc<ConnectorConfig>,
    pub held: Box<dyn HeldDispatchPort>,
    pub status: Arc<CallStatus>,
    pub poke: Arc<Notify>,
    pub cursor: ReportCursor,
    pub events: Option<RemoteEventStream>,
    /// The event stream broke or was never opened: re-attach on the next tick.
    pub reattach_due: bool,
    /// Follow the remote task by polling instead of a stream.
    pub polling: bool,
    pub last_polled: Option<(TaskStatus, Payload)>,
    /// The agent asked a question and its stream ended: wait for a reply.
    pub awaiting_input: bool,
    /// An input could not be delivered or its delivery is unknown: later
    /// inputs wait behind it.
    pub input_blocked: bool,
    pub input_not_sent: u32,
    pub unresolved: Option<UnresolvedKind>,
    pub ended: bool,
}

/// Runs a holder write until it is applied. A lost holder and an impossible
/// transition stop the actor; a store error is retried with backoff.
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
            awaiting_input: false,
            input_blocked: false,
            input_not_sent: 0,
            unresolved: None,
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

    pub async fn run(mut self) {
        let did = self.did().clone();
        match self.drive().await {
            Ok(()) => tracing::debug!(did = %did.0, "call ended"),
            Err(Stop::Lost) => tracing::info!(did = %did.0, "holder lost; actor stopped"),
            Err(Stop::Invalid) => tracing::error!(did = %did.0, "actor stopped on an unexpected dispatch state"),
        }
        self.status.pending_effect.store(false, Ordering::SeqCst);
        self.status.done.store(true, Ordering::SeqCst);
    }

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
    async fn follow(&mut self) -> Flow {
        let interval = self.config.poll_interval_of(self.did());
        let mut next_tick = Instant::now() + interval;
        while !self.ended {
            let poke = self.poke.clone();
            tokio::select! {
                event = next_event(&mut self.events) => match event {
                    Some(Ok(event)) => self.on_event(event.kind, event.event_id, event.payload).await?,
                    // A broken or finished stream is not an agent result.
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
        if self.reattach_due && !self.awaiting_input {
            self.reattach().await?;
        }
        if self.polling && !self.ended {
            self.poll_remote().await?;
        }
        if !self.ended && !self.input_blocked && self.unresolved.is_none() {
            if let Some(input) = self.ports.rules.next_input(&view).cloned() {
                self.send_input(input).await?;
            }
        }
        self.publish_status(&view);
        Ok(())
    }

    /// An unresolved call stays in its phase and is never finished on a
    /// guess; it is still polled so a cancel is seen.
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

    /// The call cannot be resolved here: report it (D2), keep it held.
    pub async fn mark_unresolved(&mut self, kind: UnresolvedKind, code: &str) {
        self.unresolved = Some(kind);
        self.status.pending_effect.store(true, Ordering::SeqCst);
        self.report_unresolved(kind, code).await;
    }

    pub async fn report_unresolved(&mut self, kind: UnresolvedKind, code: &str) {
        if let Err(error) = delayed::report_unresolved(self.did(), kind).await {
            tracing::warn!(
                did = %self.did().0, ?kind, code, ?error,
                "call unresolved; it stays in its phase until the Worker acts"
            );
        }
    }

    fn publish_status(&self, view: &DispatchView) {
        let cancel_open = view.control.cancel.is_some()
            && matches!(
                view.execution.cancellation,
                CancelObservation::NotSent | CancelObservation::Sending | CancelObservation::OutcomeUnknown
            );
        let pending = self.unresolved.is_some() || self.input_blocked || (cancel_open && !self.ended);
        self.status.pending_effect.store(pending, Ordering::SeqCst);
    }
}

/// The next remote event, or never when no stream is open.
async fn next_event(
    events: &mut Option<RemoteEventStream>,
) -> Option<Result<bcs_service_api::port::dist_agent::RemoteEvent, bcs_service_api::port::dist_agent::AgentReadError>> {
    match events {
        Some(stream) => stream.next().await,
        None => std::future::pending().await,
    }
}
