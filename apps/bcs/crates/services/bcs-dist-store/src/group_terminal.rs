//! The T7 terminal commit spine (plan task 13), shared by the strict-group
//! admission store: one owner-fenced window turns a licensed round's Done
//! facts plus archive into the durable terminal authority —
//!
//! 1. the closure evidence document is written (idempotent, identity-bound),
//! 2. the terminal CAS installs the retained `group_terminal` proof next to
//!    the surviving `group_admission` license in the checkpoint's runner
//!    state — through the ordinary sealed-owner checkpoint commit, so late
//!    legacy writes carry an exact `commit_seq` and stay fenced,
//! 3. the cold proof is confirmed against the committed T4 archive and read
//!    back — all before the window closes and the next round's runner-state
//!    installation may admit (controller ruling A2: otherwise the successor
//!    round's install makes this round's confirmation permanently
//!    unreachable and re-confirmation deadlocks as OutcomeUnknown).
//!
//! The spine deliberately keeps the license marker until the cold transfer:
//! probe callers (inbox producer, task reader, admission classification)
//! still see the licensed session. Stop evidence is never second-guessed
//! here: the barrier that proves every attempt stopped lives with the Worker
//! (it owns the dispatch facts); this store only verifies sealed authority.
use crate::{
    error::from_read,
    group_admission::{
        GroupRequestRow, StoreGroupRoundAdmission, StrictLicense,
        invalid, probe_strict_license,
    },
    layout::KeyLayout,
};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    common::{IdempotentWrite, StoreError},
    group_round::{
        GroupRoundRepo, GroupRoundView, GroupRequestIdentity, GroupRequestRecord,
    },
    session::{Checkpoint, CommittedBatch, TaskPhase, TaskRunnerState},
};
use bcs_store_api::document::DocumentStorePlugin;
use serde::{Deserialize, Serialize};
use std::sync::Arc;

/// The control revision currently recorded with the sealed checkpoint
/// document, for ordinary commits that must pass the control fence.
fn busy(code: &str) -> StoreError {
    StoreError::NotApplied { code: code.into() }
}

pub(crate) async fn probe_control_revision(
    documents: &Arc<dyn DocumentStorePlugin>,
    layout: &KeyLayout,
    sid: &Sid,
) -> Result<Option<u64>, StoreError> {
    let key = layout.ckpt(sid)?;
    let raw = documents
        .read_document(&key)
        .await
        .map_err(|e| from_read(&e))?;
    Ok(raw.and_then(|v| {
        v.value
            .pointer("/control/revision")
            .and_then(|value| value.as_u64())
    }))
}

/// The retained terminal proof of a licensed round, as sealed in the
/// checkpoint's runner state by the terminal CAS. One bounded document read;
/// callers must still verify it against the cold store before treating it
/// as public success.
pub async fn probe_retained_terminal(
    documents: &Arc<dyn DocumentStorePlugin>,
    layout: &KeyLayout,
    sid: &Sid,
) -> Result<Option<TerminalProof>, StoreError> {
    let key = layout.ckpt(sid)?;
    let raw = documents
        .read_document(&key)
        .await
        .map_err(|e| from_read(&e))?;
    Ok(raw
        .and_then(|v| {
            v.value
                .pointer("/checkpoint/runner_state/state/value/group_terminal")
                .cloned()
        })
        .and_then(|value| serde_json::from_value::<TerminalProof>(value).ok()))
}

impl StoreGroupRoundAdmission {
    /// True when the checkpoint retains the exact licensed round's terminal
    /// authority (Done task, result reference and sealed `group_terminal`).
    /// A Done task without the retained proof means the spine window is
    /// still open — the successor's runner-state install must wait.
    pub(super) fn terminal_authority_retained(
        checkpoint: &Checkpoint,
        license: &StrictLicense,
        proof: &TerminalProof,
    ) -> bool {
        checkpoint.runner_state.as_ref().is_some_and(|state| {
            state.tid == license.tid
                && state
                    .state
                    .value
                    .get("group_terminal")
                    .is_some_and(|retained| {
                        serde_json::from_value::<TerminalProof>(retained.clone())
                            .is_ok_and(|decoded| &decoded == proof)
                    })
        }) && checkpoint.tasks.iter().any(|task| {
            task.tid == license.tid
                && task.phase == TaskPhase::Done
                && task.result_ref.as_ref() == Some(&proof.result_ref)
        })
    }

