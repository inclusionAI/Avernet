//! The strict-group feed publication follower lane (plan T7 slice 2).
//!
//! `AppendEvents`/`PublishSnapshot` obligations registered with the
//! authoritative commit are delivered here, independently of round
//! progression: the lane runs on every session visit, attempts at most a
//! bounded number of the earliest undelivered entries in stable ID order
//! (their sealed `(commit, index)`), and proves both steps of every pair by
//! a durable read-back — append first (`previous` chaining through the
//! feed's own head CAS), then the task's snapshot, whose completion is never
//! implied by the append's.
//!
//! Outcome classes keep authority and delivery separated:
//!
//! * refusal (`OutOfOrder`, unavailable, unknown, lost replies): retry next
//!   visit; never blocks retirement or the next round's dispatch;
//! * conflict (`ContentConflict`, or the store's persistent
//!   identity-content conflict): the strict spine's Done gate refuses to
//!   close the round's terminal over a disputed identity; the registered
//!   intent stays durably until the conflict resolves;
//! * completion (`Appended`/`AlreadyApplied`/`Compacted` append legs,
//!   `Published`/`AlreadyApplied`/`Superseded` publish legs): recorded as a
//!   durable read-back proof; completed obligations of archived rounds are
//!   pruned by their own bounded housekeeping commit.
use super::{
    engine::{Prepared, Slot},
    transitions::next_checkpoint,
};
use crate::runtime::{err, store_error};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    common::*, result::*, session::*, task_feed::*,
};
use bcs_service_api::types::dist_runtime::RuntimeError;

/// Bounded delivery attempts per session visit.
const SWEEP_BUDGET: u8 = 4;

/// The sealed round publication of one task: the delivery identities of its
/// obligations in stable order, the complete ordered public history and the
/// ordered artifact parts derived one-to-one from the sealed artifact
/// Upsert frames. Reference (`Payload::Ref`) artifacts stay digest
/// references in the history frames — the public side restores them by
/// deref (contract-decisions S1.3), so they are not inline parts; append
/// text deltas amend their artifact's previous frame instead of adding one.
pub(super) struct RoundPublication {
    pub effects: Vec<EffectId>,
    pub history: Vec<OutputEvent>,
    pub artifacts: Vec<ArtifactPart>,
}

pub(super) fn sealed_round_publication(
    register: &[FeedPublication],
    tid: &Tid,
) -> Option<RoundPublication> {
    let mut effects = Vec::new();
    let mut history = Vec::new();
    let mut artifacts: Vec<ArtifactPart> = Vec::new();
    for entry in register.iter().filter(|p| p.tid == *tid) {
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
                amend_part(&mut artifacts, artifact_id, update)?;
            }
        }
    }
    if effects.is_empty() {
        return None;
    }
    Some(RoundPublication {
        effects,
        history,
        artifacts,
    })
}

