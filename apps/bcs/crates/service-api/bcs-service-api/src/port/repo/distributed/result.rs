use super::common::*;
use super::group_round::RoundBudgetProfile;
use async_trait::async_trait;
use bcs_dist_model::*;
use serde::{Deserialize, Serialize};
use std::num::NonZeroU32;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SavedResult {
    Saved(BlobRef),
    AlreadySaved(BlobRef),
    ContentConflict,
}

#[async_trait]
pub trait ResultStorePort: Send + Sync {
    /// Save complete authorized final record before terminal publication.
    /// Same identity with different content is NOT successful save-if-absent.
    async fn save_final(
        &self,
        source: EffectSource,
        record: FinalTaskRecord,
    ) -> Result<SavedResult, StoreError>;
    async fn get_final(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<FinalTaskRecord>, StoreError>;
}

/// Budget version and profile used when an archive's public projection was
/// measured. The version is saved with the archive so a later budget change
/// never silently re-authorizes an old measurement. D0 freezes version 1 =
/// `RoundBudgetProfile::v1()`.
#[derive(Debug, Clone, Copy)]
pub struct ArchiveBudget {
    pub version: u64,
    pub profile: RoundBudgetProfile,
}

/// Versioned per-task final archive request: the archived `FinalTaskRecord`
/// extended with the complete ordered public history, a stable result digest,
/// the ordered artifact parts and the owning `RoundKey`. Archiving succeeds on
/// its own merits and never confers public eligibility.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskFinalArchive {
    /// Exactly 2: a v1 `FinalTaskRecord` plus the history/digest/artifact
    /// extension. Version 1 requests use `save_final` instead.
    pub schema_version: u32,
    pub record: FinalTaskRecord,
    pub round: RoundKey,
    /// Complete ordered public history (state, artifact and terminal events).
    pub public_history: Vec<OutputEvent>,
    /// Stable digest of the stored result product, fixed at archive time.
    pub result_digest: Digest,
    /// Ordered artifact parts; unique per `(tid, artifact_id)`.
    pub artifacts: Vec<ArtifactPart>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SavedArchive {
    Saved,
    AlreadySaved,
    ContentConflict,
}

/// Measured bounded encoding of one task's full default public projection,
/// saved with the committed archive. `fits` false means the task can never be
/// published as Completed under this budget; it must end in a bounded failure
/// finale instead.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProjectionCheck {
    pub budget_version: u64,
    pub projection_digest: Digest,
    pub measured_bytes: u64,
    pub fits: bool,
}

/// Pure, IO-free validator of the D0-frozen public-projection encoding/size
/// contract, shared by this store and the public read adapter (T8). Wire
/// types stay in the adapter; the host injects this trait so neither side
/// depends on the other's encoding. Implementations bounded-encode the
/// complete default `GetTask` body plus the mandatory state and artifact SSE
/// frames — envelope, escaping, history and every part — and must stop
/// measuring once the frame budget is provably exceeded instead of paying
/// for the full encode.
pub trait PublicProjectionValidator: Send + Sync {
    fn measure(
        &self,
        budget: &ArchiveBudget,
        archive: &TaskFinalArchive,
    ) -> Result<ProjectionCheck, StoreError>;
}

/// Trusted read authorization supplied by the use case's registered policy,
/// mirroring `group_round::GroupRoundGrants`. The store checks it before any
/// disclosure so expiry or existence of an archive is never revealed to an
/// unauthorized caller; the denial shape is uniform across all stored states.
///
/// Negative capability: `read` authorizes exactly this repository interface.
/// It does NOT authorize public disclosure — a preparing view handed to a
/// `read`-granted caller is recovery input, never a publishable projection.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ArchiveGrants {
    pub read: bool,
}

/// One task's archive read view. A preparing archive is readable only for
/// recovery; only a committed archive carries its saved projection check, and
/// an expired tombstone stays readable after proof and result deletion so the
/// task can never be silently re-executed.
///
/// HARD CONTRACT: the [`ArchivedTaskView::Preparing`] variant exists for
/// recovery orchestration only (replaying a crashed finalize). Callers
/// serving public reads (the T8 adapter) MUST NOT project it as task state;
/// if a caller ever needs a restore-versus-public distinction, a separate
/// read dimension must be introduced at the T8 seam instead of widening the
/// meaning of `read` here.
#[derive(Debug, Clone, PartialEq)]
pub enum ArchivedTaskView {
    Preparing(TaskFinalArchive),
    Committed {
        archive: TaskFinalArchive,
        check: ProjectionCheck,
    },
    Expired {
        result_digest: Digest,
    },
}

/// Per-task immutable final-product archive over the host-selected DbPlugin
/// (plan T4). The final record, every ordered artifact part and all immutable
/// blob references are written in ONE transaction or not at all; expiry
/// marking and reclamation are independent and idempotent.
/// The controlled completion payload of a committed strict-group archive
/// (plan T7 slice 2): the complete ordered public history and the ordered
/// artifact parts, covered by the exact sealed publication obligations of
/// the round. The store re-verifies every field against the register kept
/// in the session's sealed checkpoint — this payload is a claim, never an
/// authority of its own.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PublicPublication {
    pub schema_version: u32,
    /// Sealed obligation identities this publication covers, in stable
    /// delivery order.
    pub effects: Vec<EffectId>,
    /// Complete ordered public history (state, artifact and terminal
    /// events) of the task, exactly as sealed in the covered obligations.
    pub history: Vec<OutputEvent>,
    /// Ordered artifact parts derived one-to-one from the artifact events
    /// of the covered obligations; unique per `(tid, artifact_id)`.
    pub artifacts: Vec<ArtifactPart>,
}

