//! Strict-group admission boundary. Opt-in: no production implementation is
//! installed. The caller is the Worker admission use case, never an HTTP handler.
use super::common::StoreError;
use async_trait::async_trait;
use bcs_dist_model::{AcceptedRound, Admission, RoundKey, TerminalProof};

/// Stable request lookup fields; Sid is deliberately excluded. Identity hashing
/// in the application must length-prefix these UTF-8 components and use distinct
/// domains for session/task derivation. This is not a client-authorized scope.
pub struct GroupRequestIdentity<'a> {
    scope: &'a bcs_dist_model::TaskScope,
    group: &'a bcs_dist_model::GroupId,
    request: &'a bcs_dist_model::RequestId,
}
impl<'a> GroupRequestIdentity<'a> {
    pub fn strict(scope: &'a bcs_dist_model::TaskScope, group: &'a bcs_dist_model::GroupId,
        request: &'a bcs_dist_model::RequestId) -> Self { Self { scope, group, request } }
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
}
