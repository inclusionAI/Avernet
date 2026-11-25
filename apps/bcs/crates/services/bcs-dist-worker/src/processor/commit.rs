use super::encoding::encoded_size;
use bcs_dist_encoding::digest_json;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, session::*};
use std::num::NonZeroU32;

/// The caller freezes the ID once and keeps the returned request unchanged until
/// commit resolution. Digests are computed after candidate Blob externalization.
pub(super) fn seal_candidate(
    owner: SessionOwnership,
    previous: CommitSeq,
    id: CommitId,
    mut candidate: Checkpoint,
    finalizing: bool,
    envelope_reserve: usize,
) -> Result<CommitRequest, StoreError> {
    let next = previous
        .0
        .checked_add(1)
        .ok_or_else(|| invalid("commit_sequence_exhausted"))?;
    if candidate.commit_seq != CommitSeq(next)
        || id.0.is_empty()
        || encoded_size(&id.0, 256).is_none()
    {
        return Err(invalid("candidate_commit_identity"));
    }
    let limits = candidate
        .tasks
        .front()
        .map(|task| task.output_limits)
        .unwrap_or(PHASE_ONE_OUTPUT_LIMITS);
    let Some(batch) = candidate.outbox.as_mut() else {
        return Err(invalid("candidate_outbox_missing"));
    };
    batch.source_commit = candidate.commit_seq;
    batch.commit_id = id.clone();
    batch.content_digest = Digest(String::new());
    if batch.effects.len() > 128 {
        return Err(invalid("candidate_effect_count"));
    }
    for (index, effect) in batch.effects.iter_mut().enumerate() {
        effect.id = EffectId {
            sid: owner.sid.clone(),
            commit: candidate.commit_seq,
            index: NonZeroU32::new(index as u32 + 1)
                .ok_or_else(|| invalid("candidate_effect_index"))?,
        };
        effect.content_digest = digest_json(
            &serde_json::to_value(&effect.kind).map_err(|_| invalid("candidate_encoding"))?,
        );
    }
    // The strict-group feed register (plan T7 slice 2): entries registered by
    // THIS candidate — `effect.commit == candidate.commit_seq`, since sealed
    // history only ever carries strictly older commits — receive their stable
    // delivery identity here, continuing the batch's dense index order, plus
    // the sealed commit binding. Their `commit_digest` is self-referential: it
    // joins the digest through the fixed empty-string representation and
    // receives the sealed value everywhere else.
    let mut registered = 0u32;
    for publication in candidate
        .feed_publications
        .iter_mut()
        .filter(|p| p.effect.commit == candidate.commit_seq)
    {
        registered = registered.checked_add(1).ok_or_else(|| invalid("candidate_effect_count"))?;
        publication.sid = owner.sid.clone();
        publication.commit_id = id.clone();
        publication.consumed = candidate.consumed.clone();
        publication.commit_digest = Digest(String::new());
        let index = batch.effects.len() as u32 + registered;
        publication.effect = EffectId {
            sid: owner.sid.clone(),
            commit: candidate.commit_seq,
            index: NonZeroU32::new(index).ok_or_else(|| invalid("candidate_effect_index"))?,
        };
        publication.content_digest = digest_json(
            &serde_json::to_value(&publication.kind).map_err(|_| invalid("candidate_encoding"))?,
        );
    }
    // The self-referential fields — the batch digest and the digest of this
    // candidate's own register entries — have the fixed empty-string
    // representation in the hash input. Everything else, including consumed,
    // complete effects and sealed history, participates in the digest.
    let content_digest =
        digest_json(&serde_json::to_value(&candidate).map_err(|_| invalid("candidate_encoding"))?);
    candidate
        .outbox
        .as_mut()
        .ok_or_else(|| invalid("candidate_outbox_missing"))?
        .content_digest = content_digest.clone();
    for publication in candidate
        .feed_publications
        .iter_mut()
        .filter(|p| p.effect.commit == candidate.commit_seq)
    {
        publication.commit_digest = content_digest.clone();
    }
    let checkpoint_budget = (limits.max_checkpoint_bytes as usize)
        .checked_sub(envelope_reserve)
        .and_then(|limit| {
            limit.checked_sub(if finalizing {
                0
            } else {
                limits.checkpoint_finalization_reserve as usize
            })
        })
        .ok_or_else(|| invalid("candidate_wrapper_reserve"))?;
    let outbox_budget = limits.max_outbox_bytes.saturating_sub(if finalizing {
        0
    } else {
        limits.outbox_finalization_reserve
    }) as usize;
    if encoded_size(&candidate, checkpoint_budget).is_none()
        || encoded_size(&candidate.outbox, outbox_budget).is_none()
    {
        return Err(invalid("candidate_encoded_budget"));
    }
    Ok(CommitRequest {
        purpose: CommitPurpose::Ordinary {
            control_revision: 0,
        },
        owner,
        expected_commit_seq: previous,
        commit_id: id,
        content_digest,
        candidate,
    })
}
fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}

pub(super) fn verify_checkpoint(
    checkpoint: &Checkpoint,
    receipt: &CommitReceipt,
) -> Result<(), StoreError> {
    if checkpoint.commit_seq != receipt.sequence || checkpoint.consumed != receipt.consumed {
        return Err(invalid("checkpoint_receipt_mismatch"));
    }
    let batch = checkpoint
        .outbox
        .as_ref()
        .ok_or_else(|| invalid("checkpoint_outbox_missing"))?;
    if batch.commit_id != receipt.commit_id
        || batch.source_commit != receipt.sequence
        || batch.content_digest != receipt.content_digest
        || encoded_size(checkpoint, 1024 * 1024).is_none()
    {
        return Err(invalid("checkpoint_receipt_mismatch"));
    }
    let mut value = serde_json::to_value(checkpoint).map_err(|_| invalid("checkpoint_encoding"))?;
    value["outbox"]["content_digest"] = serde_json::Value::String(String::new());
    if let Some(publications) = value.get_mut("feed_publications").and_then(|p| p.as_array_mut()) {
        for publication in publications {
            // Only this commit's own register entries carry the empty-string
            // digest placeholder at verification time; sealed history keeps
            // its real values, exactly as later seals hashed them.
            if publication.get("effect").and_then(|e| e.get("commit"))
                == Some(&serde_json::json!(receipt.sequence.0))
            {
                publication["commit_digest"] = serde_json::Value::String(String::new());
            }
        }
    }
    if digest_json(&value) != receipt.content_digest {
        return Err(invalid("checkpoint_digest"));
    }
    Ok(())
}