/// Outcome of the guarded archive completion above. A matching repeat is
/// `AlreadyAttached`; a different payload for the same archive is a loud
/// conflict, never a silent overwrite.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AttachedPublication {
    Attached,
    AlreadyAttached,
    ContentConflict,
}

#[async_trait]
pub trait TaskArchiveRepo: Send + Sync {
    /// Authorization comes from persisted committed evidence, exactly like
    /// `ResultStorePort::save_final`. A pin over every referenced immutable
    /// blob is registered before the first object write; blobs resolve
    /// idempotently; one finishing transaction writes record, parts and
    /// references. The public projection is measured before commit success and
    /// saved with the archive.
    async fn archive_final(
        &self,
        source: EffectSource,
        archive: TaskFinalArchive,
    ) -> Result<SavedArchive, StoreError>;
    /// Read one task's archive by scope/group/Tid. Authorization is checked
    /// before disclosing any archive state; unauthorized reads are uniform
    /// across absent, preparing, committed and expired tasks.
    ///
    /// HARD CONTRACT: a returned [`ArchivedTaskView::Preparing`] is for
    /// recovery orchestration only — callers serving public reads (the T8
    /// adapter) MUST NOT project it; a separate restore-versus-public read
    /// dimension belongs at the T8 seam, not here.
    async fn get_archived(
        &self,
        grants: &ArchiveGrants,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
    ) -> Result<Option<ArchivedTaskView>, StoreError>;
    /// Page over one group's committed archive Tids first; artifact parts are
    /// fetched in bounded batches afterwards, never row-per-task.
    async fn list_archived_tasks(
        &self,
        grants: &ArchiveGrants,
        scope: &TaskScope,
        group: &GroupId,
        offset: u32,
        limit: NonZeroU32,
    ) -> Result<Vec<Tid>, StoreError>;
    /// Batch-fetch the ordered artifact parts of one page of tasks. The read
    /// is length-bounded from the start of IO: two bounded queries per page
    /// (rows, then object bytes by hash-key lookup) — never the artifacts or
    /// object bytes of the whole group, never row-per-task N+1.
    async fn task_artifacts(
        &self,
        grants: &ArchiveGrants,
        scope: &TaskScope,
        group: &GroupId,
        tids: &[Tid],
    ) -> Result<Vec<(Tid, ArtifactPart)>, StoreError>;
    /// Length-bounded read of one archived task's result blob from the start of
    /// IO: a `LENGTH` probe decides whether the object may be materialized at
    /// all, and content is fetched with a SQL-level substring bound, never by
    /// decoding a full oversized object first. Returns the (possibly
    /// truncated) prefix and the full stored length.
    async fn read_task_result(
        &self,
        grants: &ArchiveGrants,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
        limit: NonZeroU32,
    ) -> Result<Option<(Vec<u8>, u64)>, StoreError>;
    /// Idempotently mark one committed archive as expired. The tombstone stays
    /// readable before — and after — proof and result deletion.
    async fn mark_task_expired(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
    ) -> Result<IdempotentWrite, StoreError>;
    /// Idempotent reclamation of one expired archive: deletes artifact parts,
    /// the archived result reference and unreferenced immutable blobs. A blob
    /// still referenced by a pending, quarantined or live archive is never
    /// deleted. The expired tombstone row itself is retained, and the delete
    /// is only issued while the tombstone remains readable — there is no
    /// window in which the task could be re-executed as absent.
    async fn reclaim_expired(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
    ) -> Result<IdempotentWrite, StoreError>;
    /// Confirm T7's separately committed TerminalProof for one task. The
    /// terminal commit must follow the archive-authorizing SaveResult commit;
    /// their commit identities are deliberately different. Verify the exact
    /// scope/Tid/generation/status/result reference against the archive AND
    /// retained owner-fenced terminal authority, including the referenced
    /// closure body and digest. An archive alone never authorizes this write.
    /// The saved projection must fit when the proof claims Completed.
    /// Retain the hot terminal authority and closure until cold confirmation
    /// has been read back; identical confirmed retries need no hot state.
    async fn confirm_terminal_proof(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
        proof: &TerminalProof,
    ) -> Result<IdempotentWrite, StoreError>;
    /// Idempotent persistent transfer of the confirmed proof, readable by
    /// (scope, group, Tid) even after the hot round state is gone; an
    /// uncommitted archive never yields a proof.
    async fn get_terminal_proof(
        &self,
        grants: &ArchiveGrants,
        scope: &TaskScope,
        group: &GroupId,
        tid: &Tid,
    ) -> Result<Option<TerminalProof>, StoreError>;
    /// Controlled completion of a committed archive with the real ordered
    /// artifact parts and the complete public history (plan T7 slice 2).
    /// The default refusal keeps unextended stores honest
    /// (`archive_attach_unconfigured`); extensions must verify every claim
    /// against the sealed checkpoint register before updating the archive
    /// row: a committed archive with empty parts may gain them exactly
    /// once, a conflicting payload is refused, and no path fabricates
    /// `AlreadySaved`/`AlreadyAttached` semantics.
    async fn attach_publication(
        &self,
        round: &RoundKey,
        publication: &PublicPublication,
    ) -> Result<AttachedPublication, StoreError> {
        let _ = (round, publication);
        Err(StoreError::NotApplied {
            code: "archive_attach_unconfigured".into(),
        })
    }
}

#[async_trait]
pub trait BlobStorePort: Send + Sync {
    /// Must reject changed bytes/digest under an existing immutable identity.
    async fn put_immutable(
        &self,
        identity: BlobRef,
        bytes: Vec<u8>,
    ) -> Result<IdempotentWrite, StoreError>;
    async fn get(&self, reference: &BlobRef) -> Result<Option<Vec<u8>>, StoreError>;
}
