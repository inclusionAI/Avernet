//! Strict-group admission boundary. Opt-in: no production implementation is
//! installed. The caller is the Worker admission use case, never an HTTP handler.
use super::common::StoreError;
use async_trait::async_trait;
use bcs_dist_model::{
    AcceptedRound, Admission, CommitId, CommitSeq, GroupId, RequestId, RoundKey, Sid, TaskScope,
    TerminalProof, Tid,
};
use serde::{Deserialize, Serialize};
use std::num::NonZeroU64;

/// The strict license installed by a licensed round admission (plan T2). It
/// lives under the `group_admission` key of the checkpoint's runner-state
/// value until the T7 terminal spine condenses it into the cold index;
/// every rewriter of `runner_state` must carry it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StrictLicense {
    pub schema_version: u32,
    pub scope: TaskScope,
    pub group: GroupId,
    pub sid: Sid,
    pub tid: Tid,
    pub generation: NonZeroU64,
    pub request_id: String,
    pub request_digest: String,
    /// Frozen input provenance carried through Worker takeover and finalization.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub history_cut: Option<bcs_dist_model::BlobRef>,
}

impl StrictLicense {
    /// The licensed round's key.
    pub fn round_key(&self) -> RoundKey {
        RoundKey {
            scope: self.scope.clone(),
            group: self.group.clone(),
            sid: self.sid.clone(),
            tid: self.tid.clone(),
            generation: self.generation,
        }
    }
}

/// The stable commit identity of a round's terminal authority CAS. Both
/// builders of the proof — the Worker's Done-boundary spine and an
/// admission gate completing a still-open window — must derive the same
/// identity from sealed facts, so their idempotent retries can never
/// install two different authorities for one round.
pub fn terminal_proof_identity(
    round: &RoundKey,
    result_digest: &bcs_dist_model::Digest,
    commit_seq: &CommitSeq,
) -> CommitId {
    CommitId(format!(
        "terminal:{}:{}:{}",
        round.tid.0,
        result_digest.0,
        commit_seq.0
    ))
}

/// Read the strict license out of a checkpoint runner-state value. Absent or
/// malformed markers are `None`; probing is never authorization.
pub fn decode_strict_license(runner_value: &serde_json::Value) -> Option<StrictLicense> {
    serde_json::from_value(runner_value.get("group_admission")?.clone()).ok()
}

