//! One outbox effect per visit, with persistent replay attempt classification.
use super::{commit::verify_checkpoint, effects::*, engine::*};
use crate::runtime::{err, store_error};
use bcs_dist_encoding::digest_json;
use bcs_dist_model::*;
use bcs_service_api::{
    port::repo::distributed::{common::*, control::*, session::CommittedBatch},
    types::dist_runtime::RuntimeError,
};
impl WorkerProcessor {
    pub(super) async fn replay_risk(
        &self,
        slot: &mut Slot,
        batch: &CommittedBatch,
        receipt: &CommitReceipt,
    ) -> Result<Option<CompletedEffects>, RuntimeError> {
        let digest = digest_json(&serde_json::json!({"batch":batch}));
        let attempt = if let Some(effect) = batch.effects.first() {
            Some(
                self.begin_risk(
                    slot,
                    "effect",
                    digest.clone(),
                    Digest("outbox-replay:v1".into()),
                    (self.ports.now)(),
                    Some((&effect.id, &effect.content_digest)),
                )
                .await?,
            )
        } else {
            None
        };
        let owner = slot.acquired.ownership.clone();
        let result = self
            .effects
            .replay(
                batch,
                receipt,
                self.config.request_timeout,
                &mut slot.replay,
                || self.runtime.may_continue_round(&owner).unwrap_or(false),
            )
            .await;
        match result {
            Ok(completed) => {
                if completed.is_some()
                    && let Some(attempt) = attempt
                {
                    slot.risk_outcome = Some((
                        attempt,
                        AttemptOutcome::Succeeded {
                            decision_digest: digest,
                        },
                    ));
                    self.finish_risk(slot).await?;
                }
                Ok(completed)
            }
            Err(EffectFailure::Store(
                error @ (StoreError::OutcomeUnknown { .. } | StoreError::NotApplied { .. }),
            )) => {
                // Only the confirmed outbox is reconstructible. Never evict an
                // unknown commit or a frozen, not-yet-committed decision.
                slot.parking_replay = slot.pending.is_none()
                    && slot.prepared.is_none()
                    && slot.evaluation.is_none()
                    && slot.settlement.is_none()
                    && !slot.round_started;
                if let Some(attempt) = attempt {
                    slot.risk_outcome = Some((
                        attempt,
                        AttemptOutcome::Transient {
                            code: "effect_storage_retry".into(),
                        },
                    ));
                }
                if slot.parking_replay {
                    self.park_replay(slot).await?;
                } else {
                    self.finish_risk(slot).await?;
                }
                Err(store_error(error))
            }
            Err(EffectFailure::Store(error)) => Err(store_error(error)),
            Err(EffectFailure::Blocked(code)) => Err(err(code)),
            Err(EffectFailure::Stopped) => Err(err("worker_not_ready")),
        }
    }

    pub(super) async fn park_replay(&self, slot: &mut Slot) -> Result<(), RuntimeError> {
        if slot.pending.is_some()
            || slot.prepared.is_some()
            || slot.evaluation.is_some()
            || slot.settlement.is_some()
            || slot.round_started
        {
            return Err(err("replay_park_pending_commit"));
        }
        let receipt = slot
            .acquired
            .last_commit
            .as_ref()
            .ok_or_else(|| err("checkpoint_proof_missing"))?;
        verify_checkpoint(&slot.acquired.envelope.checkpoint, receipt).map_err(store_error)?;
        // The flag survives cancellation of either diagnostic persistence or
        // local cleanup. A later visit resumes cleanup before issuing effects.
        // External isolation can replace the replay attempt during either await.
        // Observe that fence before retrying an outcome against its old guard.
        self.refresh_control(slot).await?;
        self.reconcile_control_fence(slot)?;
        if let Err(error) = self.finish_risk(slot).await {
            if error.code != "control_content_conflict" {
                return Err(error);
            }
            self.refresh_control(slot).await?;
            self.reconcile_control_fence(slot)?;
            if slot.risk_outcome.is_some() {
                return Err(error);
            }
        }
        self.runtime.park_committed_replay(&slot.acquired).await?;
        // Same-owner reacquisition is a new lifecycle even if the parked slot
        // was itself recovering. tick must replace this slot with the new head.
        slot.recovering = false;
        Ok(())
    }
}