    /// Cold-confirmed terminal proof of the licensed round, if the window
    /// already closed. One bounded archive read.
    pub(super) async fn cold_terminal_proof(
        &self,
        license: &StrictLicense,
    ) -> Result<Option<TerminalProof>, StoreError> {
        let Some(artifacts) = self.artifacts() else {
            return Ok(None);
        };
        artifacts
            .get_terminal_proof(
                &bcs_service_api::port::repo::distributed::result::ArchiveGrants { read: true },
                &license.scope,
                &license.group,
                &license.tid,
            )
            .await
    }

    /// Complete the spine window for a still-open licensed round: confirm
    /// the retained proof against the cold archive and read it back. Two
    /// bounded archive calls; idempotent — an already confirmed proof
    /// returns without touching the checkpoint.
    pub(super) async fn finish_terminal_window(
        &self,
        checkpoint: &Checkpoint,
        license: &StrictLicense,
    ) -> Result<bool, StoreError> {
        let Some(proof) = retained_terminal_of(checkpoint, license) else {
            return Ok(false);
        };
        let Some(artifacts) = self.artifacts() else {
            return Ok(false);
        };
        match artifacts
            .confirm_terminal_proof(&license.scope, &license.group, &license.tid, &proof)
            .await?
        {
            // A conflicting cold proof is sealed corruption of the window:
            // the write failure propagates, the window stays open.
            IdempotentWrite::ContentConflict => {
                return Err(invalid("terminal_proof_conflict"));
            }
            IdempotentWrite::Applied | IdempotentWrite::AlreadyApplied => {}
        }
        Ok(artifacts
            .get_terminal_proof(
                &bcs_service_api::port::repo::distributed::result::ArchiveGrants { read: true },
                &license.scope,
                &license.group,
                &license.tid,
            )
            .await?
            .is_some_and(|confirmed| confirmed == proof))
    }

