use crate::{
    ids::*,
    session::{AgentBinding, DownstreamCapabilities},
};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DispatchSpec {
    pub schema_version: u32,
    pub sid: Sid,
    pub tid: Tid,
    pub did: Did,
    pub node: NodeId,
    pub attempt: u32,
    pub target: AgentBinding,
    pub pool: PoolId,
    pub capabilities: DownstreamCapabilities,
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
    /// Durably recorded with the send intent before any network I/O (plan
    /// T6). A later holder that finds the call possibly-sent re-checks the
    /// proof before it may rely on the target's dedupe for a resend.
    #[serde(default)]
    pub send_proof: Option<SendProof>,
}

/// Which send attempt a proof was written for. Mirrors the store's
/// `SendOperation`, but lives with the persisted model so a proof survives
/// holder death inside the dispatch document.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ProofOperation {
    Start { request_id: RequestId },
    Input { seq: InputSeq, request_id: RequestId },
}

/// The durable proof of exactly what one send attempt is (plan T6):
/// written with the send intent, before the first network call of the
/// attempt, so a takeover can decide whether the target's dedupe may be
/// relied on for a resend — window, domain, semantics and configuration
/// all compared against the current attempt.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SendProof {
    /// Target dedupe domain the proof was issued for. A resend may only
    /// rely on the dedupe while the same credential still resolves to
    /// this domain.
    pub dedupe_domain: String,
    /// End of the target's dedupe guarantee window (wall-clock ms). A
    /// resend after the deadline may no longer rely on the dedupe.
    pub guarantee_deadline: TimestampMillis,
    pub operation: ProofOperation,
    /// Digest over the normalized request body of the attempt.
    pub body_digest: Digest,
    /// Digest over the send configuration in force when the proof was
    /// written; a changed valid configuration voids the proof.
    pub config_digest: Digest,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DispatchView {
    pub spec: DispatchSpec,
    pub control: DispatchControlState,
    pub execution: DispatchExecution,
}