/// Amend the ordered parts with the feed's own per-artifact update rule: an
/// `Upsert` replaces the part content, an `AppendText` appends its string
/// delta to an existing text part (or starts the part from the delta,
/// exactly as the shared display projection creates a fresh unit for a
/// first append). Digest-reference (`Payload::Ref`) frames stay in the
/// history — the public projection restores them by deref, never as an
/// invented inline part — and appending onto non-text content is not a
/// sealable frame (`append_requires_text` in the shared projection), so
/// the defensive revision below only keeps both derivations of this rule
/// bit-identically deterministic.
pub(super) fn amend_part(
    artifacts: &mut Vec<ArtifactPart>,
    artifact_id: &ArtifactId,
    update: &ArtifactUpdate,
) -> Option<()> {
    let index = artifacts
        .iter()
        .position(|part| part.artifact_id == *artifact_id);
    let next_order = |len: usize| -> Option<std::num::NonZeroU32> {
        std::num::NonZeroU32::new(u32::try_from(len + 1).ok()?)
    };
    match update {
        ArtifactUpdate::Upsert { content } => {
            let Payload::Inline(value) = content else {
                return Some(());
            };
            let part = match value {
                serde_json::Value::String(text) => RoundPart::Text { text: text.clone() },
                other => RoundPart::Data { data: other.clone() },
            };
            match index {
                // An Upsert replaces the content; the part keeps its
                // first-appearance order (never renumbered by storage).
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
                return Some(());
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
    Some(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum Delivery {
    /// The durable read-back proved completion.
    Asserted,
    /// The feed head is not ready for this entry yet (predecessor pending).
    Blocked,
    /// A transient refusal; retry on a later visit.
    Failed,
    /// A conflicting second sink owns the identity; the strict spine refuses.
    Conflicted,
}

impl super::WorkerProcessor {
    /// Follower sweep over the durable publication register: deliver the
    /// earliest undelivered entries in stable ID order, record read-back
    /// proofs, and schedule the register's housekeeping commit once proofs
    /// covering an archived round accumulate. Never a synchronous
    /// precondition of any transition — it only observes sealed entries.
    pub(super) async fn sweep_feed_publications(
        &self,
        slot: &mut Slot,
    ) -> Result<(), RuntimeError> {
        let register = slot
            .acquired
            .envelope
            .checkpoint
            .feed_publications
            .clone();
        let sealed_owner = slot.acquired.envelope.owner.clone();
        if register.is_empty() {
            return Ok(());
        }
        let mut budget = SWEEP_BUDGET;
        for publication in &register {
            if slot.publication_proofs.iter().any(|p| p == &publication.effect) {
                continue;
            }
            if budget == 0 {
                break;
            }
            match self
                .deliver_publication(publication, sealed_owner.clone())
                .await?
            {
                Delivery::Asserted => {
                    slot.publication_proofs.push(publication.effect.clone());
                    budget -= 1;
                }
                Delivery::Blocked | Delivery::Failed => break,
                Delivery::Conflicted => {
                    // A disputed identity must not wedges the sweep behind
                    // its own predecessors; later entries stay pending too.
                    break;
                }
            }
        }
        self.schedule_publication_prune(slot).await
    }

    /// The bounded housekeeping commit: proven obligations of archived
    /// rounds leave the register through their own sealed candidate, so
    /// neither retirement nor compaction ever clears pending items and the
    /// register itself stays bounded by outstanding work.
    async fn schedule_publication_prune(&self, slot: &mut Slot) -> Result<(), RuntimeError> {
        // "Attached" is durable archive state, not slot memory: a takeover
        // slot restores it by reading the committed archive rows before
        // pruning, so proven obligations of retired rounds retire too.
        let candidates: Vec<FeedPublication> = slot
            .acquired
            .envelope
            .checkpoint
            .feed_publications
            .iter()
            .filter(|entry| {
                slot.publication_proofs.iter().any(|p| *p == entry.effect)
                    && !slot.attached_rounds.iter().any(|t| *t == entry.tid)
            })
            .cloned()
            .collect();
        for entry in &candidates {
            if self.round_attached_durable(entry).await? {
                slot.attached_rounds.push(entry.tid.clone());
            }
        }
        let pruneable = |entry: &FeedPublication, slot: &Slot| {
            slot.publication_proofs.iter().any(|p| *p == entry.effect)
                && slot.attached_rounds.iter().any(|t| *t == entry.tid)
        };
        if slot.prepared.is_none()
            && slot.completed.is_some()
            && slot
                .acquired
                .envelope
                .checkpoint
                .feed_publications
                .iter()
                .any(|entry| pruneable(entry, slot))
        {
            let before = slot.acquired.envelope.checkpoint.clone();
            let completed = slot.completed.clone();
            let mut candidate =
                next_checkpoint(&before, completed.as_ref()).map_err(store_error)?;
            candidate
                .feed_publications
                .retain(|entry| !pruneable(entry, slot));
            slot.prepared = Some(Prepared {
                candidate,
                finalizing: false,
                id: CommitId(uuid::Uuid::new_v4().to_string()),
                purpose: self.ordinary_purpose(slot)?,
            });
        }
        Ok(())
    }

    /// Durable attach check: the committed archive of the entry's task
    /// already carries its real publication (parts plus history).
    async fn round_attached_durable(
        &self,
        entry: &FeedPublication,
    ) -> Result<bool, RuntimeError> {
        let Some(archives) = self.ports.archives.clone() else {
            return Ok(false);
        };
        match self
            .io(archives.get_archived(
                &ArchiveGrants { read: true },
                &entry.scope,
                &entry.group,
                &entry.tid,
            ))
            .await?
        {
            Some(ArchivedTaskView::Committed { archive, .. }) => {
                Ok(!archive.public_history.is_empty())
            }
            _ => Ok(false),
        }
    }

    pub(super) async fn deliver_publication(
        &self,
        publication: &FeedPublication,
        sealed_owner: ShardOwner,
    ) -> Result<Delivery, RuntimeError> {
        // The sealed authority of the registering commit. Owner equality is
        // never the authorization rule (the evidence verifier ignores the
        // field; the register's binding is), so the durable envelope owner
        // and the receipt digest binding carry the identity.
        let source = EffectSource {
            commit: CommitReceipt {
                sid: publication.sid.clone(),
                owner: sealed_owner,
                commit_id: publication.commit_id.clone(),
                content_digest: publication.commit_digest.clone(),
                sequence: publication.effect.commit,
                consumed: publication.consumed.clone(),
                proof: String::new(),
            },
            effect_id: publication.effect.clone(),
            content_digest: publication.content_digest.clone(),
        };
        match &publication.kind {
            EffectKind::AppendEvents { previous, events } => {
                let outcome = self
                    .delivery_result(self.ports.output.append(AppendCommittedEvents {
                        source: source.clone(),
                        previous: *previous,
                        events: events.clone(),
                    }))
                    .await?;
                match outcome {
                    Ok(AppendOutcome::Appended) => {
                        // Durable read-back: the completion evidence must
                        // survive its own read, never the append reply alone.
                        let read_back = self
                            .delivery_result(self.ports.output.append(AppendCommittedEvents {
                                source,
                                previous: *previous,
                                events: events.clone(),
                            }))
                            .await?;
                        Ok(match read_back.ok() {
                            Some(AppendOutcome::AlreadyApplied | AppendOutcome::Compacted(_)) => {
                                Delivery::Asserted
                            }
                            _ => Delivery::Failed,
                        })
                    }
                    Ok(AppendOutcome::AlreadyApplied) | Ok(AppendOutcome::Compacted(_)) => {
                        Ok(Delivery::Asserted)
                    }
                    Ok(AppendOutcome::OutOfOrder) => Ok(Delivery::Blocked),
                    Ok(AppendOutcome::ContentConflict) => Ok(Delivery::Conflicted),
                    // A bounded refusal or a lost reply (classified by
                    // `delivery_result`): retry on a later visit.
                    Err(_) => Ok(Delivery::Failed),
                }
            }
            EffectKind::PublishSnapshot { snapshot } => {
                let outcome = self
                    .delivery_result(self.ports.output.publish(PublishCommittedSnapshot {
                        source: source.clone(),
                        snapshot: snapshot.clone(),
                    }))
                    .await?;
                match outcome {
                    Ok(PublishOutcome::Published) => {
                        // The publish leg proves its own durable completion;
                        // the pair's append success never substitutes for it.
                        let read_back = self
                            .delivery_result(self.ports.output.publish(PublishCommittedSnapshot {
                                source,
                                snapshot: snapshot.clone(),
                            }))
                            .await?;
                        Ok(match read_back.ok() {
                            Some(
                                PublishOutcome::AlreadyApplied | PublishOutcome::Superseded { .. },
                            ) => Delivery::Asserted,
                            _ => Delivery::Failed,
                        })
                    }
                    Ok(PublishOutcome::AlreadyApplied) | Ok(PublishOutcome::Superseded { .. }) => {
                        Ok(Delivery::Asserted)
                    }
                    Ok(PublishOutcome::ContentConflict) => Ok(Delivery::Conflicted),
                    Err(_) => Ok(Delivery::Failed),
                }
            }
            // The register holds exactly the paired feed kinds; anything
            // else is a sealed-corruption loud error, never skipped.
            _ => Err(err("publication_kind_invalid")),
        }
    }

    /// One delivery attempt against the publication ports, with the store's
    /// own error preserved for class classification: refusals
    /// (`NotApplied`), lost replies (`OutcomeUnknown`) and the store's
    /// persistent identity-content conflict each map to a `Delivery` class;
    /// any other hard failure propagates (write failures are never
    /// swallowed into success).
    async fn delivery_result<T>(
        &self,
        future: impl Future<Output = Result<T, StoreError>>,
    ) -> Result<Result<T, StoreError>, RuntimeError> {
        let outcome = match tokio::time::timeout(self.config.request_timeout, future).await {
            Ok(result) => result,
            Err(_) => Err(StoreError::OutcomeUnknown {
                operation_id: "publication_delivery".into(),
            }),
        };
        match outcome {
            Ok(value) => Ok(Ok(value)),
            // A bounded refusal or a lost reply: retry on a later visit.
            Err(refusal @ (StoreError::NotApplied { .. } | StoreError::OutcomeUnknown { .. })) => {
                if matches!(refusal, StoreError::NotApplied { .. }) {
                    Ok(Err(StoreError::NotApplied {
                        code: "publication_refused".into(),
                    }))
                } else {
                    Ok(Err(StoreError::OutcomeUnknown {
                        operation_id: "publication_delivery".into(),
                    }))
                }
            }
            // A conflicting second sink owns the identity: the strict spine
            // refuses to close over it (C3). Anything else is a loud
            // failure and propagates.
            Err(other) => Err(store_error(other)),
        }
    }
}
