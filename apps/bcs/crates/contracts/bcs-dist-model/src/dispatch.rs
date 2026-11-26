use crate::ids::*;
use serde::{Deserialize, Serialize};

pub const DISPATCH_SCHEMA_VERSION: u32 = 2;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DispatchSpec {
    pub schema_version: u32,
    pub sid: Sid,
    pub tid: Tid,
    pub did: Did,
    pub source: CorrelationKey,
    pub attempt: u32,
    pub agent: AgentId,
    pub pool: PoolId,
    pub downstream_context: Option<String>,
    pub request: Payload,
    pub deadline: TimestampMillis,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Holder {
    pub instance: Instance,
    pub epoch: HolderEpoch,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RemoteTaskRef {
    pub task_id: String,
    pub context_id: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DispatchPhase {
    Created,
    Claimed,
    Sending,
    Accepted,
    Ended,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DispatchOutcome {
    Completed,
    Failed,
    Canceled,
    Unknown,
}

/// Definitive remote termination; uncertainty cannot be supplied as completion.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FinalDispatchOutcome {
    Completed,
    Failed,
    Canceled,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PendingInput {
    pub seq: InputSeq,
    pub input_id: InputId,
    pub business_digest: Digest,
    pub payload: Payload,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CancelIntent {
    pub request_id: RequestId,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum CancelObservation {
    NotSent,
    Sending,
    RequestAccepted,
    ConfirmedCanceled,
    AlreadyFinished { outcome: FinalDispatchOutcome },
    Rejected { code: String },
    OutcomeUnknown,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DispatchControlState {
    pub cancel: Option<CancelIntent>,
    pub pending_input: Vec<PendingInput>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DispatchExecution {
    pub holder: Option<Holder>,
    pub phase: DispatchPhase,
    pub remote: Option<RemoteTaskRef>,
    /// Survives removing pending_input; no acknowledgement may skip an unknown input.
    pub confirmed_input_through: Option<InputSeq>,
    pub cancellation: CancelObservation,
    pub outcome: Option<DispatchOutcome>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DispatchView {
    pub spec: DispatchSpec,
    pub control: DispatchControlState,
    pub execution: DispatchExecution,
}
