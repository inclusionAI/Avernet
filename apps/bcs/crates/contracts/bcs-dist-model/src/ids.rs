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
    NodeId,
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

/// Why a shard cannot be read from a prefixed id string.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShardParseError {
    /// The id has no `s<digits>.` segment before the first `.`.
    MissingShardPrefix,
    /// The digits name a shard past the u32 range.
    ShardOutOfRange,
}

impl std::error::Error for ShardParseError {}

impl std::fmt::Display for ShardParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::MissingShardPrefix => write!(f, "id has no s<digits>. shard prefix"),
            Self::ShardOutOfRange => write!(f, "shard digits exceed the u32 range"),
        }
    }
}

/// The shard an id string carries: the u32 of the `s<digits>` segment before
/// the first `.`. Task and dispatch ids embed their session's segment, so
/// their string forms are accepted too. Only the prefix shape is checked:
/// length caps and charset policy on the rest of the id stay with the
/// boundary that admits the caller's input.
pub fn shard_of(id: &str) -> Result<ShardId, ShardParseError> {
    shard_parts(id).map(|(shard, _, _)| shard)
}

/// `(shard, digits, rest)` of `s<digits>.<rest>` split at the first `.`.
fn shard_parts(id: &str) -> Result<(ShardId, &str, &str), ShardParseError> {
    let (head, rest) = id.split_once('.').ok_or(ShardParseError::MissingShardPrefix)?;
    let digits = head
        .strip_prefix('s')
        .ok_or(ShardParseError::MissingShardPrefix)?;
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return Err(ShardParseError::MissingShardPrefix);
    }
    let shard = digits
        .parse::<u32>()
        .map_err(|_| ShardParseError::ShardOutOfRange)?;
    Ok((ShardId(shard), digits, rest))
}

impl Sid {
    /// The shard this session is pinned to: the `s<digits>` before the first `.`.
    pub fn shard(&self) -> Result<ShardId, ShardParseError> {
        shard_of(&self.0)
    }

    /// The shard, its digit text and the text after the first `.`: the pieces
    /// of `s<digits>.<rest>`. Extra digit caps and charset rules for the rest
    /// belong to the boundary that owns the caller's input.
    pub fn parts(&self) -> Result<(ShardId, &str, &str), ShardParseError> {
        shard_parts(&self.0)
    }
}

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

#[cfg(test)]
mod shard_tests {
    use super::*;

    #[test]
    fn reads_the_shard_before_the_first_dot() {
        assert_eq!(shard_of("s0042.7f3a9c"), Ok(ShardId(42)));
        assert_eq!(shard_of("s0000001.x"), Ok(ShardId(1)));
        assert_eq!(shard_of("s4294967295.x"), Ok(ShardId(u32::MAX)));
        assert_eq!(shard_of("s0042.x.y"), Ok(ShardId(42)));
        assert_eq!(Sid("s0042.7f3a9c".into()).shard(), Ok(ShardId(42)));
        assert_eq!(
            Sid("s0042.7f3a9c".into()).parts(),
            Ok((ShardId(42), "0042", "7f3a9c"))
        );
    }

    #[test]
    fn rejects_ids_without_a_plain_shard_prefix() {
        for bad in [
            "", "7f3a9c", "x0042.7f3a9c", "s.7f3a9c", "s0042", ".s0042.x",
            "S0042.x", "s+1.7f3a9c", "s-1.7f3a9c", "s 1.x", "s１2.x",
        ] {
            assert_eq!(shard_of(bad), Err(ShardParseError::MissingShardPrefix), "{bad:?}");
        }
    }

    #[test]
    fn rejects_digits_past_the_u32_range() {
        for bad in ["s4294967296.x", "s99999999999.a"] {
            assert_eq!(shard_of(bad), Err(ShardParseError::ShardOutOfRange), "{bad:?}");
        }
    }
}
