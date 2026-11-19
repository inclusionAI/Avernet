use super::{decision::ValidatedDecision, effects::CompletedEffects, encoding::encoded_size};
use bcs_dist_encoding::{digest_fields, hex_of};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, session::*};
use std::num::NonZeroU32;

pub(super) fn next_checkpoint(
    before: &Checkpoint,
    completed: Option<&CompletedEffects>,
) -> Result<Checkpoint, StoreError> {
    if let Some(batch) = &before.outbox
        && !completed.is_some_and(|done| {
            done.commit_id == batch.commit_id
                && done.sequence == batch.source_commit
                && done.digest == batch.content_digest
        })
    {
        return Err(invalid("previous_effects_incomplete"));
    }
    let next = before
        .commit_seq
        .0
        .checked_add(1)
        .ok_or_else(|| invalid("commit_sequence_exhausted"))?;
    let mut candidate = before.clone();
    candidate.commit_seq = CommitSeq(next);
    candidate.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: CommitId(String::new()),
        source_commit: CommitSeq(next),
        content_digest: Digest(String::new()),
        effects: vec![],
    });
    Ok(candidate)
}

pub(super) fn apply_decision(
    before: &Checkpoint,
    metadata: &TaskSnapshot,
    decision: ValidatedDecision,
    now: TimestampMillis,
    completed: Option<&CompletedEffects>,
) -> Result<Checkpoint, StoreError> {
    let mut candidate = next_checkpoint(before, completed)?;
    let task = candidate
        .tasks
        .iter_mut()
        .find(|task| task.tid == metadata.tid)
        .ok_or_else(|| invalid("active_task_missing"))?;
    if task.tid != metadata.tid
        || task.phase != TaskPhase::Running
        || task.output_limits != metadata.output_limits
    {
        return Err(invalid("active_task_identity"));
    }
    let mut content = candidate
        .view_drafts
        .get(&task.tid)
        .cloned()
        .unwrap_or_else(|| metadata.content.clone());
    let old_status = content.status;
    let old_interactions = content.interactions.clone();
    let mut events = decision.events;
    if let Some((status, interactions)) = decision.status {
        content.status = status;
        content.interactions = interactions;
    } else if content.status == TaskStatus::Submitted {
        content.status = TaskStatus::Working;
        events.insert(
            0,
            OutputBody::Status {
                status: content.status,
                interactions: vec![],
            },
        );
    }
    content.snapshot = Payload::Inline(
        serde_json::to_value(&decision.display).map_err(|_| invalid("display_encoding"))?,
    );
    task.output_usage = decision.usage;
    candidate.runner_state = Some(TaskRunnerState {
        tid: task.tid.clone(),
        state: decision.next_state,
    });
    let active_deadlines = candidate
        .deadlines
        .iter()
        .map(|d| d.id.clone())
        .collect::<std::collections::BTreeSet<_>>();
    for deadline in &candidate.deadlines {
        if !matches!(deadline.kind, DeadlineKind::Snapshot { .. }) {
            candidate
                .deadline_history
                .entry(deadline.id.clone())
                .or_insert_with(|| deadline.clone());
        }
    }
    candidate.deadlines.retain(|deadline| {
        !belongs_to_task(deadline, &task.tid)
            || matches!(deadline.kind, DeadlineKind::Snapshot { .. })
            || deadline.id == super::lifecycle::task_timeout_id(&task.tid)
    });
    for deadline in decision.deadlines {
        let previous = candidate.deadline_history.get(&deadline.id);
        if previous.is_some_and(|prior| prior != &deadline) {
            return Err(invalid("deadline_identity_conflict"));
        }
        let arm = previous.is_none() || active_deadlines.contains(&deadline.id);
        candidate
            .deadline_history
            .insert(deadline.id.clone(), deadline.clone());
        if arm && !candidate.deadlines.iter().any(|old| old.id == deadline.id) {
            candidate.deadlines.push(deadline);
        }
    }
    if candidate.deadline_history.len() > 256 {
        return Err(invalid("deadline_history_capacity"));
    }
    let mut kinds = decision.effects;
    for kind in &kinds {
        if let EffectKind::CreateDispatch { spec } = kind {
            candidate.dispatch_seq.insert(
                spec.did.clone(),
                DispatchSequence {
                    tid: spec.tid.clone(),
                    holder_epoch: HolderEpoch(0),
                    sequence: InputSeq(0),
                },
            );
        }
    }
    let outputs = assign_events(&metadata.tid, candidate.commit_seq, events)?;
    for event in &outputs {
        if matches!(
            event.body,
            OutputBody::Artifact {
                class: OutputClass::Output | OutputClass::Tool,
                ..
            }
        ) {
            task.publication.pending_events = task
                .publication
                .pending_events
                .checked_add(1)
                .ok_or_else(|| invalid("snapshot_event_count"))?;
            let bytes = encoded_size(event, 2 * 1024 * 1024)
                .ok_or_else(|| invalid("snapshot_delta_bound"))? as u64;
            task.publication.pending_bytes = task
                .publication
                .pending_bytes
                .checked_add(bytes)
                .ok_or_else(|| invalid("snapshot_delta_count"))?;
        }
    }
    if !outputs.is_empty() {
        candidate.last_event_id = outputs.last().map(|event| event.id);
        kinds.push(EffectKind::AppendEvents {
            previous: before.last_event_id,
            events: outputs,
        });
    }
    let policy = task.output_limits.snapshots;
    if task.publication.pending_events > 0 && task.publication.flush_deadline.is_none() {
        let at = now
            .0
            .checked_add(
                i64::try_from(policy.max_delay_millis).map_err(|_| invalid("snapshot_delay"))?,
            )
            .ok_or_else(|| invalid("snapshot_deadline"))?;
        let digest = digest_fields(&[
            b"snapshot-deadline:v1",
            metadata.tid.0.as_bytes(),
            &candidate.commit_seq.0.to_be_bytes(),
        ]);
        let deadline = Deadline {
            id: DeadlineId(format!("snapshot-{}", hex_of(&digest))),
            at: TimestampMillis(at),
            kind: DeadlineKind::Snapshot {
                tid: task.tid.clone(),
            },
        };
        task.publication.flush_deadline = Some(deadline.clone());
        candidate.deadlines.push(deadline);
    }
    let publish = content.status != old_status
        || content.interactions != old_interactions
        || task.publication.pending_events >= policy.max_pending_events
        || task.publication.pending_bytes >= policy.max_pending_bytes
        || task
            .publication
            .flush_deadline
            .as_ref()
            .is_some_and(|deadline| deadline.at <= now);
    if publish {
        task.publication.pending_events = 0;
        task.publication.pending_bytes = 0;
        task.publication.published_through = candidate.last_event_id;
        task.publication.flush_deadline = None;
        candidate.deadlines.retain(|deadline| !matches!(&deadline.kind, DeadlineKind::Snapshot { tid } if *tid == task.tid));
        kinds.push(EffectKind::PublishSnapshot {
            snapshot: published(
                metadata,
                content.clone(),
                task.output_usage,
                candidate.commit_seq,
                candidate.last_event_id,
                now,
            ),
        });
    }
    if let Some((status, result)) = decision.finish {
        let record = FinalTaskRecord {
            schema_version: 1,
            scope: metadata.scope.clone(),
            sid: metadata.sid.clone(),
            tid: task.tid.clone(),
            status,
            snapshot: content.snapshot.clone(),
            result,
            source_commit: candidate.commit_seq,
            finalized_at: now,
        };
        let digest = digest_fields(&[
            b"finalize:v1",
            metadata.tid.0.as_bytes(),
            &candidate.commit_seq.0.to_be_bytes(),
        ]);
        task.phase = TaskPhase::AwaitingResult { status };
        task.finalization = Some(TaskFinalization {
            operation_id: RequestId(format!("finalize-{}", hex_of(&digest))),
            record: record.clone(),
            terminal_event_id: None,
            terminal_published: false,
        });
        if candidate
            .runner_state
            .as_ref()
            .is_some_and(|state| state.tid == metadata.tid)
        {
            candidate.runner_state = None;
        }
        kinds.push(EffectKind::SaveResult { record });
    }
    candidate.view_drafts.insert(task.tid.clone(), content);
    install_effects(&mut candidate, &metadata.sid, kinds)?;
    Ok(candidate)
}

