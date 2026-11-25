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
use bcs_service_api::port::repo::distributed::{
    common::*,
    group_round::StrictLicense,
    result::*,
    session::*,
};

impl super::WorkerProcessor {
    /// The AwaitingResult strict gate. Returns a replacement candidate when
    /// the visit must register barrier cancels instead of archiving, `None`
    /// when the round may proceed to `archive_confirmed`. Errors propagate.
    #[allow(clippy::too_many_arguments, reason = "sealed head context")]
    pub(super) async fn strict_round_prepares_done(
        &self,
        before: &Checkpoint,
        tid: &Tid,
        now: TimestampMillis,
        completed: &CompletedEffects,
        sid: &Sid,
        receipt: Option<CommitReceipt>,
    ) -> Result<Option<Checkpoint>, RuntimeError> {
        let Some(license) = task_license(before, tid) else {
            return Ok(None);
        };
        // (1) Stop barrier over the sealed attempt set. A missing dispatch
        // record is never read as "no attempts" — the sealed checkpoint
        // registry drove the attempt set, its absence is corruption.
        let mut barrier_cancels = Vec::new();
        for (did, _) in before
            .dispatch_seq
            .iter()
            .filter(|(_, sequence)| sequence.tid == *tid)
        {
            let Some(record) = self.io(self.ports.dispatch.get(did)).await? else {
                return Err(err("barrier_dispatch_missing"));
            };
            let view = record.view;
            if attempt_stopped(&view.execution) {
                continue;
            }
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
        if !barrier_cancels.is_empty() {
            // Intents before effects: the cancel registration commits as its
            // own sealed candidate; the Done transition stays deferred.
            let mut candidate = next_checkpoint(before, Some(completed)).map_err(store_error)?;
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
            return Ok(Some(candidate));
        }
        // (2) The per-task final archive exists before any authority claim.
        let receipt = receipt.ok_or_else(|| err("checkpoint_proof_missing"))?;
        self.ensure_round_archive(before, tid, &license, completed, &receipt)
            .await?;
        Ok(None)
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
    pub(super) async fn strict_round_closes(
        &self,
        before: &Checkpoint,
        tid: &Tid,
    ) -> Result<bool, RuntimeError> {
        let Some(license) = task_license(before, tid) else {
            return Ok(false);
        };
        let Some(rounds) = self.ports.rounds.clone() else {
            return Ok(false);
        };
        let round = license.round_key();
        let retained = retained_terminal(before, tid);
        if let Some(view) = self.io(rounds.get(&round)).await?
            && view.terminal.is_some()
        {
            // The window closed: the successor generation may retire.
            return Ok(false);
        }
        let proof = match retained {
            Some(retained) => retained,
            None => self.build_terminal_proof(before, tid, license)?,
        };
        self.io(rounds.commit_terminal(&round, &proof)).await?;
        Ok(true)
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
    if execution
        .outcome
        .is_some_and(|outcome| !matches!(outcome, DispatchOutcome::Unknown))
    {
        return true;
    }
    // Never begun: no holder claimed a send, no remote task exists. The
    // attempt cannot be in flight; a registered cancel intent keeps
    // future claimants out.
    execution.phase == DispatchPhase::Created && execution.send_proof.is_none()
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