    /// The production T7 terminal commit (plan interface
    /// `commit_terminal(round, proof) -> CommitSeq`): runs the whole
    /// owner-fenced window synchronously — closure evidence, terminal CAS,
    /// cold confirmation and read-back.
    pub(crate) async fn commit_terminal_authority(
        &self,
        round: &RoundKey,
        proof: &TerminalProof,
    ) -> Result<CommitSeq, StoreError> {
        let artifacts = self.artifacts().ok_or_else(|| {
            busy("terminal_archive_unconfigured")
        })?;
        if proof.key != *round {
            return Err(invalid("terminal_proof_round"));
        }
        let sid = round.sid.clone();
        let snapshot = self.acquire(&sid).await?;
        let checkpoint = &snapshot.envelope.checkpoint;
        let license = self
            .license(&snapshot)
            .await
            .filter(|license| license.round_key() == *round)
            .ok_or_else(|| invalid("terminal_license_missing"))?;
        let closed = Self::terminal_authority_retained(checkpoint, &license, proof);
        if !closed
            && !checkpoint.tasks.iter().any(|task| {
                task.tid == license.tid
                    && task.phase == TaskPhase::Done
                    && task.result_ref.as_ref() == Some(&proof.result_ref)
            })
        {
            return Err(invalid("terminal_done_missing"));
        }
        let done_intent = checkpoint
            .tasks
            .iter()
            .find(|task| {
                task.tid == license.tid
                    && task.phase == TaskPhase::Done
                    && task.result_ref.as_ref() == Some(&proof.result_ref)
            })
            .and_then(|task| task.finalization.as_ref())
            .map(|frozen| frozen.record.status);
        if done_intent != Some(proof.status) || proof.schema_version != RoundSchemaVersion::V1 {
            return Err(invalid("terminal_proof_mismatch"));
        }
        // The closure evidence: written before the CAS, identity-bound and
        // digest-checked by the cold confirm's verified authority.
        let closure = serde_json::json!({"key": round, "stopped": true});
        if bcs_dist_encoding::digest_json(&closure).0 != proof.closure_ref.digest.0 {
            return Err(invalid("terminal_closure_digest"));
        }
        let retained_closure = self
            .documents
            .read_document(&proof.closure_ref.key)
            .await
            .map_err(|e| from_read(&e))?;
        // A concurrent writer's document must be this proof's exact closure
        // bytes; anything else never satisfies this window, independent of
        // the later confirm-side verification.
        if let Some(retained) = &retained_closure
            && bcs_dist_encoding::digest_json(&retained.value).0 != proof.closure_ref.digest.0
        {
            return Err(invalid("terminal_closure_digest"));
        }
        if retained_closure.is_none() {
            match self.documents.create_document(&proof.closure_ref.key, &closure).await {
                Ok(bcs_store_api::WriteOutcome::Applied { .. }) => {}
                Ok(_) => {
                    if self
                        .documents
                        .read_document(&proof.closure_ref.key)
                        .await
                        .map_err(|e| from_read(&e))?
                        .is_none()
                    {
                        return Err(busy("terminal_closure_unavailable"));
                    }
                }
                Err(_) => {
                    // The closure document never durably appeared: the write
                    // failure propagates, the window stays open.
                    if self
                        .documents
                        .read_document(&proof.closure_ref.key)
                        .await
                        .map_err(|e| from_read(&e))?
                        .is_none()
                    {
                        return Err(busy("terminal_closure_unavailable"));
                    }
                }
            }
        }
        if !closed {
            let sequence = proof.commit_seq;
            // The authority CAS carries the control revision the sealed
            // envelope currently holds: an ordinary commit is only allowed
            // at the head the control fence accepts.
            let control_revision = probe_control_revision(&self.documents, &self.layout, &sid)
                .await?
                .ok_or_else(|| invalid("terminal_control_missing"))?;
            let mut candidate = snapshot.envelope.checkpoint.clone();
            if Some(sequence.0) != checkpoint.commit_seq.0.checked_add(1) {
                return Err(invalid("terminal_sequence"));
            }
            candidate.commit_seq = sequence;
            // The terminal CAS condenses the closing round's hot authority
            // but must carry the existing cold index verbatim: rounds the
            // install chain already moved to the cold cut stay there, or a
            // successor install would read this runner state as if they had
            // never closed and `get(predecessor)` would be lost forever.
            let mut value = serde_json::json!({
                "group_admission": license,
                "group_terminal": proof,
            });
            if let Some(cold) = checkpoint
                .runner_state
                .as_ref()
                .and_then(|state| state.state.value.get(COLD_INDEX_KEY))
            {
                value[COLD_INDEX_KEY] = cold.clone();
            }
            candidate.runner_state = Some(TaskRunnerState {
                tid: license.tid.clone(),
                state: RunnerState {
                    format_version: 1,
                    value,
                },
            });
            let commit_id = proof.commit_id.clone();
            // Same whole-checkpoint digest convention as the Worker's sealed
            // commits: install the new batch with the placeholder digest,
            // hash the complete sealed candidate, then store the digest.
            candidate.outbox = Some(CommittedBatch {
                created_at: None,
                commit_id: commit_id.clone(),
                source_commit: sequence,
                content_digest: Digest(String::new()),
                effects: vec![],
            });
            let digest = super::group_admission::authority_digest(&candidate)?;
            candidate.outbox = Some(CommittedBatch {
                created_at: None,
                commit_id: commit_id.clone(),
                source_commit: sequence,
                content_digest: digest.clone(),
                effects: vec![],
            });
            use bcs_service_api::port::repo::distributed::session::{
                CommitOutcome, CommitPurpose, CommitRequest,
            };
            match self
                .sessions
                .commit(CommitRequest {
                    purpose: CommitPurpose::Ordinary { control_revision },
                    owner: snapshot.ownership.clone(),
                    expected_commit_seq: checkpoint.commit_seq,
                    commit_id,
                    content_digest: digest,
                    candidate,
                })
                .await?
            {
                CommitOutcome::Committed(_) | CommitOutcome::AlreadyCommitted(_) => {}
                CommitOutcome::Fenced => return Err(busy("strict_session_fenced")),
                _ => {
                    // A concurrent writer advanced the authority: idempotence
                    // resolves on re-read, anything else stays an error.
                    let again = self.acquire(&sid).await?;
                    if !Self::terminal_authority_retained(
                        &again.envelope.checkpoint,
                        &license,
                        proof,
                    ) {
                        return Err(invalid("terminal_authority_conflict"));
                    }
                }
            }
        }
        // Cold confirm inside the same window, then read back.
        match artifacts
            .confirm_terminal_proof(&license.scope, &license.group, &license.tid, proof)
            .await?
        {
            IdempotentWrite::ContentConflict => {
                return Err(invalid("terminal_proof_conflict"));
            }
            IdempotentWrite::Applied | IdempotentWrite::AlreadyApplied => {}
        }
        if artifacts
            .get_terminal_proof(
                &bcs_service_api::port::repo::distributed::result::ArchiveGrants { read: true },
                &license.scope,
                &license.group,
                &license.tid,
            )
            .await?
            != Some(proof.clone())
        {
            return Err(busy("terminal_cold_transfer_unconfirmed"));
        }
        Ok(proof.commit_seq)
    }
}

