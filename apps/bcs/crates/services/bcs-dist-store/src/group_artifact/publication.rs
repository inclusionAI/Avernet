//! Guarded completion of committed strict-group archives with the real
//! publication (plan T7 slice 2).
//!
//! The Worker claims a `PublicPublication`; this module never trusts it.
//! Every field is re-derived from the paired publication register sealed in
//! the session's durable checkpoint envelope, the row must already be
//! committed, and exactly one guarded transaction fills a still-empty
//! publication — an attached archive replays as `AlreadyAttached` only on a
//! bit-for-bit match, and any different payload is a loud `ContentConflict`.
//! No path fabricates `AlreadySaved` semantics.
use super::*;
use crate::codec::decode_json;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::session::{
    CheckpointEnvelope, EffectKind,
};
use std::num::NonZeroU32;

/// The publication a sealed round expects, re-derived from the register.
struct SealedPublication {
    effects: Vec<EffectId>,
    history: Vec<OutputEvent>,
    artifacts: Vec<ArtifactPart>,
    archive_json: String,
}

impl GroupArtifactStore {
    pub(super) async fn attach_publication_impl(
        &self,
        round: &RoundKey,
        publication: &PublicPublication,
    ) -> Result<AttachedPublication, StoreError> {
        if publication.schema_version != 1 {
            return Err(StoreError::UnsupportedFormat {
                format: publication.schema_version,
            });
        }
        let sealed = self.sealed_publication(round).await?;
        if sealed.effects != publication.effects {
            return Err(invalid("archive_attach_mismatch"));
        }
        let hash = archive_key(&round.scope, &round.group, &round.tid)?;
        let Some(row) = self.read_row(&hash).await? else {
            return Err(not_applied("archive_absent"));
        };
        row.verify_identity(&round.scope, &round.group, &round.tid)?;
        if row.state != COMMITTED {
            return Err(not_applied("archive_not_committed"));
        }
        let archive = stored_request(&row)?;
        if !archive.public_history.is_empty() || !archive.artifacts.is_empty() {
            // Exactly-once: a completed publication replays as attached only
            // when the sealed register produced the very same content.
            return Ok(if archive.public_history == sealed.history
                && archive.artifacts == sealed.artifacts
            {
                AttachedPublication::AlreadyAttached
            } else {
                AttachedPublication::ContentConflict
            });
        }
        if sealed.history != publication.history || sealed.artifacts != publication.artifacts {
            return Err(invalid("archive_attach_mismatch"));
        }
        let mut completed = archive;
        completed.public_history = sealed.history;
        completed.artifacts = sealed.artifacts;
        self.validate_request(&completed)?;
        let check = self.validator.measure(&self.budget, &completed)?;
        if !check.fits {
            return Err(invalid("archive_projection_budget"));
        }
        let archive_json =
            serde_json::to_string(&completed).map_err(|_| invalid("archive_encoding"))?;
        // Part blobs resolve idempotently before the finishing transaction,
        // exactly like the original archive path.
        let mut part_rows = Vec::with_capacity(completed.artifacts.len());
        for part in &completed.artifacts {
            let bytes =
                serde_json::to_vec(&part.part).map_err(|_| invalid("archive_part"))?;
            let reference = part_blob_reference(&bytes);
            match self
                .put_object(&reference.key, &reference.digest, &bytes, "")
                .await?
            {
                IdempotentWrite::Applied | IdempotentWrite::AlreadyApplied => {}
                IdempotentWrite::ContentConflict => {
                    return Err(invalid("archive_object_conflict"));
                }
            }
            part_rows.push((part, reference.key));
        }
        let mut steps = Vec::new();
        // First book the register of part rows inside the same transaction —
        // exactly the columns `archive_final` writes — then the guarded
        // archive-json update; a half-attached archive never observes.
        for (part, content_key) in &part_rows {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                "INSERT INTO bcs_fsm_task_artifacts (tid, artifact_id, archive_hash, \
                 part_order, content_key) VALUES (?, ?, ?, ?, ?)",
                vec![
                    DbValue::String(round.tid.0.clone()),
                    DbValue::String(part.artifact_id.0.clone()),
                    DbValue::String(hash.clone()),
                    numeric(u64::from(part.order.get())),
                    DbValue::String(content_key.clone()),
                ],
            )));
        }
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_fsm_task_archives SET archive_json = ?, budget_version = ?, \
             projection_digest = ?, measured_bytes = ?, projection_fits = ?, \
             updated_at = ? WHERE archive_hash = ? AND state = ? AND archive_json = ?",
            vec![
                DbValue::String(archive_json),
                numeric(check.budget_version),
                DbValue::String(check.projection_digest.0),
                numeric(check.measured_bytes),
                DbValue::U64(u64::from(check.fits as u8)),
                numeric(now_millis()),
                DbValue::String(hash.clone()),
                DbValue::String(COMMITTED.into()),
                DbValue::String(sealed.archive_json),
            ],
        )));
        match self.db.transaction(steps).await {
            Ok(_) => Ok(AttachedPublication::Attached),
            Err(_) => {
                // A concurrent attach may have won first: re-read and
                // classify, never reporting success without proof.
                match self.read_row(&hash).await? {
                    Some(existing)
                        if existing.state == COMMITTED
                            && stored_request(&existing)?
                                .public_history
                                == completed.public_history
                            && stored_request(&existing)?.artifacts == completed.artifacts =>
                    {
                        Ok(AttachedPublication::AlreadyAttached)
                    }
                    _ => Err(StoreError::OutcomeUnknown {
                        operation_id: hash,
                    }),
                }
            }
        }
    }


    /// Re-derive a round's whole publication from the durable register of
    /// its session: the delivery identities in stable order, the complete
    /// ordered public history and the ordered artifact parts — plus the
    /// committed row's own archive content the update guards on. The sealed
    /// envelope is the only authority read here.
    async fn sealed_publication(
        &self,
        round: &RoundKey,
    ) -> Result<SealedPublication, StoreError> {
        let document = self
            .documents
            .read_document(&self.layout.ckpt(&round.sid)?)
            .await
            .map_err(|error| crate::error::from_read(&error))?
            .ok_or_else(|| not_applied("session_envelope_missing"))?;
        let envelope: CheckpointEnvelope = decode_json(document.value)?;
        if envelope.checkpoint.commit_seq.0 == 0 {
            return Err(not_applied("session_checkpoint_missing"));
        }
        let hash = archive_key(&round.scope, &round.group, &round.tid)?;
        let row = self
            .read_row(&hash)
            .await?
            .ok_or_else(|| not_applied("archive_absent"))?;
        if row.state != COMMITTED {
            return Err(not_applied("archive_not_committed"));
        }
        let archive = stored_request(&row)?;
        let archive_json = row
            .archive_json
            .clone()
            .ok_or_else(|| invalid("archive_corrupt"))?;
        let mut effects = Vec::new();
        let mut history = Vec::new();
        let mut artifacts: Vec<ArtifactPart> = Vec::new();
        for entry in envelope
            .checkpoint
            .feed_publications
            .iter()
            .filter(|p| p.sid == round.sid && p.tid == round.tid && p.generation == round.generation)
        {
            let EffectKind::AppendEvents { events, .. } = &entry.kind else {
                continue;
            };
            effects.push(entry.effect.clone());
            for event in events {
                history.push(event.clone());
                if let OutputBody::Artifact {
                    artifact_id,
                    update,
                    ..
                } = &event.body
                {
                    match amend_part(&mut artifacts, artifact_id, update) {
                        Ok(()) => {}
                        Err(error) => return Err(error),
                    }
                }
            }
        }
        if effects.is_empty() {
            return Err(not_applied("publication_not_registered"));
        }
        let _ = archive;
        Ok(SealedPublication {
            effects,
            history,
            artifacts,
            archive_json,
        })
    }
}

