use super::encoding::encoded_size;
use bcs_dist_encoding::digest_json;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    common::*, dispatch::*, result::*, session::*, task_feed::*,
};
use std::{collections::BTreeMap, sync::Arc, time::Duration};

pub(super) struct EffectPorts {
    pub output: Arc<dyn TaskPublisherPort>,
    pub results: Arc<dyn ResultStorePort>,
    pub dispatch: Arc<dyn DispatchControlPort>,
    pub blobs: Arc<dyn BlobStorePort>,
    pub node_history: Option<Arc<dyn bcs_service_api::port::repo::distributed::node_history::NodeHistoryRepo>>,
}
#[derive(Clone)]
pub(super) struct CompletedEffects {
    pub commit_id: CommitId,
    pub sequence: CommitSeq,
    pub digest: Digest,
    pub saved: BTreeMap<Tid, BlobRef>,
}

pub(super) struct ReplayProgress {
    pub(super) next: usize,
    completion: CompletedEffects,
}

#[derive(Debug)]
pub(super) enum EffectFailure {
    Store(StoreError),
    Blocked(&'static str),
    Stopped,
}
impl From<StoreError> for EffectFailure {
    fn from(error: StoreError) -> Self {
        Self::Store(error)
    }
}

/// Reject malformed persisted batches before issuing even their first effect.
/// The repository still verifies durable source authority on each operation.
pub(super) fn validate_batch(
    batch: &CommittedBatch,
    receipt: &CommitReceipt,
) -> Result<(), EffectFailure> {
    if batch.commit_id != receipt.commit_id
        || batch.content_digest != receipt.content_digest
        || batch.source_commit != receipt.sequence
        || batch.effects.len() > 128
        || encoded_size(batch, 256 * 1024).is_none()
    {
        return Err(EffectFailure::Blocked("outbox_commit_identity"));
    }
    let mut published = false;
    for (index, effect) in batch.effects.iter().enumerate() {
        if effect.id.sid != receipt.sid
            || effect.id.commit != receipt.sequence
            || effect.id.index.get() as usize != index + 1
            || digest_json(
                &serde_json::to_value(&effect.kind)
                    .map_err(|_| EffectFailure::Blocked("outbox_encoding"))?,
            ) != effect.content_digest
        {
            return Err(EffectFailure::Blocked("outbox_effect_identity"));
        }
        match &effect.kind {
            EffectKind::PublishSnapshot { .. } => published = true,
            EffectKind::AppendEvents { .. } if published => {
                return Err(EffectFailure::Blocked("outbox_output_order"));
            }
            _ => {}
        }
    }
    Ok(())
}

impl EffectPorts {
    /// One effect per visit bounds slow-session occupancy. This local cursor is
    /// only an optimization; takeover replays the durable batch with original IDs.
    pub(super) async fn replay(
        &self,
        batch: &CommittedBatch,
        receipt: &CommitReceipt,
        timeout: Duration,
        progress: &mut Option<ReplayProgress>,
        may_continue: impl Fn() -> bool,
    ) -> Result<Option<CompletedEffects>, EffectFailure> {
        validate_batch(batch, receipt)?;
        let progress = progress.get_or_insert_with(|| ReplayProgress {
            next: 0,
            completion: CompletedEffects {
                commit_id: receipt.commit_id.clone(),
                sequence: receipt.sequence,
                digest: receipt.content_digest.clone(),
                saved: BTreeMap::new(),
            },
        });
        if progress.completion.commit_id != receipt.commit_id
            || progress.completion.digest != receipt.content_digest
        {
            return Err(EffectFailure::Blocked("effect_progress_identity"));
        }
        if let Some(effect) = batch.effects.get(progress.next) {
            if !may_continue() {
                return Err(EffectFailure::Stopped);
            }
            let source = EffectSource {
                commit: receipt.clone(),
                effect_id: effect.id.clone(),
                content_digest: effect.content_digest.clone(),
            };
            let operation_id = format!(
                "effect:{}:{}:{}",
                effect.id.sid.0, effect.id.commit.0, effect.id.index
            );
            let archive = tokio::time::timeout(timeout, self.apply(source, &effect.kind))
                .await
                .map_err(|_| EffectFailure::Store(StoreError::OutcomeUnknown { operation_id }))??;
            if let Some((tid, reference)) = archive {
                progress.completion.saved.insert(tid, reference);
            }
            progress.next += 1;
            if let EffectKind::CreateDispatch { spec } = &effect.kind {
                // Durable creation is already confirmed. Dropping this bounded
                // hint cannot revoke success; independent supervision retries it.
                let _ = tokio::time::timeout(
                    timeout.min(Duration::from_millis(50)),
                    self.dispatch.notify(&spec.pool, &spec.did),
                )
                .await;
            }
        }
        Ok((progress.next == batch.effects.len()).then(|| progress.completion.clone()))
    }