/// 关闭轮次后的有界压缩 (plan T7): drop the closed round's hot history from
/// the checkpoint — the retained `group_terminal` authority and the cold
/// proof this window confirms carry its public truth from now on. Only the
/// closing license's own task facts move; everything the ledger still owes
/// stays exactly where it was.
///
/// Retained, verbatim:
/// - the `last_event_id` / `consumed` watermarks (拒绝旧事件),
/// - the runner state (`group_admission` license + `group_terminal` proof;
///   the next-generation install condenses it into the per-Tid cold proof),
/// - every unfinished obligation: all `feed_publications` entries (their
///   completion is proven by the publication lane, never by this window),
///   every `dispatch_seq` entry (dispatch supervision watermarks),
/// - every other task, deadline and view draft (work not closed here).
///
/// Dropped, for the closed task alone: its `TaskProgress` (the biggest
/// per-round artifact — the frozen input payload and finalization record,
/// whose authority was just sealed into the runner state), its `view_drafts`
/// entry, and its `deadlines` / `deadline_history` identities.
pub fn compact_closed_round(checkpoint: &mut Checkpoint, license: &StrictLicense) {
    let tid = &license.tid;
    checkpoint.tasks.retain(|task| task.tid != *tid);
    checkpoint.view_drafts.remove(tid);
    checkpoint
        .deadlines
        .retain(|deadline| !matches!(&deadline.kind, DeadlineKind::Task { tid: closed } if closed == tid));
    checkpoint.deadline_history.retain(|_, deadline| {
        !matches!(&deadline.kind, DeadlineKind::Task { tid: closed } if closed == tid)
    });
}

/// Build the terminal proof of an open window purely from sealed facts: the
/// Done task's frozen record, its archived result reference, and the head
/// the authority CAS will take. The identity is the shared deterministic
/// recipe, so a gate-driven close and a Worker-driven close write the same
/// authority or fail as a conflict — never two.
pub fn sealed_window_proof(checkpoint: &Checkpoint, license: &StrictLicense) -> Option<TerminalProof> {
    use bcs_service_api::port::repo::distributed::group_round::terminal_proof_identity;
    let task = checkpoint
        .tasks
        .iter()
        .find(|task| {
            task.tid == license.tid
                && matches!(task.phase, TaskPhase::Done { .. })
                && task.finalization.is_some()
        })?;
    let result_ref = task.result_ref.as_ref()?;
    let commit_seq = CommitSeq(
        checkpoint
            .commit_seq
            .0
            .checked_add(1)?,
    );
    let round = license.round_key();
    let closure = serde_json::json!({"key": &round, "stopped": true});
    Some(TerminalProof {
        schema_version: RoundSchemaVersion::V1,
        key: round.clone(),
        commit_id: terminal_proof_identity(
            &round,
            &result_ref.digest,
            &CommitSeq(commit_seq.0.saturating_sub(1)),
        ),
        commit_seq,
        status: task.finalization.as_ref()?.record.status,
        result_ref: result_ref.clone(),
        closure_ref: BlobRef {
            key: format!("closure:{}", license.tid.0),
            digest: bcs_dist_encoding::digest_json(&closure),
        },
    })
}

fn retained_terminal_of(
    checkpoint: &Checkpoint,
    license: &StrictLicense,
) -> Option<TerminalProof> {
    checkpoint
        .runner_state
        .as_ref()?
        .state
        .value
        .get("group_terminal")
        .cloned()
        .and_then(|value| serde_json::from_value::<TerminalProof>(value).ok())
        .filter(|proof| proof.key == license.round_key())
}

