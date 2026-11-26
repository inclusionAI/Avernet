//! The Connector's pure rules: the `DistConnectorCoreService` implementation.
//!
//! Every decision that does not need I/O lives here, so it can be tested
//! without a store or an agent and so every holder of a call decides the same
//! way from the same `disp` document. The actor (`call.rs` and friends) does
//! the I/O and asks these rules what to do.
//!
//! Glossary:
//! - **resume step**: what a holder does first with a call it just claimed,
//!   chosen by the stored phase (B15).
//! - **key event**: final, error, input-required, auth-required. Admitted to
//!   the inbox under a stable identity without holder epoch or seq (B8), so a
//!   later holder re-reporting it is deduplicated.
//! - **intermediate event**: delta, thinking, tool, snapshot. Appended with
//!   `(did, holder_epoch, seq)` (B7, B9); not deduplicated across holders.
//! - **round**: the question round of the call, `confirmed_input_through +
//!   1` (1 before any reply was confirmed). It is derived from the document,
//!   not counted in memory, so every holder computes the same round and the
//!   same question gets the same identity on every holder.
//! - **`state` word**: the `state` field of an inline payload, the remote
//!   A2A task state (`completed`, `failed`, `canceled`, `rejected`,
//!   `input_required`, `auth_required`). It is part of a key event's business
//!   digest and decides `Canceled` vs `Failed` (B10).

use crate::identity::{self, KeyEvent};
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::{
    DistConnectorCoreService, Reattach, Report, ReportCursor, ResumeStep, UncertainSend,
};
use bcs_service_api::port::repo::distributed::inbox::{
    AppendAgentEvent, ConnectorAdmission, ConnectorEventIdentity,
};

/// Schema version of the inputs the Connector writes.
const SCHEMA_VERSION: u32 = 1;

/// Stateless rules; one value serves every call.
#[derive(Debug, Default, Clone, Copy)]
pub struct DistConnectorRules;

impl DistConnectorRules {
    /// The question round a new input/auth-required event belongs to, and
    /// the `seq` of the next reply to send: replies through
    /// `confirmed_input_through` are with the agent, so the open question is
    /// the next one. Example: no reply confirmed yet → round 1; reply 1
    /// confirmed → round 2.
    fn round(view: &DispatchView) -> u64 {
        view.execution
            .confirmed_input_through
            .map_or(0, |seq| seq.0)
            .saturating_add(1)
    }
}

/// The `state` word of an inline payload, empty when absent or stored by reference.
///
/// Example: `{"state": "canceled", "code": "agent_canceled"}` → `canceled`.
/// Terminal and question events built from a task query always carry it
/// (see `with_state` in `send.rs`).
fn state_of(payload: &Payload) -> &str {
    match payload {
        Payload::Inline(value) => value.get("state").and_then(|s| s.as_str()).unwrap_or(""),
        Payload::Ref(_) => "",
    }
}

impl DistConnectorCoreService for DistConnectorRules {
    /// By phase (B15): `created`/`claimed` = nothing left the process, so send
    /// (or end canceled at once if a cancel is already there); `sending` = a
    /// former holder may have sent the start, so resolve it; `accepted` = the
    /// remote task is known, so re-attach to it; `ended` = nothing to do.
    fn resume_step(&self, view: &DispatchView) -> ResumeStep {
        match view.execution.phase {
            DispatchPhase::Created | DispatchPhase::Claimed => {
                if view.control.cancel.is_some() {
                    ResumeStep::EndCanceledUnsent
                } else {
                    ResumeStep::Send
                }
            }
            DispatchPhase::Sending => ResumeStep::ResolveStart,
            DispatchPhase::Accepted => ResumeStep::Reattach,
            DispatchPhase::Ended => ResumeStep::Nothing,
        }
    }

    fn start_request_id(&self, did: &Did) -> RequestId {
        identity::start_request_id(did)
    }

    fn input_request_id(&self, did: &Did, seq: InputSeq) -> RequestId {
        identity::input_request_id(did, seq)
    }

    /// The SENT policy for a start that may have reached the agent: resend
    /// only if the agent declares the start idempotent, else ask the agent by
    /// request id if it can answer that, else leave the call unresolved.
    fn uncertain_start(&self, capabilities: &DownstreamCapabilities) -> UncertainSend {
        if capabilities.idempotent_start {
            UncertainSend::Resend
        } else if capabilities.query_by_request {
            UncertainSend::AskAgent
        } else {
            UncertainSend::Unresolved
        }
    }