/// The store's own derivation of the feed's per-artifact update rule —
/// the verification side never imports the worker's derivation. An
/// `Upsert` replaces the part content, an `AppendText` appends its string
/// delta to an existing text part (or starts the part from the delta). A
/// sealed frame cannot append onto non-text content (`append_requires_text`
/// in the shared projection) and a `Payload::Ref` frame stays a digest
/// reference in the history — so those cases only keep this mirror
/// deterministic, never inventing content for the public projection to
/// restore elsewhere.
fn amend_part(
    artifacts: &mut Vec<ArtifactPart>,
    artifact_id: &ArtifactId,
    update: &ArtifactUpdate,
) -> Result<(), StoreError> {
    let index = artifacts
        .iter()
        .position(|part| part.artifact_id == *artifact_id);
    let next_order = |len: usize| -> Result<NonZeroU32, StoreError> {
        NonZeroU32::new(u32::try_from(len + 1).map_err(|_| invalid("archive_part_count"))?)
            .ok_or_else(|| invalid("archive_part_count"))
    };
    match update {
        ArtifactUpdate::Upsert { content } => {
            let Payload::Inline(value) = content else {
                return Ok(());
            };
            let part = match value {
                serde_json::Value::String(text) => RoundPart::Text { text: text.clone() },
                other => RoundPart::Data { data: other.clone() },
            };
            match index {
                Some(position) => artifacts[position].part = part,
                None => artifacts.push(ArtifactPart {
                    artifact_id: artifact_id.clone(),
                    order: next_order(artifacts.len())?,
                    part,
                }),
            }
        }
        ArtifactUpdate::AppendText { delta } => {
            let Payload::Inline(serde_json::Value::String(delta)) = delta else {
                return Ok(());
            };
            match index {
                Some(position) => match &mut artifacts[position].part {
                    RoundPart::Text { text } => text.push_str(delta),
                    RoundPart::Data { .. } => {
                        artifacts[position].part = RoundPart::Text {
                            text: delta.clone(),
                        }
                    }
                },
                None => artifacts.push(ArtifactPart {
                    artifact_id: artifact_id.clone(),
                    order: next_order(artifacts.len())?,
                    part: RoundPart::Text { text: delta.clone() },
                }),
            }
        }
    }
    Ok(())
}
