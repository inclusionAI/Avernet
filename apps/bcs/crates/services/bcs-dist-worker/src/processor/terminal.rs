//! T7 commit spine (plan task 13): one authoritative terminal commit per
//! strict FSM-group round, immediately admitting the next round.
//!
//! The round-closing sequence for a licensed session:
//!
//! 1. **Stop barrier** (at the `AwaitingResult` boundary): every dispatch
//!    attempt the sealed checkpoint registered must be provably stopped from
//!    the durable dispatch facts — a definitive remote outcome, a confirmed
//!    cancellation, or a never-begun attempt. Unknown outcomes, lost replies
//!    and lease expiries are never stop proofs. Unstopped attempts get their
//!    cancel intent registered durably first ("intents before effects"), and
//!    the Done transition waits.
//! 2. **Archive ensure** (same window): the T4 per-task archive is written
//!    from the finalized record sealed in the checkpoint and the exact
//!    committed effect that saved it — evidence-verified, idempotent.
//! 3. **Terminal authority commit** (at the `Done` boundary): the Worker
//!    builds the round's `TerminalProof` from sealed facts and hands it to
//!    `GroupRoundRepo::commit_terminal`, which closes the owner-fenced
//!    window (closure evidence + terminal CAS + cold confirm + read-back)
//!    before the next round's runner-state install may admit.
//!
//! SSE delivery is never a condition of any step above: the feed obligations
//! ride their own pairing (slice 14).
use super::{
    effects::CompletedEffects,
    transitions::{install_effects, next_checkpoint, task_license},
};
use crate::runtime::{err, store_error};
use bcs_service_api::types::dist_runtime::RuntimeError;
use bcs_dist_encoding::{digest_fields, digest_json, hex_of};
use bcs_dist_model::*;
use super::{engine::Slot, publication::{Delivery, sealed_round_publication}};
use bcs_service_api::port::repo::distributed::{
    common::*,
    group_round::StrictLicense,
    result::*,
    session::*,
};

pub(super) enum StopBarrier {
    Ready,
    Register(Checkpoint),
    Waiting,
}

impl super::WorkerProcessor {
    /// A durable cancellation request prevents a duplicate intent, but is not a stop proof.
    #[allow(clippy::too_many_arguments, reason = "sealed head context")]
    pub(super) async fn strict_round_prepares_done(
        &self,
        before: &Checkpoint,
        tid: &Tid,
        now: TimestampMillis,
        completed: &CompletedEffects,
        sid: &Sid,
        receipt: Option<CommitReceipt>,
    ) -> Result<StopBarrier, RuntimeError> {
        let Some(license) = task_license(before, tid) else {
            return Ok(StopBarrier::Ready);
        };
        // (1) Stop barrier over the sealed attempt set. A missing dispatch
        // record is never read as "no attempts" — the sealed checkpoint
        // registry drove the attempt set, its absence is corruption.
        let mut barrier_cancels = Vec::new();
        let mut waiting = false;
        for (did, _) in before
            .dispatch_seq
            .iter()
            .filter(|(_, sequence)| sequence.tid == *tid)
        {
            let Some(record) = self.io(self.ports.dispatch.get(did)).await? else {
                return Err(err("barrier_dispatch_missing"));
            };
            let view = record.view;
            if attempt_stopped(&view.execution)
                || (view.execution.phase == DispatchPhase::Created
                    && view.execution.send_proof.is_none()
                    && view.control.cancel.is_some())
            {
                continue;
            }
            waiting = true;
            if view.control.cancel.is_none() {
                let identity = format!("stop-barrier:{}:{}:{}", sid.0, did.0, before.commit_seq.0);
                barrier_cancels.push(EffectKind::RequestCancel {
                    did: did.clone(),
                    intent: CancelIntent {
                        request_id: RequestId(identity),
                        reason: Some("round_stop_barrier".into()),
                    },
                });
            }
        }
        if waiting && barrier_cancels.is_empty() {
            return Ok(StopBarrier::Waiting);
        }
        // A cancel-only checkpoint replaces the SaveResult outbox. Verify its archive
        // while that original commit is still authoritative and retain its result ref.
        let receipt = receipt.ok_or_else(|| err("checkpoint_proof_missing"))?;
        self.ensure_round_archive(before, tid, &license, completed, &receipt)
            .await?;
        if !barrier_cancels.is_empty() {
            let mut candidate = next_checkpoint(before, Some(completed)).map_err(store_error)?;
            let task = candidate.tasks.iter_mut().find(|task| task.tid == *tid)
                .ok_or_else(|| err("terminal_task_missing"))?;
            if task.result_ref.is_none() {
                task.result_ref = Some(completed.saved.get(tid)
                    .ok_or_else(|| err("round_result_reference_missing"))?.clone());
            }
            install_effects(&mut candidate, sid, barrier_cancels).map_err(store_error)?;
            let digest = digest_fields(&[
                b"stop-barrier:v1",
                tid.0.as_bytes(),
                &candidate.commit_seq.0.to_be_bytes(),
            ]);
            candidate.deadlines.push(Deadline {
                id: DeadlineId(format!("stop-barrier-{}", hex_of(&digest))),
                at: TimestampMillis(
                    now.0.checked_add(1_000).ok_or_else(|| err("barrier_deadline"))?,
                ),
                kind: DeadlineKind::Task { tid: tid.clone() },
            });
            return Ok(StopBarrier::Register(candidate));
        }
        Ok(StopBarrier::Ready)
    }