/// Publishing a quiet display is not a Runner transition. Preserve every
/// business deadline and the private state exactly while clearing flush progress.
pub(super) fn flush_snapshot(
    before: &Checkpoint,
    metadata: &TaskSnapshot,
    display: DisplaySnapshot,
    now: TimestampMillis,
    completed: Option<&CompletedEffects>,
) -> Result<Checkpoint, StoreError> {
    let task = before
        .tasks
        .iter()
        .find(|task| task.tid == metadata.tid && task.phase == TaskPhase::Running)
        .ok_or_else(|| invalid("snapshot_task_missing"))?;
    let state = before
        .runner_state
        .as_ref()
        .filter(|s| s.tid == task.tid)
        .ok_or_else(|| invalid("runner_state_missing"))?;
    let decision = ValidatedDecision {
        next_state: state.state.clone(),
        display,
        usage: task.output_usage,
        events: vec![],
        effects: vec![],
        finish: None,
        deadlines: before
            .deadlines
            .iter()
            .filter(|d| {
                belongs_to_task(d, &task.tid) && !matches!(d.kind, DeadlineKind::Snapshot { .. })
            })
            .cloned()
            .collect(),
        status: None,
    };
    apply_decision(before, metadata, decision, now, completed)
}

pub(super) fn archive_confirmed(
    before: &Checkpoint,
    metadata: &TaskSnapshot,
    now: TimestampMillis,
    completed: &CompletedEffects,
) -> Result<Checkpoint, StoreError> {
    let mut candidate = next_checkpoint(before, Some(completed))?;
    let task = candidate
        .tasks
        .iter_mut()
        .find(|task| task.tid == metadata.tid)
        .ok_or_else(|| invalid("finalizing_task_missing"))?;
    let TaskPhase::AwaitingResult { status } = task.phase else {
        return Err(invalid("finalizing_task_phase"));
    };
    let reference = completed
        .saved
        .get(&task.tid)
        .ok_or_else(|| invalid("archive_unconfirmed"))?
        .clone();
    let finalization = task
        .finalization
        .as_mut()
        .ok_or_else(|| invalid("frozen_result_missing"))?;
    if task.tid != metadata.tid
        || finalization.record.status != status
        || finalization.record.tid != task.tid
    {
        return Err(invalid("frozen_result_identity"));
    }
    let terminal_id = EventId {
        commit: candidate.commit_seq,
        index: NonZeroU32::MIN,
    };
    let content = TaskContent {
        status: terminal_status(status),
        interactions: vec![],
        snapshot: finalization.record.snapshot.clone(),
        result_ref: Some(reference.clone()),
    };
    task.phase = TaskPhase::Done;
    task.result_ref = Some(reference.clone());
    finalization.terminal_event_id = Some(terminal_id);
    task.publication = SnapshotProgress {
        published_through: Some(terminal_id),
        ..SnapshotProgress::default()
    };
    candidate
        .deadlines
        .retain(|deadline| !belongs_to_task(deadline, &task.tid));
    if candidate
        .runner_state
        .as_ref()
        .is_some_and(|state| state.tid == metadata.tid)
    {
        candidate.runner_state = None;
    }
    candidate
        .view_drafts
        .insert(task.tid.clone(), content.clone());
    candidate.last_event_id = Some(terminal_id);
    let kinds = vec![
        EffectKind::AppendEvents {
            previous: before.last_event_id,
            events: vec![OutputEvent {
                id: terminal_id,
                tid: task.tid.clone(),
                body: OutputBody::Terminal {
                    status,
                    result_ref: reference,
                },
            }],
        },
        EffectKind::PublishSnapshot {
            snapshot: published(
                metadata,
                content,
                task.output_usage,
                candidate.commit_seq,
                Some(terminal_id),
                now,
            ),
        },
    ];
    install_effects(&mut candidate, &metadata.sid, kinds)?;
    Ok(candidate)
}

