use crate::ids::*;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AgentEventKind {
    Delta,
    Thinking,
    Tool,
    Snapshot,
    InputRequired { interaction: InteractionId },
    AuthRequired { interaction: InteractionId },
    Final,
    Error,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentEvent {
    pub did: Did,
    pub holder_epoch: HolderEpoch,
    pub seq: InputSeq,
    pub kind: AgentEventKind,
    /// Stable upstream identity for semantic replay; absent for raw deltas.
    pub remote_event_id: Option<String>,
    pub payload: Payload,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum InputBody {
    User {
        payload: Payload,
    },
    HumanReply {
        interaction: InteractionId,
        responder: PrincipalId,
        payload: Payload,
    },
    Cancel {
        reason: Option<String>,
    },
    Agent {
        event: AgentEvent,
    },
}

/// Retry identity and digest exclude holder epoch, delivery count and receive time.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InputEnvelope {
    pub schema_version: u32,
    pub sid: Sid,
    pub tid: Tid,
    pub input_id: InputId,
    pub business_digest: Digest,
    pub body: InputBody,
}

/// No broker offset is required to acknowledge a logically durable input.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AdmissionReceipt {
    pub sid: Sid,
    pub tid: Tid,
    pub input_id: InputId,
    pub business_digest: Digest,
}

/// Resolved input contents use their own fixed phase-one capacity. Output display
/// quotas can be smaller without retroactively changing admitted input capacity.
pub const PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES: usize = 8 * 1024 * 1024;