    /// Write the T4 per-task final archive from the frozen record and the
    /// exact committed SaveResult effect, idempotently. The result digest
    /// comes from the effect completion itself — the same recipe the result
    /// store, the T4 archive and terminal proofs share — so the Worker never
    /// re-derives storage identity.
    async fn ensure_round_archive(
        &self,
        before: &Checkpoint,
        tid: &Tid,
        license: &StrictLicense,
        completed: &CompletedEffects,
        receipt: &CommitReceipt,
    ) -> Result<(), RuntimeError> {
        let _ = &receipt.sid;
        let Some(archives) = self.ports.archives.clone() else {
            return Err(err("round_archive_unconfigured"));
        };
        let grants = ArchiveGrants { read: true };
        let scope = license.scope.clone();
        let group = license.group.clone();
        if let Some(view) = self
            .io(archives.get_archived(&grants, &scope, &group, tid))
            .await?
        {
            return match view {
                ArchivedTaskView::Committed { .. } | ArchivedTaskView::Preparing(_) => Ok(()),
                ArchivedTaskView::Expired { .. } => Err(err("round_archive_tombstoned")),
            };
        }
        let Some(result_reference) = completed.saved.get(tid) else {
            return Err(err("round_result_reference_missing"));
        };
        let record = frozen_record(before, tid)?;
        let archive = TaskFinalArchive {
            schema_version: 2,
            record: record.clone(),
            round: license.round_key(),
            // The complete ordered public history is the paired publication
            // obligation (slice 14); this slice archives exactly what the
            // sealed commit can prove and never guesses gaps.
            public_history: Vec::new(),
            result_digest: result_reference.digest.clone(),
            artifacts: Vec::new(),
        };
        let source = effect_source(before, receipt, completed, tid)?;
        match self.io(archives.archive_final(source, archive)).await? {
            SavedArchive::Saved | SavedArchive::AlreadySaved => Ok(()),
            SavedArchive::ContentConflict => Err(err("round_archive_conflict")),
        }
    }

