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
    /// The fenced verification handoff (plan T7): one claim-fenced CAS
    /// registered the verification mode, the attempt identity, the stable
    /// query key, the approved method, the next due time and backoff — and
    /// moved the holder line so the old holder's writes are fenced. Normal
    /// claims refuse a dispatch in this state from then on.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub verification: Option<VerificationHandoff>,
    /// The durable `Quarantined` record (plan T7): the stop-verification
    /// deadline passed with the outcome still unknown. Independent of any
    /// terminal state — the call's phase never crosses the terminal
    /// threshold on the strength of this record alone.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quarantined: Option<QuarantineRecord>,
}

/// The independent verifier claim line (plan T7): epochs on this line are
/// handed out only by the store's verification CAS, so a write from an
/// epoch that is no longer current is refused — even while the connector
/// that originally held the call is still alive.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct VerificationClaim {
    pub instance: Instance,
    pub epoch: HolderEpoch,
}

/// The stable query key of a handed-off obligation (plan T7): the original
/// operation's request id, or the accepted remote task reference once one
/// was recorded. Durable, so the verifier re-derives no identity and never
/// fabricates a task id. `Retained` marks an obligation with no approved
/// query key: it is kept and alarmed about, never guessed away.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum VerificationKey {
    ByRequest { request_id: RequestId },
    ByTask { remote: RemoteTaskRef },
    Retained,
}

/// What the verifier may still do with a handed-off call (plan T7): the
/// approved query, an approved cancel, or — with no capability or permission
/// — only retention and warning.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VerificationMethod {
    Query,
    CancelOnly,
    Retain,
}

/// The durable verification handoff written by the single claim-fenced CAS.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct VerificationHandoff {
    /// The current verifier claim, `None` until the first bounded takeover.
    pub claim: Option<VerificationClaim>,
    /// The attempt of the send whose outcome stayed unknown.
    pub attempt: u32,
    pub key: VerificationKey,
    pub method: VerificationMethod,
    /// When the next verification attempt is due.
    pub next_due: TimestampMillis,
    /// Backoff between verification attempts, in milliseconds.
    pub backoff_ms: u64,
}

/// The abnormal public state reached when the stop-verification deadline
/// passed with the outcome still unknown (plan T7): saved independently,
/// never a terminal state.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct QuarantineRecord {
    pub at: TimestampMillis,
    pub reason: String,
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
