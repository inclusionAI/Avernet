//! The Connector's pure rules: the `DistConnectorCoreService` implementation.

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
    /// The question round a new input/auth-required event belongs to.
    fn round(view: &DispatchView) -> u64 {
        view.execution
            .confirmed_input_through
            .map_or(0, |seq| seq.0)
            .saturating_add(1)
    }
}

/// The `state` word of an inline payload, empty when absent or stored by reference.
fn state_of(payload: &Payload) -> &str {
    match payload {
        Payload::Inline(value) => value.get("state").and_then(|s| s.as_str()).unwrap_or(""),
        Payload::Ref(_) => "",
    }
}

impl DistConnectorCoreService for DistConnectorRules {
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

    fn uncertain_start(&self, capabilities: &DownstreamCapabilities) -> UncertainSend {
        if capabilities.idempotent_start {
            UncertainSend::Resend
        } else if capabilities.query_by_request {
            UncertainSend::AskAgent
        } else {
            UncertainSend::Unresolved
        }
    }

    fn uncertain_input(&self, capabilities: &DownstreamCapabilities) -> UncertainSend {
        if capabilities.idempotent_input {
            UncertainSend::Resend
        } else {
            UncertainSend::Unresolved
        }
    }

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
