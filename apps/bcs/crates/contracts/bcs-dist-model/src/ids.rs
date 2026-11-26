use serde::{Deserialize, Serialize};
use std::num::NonZeroU32;

macro_rules! identities {
    ($($name:ident),+ $(,)?) => {$ (
        #[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
        #[serde(transparent)]
        pub struct $name(pub String);
    )+};
}

identities!(
    Sid,
    Tid,
    Did,
    InputId,
    InteractionId,
    Digest,
    TenantId,
    PrincipalId,
    GroupId,
    AgentId,
    CorrelationKey,
    ArtifactId,
    DeadlineId,
    PoolId,
    RequestId,
    CommitId,
    CredentialRef,
    BindingVersion,
    BackendId,
    LogGeneration
);

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ShardId(pub u32);

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(transparent)]
pub struct CommitSeq(pub u64);

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(transparent)]
pub struct WorkerEpoch(pub u64);

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(transparent)]
pub struct HolderEpoch(pub u64);

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(transparent)]
pub struct InputSeq(pub u64);

/// Wall-clock milliseconds, never a logical write version or event position.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(transparent)]
pub struct TimestampMillis(pub i64);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Instance {
    pub node: String,
    pub incarnation: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TaskScope {
    pub tenant: TenantId,
    pub principal: PrincipalId,
}

/// Ordered within one Sid only. Indices start at one and are fixed before commit.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct EventId {
    pub commit: CommitSeq,
    pub index: NonZeroU32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EffectId {
    pub sid: Sid,
    pub commit: CommitSeq,
    pub index: NonZeroU32,
}

/// Backend-generated token. No Ord implementation: callers cannot compare offsets.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReplayToken {
    pub backend: BackendId,
    pub generation: LogGeneration,
    pub value: String,
}

/// Positions deliberately have no ordering in business code.
/// ```compile_fail
/// use bcs_dist_model::InboxPos;
/// fn compare(a: InboxPos, b: InboxPos) -> bool { a < b }
/// ```
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InboxPos {
    pub sid: Sid,
    pub token: ReplayToken,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TaskCursor {
    pub sid: Sid,
    pub tid: Tid,
    pub token: ReplayToken,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PageToken(pub String);

/// Immutable content identity, not an expiring signed download URL.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BlobRef {
    pub key: String,
    pub digest: Digest,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", content = "value", rename_all = "snake_case")]
pub enum Payload {
    Inline(serde_json::Value),
    Ref(BlobRef),
}
