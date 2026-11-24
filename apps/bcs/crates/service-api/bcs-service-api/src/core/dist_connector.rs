//! Pure Connector rules: how one downstream call is run and reported.
//!
//! The Connector engine does the IO; this contract fixes the decisions, so the
//! Worker can rely on them. Order of one call, asserted by the engine's tests:
//!
//! ```text
//! receive → claim → ack
//!   → begin_send(Start) → start → record_acceptance
//!   → report events (append_stream | admit)
//!   → on poll: begin_send(Input) → send_input → confirm_input
//!              cancel intent → record_cancel(Sending) → cancel → record_cancel(observed)
//!   → admit final/error → finish
//! ```
//!
//! Reporting rules:
//! - every reported event carries `(did, holder_epoch, seq)`; `seq` starts at 1
//!   after each claim and rises by one per reported event;
//! - key events (input required, auth required, final, error) get an input id
//!   that excludes the holder epoch and needs no remote event id: one final and
//!   one error per call; input/auth required once per question round, where the
//!   round is `confirmed_input_through + 1`;
//! - the business digest of a key event covers its identity and terminal state,
//!   not its payload: a later holder re-reads the remote task, whose bytes
//!   differ from the streamed event, and the first admitted report wins;
//! - a remote `canceled` or `rejected` state is an `Error` event with the state
//!   in the payload (`{"state": "canceled" | "rejected" | "failed", ...}`).
use crate::port::repo::distributed::inbox::{AppendAgentEvent, ConnectorAdmission};
use bcs_dist_model::*;

/// Transition code a held-dispatch provider answers `begin_send(Input)` with
/// when a former holder generation already recorded send intent for that
/// input: the intent is durable, but whether the agent received it is unknown.
/// The holder then applies `uncertain_input` instead of sending blindly.
pub const INPUT_MAYBE_SENT: &str = "input_maybe_sent";

/// Report position of one held call. A new cursor is made after every claim.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReportCursor {
    pub holder_epoch: HolderEpoch,
    /// Sequence of the last reported event; zero before the first one.
    pub last_seq: InputSeq,
}

/// What a holder does first with the call it holds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResumeStep {
    /// Created or claimed, no cancel: persist send intent, then start.
    Send,
    /// Created or claimed with a cancel intent: nothing was sent.
    EndCanceledUnsent,
    /// Sending: the start may have reached the agent.
    ResolveStart,
    /// Accepted: the remote task is known.
    Reattach,
    /// Ended.
    Nothing,
}

/// How to resolve a send whose outcome is unknown. No blind resend.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UncertainSend {
    /// The agent declares the send idempotent: send again with the same request id.
    Resend,
    /// The agent can be asked by request id.
    AskAgent,
    /// Neither: the call stays in its phase and is supervised.
    Unresolved,
}

/// How to follow a remote task that is already accepted.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reattach {
    Subscribe { query_first: bool },
    Poll,
    Unresolved,
}

/// One normalized agent event, ready for the session inbox.
#[derive(Debug, Clone, PartialEq)]
pub enum Report {
    /// Intermediate event: appended, filtered by `(did, holder_epoch, seq)`.
    Stream(AppendAgentEvent),
    /// Key event: admitted under a stable input id.
    Key(ConnectorAdmission),
}

/// Deterministic, no IO, clock reads or random IDs.
pub trait DistConnectorCoreService: Send + Sync {
    fn resume_step(&self, view: &DispatchView) -> ResumeStep;
    /// Stable for every attempt and every holder; the agent's message id.
    fn start_request_id(&self, did: &Did) -> RequestId;
    fn input_request_id(&self, did: &Did, seq: InputSeq) -> RequestId;
    fn uncertain_start(&self, capabilities: &DownstreamCapabilities) -> UncertainSend;
    fn uncertain_input(&self, capabilities: &DownstreamCapabilities) -> UncertainSend;
    fn reattach(&self, capabilities: &DownstreamCapabilities) -> Reattach;
    /// Advances the cursor by one and builds the report. For key events the
    /// interaction of `kind` is replaced by the derived one.
    fn report(
        &self,
        view: &DispatchView,
        cursor: &mut ReportCursor,
        kind: AgentEventKind,
        remote_event_id: Option<String>,
        payload: Payload,
    ) -> Report;
    /// The definitive end a key event implies: final → completed; error →
    /// canceled when the payload state is `canceled`, otherwise failed.
    fn end_of(&self, kind: &AgentEventKind, payload: &Payload) -> Option<FinalDispatchOutcome>;
    /// Lowest pending input above the confirmed watermark, if it is the next one.
    fn next_input<'a>(&self, view: &'a DispatchView) -> Option<&'a PendingInput>;
}
