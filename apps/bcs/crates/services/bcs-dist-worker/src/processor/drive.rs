use super::{commit::*, effects::*, engine::*};
use crate::runtime::{err, store_error};
use bcs_dist_model::*;
use bcs_service_api::{
    port::repo::distributed::{group_round::decode_strict_license, inbox::*, session::*},
    types::dist_runtime::RuntimeError,
};

impl WorkerProcessor {
    pub(super) async fn step(&self, slot: &mut Slot) -> Result<(), RuntimeError> {
        let owner = slot.acquired.ownership.clone();
        if !self.runtime.may_continue_round(&owner)? {
            return Err(err("worker_not_ready"));
        }
        if slot.releasing {
            self.runtime.release_session(&owner).await?;
            return Ok(());
        }
        if slot.parking_replay {
            return self.park_replay(slot).await;
        }
        if let Some(request) = slot.pending.clone() {
            let resolution = self
                .io(self.runtime.sessions.resolve_commit(
                    &owner,
                    &request.commit_id,
                    &request.content_digest,
                ))
                .await?;
            match resolution {
                CommitResolution::Committed(receipt) => self.confirm(slot, &request, receipt)?,
                CommitResolution::DefinitelyNotCommitted | CommitResolution::OutcomeUnknown => {
                    self.issue(slot, request).await?
                }
                CommitResolution::Fenced => {
                    self.runtime.forget_fenced(&owner).await?;
                    return Err(err("commit_fenced"));
                }
                CommitResolution::ContentConflict => return Err(err("commit_content_conflict")),
            }
            return Ok(());
        }
        if let Err(error) = self.refresh_control(slot).await {
            if error.code == "control_head_conflict" && self.reload_strict_head(slot).await? {
                return Ok(());
            }
            return Err(error);
        }
        self.reconcile_control_fence(slot)?;
        // The strict-group publication follower lane (plan T7 slice 2):
        // deliver sealed feed obligations in stable ID order — never a
        // synchronous precondition of any transition below — and let its
        // bounded housekeeping commit retire proven obligations.
        self.sweep_feed_publications(slot).await?;
        if slot.risk_outcome.is_some() {
            self.finish_risk(slot).await?;
            return Ok(());
        }
        if slot.control.as_ref().is_some_and(|c|matches!(c.control.mode,bcs_service_api::port::repo::distributed::control::ControlMode::Blocked { .. }|bcs_service_api::port::repo::distributed::control::ControlMode::Recovering { .. })) {
            self.drive_control(slot).await?;
            return Ok(());
        }
        if slot.effects_pending || slot.recovering {
            if let Some(batch) = &slot.acquired.envelope.checkpoint.outbox {
                let receipt = slot
                    .acquired
                    .last_commit
                    .as_ref()
                    .ok_or_else(|| err("checkpoint_proof_missing"))?;
                verify_checkpoint(&slot.acquired.envelope.checkpoint, receipt)
                    .map_err(store_error)?;
                let batch = batch.clone();
                let receipt = receipt.clone();
                let completed = self.replay_risk(slot, &batch, &receipt).await?;
                let Some(completed) = completed else {
                    return Ok(());
                };
                slot.completed = Some(completed);
                slot.replay = None;
            } else if slot.acquired.envelope.checkpoint.commit_seq != CommitSeq(0) {
                return Err(err("checkpoint_proof_missing"));
            }
            if slot.recovering {
                self.runtime.recovery_completed(&owner).await?;
                slot.reader_paused = false;
            } else {
                self.runtime.effects_completed(&owner)?;
            }
            slot.recovering = false;
            slot.effects_pending = false;
            return Ok(());
        }
        if slot
            .prepared
            .as_ref()
            .is_some_and(|p| !Self::purpose_allowed(slot, &p.purpose))
        {
            self.discard_control_fenced(slot)?;
            return Ok(());
        }
        if slot.prepared.is_some() {
            // Preserve this frozen candidate and ID before the first Blob write.
            // Failed externalization retries this value, never re-enters Runner.
            let prepared = slot
                .prepared
                .as_mut()
                .ok_or_else(|| err("prepared_missing"))?;
            if let Some(batch) = prepared.candidate.outbox.as_mut() {
                batch.created_at.get_or_insert_with(|| (self.ports.now)());
            }
            if !self
                .references
                .externalize_candidate(&owner.sid, &mut prepared.candidate, prepared.finalizing)
                .await
                .map_err(store_error)?
            {
                return Ok(());
            }
            let mut request = seal_candidate(
                owner.clone(),
                slot.acquired.envelope.checkpoint.commit_seq,
                prepared.id.clone(),
                prepared.candidate.clone(),
                prepared.finalizing,
                self.config.envelope_reserve_bytes,
            )
            .map_err(store_error)?;
            request.purpose = prepared.purpose.clone();
            slot.pending = Some(request.clone());
            slot.pending_since = Some(tokio::time::Instant::now());
            slot.prepared = None;
            self.runtime.round_unknown(&owner)?;
            self.issue(slot, request).await?;
            return Ok(());
        }
        if slot.ack_pending {
            let receipt = slot
                .acquired
                .last_commit
                .as_ref()
                .ok_or_else(|| err("checkpoint_proof_missing"))?;
            let consumed_input = slot
                .input
                .as_ref()
                .is_some_and(|input| receipt.consumed.as_ref() == Some(&input.read_through));
            if let Some(input) = &slot.input
                && consumed_input
                && slot.delivery_valid
            {
                self.io(self
                    .runtime
                    .inbox
                    .confirm_committed(&input.delivery, receipt))
                    .await?;
                slot.delivery_valid = false;
            }
            self.io(self.runtime.inbox.trim_committed(receipt)).await?;
            self.runtime.rewatch_inputs(&owner).await?;
            slot.reader_paused = false;
            if consumed_input {
                slot.input = None;
            }
            slot.ack_pending = false;
            return Ok(());
        }
        if slot.settlement.is_some() {
            return self.settle(slot).await;
        }
        if slot.commits_since_yield >= self.config.max_commits_per_turn && slot.input.is_none() {
            // Only this quiescent cut is eligible: unknown commits, committed
            // effects and acknowledgements were handled above. Durable pending
            // work stays due while a hot Session yields capacity to siblings.
            return self.settle(slot).await;
        }
        if slot.reader_paused && slot.input.is_none() {
            self.runtime.rewatch_inputs(&owner).await?;
            slot.reader_paused = false;
        }
        if slot.spec.is_none() {
            slot.spec = Some(
                self.io(self.runtime.sessions.get_spec(&owner.sid))
                    .await?
                    .ok_or_else(|| err("session_spec_missing"))?,
            );
        }
        if self.drive_control(slot).await? {
            return Ok(());
        }
        if !slot.round_started {
            let acquired = self.runtime.begin_round(&owner.sid)?;
            if acquired != slot.acquired {
                return Err(err("processor_head_mismatch"));
            }
            slot.round_started = true;
        }
        match self.prepare(slot).await {
            Ok(Some((candidate, finalizing))) => {
                slot.prepared = Some(Prepared {
                    candidate,
                    finalizing,
                    id: CommitId(uuid::Uuid::new_v4().to_string()),
                    purpose: self.ordinary_purpose(slot)?,
                });
                Ok(())
            }
            Err(error) if error.code == "processor_preparing" => Ok(()),
            // Closing strict authority can replace the slot with a fresh acquisition.
            // Its runtime phase is Recovering, not the Running phase we entered.
            Ok(None) if slot.recovering && !slot.round_started => Ok(()),
            result => {
                self.runtime.round_aborted(&owner)?;
                slot.round_started = false;
                result?;
                self.settle(slot).await
            }
        }
    }
    async fn issue(&self, slot: &mut Slot, request: CommitRequest) -> Result<(), RuntimeError> {
        // Slot.pending was installed before calling. Timeout/cancellation keep it.
        let outcome = match self.io(self.runtime.sessions.commit(request.clone())).await {
            Ok(outcome) => outcome,
            Err(error) if error.code == "session_control_paused" => {
                self.refresh_control(slot).await?;
                if !Self::purpose_allowed(slot, &request.purpose) {
                    self.discard_control_fenced(slot)?;
                    return Ok(());
                }
                return Err(error);
            }
            Err(error) => return Err(error),
        };
        match outcome {
            CommitOutcome::Committed(receipt) | CommitOutcome::AlreadyCommitted(receipt) => {
                self.confirm(slot, &request, receipt)
            }
            CommitOutcome::Fenced => {
                self.runtime.forget_fenced(&request.owner).await?;
                Err(err("commit_fenced"))
            }
            CommitOutcome::CommitSeqConflict => {
                if self.reload_strict_head(slot).await? {
                    Ok(())
                } else {
                    Err(err("commit_head_conflict"))
                }
            }
            CommitOutcome::ContentConflict => Err(err("commit_content_conflict")),
        }
    }
    /// Strict admission is another fenced checkpoint writer under this
    /// same owner. A resident slot can adopt its durable successor rather
    /// than endlessly retrying against the old Done head. In particular, an
    /// uncertain local CAS is only discarded when the next position is
    /// provably occupied by a different commit.
    async fn reload_strict_head(&self, slot: &mut Slot) -> Result<bool, RuntimeError> {
        let prior = &slot.acquired.envelope.checkpoint;
        let Some(license) = prior.runner_state.as_ref()
            .and_then(|runner| decode_strict_license(&runner.state.value))
        else {
            return Ok(false);
        };
        let owner = slot.acquired.ownership.clone();
        let envelope = self.io(self.runtime.sessions.load(&owner.sid)).await?
            .ok_or_else(|| err("session_envelope_missing"))?;
        if envelope.owner != owner.owner {
            self.runtime.forget_fenced(&owner).await?;
            return Err(err("commit_fenced"));
        }
        if envelope.checkpoint.commit_seq.0 <= prior.commit_seq.0 {
            return Ok(false);
        }
        let next = envelope.checkpoint.runner_state.as_ref()
            .and_then(|runner| decode_strict_license(&runner.state.value));
        if next.is_none_or(|next| {
            next.sid != owner.sid || next.group != license.group || next.scope != license.scope
                || next.generation < license.generation
        }) {
            return Ok(false);
        }
        let batch = envelope.checkpoint.outbox.as_ref()
            .ok_or_else(|| err("checkpoint_proof_missing"))?;
        if let Some(pending) = &slot.pending
            && (batch.source_commit != pending.candidate.commit_seq || batch.commit_id == pending.commit_id)
        {
            return Ok(false);
        }
        let Some(receipt) = (match self.io(self.runtime.sessions.resolve_commit(
            &owner, &batch.commit_id, &batch.content_digest,
        )).await? {
            CommitResolution::Committed(receipt) => Some(receipt),
            CommitResolution::Fenced => {
                self.runtime.forget_fenced(&owner).await?;
                return Err(err("commit_fenced"));
            }
            _ => None,
        }) else {
            // The captured head may have advanced again during resolution.
            return Ok(false);
        };
        if receipt.sid != owner.sid || receipt.owner != owner.owner {
            return Err(err("checkpoint_receipt_mismatch"));
        }
        verify_checkpoint(&envelope.checkpoint, &receipt).map_err(store_error)?;
        let acquired = AcquiredSession { ownership: owner.clone(), envelope, last_commit: Some(receipt) };
        self.runtime.refresh_local_reacquire(&owner, acquired.clone())?;
        // Recovery re-watches from the sealed consumed cursor, not from an
        // uncommitted local batch this replacement may be discarding.
        *slot = Slot::new(acquired);
        Ok(true)
    }