    /// For a reply that may have reached the agent: resend only if the agent
    /// declares inputs idempotent. There is no "ask by request id" for inputs.
    fn uncertain_input(&self, capabilities: &DownstreamCapabilities) -> UncertainSend {
        if capabilities.idempotent_input {
            UncertainSend::Resend
        } else {
            UncertainSend::Unresolved
        }
    }

    /// How to follow a remote task when no stream is open, from the
    /// capabilities the Worker wrote into `/spec/capabilities`: subscribe
    /// (reading the task first when it can be queried), else poll by task
    /// query, else the task cannot be followed (the actor goes blind).
    fn reattach(&self, capabilities: &DownstreamCapabilities) -> Reattach {
        if capabilities.subscribe {
            Reattach::Subscribe {
                query_first: capabilities.query_by_task,
            }
        } else if capabilities.query_by_task {
            Reattach::Poll
        } else {
            Reattach::Unresolved
        }
    }

    fn report(
        &self,
        view: &DispatchView,
        cursor: &mut ReportCursor,
        kind: AgentEventKind,
        remote_event_id: Option<String>,
        payload: Payload,
    ) -> Report {
        // Every reported event takes the next seq, key events included, so
        // seq counts all reports of this holder generation (B7).
        cursor.last_seq = InputSeq(cursor.last_seq.0.saturating_add(1));
        let did = &view.spec.did;
        let key = match &kind {
            AgentEventKind::Final => Some(KeyEvent::Final),
            AgentEventKind::Error => Some(KeyEvent::Error),
            AgentEventKind::InputRequired { .. } => Some(KeyEvent::InputRequired(Self::round(view))),
            AgentEventKind::AuthRequired { .. } => Some(KeyEvent::AuthRequired(Self::round(view))),
            AgentEventKind::Delta
            | AgentEventKind::Thinking
            | AgentEventKind::Tool
            | AgentEventKind::Snapshot => None,
        };
        // The agent's own interaction id (if any) is replaced by the derived
        // one, so every holder names the same question the same way.
        let kind = match (kind, key) {
            (AgentEventKind::InputRequired { .. }, Some(key)) => AgentEventKind::InputRequired {
                interaction: identity::interaction_id(did, key),
            },
            (AgentEventKind::AuthRequired { .. }, Some(key)) => AgentEventKind::AuthRequired {
                interaction: identity::interaction_id(did, key),
            },
            (kind, _) => kind,
        };
        let state = state_of(&payload).to_owned();
        let digest = key.map(|key| identity::key_digest(did, key, &state));
        let event = AgentEvent {
            did: did.clone(),
            holder_epoch: cursor.holder_epoch,
            seq: cursor.last_seq,
            kind,
            remote_event_id,
            payload,
        };
        match (key, digest) {
            (Some(key), Some(business_digest)) => Report::Key(ConnectorAdmission {
                input: InputEnvelope {
                    schema_version: SCHEMA_VERSION,
                    sid: view.spec.sid.clone(),
                    tid: view.spec.tid.clone(),
                    input_id: identity::key_input_id(did, key),
                    business_digest,
                    body: InputBody::Agent { event },
                },
                identity: ConnectorEventIdentity { round: key.round(), state },
            }),
            _ => Report::Stream(AppendAgentEvent {
                sid: view.spec.sid.clone(),
                tid: view.spec.tid.clone(),
                event,
            }),
        }
    }

    /// Whether an event ends the call, and how: a final event completes it;
    /// an error with state `canceled` cancels it; any other error fails it.
    fn end_of(&self, kind: &AgentEventKind, payload: &Payload) -> Option<FinalDispatchOutcome> {
        match kind {
            AgentEventKind::Final => Some(FinalDispatchOutcome::Completed),
            AgentEventKind::Error if state_of(payload) == "canceled" => {
                Some(FinalDispatchOutcome::Canceled)
            }
            AgentEventKind::Error => Some(FinalDispatchOutcome::Failed),
            _ => None,
        }
    }

    /// The reply to send next: the pending input whose `seq` is exactly the
    /// current round. Entries at or below `confirmed_input_through` are
    /// already with the agent (the Worker removes them later); if the next
    /// seq is missing, nothing is sent, so replies go out gap-free (B12).
    fn next_input<'a>(&self, view: &'a DispatchView) -> Option<&'a PendingInput> {
        let next = Self::round(view);
        view.control
            .pending_input
            .iter()
            .filter(|input| input.seq.0 >= next)
            .min_by_key(|input| input.seq)
            .filter(|input| input.seq.0 == next)
    }
}
