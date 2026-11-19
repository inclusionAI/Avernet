//! Independent Session control plane. These writes never replace business state.
use super::{admission::*, common::*, ownership::ShardClaim};
use async_trait::async_trait;
use bcs_dist_model::*;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ControlGuard {
    pub owner: SessionOwnership,
    pub expected_head: CommitSeq,
    pub expected_revision: u64,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AttemptIdentity {
    pub id: RequestId,
    pub head: CommitSeq,
    pub state_digest: Digest,
    pub input_digest: Digest,
    pub context_digest: Digest,
    /// Recovered pending attempts reuse this value, not the new process clock.
    pub frozen_now: TimestampMillis,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AttemptOutcome {
    Succeeded {
        decision_digest: Digest,
    },
    Transient {
        code: String,
    },
    Deterministic {
        code: String,
        isolation_id: RequestId,
        safe_to_finalize: bool,
    },
    Uninterpretable {
        code: String,
        isolation_id: RequestId,
    },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RiskAttempt {
    pub identity: AttemptIdentity,
    #[serde(default)]
    pub replay: Option<ReplayIdentity>,
    pub outcome: Option<AttemptOutcome>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReplayIdentity {
    pub effect: EffectId,
    pub content_digest: Digest,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IsolationRecord {
    pub operation_id: RequestId,
    pub reason: String,
    pub head: CommitSeq,
    pub failed_attempt: RequestId,
    pub closure: Option<AdmissionClosure>,
    pub completion: Option<ClosureCertificate>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RecoveryAuthorization {
    pub guard: ControlGuard,
    pub isolation_id: RequestId,
    pub completion: ClosureCertificate,
    pub request_id: RequestId,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ControlMode {
    Normal,
    Quarantined {
        isolation: IsolationRecord,
    },
    Blocked {
        isolation: IsolationRecord,
    },
    Recovering {
        isolation: IsolationRecord,
        authorization: RecoveryAuthorization,
    },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionControl {
    pub revision: u64,
    pub mode: ControlMode,
    pub active_attempt: Option<RiskAttempt>,
    /// One pending Runner may coexist with a verified committed-batch replay.
    #[serde(default)]
    pub suspended_runner: Option<RiskAttempt>,
    /// Last replay result is atomic with restoring the suspended Runner.
    #[serde(default)]
    pub completed_replay: Option<RiskAttempt>,
    pub last_business_progress: CommitSeq,
    pub last_recovery: Option<GateRecoveryReceipt>,
}
impl Default for SessionControl {
    fn default() -> Self {
        Self {
            revision: 0,
            mode: ControlMode::Normal,
            active_attempt: None,
            suspended_runner: None,
            completed_replay: None,
            last_business_progress: CommitSeq(0),
            last_recovery: None,
        }
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ControlSnapshot {
    pub owner: SessionOwnership,
    pub head: CommitSeq,
    pub control: SessionControl,
    /// Canonical digest of the raw business checkpoint, excluding control/owner.
    pub checkpoint_digest: Digest,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ControlOutcome {
    Applied(ControlSnapshot),
    AlreadyApplied(ControlSnapshot),
    Fenced,
    HeadConflict,
    RevisionConflict,
    ContentConflict,
    NotReady,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ControlAcquireOutcome {
    Acquired(ControlSnapshot),
    Fenced,
    Deferred { check_after: std::time::Duration },
    Missing,
}
#[async_trait]
pub trait SessionControlPort: Send + Sync {
    /// Validates current shard claim/StrictLease and seals only the raw owner
    /// header, preserving malformed business content and all control evidence.
    /// Uses the same handoff fence and local reservation as ordinary acquisition.
    async fn acquire_control(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
    ) -> Result<ControlAcquireOutcome, StoreError>;
    /// Parses owner/head/control independently from the business checkpoint.
    /// Corrupt business data must not prevent a Blocked reason being recorded.
    async fn load_control(&self, sid: &Sid) -> Result<Option<ControlSnapshot>, StoreError>;
    /// Includes superseded or recovered isolation evidence retained independently.
    async fn get_isolation(
        &self,
        sid: &Sid,
        id: &RequestId,
    ) -> Result<Option<IsolationRecord>, StoreError>;
    async fn get_attempt(
        &self,
        sid: &Sid,
        id: &RequestId,
    ) -> Result<Option<RiskAttempt>, StoreError>;
    /// Exact original identity retry is idempotent. A new attempt cannot replace
    /// an unresolved one. Takeover preserves the frozen input/context/time.
    async fn begin_attempt(
        &self,
        guard: ControlGuard,
        attempt: AttemptIdentity,
    ) -> Result<ControlOutcome, StoreError>;
    /// Committed necessary effects may continue in Quarantined. The provider
    /// binds this attempt to the exact current batch effect and content digest;
    /// Blocked/Recovering never authorize new business/effect attempts.
    async fn begin_replay_attempt(
        &self,
        guard: ControlGuard,
        attempt: AttemptIdentity,
        effect: EffectId,
        content_digest: Digest,
    ) -> Result<ControlOutcome, StoreError>;
    /// Deterministic failure and Quarantined/Blocked are one control CAS. The
    /// caller verifies independent finalization before safe_to_finalize=true;
    /// the provider also refuses that classification for an undecodable state.
    async fn finish_attempt(
        &self,
        guard: ControlGuard,
        attempt: RequestId,
        outcome: AttemptOutcome,
    ) -> Result<ControlOutcome, StoreError>;
    /// Independent diagnostic failure atomically records its evidence and a new
    /// isolation, including during Recovering. Only failure outcomes are legal.
    /// Exact guard and stable attempt identity prevent refreshing stale authority.
    async fn isolate(
        &self,
        guard: ControlGuard,
        attempt: AttemptIdentity,
        outcome: AttemptOutcome,
    ) -> Result<ControlOutcome, StoreError>;
    /// Copies only a provider-confirmed close/cut into the matching isolation.
    async fn record_closure(
        &self,
        guard: ControlGuard,
        closure: AdmissionClosure,
    ) -> Result<ControlOutcome, StoreError>;
    /// Completion is checked against actual durable closure evidence. Merely
    /// presenting a cursor at cut or a caller-generated digest is insufficient.
    async fn record_closure_complete(
        &self,
        guard: ControlGuard,
        certificate: ClosureCertificate,
    ) -> Result<ControlOutcome, StoreError>;
    /// Application authorization happens before this repository boundary. Stable
    /// request, exact isolation/head/revision and completed closure are mandatory.
    async fn begin_recovery(
        &self,
        authorization: RecoveryAuthorization,
    ) -> Result<ControlOutcome, StoreError>;
    /// Reads the authority gate receipt. Both gate and checkpoint control must
    /// confirm this recovery before ordinary commits become legal again.
    async fn confirm_recovery(
        &self,
        guard: ControlGuard,
        receipt: GateRecoveryReceipt,
    ) -> Result<ControlOutcome, StoreError>;
}
