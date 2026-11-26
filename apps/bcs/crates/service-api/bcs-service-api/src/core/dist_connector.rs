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
//!
//! Glossary: a *holder* is the Connector instance plus holder epoch
//! ("generation") that claimed the dispatch; *start* is the call's first
//! message to the agent; an *input* is a later human reply (B12), numbered
//! `seq` in `/control/pending_input`; a *key event* is one the session must
//! see exactly once (final, error, input/auth required), an *intermediate
//! event* is everything else (deltas, thinking, tools, snapshots).
//! `DownstreamCapabilities` is written by the Worker in `/spec/capabilities`
//! and decides how an unknown send is resolved and how a task is followed.
use crate::port::repo::distributed::inbox::{AppendAgentEvent, ConnectorAdmission};
use bcs_dist_model::*;

/// Transition code a held-dispatch provider answers `begin_send(Input)` with
/// when a former holder generation already recorded send intent for that
/// input: the intent is durable, but whether the agent received it is unknown.
/// The holder then applies `uncertain_input` instead of sending blindly.
///
/// Answered only when the intent was recorded, is not this handle's own, and
/// the input is not confirmed yet. A confirmed input is refused differently
/// (as out of order), so a false "maybe sent" never reaches an input the
/// agent definitely accepted. Comes back as
/// `HolderError::InvalidTransition { code }` with this code: with
/// `uncertain_input` = `Resend` the actor sends again under the same request
/// id, otherwise it blocks the input as uncertain.
pub const INPUT_MAYBE_SENT: &str = "input_maybe_sent";

/// Report position of one held call. A new cursor is made after every claim.
///
/// So `seq` restarts at 1 for every holder generation (B7); `(did,
/// holder_epoch, seq)` stays unique because the epoch differs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReportCursor {
    pub holder_epoch: HolderEpoch,
    /// Sequence of the last reported event; zero before the first one.
    pub last_seq: InputSeq,
}

/// What a holder does first with the call it holds.
///
/// Decided from the stored phase alone (B15), so the first holder and every
/// later one (after a takeover or resume) take the same path. The actor runs
/// this step once, then follows the call.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResumeStep {
    /// Created or claimed, no cancel: persist send intent, then start.
    /// The actor moves the phase `claimed → sending` (`begin_send(Start)`)
    /// before it calls the agent (B3).
    Send,
    /// Created or claimed with a cancel intent: nothing was sent.
    /// The actor ends the call as canceled without contacting the agent.
    EndCanceledUnsent,
    /// Sending: the start may have reached the agent.
    /// The actor resolves it by `uncertain_start` (resend, ask the agent, or
    /// leave the call unresolved); it never resends blindly.
    ResolveStart,
    /// Accepted: the remote task is known.
    /// The actor follows the stored remote task again as `reattach` decides.
    Reattach,
    /// Ended.
    /// The actor has nothing to do and stops.
    Nothing,
}

/// How to resolve a send whose outcome is unknown. No blind resend.
///
/// "Unknown" = the request was handed to the transport but no answer says
/// whether the agent accepted it (it may have).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UncertainSend {
    /// The agent declares the send idempotent: send again with the same request id.
    /// The resends are bounded; when they run out the send is unresolved.
    Resend,
    /// The agent can be asked by request id.
    /// It is asked a few times, never sent to again; "not found" is no proof
    /// it was not received, so the call is then left unresolved.
    AskAgent,
    /// Neither: the call stays in its phase and is supervised.
    /// Start: the call is marked unresolved (B17). Input: the input is
    /// blocked; the call itself stays followed.
    Unresolved,
}

/// How to follow a remote task that is already accepted.
///
/// Used when the actor has no open event stream: after a takeover or resume,
/// or when a stream broke.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reattach {
    /// Open a new event stream on the remote task. With `query_first`, read
    /// the task's current state first, to report what the former holder may
    /// have missed; the stream has no replay position.
    Subscribe { query_first: bool },
    /// No stream: read the task's state on every tick.
    Poll,
    /// The task can be neither subscribed to nor read: the actor marks the
    /// call blind. Replies and cancels are still sent; events are not seen.
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
///
/// The Connector's decisions, kept apart from its IO so every holder decides
/// the same way and the rules can be tested alone. Implemented by
/// `bcs_dist_connector::rules::DistConnectorRules`.
pub trait DistConnectorCoreService: Send + Sync {
    /// The first step for a held call, by its phase: `created`/`claimed` →
    /// `Send` (or `EndCanceledUnsent` when a cancel is already written),
    /// `sending` → `ResolveStart`, `accepted` → `Reattach`, `ended` →
    /// `Nothing`.
    fn resume_step(&self, view: &DispatchView) -> ResumeStep;
    /// Stable for every attempt and every holder; the agent's message id.
    /// Example: `<did>:start`. Being stable is what makes a resend
    /// idempotent and an ask-by-request possible.
    fn start_request_id(&self, did: &Did) -> RequestId;
    /// Request id of the human reply `seq`, stable like the start's.
    /// Example: `<did>:in:<seq>`, e.g. `d-42:in:2`.
    fn input_request_id(&self, did: &Did, seq: InputSeq) -> RequestId;
    /// How to resolve a start that may have reached the agent:
    /// `idempotent_start` → `Resend`, else `query_by_request` → `AskAgent`,
    /// else `Unresolved`.
    fn uncertain_start(&self, capabilities: &DownstreamCapabilities) -> UncertainSend;
    /// How to resolve a reply that may have reached the agent:
    /// `idempotent_input` → `Resend`, else `Unresolved` (never `AskAgent`).
    fn uncertain_input(&self, capabilities: &DownstreamCapabilities) -> UncertainSend;
    /// How to follow an accepted task: `subscribe` → `Subscribe` (with
    /// `query_first` = `query_by_task`), else `query_by_task` → `Poll`, else
    /// `Unresolved`.
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
