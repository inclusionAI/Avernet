//! Verify committed effects against durable facts, never an opaque proof string.
use crate::{codec::decode_json, error, identity::digest_json, layout::KeyLayout};
use bcs_dist_model::Sid;
use bcs_service_api::port::repo::distributed::{common::*, session::*};
use bcs_store_api::document::DocumentStorePlugin;
use std::sync::Arc;

#[derive(Clone)]
pub struct EffectVerifier {
    documents: Arc<dyn DocumentStorePlugin>,
    layout: KeyLayout,
}
impl EffectVerifier {
    pub fn new(documents: Arc<dyn DocumentStorePlugin>, layout: KeyLayout) -> Self {
        Self { documents, layout }
    }

    /// The strict-group paired first-write permission (plan T7 slice 2).
    /// `AppendEvents`/`PublishSnapshot` effects split out of a strict
    /// session's blocking outbox stay verifiable after the checkpoint
    /// advanced past their commit: the authoritative commit that registered
    /// them left the durable publication register inside the sealed
    /// checkpoint, and this check matches the delivery claim against that
    /// register entry exactly — sealed commit identity (id, sequence
    /// binding, digest, consumed position), the paired delivery identity
    /// (`effect_id`), the effect content digest and the full effect body
    /// (append: previous/event range/event digests; snapshot: snapshot
    /// digest/`source_commit`/`snapshot_event_id`), plus the licensed
    /// `Sid/generation/Tid` binding.
    ///
    /// Sessions without register entries answer `Ok(false)`, so callers
    /// keep their original error and no non-strict behavior changes. A
    /// register entry under the same delivery identity with different
    /// binding or content is a loud permission conflict, never a silent
    /// authorization.
    pub async fn verify_paired(
        &self,
        source: &EffectSource,
        kind: &EffectKind,
    ) -> Result<bool, StoreError> {
        let Some(envelope) = self.envelope(&source.commit.sid).await? else {
            return Ok(false);
        };
        for publication in &envelope.checkpoint.feed_publications {
            if publication.effect != source.effect_id {
                continue;
            }
            if publication.sid != source.commit.sid
                || publication.commit_id != source.commit.commit_id
                || publication.commit_digest != source.commit.content_digest
                || publication.consumed != source.commit.consumed
                || publication.content_digest != source.content_digest
                || digest_json(&serde_json::to_value(kind).map_err(|_| invalid())?)
                    != source.content_digest
                || publication.kind != *kind
            {
                return Err(StoreError::Invalid {
                    code: "publication_permission_conflict".into(),
                });
            }
            return Ok(true);
        }
        Ok(false)
    }

    /// The sealed session envelope behind a paired permission check, or
    /// `None` when the session has no durable envelope yet.
    async fn envelope(
        &self,
        sid: &Sid,
    ) -> Result<Option<CheckpointEnvelope>, StoreError> {
        match self
            .documents
            .read_document(&self.layout.ckpt(sid)?)
            .await
            .map_err(|error| error::from_read(&error))?
        {
            Some(document) => Ok(Some(decode_json(document.value)?)),
            None => Ok(None),
        }
    }

    /// A sealed successor may replay an earlier owner's exact committed effect.
    /// Current owner equality is therefore not the authorization rule. The
    /// durable batch, position, identity, digest and full body must all match.
    /// Once an effect leaves the outbox, its provider's immutable completion
    /// evidence must authorize retries instead of this current-head lookup.
    pub async fn verify(&self, source: &EffectSource, kind: &EffectKind) -> Result<(), StoreError> {
        validate_source(source)?;
        if source.effect_id.sid != source.commit.sid
            || source.effect_id.commit != source.commit.sequence
            || digest_json(&serde_json::to_value(kind).map_err(|_| invalid())?)
                != source.content_digest
        {
            return Err(invalid());
        }
        let document = self
            .documents
            .read_document(&self.layout.ckpt(&source.commit.sid)?)
            .await
            .map_err(|error| error::from_read(&error))?
            .ok_or_else(invalid)?;
        let envelope: CheckpointEnvelope = decode_json(document.value)?;
        if envelope.checkpoint.format_version != 1 {
            return Err(StoreError::UnsupportedFormat {
                format: envelope.checkpoint.format_version,
            });
        }
        let batch = envelope.checkpoint.outbox.as_ref().ok_or_else(invalid)?;
        if envelope.checkpoint.commit_seq != source.commit.sequence
            || envelope.checkpoint.consumed != source.commit.consumed
            || batch.source_commit != source.commit.sequence
            || batch.commit_id != source.commit.commit_id
            || batch.content_digest != source.commit.content_digest
        {
            return Err(invalid());
        }
        let mut effects = batch
            .effects
            .iter()
            .filter(|effect| effect.id == source.effect_id);
        let effect = effects.next().ok_or_else(invalid)?;
        if effects.next().is_some()
            || effect.content_digest != source.content_digest
            || effect.kind != *kind
        {
            return Err(invalid());
        }
        Ok(())
    }
}

/// Stable provenance retained with a completed effect. Owner and proof strings
/// are intentionally absent: a takeover does not change committed authority.
pub fn completion_identity(source: &EffectSource) -> Result<String, StoreError> {
    validate_source(source)?;
    serde_json::to_string(&serde_json::json!({
        "commit_id": source.commit.commit_id,
        "commit_digest": source.commit.content_digest,
        "sequence": source.commit.sequence,
        "consumed": source.commit.consumed,
        "effect_id": source.effect_id,
        "effect_digest": source.content_digest,
    }))
    .map_err(|_| invalid())
}
fn validate_source(source: &EffectSource) -> Result<(), StoreError> {
    for text in [
        &source.commit.sid.0,
        &source.commit.commit_id.0,
        &source.commit.content_digest.0,
        &source.effect_id.sid.0,
        &source.content_digest.0,
    ] {
        if text.is_empty() || text.len() > 256 {
            return Err(invalid());
        }
    }
    if source.commit.sequence.0 == 0
        || source.effect_id.sid != source.commit.sid
        || source.effect_id.commit != source.commit.sequence
    {
        return Err(invalid());
    }
    if let Some(position) = &source.commit.consumed
        && (position.sid != source.commit.sid
            || position.token.value.len() > 1024
            || position.token.backend.0.len() > 256
            || position.token.generation.0.len() > 256)
    {
        return Err(invalid());
    }
    Ok(())
}
fn invalid() -> StoreError {
    StoreError::Invalid {
        code: "unproven_effect".into(),
    }
}