/// Stable request lookup fields; Sid is deliberately excluded. Identity hashing
/// in the application must length-prefix these UTF-8 components and use distinct
/// domains for session/task derivation. This is not a client-authorized scope.
pub struct GroupRequestIdentity<'a> {
    scope: &'a TaskScope,
    group: &'a GroupId,
    request: &'a RequestId,
}
impl<'a> GroupRequestIdentity<'a> {
    pub fn strict(scope: &'a TaskScope, group: &'a GroupId,
        request: &'a RequestId) -> Self { Self { scope, group, request } }
    pub fn components(&self) -> Vec<&str> {
        vec!["group_a2a:v1", &self.scope.tenant.0, &self.scope.principal.0, &self.group.0, &self.request.0]
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum GroupRequestRecord {
    Absent,
    /// Original normalization must be recoverable even before acceptance. This
    /// conveys no execution/public-read permission and cannot be reported Busy.
    Prepared(AcceptedRound),
    Accepted(AcceptedRound),
    Expired,
}

/// Frozen request intent, including original normalization and return settings.
/// The use case authorizes current scope/group before invoking this repository.
pub type GroupStartRequest = AcceptedRound;

#[derive(Debug, Clone, PartialEq)]
pub struct GroupRoundView {
    pub accepted: AcceptedRound,
    pub terminal: Option<TerminalProof>,
    /// Complete result loaded through the verified terminal reference, never
    /// from an independently trusted archive row. No public wire DTO lives here.
    pub result: Option<bcs_dist_model::Payload>,
}

/// Trusted authorization result supplied by the use case's registered policy.
/// Never deserialize grants from client metadata. Re-evaluate at each operation
/// and on revocation while a subscription is open.
#[derive(Debug, Clone, Copy)]
pub struct GroupRoundGrants {
    pub published: bool,
    pub invoke: bool,
    pub read: bool,
    pub cancel: bool,
}
#[derive(Debug, Clone, Copy)]
pub enum GroupRoundOperation { New, Replay, Read, Subscribe, Cancel }
impl GroupRoundGrants {
    pub fn allows(self, operation: GroupRoundOperation) -> bool {
        match operation {
            GroupRoundOperation::New => self.published && self.invoke,
            GroupRoundOperation::Replay | GroupRoundOperation::Read | GroupRoundOperation::Subscribe => self.read,
            GroupRoundOperation::Cancel => self.read && self.cancel,
        }
    }
}

/// V1 candidate profile; a caller may request less, never silently exceed these
/// bounds. Encoded envelopes count toward limits; result references are not an
/// exemption for inbound resolution. Cursor expiry must not switch read cuts.
#[derive(Debug, Clone, Copy)]
pub struct RoundBudgetProfile {
    pub inbound_bytes: usize,
    pub public_frame_bytes: usize,
    pub feed_event_bytes: usize,
    pub feed_batch_bytes: usize,
    pub cursor_lifetime_millis: u64,
    pub max_page_rows: u32,
}
impl RoundBudgetProfile {
    pub fn validate_projection(&self, page_rows: u32, history_rows: u32) -> Result<(), StoreError> {
        if page_rows == 0 || page_rows > self.max_page_rows || history_rows > 100 {
            return Err(StoreError::Invalid { code: "group_projection_limits".into() });
        }
        Ok(())
    }
    pub const fn v1() -> Self {
        Self { inbound_bytes: 8*1024*1024, public_frame_bytes: 8*1024*1024,
            feed_event_bytes: 128*1024, feed_batch_bytes: 256*1024,
            cursor_lifetime_millis: 300_000, max_page_rows: 100 }
    }
}

/// A repository implementation must verify sealed ownership internally. IDs and
/// caller-created TerminalProof descriptors are not capabilities. All mutations
/// use the same checkpoint authority; no separate session occupancy lock.
#[async_trait]
pub trait GroupRoundRepo: Send + Sync {
    /// Invoke after current authorization and BEFORE applying current defaults
    /// or capabilities. Use the returned original normalization_ref for replay.
    /// Abandoned, fenced preparations may be Absent; unknown reads are errors.
    async fn lookup(&self, identity: &GroupRequestIdentity<'_>) -> Result<GroupRequestRecord, StoreError>;

    /// Resolve (scope, group, request_id) before new admission. Replay retains the
    /// first identity/configuration, changed normalized content is Conflict.
    /// Busy installs no accepted task, reserves no message key, and never queues.
    /// Unknown storage outcomes remain errors and are recovered by the same key.
    /// Accepted includes a recoverable scheduling obligation. The expected round
    /// generation cannot be silently advanced by background replay.
    async fn admit(&self, request: &GroupStartRequest) -> Result<Admission, StoreError>;

    /// Only accepted records are visible. Verify all RoundKey fields; prepared
    /// data is absent. A terminal result is readable only after its authoritative
    /// commit; an archive row alone is not a terminal proof. Read errors propagate.
    async fn get(&self, key: &RoundKey) -> Result<Option<GroupRoundView>, StoreError>;

    /// Commit the authoritative terminal state of the licensed round and
    /// transfer its cold proof, inside one owner-fenced window (plan T7).
    ///
    /// The sequence inside that window is binding: the terminal CAS settles
    /// first (the checkpoint retains the round's exact `TerminalProof`
    /// original text alongside the Done task and its result reference, plus
    /// the readable closure evidence), then the cold proof is confirmed
    /// against the archive and read back. Only a caller holding this
    /// function's completion may install the next round's runner state — an
    /// admission of `generation + 1` before the cold proof exists would make
    /// the licensed round's confirmation permanently unreachable (the A2
    /// cold-confirm race). Late legacy writes carry an exact `commit_seq`
    /// and are fenced by the same owner fence. SSE delivery is a follower,
    /// never a condition of this commit.
    ///
    /// The default refuses: unconfigured strict rounds never fake a
    /// terminal authority.
    async fn commit_terminal(
        &self,
        _round: &RoundKey,
        _proof: &TerminalProof,
    ) -> Result<CommitSeq, StoreError> {
        Err(StoreError::NotApplied {
            code: "terminal_commit_unconfigured".into(),
        })
    }
}