impl StoreGroupRoundAdmission {
    /// The licensed round's public terminal and result, disclosed only
    /// through the authoritative cut (the cold-confirmed proof and its
    /// committed archive). An archive that merely exists contributes
    /// nothing until the window closed.
    pub(crate) async fn confirmed_round_view(
        &self,
        license: &StrictLicense,
        key: &RoundKey,
    ) -> Result<(Option<TerminalProof>, Option<Payload>), StoreError> {
        use bcs_service_api::port::repo::distributed::result::{ArchiveGrants, ArchivedTaskView};
        let Some(artifacts) = self.artifacts() else {
            return Ok((None, None));
        };
        let grants = ArchiveGrants { read: true };
        let Some(proof) = artifacts
            .get_terminal_proof(&grants, &license.scope, &license.group, &license.tid)
            .await?
        else {
            return Ok((None, None));
        };
        if proof.key != *key {
            return Ok((None, None));
        }
        let result = match artifacts
            .get_archived(&grants, &key.scope, &key.group, &key.tid)
            .await?
        {
            Some(ArchivedTaskView::Committed { archive, .. })
                if archive.round == *key && archive.record.sid == key.sid =>
            {
                Some(archive.record.result.clone())
            }
            _ => None,
        };
        Ok((Some(proof), result))
    }

    /// Compose the public view of a cold-confirmed round through the frozen
    /// request row: the cold index entry retained by the session names the
    /// row, the T4 archive proves the terminal, and nothing is fabricated.
    /// A row, proof or archive that fails identity ends the read as absent.
    pub(crate) async fn cold_round_view(
        &self,
        entry: &ColdIndexEntry,
        key: &RoundKey,
    ) -> Result<Option<GroupRoundView>, StoreError> {
        use bcs_service_api::port::repo::distributed::result::{ArchiveGrants, ArchivedTaskView};
        let Some(artifacts) = self.artifacts() else {
            return Ok(None);
        };
        let row_key = self.layout.group_request(
            &key.scope,
            &key.group,
            &RequestId(entry.request_id.clone()),
        )?;
        let Some((_, row)) = self.load::<GroupRequestRow>(&row_key).await? else {
            return Ok(None);
        };
        if row.request.input.key != *key {
            return Ok(None);
        }
        let grants = ArchiveGrants { read: true };
        let Some(proof) = artifacts
            .get_terminal_proof(&grants, &key.scope, &key.group, &key.tid)
            .await?
        else {
            return Ok(None);
        };
        let result = match artifacts
            .get_archived(&grants, &key.scope, &key.group, &key.tid)
            .await?
        {
            Some(ArchivedTaskView::Committed { archive, .. })
                if archive.round == *key && archive.record.sid == key.sid =>
            {
                Some(archive.record.result.clone())
            }
            _ => None,
        };
        Ok(Some(GroupRoundView {
            accepted: row.request.clone(),
            terminal: Some(proof),
            result,
        }))
    }
}

/// One closed round's cold index entry, retained by the session checkpoint
/// after the next round installs: `tid` plus the frozen request identity
/// that maps back to the request registry row. The temporary hot marker
/// (`group_terminal`) condenses into this entry once the owner-fenced
/// window closed.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ColdIndexEntry {
    pub schema_version: u32,
    pub tid: Tid,
    pub request_id: String,
    pub request_digest: String,
}

/// The runner-state key of the closed rounds' cold index.
pub(crate) const COLD_INDEX_KEY: &str = "group_cold";

/// Probe a closed round's cold index entry from the session checkpoint
/// document. One bounded document read; the cold proof must still be
/// verified through the archive before public disclosure.
pub async fn probe_cold_index(
    documents: &Arc<dyn DocumentStorePlugin>,
    layout: &KeyLayout,
    sid: &Sid,
    tid: &Tid,
) -> Result<Option<ColdIndexEntry>, StoreError> {
    let key = layout.ckpt(sid)?;
    let raw = documents
        .read_document(&key)
        .await
        .map_err(|e| from_read(&e))?;
    let Some(value) = raw.and_then(|v| {
        v.value
            .pointer("/checkpoint/runner_state/state/value/group_cold")
            .cloned()
    }) else {
        return Ok(None);
    };
    let Some(entries) = value.as_array() else {
        return Ok(None);
    };
    Ok(entries
        .iter()
        .filter_map(|entry| {
            serde_json::from_value::<ColdIndexEntry>(entry.clone()).ok()
        })
        .find(|entry| &entry.tid == tid && entry.schema_version == 1))
}