    /// The Done strict gate. Returns `Ok(true)` when this visit consumed the
    /// round on the terminal authority window (the successor processor
    /// generation retires the round after re-acquisition), `Ok(false)` when
    /// the round may retire through the generic path.
    ///
    /// Slice 14 (plan T7) additions, both before any authority claim:
    /// 1. the committed archive gains the round's real publication (parts +
    ///    complete ordered history) through the guarded attach seam;
    /// 2. the round's own publication identities are probed for the
    ///    persistent conflict class — a second sink disputing the paired
    ///    publications refuses the terminal while the sealed intent stays
    ///    durably registered (C3). Transient refusals never block here.
    pub(super) async fn strict_round_closes(
        &self,
        before: &Checkpoint,
        tid: &Tid,
        slot: &mut Slot,
    ) -> Result<bool, RuntimeError> {
        let Some(license) = task_license(before, tid) else {
            return Ok(false);
        };
        let Some(rounds) = self.ports.rounds.clone() else {
            return Ok(false);
        };
        let round = license.round_key();
        self.attach_round_publication(before, tid, &license, &mut slot.attached_rounds)
            .await?;
        let retained = retained_terminal(before, tid);
        if let Some(view) = self.io(rounds.get(&round)).await?
            && view.terminal.is_some()
        {
            // The window closed: the successor generation may retire.
            return Ok(false);
        }
        // The conflict probe runs over the round's own obligations before
        // any authority claim. Transient refusals never block the close
        // (they remain the follower lane's retry work); a persistent
        // identity conflict refuses it — C3's second sink.
        let sealed_owner = slot.acquired.envelope.owner.clone();
        for publication in before
            .feed_publications
            .iter()
            .filter(|p| p.tid == *tid)
        {
            if slot
                .publication_proofs
                .iter()
                .any(|p| p == &publication.effect)
            {
                continue;
            }
            match self
                .deliver_publication(publication, sealed_owner.clone())
                .await?
            {
                Delivery::Asserted => slot.publication_proofs.push(publication.effect.clone()),
                Delivery::Conflicted => {
                    // C3: the terminal never settles above a disputed
                    // publication identity; the registered intent stays.
                    return Err(err("round_publication_conflict"));
                }
                Delivery::Blocked | Delivery::Failed => break,
            }
        }
        let proof = match retained {
            Some(retained) => retained,
            None => self.build_terminal_proof(before, tid, license)?,
        };
        self.io(rounds.commit_terminal(&round, &proof)).await?;
        // The rounds terminal CAS is an out-of-band fenced envelope writer:
        // this slot's sealed view is stale by definition now. Reconstruct
        // the successor generation's acquisition in place — ownership
        // unchanged, the durable envelope and its reconstructed commit
        // proof reloaded — so retirement and the publication lane continue
        // on the real head instead of a dead one.
        let envelope = self
            .io(self.runtime.sessions.load(&round.sid))
            .await?
            .ok_or_else(|| err("session_envelope_missing"))?;
        let mut acquired = slot.acquired.clone();
        acquired.last_commit = envelope
            .checkpoint
            .outbox
            .as_ref()
            .map(|batch| CommitReceipt {
                sid: round.sid.clone(),
                owner: envelope.owner.clone(),
                commit_id: batch.commit_id.clone(),
                content_digest: batch.content_digest.clone(),
                sequence: batch.source_commit,
                consumed: envelope.checkpoint.consumed.clone(),
                proof: format!(
                    "document:{}:{}:{}",
                    round.sid.0, batch.source_commit.0, batch.commit_id.0
                ),
            });
        acquired.envelope = envelope;
        self.runtime
            .refresh_local_reacquire(&acquired.ownership, acquired.clone())?;
        *slot = Slot::new(acquired);
        Ok(true)
    }

    /// Complete the round's committed archive with the real ordered artifact
    /// parts and the complete public history through the guarded attach
    /// seam. The store re-verifies every field against the sealed register;
    /// a success (fresh or idempotent) marks the round attached so the
    /// follower lane may retire its proven obligations.
    async fn attach_round_publication(
        &self,
        before: &Checkpoint,
        tid: &Tid,
        license: &StrictLicense,
        attached: &mut Vec<Tid>,
    ) -> Result<(), RuntimeError> {
        if attached.iter().any(|t| t == tid) {
            return Ok(());
        }
        let Some(archives) = self.ports.archives.clone() else {
            return Err(err("round_archive_unconfigured"));
        };
        let Some(sealed) = sealed_round_publication(&before.feed_publications, tid)
        else {
            attached.push(tid.clone());
            return Ok(());
        };
        let publication = PublicPublication {
            schema_version: 1,
            effects: sealed.effects,
            history: sealed.history,
            artifacts: sealed.artifacts,
        };
        match self
            .io(archives.attach_publication(&license.round_key(), &publication))
            .await?
        {
            AttachedPublication::Attached | AttachedPublication::AlreadyAttached => {
                attached.push(tid.clone());
                Ok(())
            }
            AttachedPublication::ContentConflict => {
                Err(err("round_publication_attach_conflict"))
            }
        }
    }