pub(super) fn install_effects(
    candidate: &mut Checkpoint,
    sid: &Sid,
    kinds: Vec<EffectKind>,
) -> Result<(), StoreError> {
    let batch = candidate
        .outbox
        .as_mut()
        .ok_or_else(|| invalid("outbox_missing"))?;
    batch.effects = kinds
        .into_iter()
        .enumerate()
        .map(|(index, kind)| {
            Ok(Effect {
                id: EffectId {
                    sid: sid.clone(),
                    commit: candidate.commit_seq,
                    index: NonZeroU32::new(index as u32 + 1)
                        .ok_or_else(|| invalid("effect_index"))?,
                },
                content_digest: Digest(String::new()),
                kind,
            })
        })
        .collect::<Result<_, StoreError>>()?;
    Ok(())
}
fn assign_events(
    tid: &Tid,
    commit: CommitSeq,
    bodies: Vec<OutputBody>,
) -> Result<Vec<OutputEvent>, StoreError> {
    bodies
        .into_iter()
        .enumerate()
        .map(|(index, body)| {
            Ok(OutputEvent {
                tid: tid.clone(),
                id: EventId {
                    commit,
                    index: NonZeroU32::new(index as u32 + 1)
                        .ok_or_else(|| invalid("event_index"))?,
                },
                body,
            })
        })
        .collect()
}
fn published(
    metadata: &TaskSnapshot,
    content: TaskContent,
    usage: OutputUsage,
    sequence: CommitSeq,
    covered: Option<EventId>,
    now: TimestampMillis,
) -> TaskSnapshot {
    TaskSnapshot {
        content,
        output_usage: usage,
        source_commit: sequence,
        snapshot_event_id: covered,
        updated_at: now,
        ..metadata.clone()
    }
}
fn belongs_to_task(deadline: &Deadline, task: &Tid) -> bool {
    match &deadline.kind {
        DeadlineKind::Task { tid }
        | DeadlineKind::Dispatch { tid, .. }
        | DeadlineKind::Snapshot { tid } => tid == task,
    }
}
fn terminal_status(status: TerminalStatus) -> TaskStatus {
    match status {
        TerminalStatus::Completed => TaskStatus::Completed,
        TerminalStatus::Failed => TaskStatus::Failed,
        TerminalStatus::Canceled => TaskStatus::Canceled,
        TerminalStatus::Rejected => TaskStatus::Rejected,
    }
}
fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}