// The repository entry surface lives with the terminal spine: admission,
// lookup and public reads all close through the same owner-fenced window
// this module owns (plan T7).
#[async_trait]
impl GroupRoundRepo for StoreGroupRoundAdmission {
    /// The T7 terminal commit spine: closure evidence, the owner-fenced
    /// terminal CAS, then cold confirmation and read-back — in one window.
    async fn commit_terminal(
        &self,
        round: &RoundKey,
        proof: &TerminalProof,
    ) -> Result<CommitSeq, StoreError> {
        self.commit_terminal_authority(round, proof).await
    }

    async fn lookup(
        &self,
        identity: &GroupRequestIdentity<'_>,
    ) -> Result<GroupRequestRecord, StoreError> {
        let parts = identity.components();
        if parts.len() != 5 || parts[0] != "group_a2a:v1" {
            return Err(invalid("group_request_identity"));
        }
        let scope = TaskScope {
            tenant: TenantId(parts[1].into()),
            principal: PrincipalId(parts[2].into()),
        };
        let group = GroupId(parts[3].into());
        let request_id = RequestId(parts[4].into());
        let key = self.layout.group_request(&scope, &group, &request_id)?;
        let Some((_, row)) = self.load::<GroupRequestRow>(&key).await? else {
            return Ok(GroupRequestRecord::Absent);
        };
        // Abandonment is checkout by the fence, not a fresh message key; the
        // row stays as evidence. Expired tombstones answer before everything.
        if row.state == "abandoned" {
            return Ok(GroupRequestRecord::Absent);
        }
        if row.state == "expired" {
            return Ok(GroupRequestRecord::Expired);
        }
        if row.state != "prepared" && row.state != "accepted" {
            return Err(invalid("group_request_state"));
        }
        if row.state == "accepted" {
            // The round installed under this message key once; supersession
            // does not rewrite first identity.
            return Ok(GroupRequestRecord::Accepted(row.request.clone()));
        }
        let license =
            probe_strict_license(&self.documents, &self.layout, &row.request.input.key.sid)
                .await?;
        if license.is_some_and(|l| {
            l.request_id == row.request.request_id.0
                && l.request_digest == row.request.request_digest.0
        }) {
            return Ok(GroupRequestRecord::Accepted(row.request.clone()));
        }
        Ok(GroupRequestRecord::Prepared(row.request.clone()))
    }