    /// Build the round's terminal proof from sealed facts only: the frozen
    /// final record, the archived result reference and the current authority
    /// head. The closure evidence digest fixes `{"key": round,
    /// "stopped": true}` — the stop barrier proved exactly that.
    fn build_terminal_proof(
        &self,
        before: &Checkpoint,
        tid: &Tid,
        license: StrictLicense,
    ) -> Result<TerminalProof, RuntimeError> {
        let task = before
            .tasks
            .iter()
            .find(|task| task.tid == *tid && matches!(task.phase, TaskPhase::Done { .. }))
            .ok_or_else(|| err("terminal_done_missing"))?;
        let finalization = task
            .finalization
            .as_ref()
            .ok_or_else(|| err("terminal_finalization_missing"))?;
        let result_ref = task
            .result_ref
            .as_ref()
            .ok_or_else(|| err("terminal_result_ref_missing"))?
            .clone();
        let round = license.round_key();
        let closure = serde_json::json!({"key": &round, "stopped": true});
        Ok(TerminalProof {
            schema_version: RoundSchemaVersion::V1,
            key: round.clone(),
            commit_id: bcs_service_api::port::repo::distributed::group_round::terminal_proof_identity(
                &round,
                &result_ref.digest,
                &before.commit_seq,
            ),
            commit_seq: CommitSeq(
                before
                    .commit_seq
                    .0
                    .checked_add(1)
                    .ok_or_else(|| err("commit_sequence_exhausted"))?,
            ),
            status: finalization.record.status,
            result_ref,
            closure_ref: BlobRef {
                key: format!("closure:{}", tid.0),
                digest: digest_json(&closure),
            },
        })
    }
}

/// Classify one attempt's durable stop facts. Uncertainty is not evidence.
fn attempt_stopped(execution: &DispatchExecution) -> bool {
    if matches!(
        execution.cancellation,
        CancelObservation::ConfirmedCanceled | CancelObservation::AlreadyFinished { .. }
    ) {
        return true;
    }
    execution.outcome.is_some_and(|outcome| outcome != DispatchOutcome::Unknown)
}

/// The frozen final record of a terminal task.
fn frozen_record(before: &Checkpoint, tid: &Tid) -> Result<FinalTaskRecord, RuntimeError> {
    let task = before
        .tasks
        .iter()
        .find(|task| task.tid == *tid)
        .ok_or_else(|| err("terminal_task_missing"))?;
    let finalization = task
        .finalization
        .as_ref()
        .ok_or_else(|| err("terminal_finalization_missing"))?;
    Ok(finalization.record.clone())
}

/// The evidence-verified source of the archive write: the last committed
/// receipt (the sealed owner, position and identity of the head) plus the
/// SaveResult effect's sealed identity.
fn effect_source(
    before: &Checkpoint,
    receipt: &CommitReceipt,
    completed: &CompletedEffects,
    tid: &Tid,
) -> Result<EffectSource, RuntimeError> {
    if receipt.commit_id != completed.commit_id || receipt.sequence != completed.sequence {
        return Err(err("terminal_receipt_mismatch"));
    }
    let batch = before.outbox.as_ref().ok_or_else(|| err("outbox_missing"))?;
    let effect = batch
        .effects
        .iter()
        .find(|effect| {
            matches!(effect.kind, EffectKind::SaveResult { .. })
                && effect.id.sid == receipt.sid
        })
        .ok_or_else(|| err("terminal_save_effect_missing"))?;
    let _ = tid;
    Ok(EffectSource {
        commit: receipt.clone(),
        effect_id: effect.id.clone(),
        content_digest: effect.content_digest.clone(),
    })
}

/// The retained terminal proof sealed by the authority commit.
fn retained_terminal(checkpoint: &Checkpoint, tid: &Tid) -> Option<TerminalProof> {
    checkpoint
        .runner_state
        .as_ref()?
        .state
        .value
        .get("group_terminal")
        .cloned()
        .and_then(|value| serde_json::from_value::<TerminalProof>(value).ok())
        .filter(|proof| proof.key.tid == *tid)
}
