//! Verify committed effects against durable facts, never an opaque proof string.
use crate::{codec::decode_json, error, identity::digest_json, layout::KeyLayout};
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
        if envelope.checkpoint.format_version != 2 {
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