    async fn admit(&self, request: &AcceptedRound) -> Result<Admission, StoreError> {
        let key = &request.input.key;
        if request.input.schema_version != RoundSchemaVersion::V1 {
            return Err(StoreError::UnsupportedFormat { format: 1 });
        }
        if !matches!(request.input.source, GroupRoundSource::GroupA2a) {
            return Err(invalid("group_round_source"));
        }
        if key.generation.get() == 0 {
            return Err(invalid("group_round_generation"));
        }
        KeyLayout::shard_of(&key.sid)?;
        self.layout.ckpt(&key.sid)?;
        self.layout.group_request(&key.scope, &key.group, &request.request_id)?;
        if request.request_id.0.is_empty() {
            return Err(invalid("group_request_identity"));
        }
        self.check_binding(request).await?;
        // Zero-write classification against the sealed authority.
        let snapshot = self.acquire(&key.sid).await?;
        let license = self.license(&snapshot).await;
        let row = self.row(request).await?;
        let row_lane = match &row {
            Some((_, row)) => Some((row.state.as_str(), row.request.clone())),
            None => None,
        };
        if matches!(row_lane, Some(("expired", _))) {
            return Ok(Admission::Expired);
        }
        match &license {
            Some(license) if license.request_id == request.request_id.0 => {
                let original = match row_lane {
                    Some(("prepared", original)) | Some(("accepted", original)) => original,
                    // The row is destructible only via the T4 lifecycle; the
                    // license still pins the first identity.
                    None if license.request_digest == request.request_digest.0 => {
                        request.clone()
                    }
                    None => return Ok(Admission::Conflict),
                    Some(_) => return Ok(Admission::Conflict),
                };
                let admitted = Self::replay(&original, request);
                if matches!(admitted, Admission::Replay { .. }) {
                    // Recovery/aftermath repair for the same accepted round;
                    // the Start entry stays exactly one durable record.
                    self.aftermath(request).await?;
                }
                return Ok(admitted);
            }
            Some(license) => {
                // A stored, installed round with this message id answers by
                // its own first identity even after a superseding round took
                // the session: the retry keeps reusing the original Tid. An
                // uninstalled (pending) row never rides this path — it may
                // still be busy or re-attempted once the session frees.
                if let Some(("accepted", original)) = &row_lane
                    && original.request_id == request.request_id
                    && original.input.key.tid == request.input.key.tid
                {
                    return if original.request_digest != request.request_digest {
                        Ok(Admission::Conflict)
                    } else {
                        Ok(Self::replay(original, request))
                    };
                }
                if Self::round_busy(license, &snapshot) {
                    return Ok(Admission::Busy);
                }
                // Controller ruling A2: the previous round's cold terminal
                // proof must be transferred (and read back) before this
                // round's runner-state install makes its confirmation
                // unreachable. An open window repairs itself once (the
                // retained proof re-confirms), then stays Busy until the
                // spine truly closed. Deployments that did not join the T4
                // archive keep the pre-T7 classification untouched.
                if self.artifacts.is_some()
                    && !self.license_window_closed(&snapshot, license).await?
                {
                    return Ok(Admission::Busy);
                }
                // Every new-generation install pins the license chain: a
                // stale (old) generation of any message id — including a
                // re-sent forfeited round — never installs and never
                // resurrects; only the exact expected next generation does.
                if request.input.key.generation.get() != license.generation.get().saturating_add(1)
                {
                    return Err(invalid("stale_generation"));
                }
                // Install re-checks the row identity: a stored row with this
                // message id and changed normalization answers Conflict.
                return self.install(request, snapshot, row).await;
            }
            None => {
                // No license yet: legacy or queued leftovers keep it busy.
                if snapshot
                    .envelope
                    .checkpoint
                    .tasks
                    .iter()
                    .any(|t| t.phase != TaskPhase::Done)
                {
                    return Ok(Admission::Busy);
                }
                if let Some((_, original)) = &row_lane {
                    if original.request_id == request.request_id
                        && original.request_digest != request.request_digest
                    {
                        return Ok(Admission::Conflict);
                    }
                }
                if request.input.key.generation.get() != 1 {
                    return Err(invalid("stale_generation"));
                }
                return self.install(request, snapshot, row).await;
            }
        }
    }

    async fn get(&self, key: &RoundKey) -> Result<Option<GroupRoundView>, StoreError> {
        // The live-licensed path first: the frozen request row composes the
        // view, and the terminal/result fields come from the SAME
        // authoritative cut (the cold-confirmed proof), never from an
        // archive row that merely exists.
        let license = probe_strict_license(&self.documents, &self.layout, &key.sid).await?;
        if let Some(license) = license.clone().filter(|l| l.round_key() == *key) {
            let row_key = self.layout.group_request(
                &key.scope,
                &key.group,
                &RequestId(license.request_id.clone()),
            )?;
            let Some((_, row)) = self.load::<GroupRequestRow>(&row_key).await? else {
                return Err(StoreError::NotApplied {
                    code: "group_request_row_missing".into(),
                });
            };
            if row.request.input.key != *key {
                return Ok(None);
            }
            // Read errors from the authoritative proof/archive surface
            // as errors (the port's own contract); they never downgrade
            // the round to a terminal-less public view.
            let (terminal, result) = self.confirmed_round_view(&license, &key).await?;
            return Ok(Some(GroupRoundView {
                accepted: row.request.clone(),
                terminal,
                result,
            }));
        }
        // A closed round superseded by a newer license stays readable
        // through its retained cold index entry and the frozen request row;
        // unconfirmed rounds never yield a public terminal.
        if let Some(entry) =
            probe_cold_index(&self.documents, &self.layout, &key.sid, &key.tid)
                .await?
        {
            return self.cold_round_view(&entry, key).await;
        }
        Ok(None)
    }
}
