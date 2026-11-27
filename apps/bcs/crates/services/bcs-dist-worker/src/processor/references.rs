use super::encoding::encoded_size;
use bcs_dist_encoding::{blob_reference, hex_of};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, result::*, session::*};
use std::{sync::Arc, time::Duration};

pub(super) struct References {
    pub blobs: Arc<dyn BlobStorePort>,
    pub timeout: Duration,
    pub envelope_reserve: usize,
}
impl References {
    pub(super) async fn resolve(
        &self,
        payload: &Payload,
        maximum: usize,
    ) -> Result<serde_json::Value, StoreError> {
        self.resolve_value(payload, Some(maximum)).await
    }
    pub(super) async fn resolve_output(
        &self,
        payload: &Payload,
    ) -> Result<serde_json::Value, StoreError> {
        self.resolve_value(payload, None).await
    }
    async fn resolve_value(
        &self,
        payload: &Payload,
        maximum: Option<usize>,
    ) -> Result<serde_json::Value, StoreError> {
        match payload {
            Payload::Inline(value) => {
                if let Some(maximum) = maximum {
                    bounded(value, maximum)?;
                }
                Ok(value.clone())
            }
            Payload::Ref(reference) => {
                bounded(reference, 1024)?;
                let bytes = tokio::time::timeout(self.timeout, self.blobs.get(reference))
                    .await
                    .map_err(|_| unavailable("blob_read_timeout"))??
                    .ok_or_else(|| unavailable("blob_missing"))?;
                if maximum.is_some_and(|maximum| bytes.len() > maximum)
                    || blob_reference(&reference.key, &bytes).digest != reference.digest
                {
                    return Err(invalid("blob_digest_or_size"));
                }
                let value: serde_json::Value =
                    serde_json::from_slice(&bytes).map_err(|_| invalid("blob_json"))?;
                if let Some(maximum) = maximum {
                    bounded(&value, maximum)?;
                }
                Ok(value)
            }
        }
    }