    fn confirm(
        &self,
        slot: &mut Slot,
        request: &CommitRequest,
        receipt: bcs_service_api::port::repo::distributed::common::CommitReceipt,
    ) -> Result<(), RuntimeError> {
        verify_checkpoint(&request.candidate, &receipt).map_err(store_error)?;
        let mut envelope = slot.acquired.envelope.clone();
        envelope.checkpoint = request.candidate.clone();
        let effects = request
            .candidate
            .outbox
            .as_ref()
            .map_or(0, |b| b.effects.len() as u64);
        self.runtime.checkpoint_confirmed(
            &request.owner,
            envelope.clone(),
            receipt.clone(),
            effects,
        )?;
        slot.dispatch_specs
            .retain(|did, _| envelope.checkpoint.dispatch_seq.contains_key(did));
        slot.acquired.envelope = envelope;
        slot.commits_since_yield = slot.commits_since_yield.saturating_add(1);
        slot.acquired.last_commit = Some(receipt.clone());
        slot.pending = None;
        slot.pending_since = None;
        slot.completed = if effects == 0 {
            Some(CompletedEffects {
                commit_id: receipt.commit_id,
                sequence: receipt.sequence,
                digest: receipt.content_digest,
                saved: Default::default(),
            })
        } else {
            None
        };
        slot.effects_pending = effects != 0;
        slot.round_started = false;
        slot.ack_pending = true;
        Ok(())
    }
    async fn settle(&self, slot: &mut Slot) -> Result<(), RuntimeError> {
        let owner = &slot.acquired.ownership;
        if slot.settlement.is_none() {
            let observation = self.io(self.runtime.scheduling.observe(owner)).await?;
            let ScheduleObservation::Observed(observed) = observation else {
                return Err(err("schedule_fenced"));
            };
            let unconsumed = self
                .io(self.runtime.inbox.has_unconsumed(
                    &owner.sid,
                    slot.acquired.envelope.checkpoint.consumed.as_ref(),
                ))
                .await?;
            let next_check = if unconsumed {
                Some((self.ports.now)())
            } else {
                slot.acquired
                    .envelope
                    .checkpoint
                    .deadlines
                    .iter()
                    .map(|d| d.at)
                    .min()
            };
            self.runtime.scheduling_started(owner)?;
            slot.settlement = Some(SettleSession {
                owner: owner.clone(),
                observed,
                next_check,
            });
        }
        let request = slot
            .settlement
            .as_ref()
            .ok_or_else(|| err("settlement_missing"))?
            .clone();
        // A Done-phase strict task still owns an open terminal-authority
        // window (plan T7 slice 2): the spine must close it before the
        // session may yield residency. Parked windows would strand the
        // round's own commit.
        let done_window_open = slot
            .acquired
            .envelope
            .checkpoint
            .tasks
            .iter()
            .any(|task| matches!(task.phase, TaskPhase::Done));
        let release = !done_window_open
            && slot.input.is_none()
            && (slot.commits_since_yield >= self.config.max_commits_per_turn
                || request.next_check.is_none_or(|at| at > (self.ports.now)()));
        match self.io(self.runtime.scheduling.settle(request)).await? {
            SettleOutcome::Applied => {
                self.runtime.scheduling_completed(owner)?;
                slot.settlement = None;
                // Durable activation/deadlines, rather than a resident idle slot,
                // preserve future work and make room for other Sessions.
                if release {
                    slot.releasing = true;
                    self.runtime.release_session(owner).await?;
                    self.runtime.defer_local_reacquire(owner)?;
                }
                Ok(())
            }
            SettleOutcome::NeedsRecheck => {
                self.runtime.scheduling_completed(owner)?;
                slot.settlement = None;
                Ok(())
            }
            SettleOutcome::Fenced => {
                self.runtime.forget_fenced(owner).await?;
                Err(err("schedule_fenced"))
            }
        }
    }
}