    async fn apply(
        &self,
        source: EffectSource,
        kind: &EffectKind,
    ) -> Result<Option<(Tid, BlobRef)>, EffectFailure> {
        match kind {
            EffectKind::AppendEvents { previous, events } => {
                match self
                    .output
                    .append(AppendCommittedEvents {
                        source: source.clone(),
                        previous: *previous,
                        events: events.clone(),
                    })
                    .await?
                {
                    AppendOutcome::Appended | AppendOutcome::AlreadyApplied => {}
                    AppendOutcome::Compacted(proof)
                        if proof.sid == source.commit.sid
                            && proof.covered_commit >= source.commit.sequence => {}
                    _ => return Err(EffectFailure::Blocked("output_append_conflict")),
                }
            }
            EffectKind::PublishSnapshot { snapshot } => {
                if matches!(
                    self.output
                        .publish(PublishCommittedSnapshot {
                            source,
                            snapshot: snapshot.clone()
                        })
                        .await?,
                    PublishOutcome::ContentConflict
                ) {
                    return Err(EffectFailure::Blocked("output_publication_conflict"));
                }
            }
            EffectKind::CreateDispatch { spec } => {
                if let Some(pin) = &spec.node_history {
                    let repo = self.node_history.as_ref().ok_or(EffectFailure::Blocked("node_history_unconfigured"))?;
                    super::node_history::freeze_for_dispatch(spec, pin, repo.as_ref(), self.blobs.as_ref()).await?;
                }
                if self
                    .dispatch
                    .create(CreateCommittedDispatch {
                        source,
                        spec: spec.clone(),
                    })
                    .await?
                    == IdempotentWrite::ContentConflict
                {
                    return Err(EffectFailure::Blocked("dispatch_creation_conflict"));
                }
                // Creation includes durable discovery. Notification is a hint;
                // the independent Supervisor repairs its absence.
                // Keep hint I/O out of this mandatory persistence completion.
            }
            EffectKind::RequestCancel { did, intent } => {
                control(
                    self.dispatch
                        .request_cancel(CommittedCancelIntent {
                            source,
                            did: did.clone(),
                            intent: intent.clone(),
                        })
                        .await?,
                )?;
            }
            EffectKind::PushInput { did, input } => {
                control(
                    self.dispatch
                        .push_input(CommittedDispatchInput {
                            source,
                            did: did.clone(),
                            input: input.clone(),
                        })
                        .await?,
                )?;
            }
            EffectKind::SaveResult { record } => {
                let reference = match self.results.save_final(source, record.clone()).await? {
                    SavedResult::Saved(reference) | SavedResult::AlreadySaved(reference) => {
                        reference
                    }
                    SavedResult::ContentConflict => {
                        return Err(EffectFailure::Blocked("archive_content_conflict"));
                    }
                };
                return Ok(Some((record.tid.clone(), reference)));
            }
        }
        Ok(None)
    }
}
fn control(outcome: ControlOutcome) -> Result<(), EffectFailure> {
    match outcome {
        ControlOutcome::ContentConflict => Err(EffectFailure::Blocked("dispatch_control_conflict")),
        _ => Ok(()),
    }
}