    /// Candidate is an owned copy, never the confirmed checkpoint. Equivalent
    /// display values in draft/publication/archive share the content-addressed Ref.
    pub(super) async fn externalize_candidate(
        &self,
        sid: &Sid,
        candidate: &mut Checkpoint,
        finalizing: bool,
    ) -> Result<bool, StoreError> {
        let limits = candidate
            .tasks
            .front()
            .map(|task| task.output_limits)
            .unwrap_or(PHASE_ONE_OUTPUT_LIMITS);
        if !self
            .externalize_pass(sid, candidate, limits.inline_threshold_bytes as usize)
            .await?
        {
            return Ok(false);
        }
        let checkpoint_budget = limits
            .max_checkpoint_bytes
            .saturating_sub(if finalizing {
                0
            } else {
                limits.checkpoint_finalization_reserve
            })
            .saturating_sub(self.envelope_reserve as u32) as usize;
        let outbox_budget = limits.max_outbox_bytes.saturating_sub(if finalizing {
            0
        } else {
            limits.outbox_finalization_reserve
        }) as usize;
        if !fits_sealed(candidate, checkpoint_budget, outbox_budget) {
            // Several individually small payloads can still exceed the batch
            // budget. Externalize those larger than a bounded Ref envelope too.
            if !self
                .externalize_pass(sid, candidate, limits.max_reference_metadata_bytes as usize)
                .await?
            {
                return Ok(false);
            }
        }
        if !fits_sealed(candidate, checkpoint_budget, outbox_budget) {
            return Err(invalid("encoded_budget"));
        }
        Ok(true)
    }
    async fn externalize_pass(
        &self,
        sid: &Sid,
        candidate: &mut Checkpoint,
        threshold: usize,
    ) -> Result<bool, StoreError> {
        macro_rules! one {
            ($payload:expr) => {
                if self.externalize(sid, $payload, threshold).await? {
                    return Ok(false);
                }
            };
        }
        for task in &mut candidate.tasks {
            one!(&mut task.input);
            if let Some(finalization) = &mut task.finalization
                && finalization.record.source_commit == candidate.commit_seq
            {
                one!(&mut finalization.record.snapshot);
                one!(&mut finalization.record.result);
            }
        }
        for content in candidate.view_drafts.values_mut() {
            one!(&mut content.snapshot);
        }
        // The paired publication register travels with the sealed candidate
        // (plan T7 slice 2): retained obligations externalize with the same
        // blob/digest machinery as the outbox era, or a wedged feed would
        // accumulate over-threshold inline artifacts until the candidate
        // could never fit its sealed budget again. Registered content is
        // still pre-seal here — the entry's digest is computed by
        // `seal_candidate` over whatever form remains, exactly like a batch
        // effect's.
        for publication in &mut candidate.feed_publications {
            match &mut publication.kind {
                EffectKind::AppendEvents { events, .. } => {
                    for event in events {
                        if let OutputBody::Artifact { update, .. } = &mut event.body {
                            let value = match update {
                                ArtifactUpdate::Upsert { content } => content,
                                ArtifactUpdate::AppendText { delta } => delta,
                            };
                            one!(value);
                        }
                    }
                }
                EffectKind::PublishSnapshot { snapshot } => {
                    one!(&mut snapshot.content.snapshot)
                }
                _ => {}
            }
        }
        if let Some(batch) = &mut candidate.outbox {
            for effect in &mut batch.effects {
                match &mut effect.kind {
                    EffectKind::AppendEvents { events, .. } => {
                        for event in events {
                            if let OutputBody::Artifact { update, .. } = &mut event.body {
                                let value = match update {
                                    ArtifactUpdate::Upsert { content } => content,
                                    ArtifactUpdate::AppendText { delta } => delta,
                                };
                                one!(value);
                            }
                        }
                    }
                    EffectKind::PublishSnapshot { snapshot } => {
                        one!(&mut snapshot.content.snapshot)
                    }
                    EffectKind::SaveResult { record } => {
                        one!(&mut record.snapshot);
                        one!(&mut record.result);
                    }
                    EffectKind::CreateDispatch { spec } => one!(&mut spec.request),
                    EffectKind::PushInput { input, .. } => one!(&mut input.payload),
                    EffectKind::RequestCancel { .. } => {}
                }
            }
        }
        Ok(true)
    }
    async fn externalize(
        &self,
        sid: &Sid,
        payload: &mut Payload,
        threshold: usize,
    ) -> Result<bool, StoreError> {
        let Payload::Inline(value) = payload else {
            return Ok(false);
        };
        if encoded_size(value, threshold).is_some() {
            return Ok(false);
        }
        let bytes = serde_json::to_vec(value).map_err(|_| invalid("blob_encoding"))?;
        let digest = blob_reference("", &bytes).digest;
        let key = format!("worker-blob:{}:{}", sid.0, hex_of(&digest));
        let reference = BlobRef { key, digest };
        bounded(
            &reference,
            PHASE_ONE_OUTPUT_LIMITS.max_reference_metadata_bytes as usize,
        )?;
        let outcome = tokio::time::timeout(
            self.timeout,
            self.blobs.put_immutable(reference.clone(), bytes.clone()),
        )
        .await;
        match outcome {
            Ok(Ok(IdempotentWrite::Applied | IdempotentWrite::AlreadyApplied)) => {}
            Ok(Ok(IdempotentWrite::ContentConflict)) => {
                return Err(invalid("candidate_blob_conflict"));
            }
            Ok(Err(
                error @ StoreError::Invalid { .. } | error @ StoreError::UnsupportedFormat { .. },
            )) => return Err(error),
            Ok(Err(error @ StoreError::NotApplied { .. })) => return Err(error),
            Ok(Err(StoreError::OutcomeUnknown { .. })) | Err(_) => {
                // Resolve the same immutable identity once; never invent a new
                // Blob key or submit an unconfirmed Ref after a lost response.
                match tokio::time::timeout(self.timeout, self.blobs.get(&reference)).await {
                    Ok(Ok(Some(stored))) if stored == bytes => {}
                    Ok(Ok(Some(_))) => return Err(invalid("candidate_blob_conflict")),
                    _ => {
                        return Err(StoreError::OutcomeUnknown {
                            operation_id: reference.key,
                        });
                    }
                }
            }
        }
        *payload = Payload::Ref(reference);
        Ok(true)
    }
}
fn bounded(value: &impl serde::Serialize, maximum: usize) -> Result<usize, StoreError> {
    encoded_size(value, maximum).ok_or_else(|| invalid("encoded_budget"))
}
fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}
fn unavailable(code: &str) -> StoreError {
    StoreError::NotApplied { code: code.into() }
}

/// Account for identities/digests installed by seal_candidate. Otherwise a
/// candidate just below the physical limit can become permanently uncommittable.
fn fits_sealed(candidate: &Checkpoint, checkpoint_budget: usize, outbox_budget: usize) -> bool {
    let mut framed = candidate.clone();
    if let Some(batch) = framed.outbox.as_mut() {
        batch.commit_id = CommitId("x".repeat(254));
        batch.content_digest = Digest("x".repeat(71));
        for effect in &mut batch.effects {
            effect.content_digest = Digest("x".repeat(71));
        }
    }
    encoded_size(&framed, checkpoint_budget).is_some()
        && encoded_size(&framed.outbox, outbox_budget).is_some()
}
