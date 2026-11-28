//! Runner-independent terminal transitions share the ordinary archive protocol.
use super::{
    effects::CompletedEffects,
    transitions::{install_effects, next_checkpoint},
};
use bcs_dist_encoding::{digest_fields, hex_of};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, session::*};

pub(super) fn finalize_independently(
    before: &Checkpoint,
    metadata: &TaskSnapshot,
    status: TerminalStatus,
    reason: &'static str,
    now: TimestampMillis,
    completed: Option<&CompletedEffects>,
) -> Result<Checkpoint, StoreError> {
    let mut candidate = next_checkpoint(before, completed)?;
    let task = candidate
        .tasks
        .iter_mut()
        .find(|task| task.tid == metadata.tid)
        .ok_or_else(|| invalid("finalizer_task_missing"))?;
    if !matches!(task.phase, TaskPhase::Running | TaskPhase::Queued)
        || task.finalization.is_some()
        || task.output_limits != metadata.output_limits
    {
        return Err(invalid("finalizer_frozen_task"));
    }
    let content = candidate
        .view_drafts
        .get(&task.tid)
        .unwrap_or(&metadata.content);
    let record = FinalTaskRecord {
        schema_version: 1,
        scope: metadata.scope.clone(),
        sid: metadata.sid.clone(),
        tid: task.tid.clone(),
        status,
        snapshot: content.snapshot.clone(),
        result: Payload::Inline(serde_json::json!({"code":reason})),
        source_commit: candidate.commit_seq,
        finalized_at: now,
    };
    let identity = digest_fields(&[
        b"independent-finalize:v1",
        metadata.tid.0.as_bytes(),
        &candidate.commit_seq.0.to_be_bytes(),
        reason.as_bytes(),
    ]);
    let operation_id = RequestId(format!("finalize-{}", hex_of(&identity)));
    task.phase = TaskPhase::AwaitingResult { status };
    task.finalization = Some(TaskFinalization {
        operation_id: operation_id.clone(),
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
    let mut effects = Vec::new();
    for (did, _) in candidate
        .dispatch_seq
        .iter()
        .filter(|(_, sequence)| sequence.tid == metadata.tid && !sequence.terminal_seen)
    {
        let identity = digest_fields(&[
            b"finalizer-cancel:v1",
            operation_id.0.as_bytes(),
            did.0.as_bytes(),
        ]);
        effects.push(EffectKind::RequestCancel {
            did: did.clone(),
            intent: CancelIntent {
                request_id: RequestId(format!("cancel-{}", hex_of(&identity))),
                reason: Some(reason.into()),
            },
        });
    }
    effects.push(EffectKind::SaveResult { record });
    install_effects(&mut candidate, &metadata.sid, effects)?;
    Ok(candidate)
}

/// A Done checkpoint alone is insufficient. The exact batch's completion is
/// required before dropping terminal local data or initializing the next task.
pub(super) fn retire_published(
    before: &Checkpoint,
    completed: Option<&CompletedEffects>,
) -> Result<Checkpoint, StoreError> {
    let mut candidate = next_checkpoint(before, completed)?;
    let done: Vec<Tid> = candidate
        .tasks
        .iter()
        .filter(|task| task.phase == TaskPhase::Done)
        .map(|task| {
            if task
                .finalization
                .as_ref()
                .is_none_or(|f| f.terminal_event_id.is_none())
                || task.result_ref.is_none()
            {
                return Err(invalid("terminal_proof_missing"));
            }
            Ok(task.tid.clone())
        })
        .collect::<Result<_, _>>()?;
    candidate.tasks.retain(|task| !done.contains(&task.tid));
    candidate
        .deadline_history
        .retain(|_, deadline| match &deadline.kind {
            DeadlineKind::CancelGrace { .. } => false,
            DeadlineKind::Task { tid }
            | DeadlineKind::Snapshot { tid }
            | DeadlineKind::Dispatch { tid, .. } => !done.contains(tid),
        });
    candidate.view_drafts.retain(|tid, _| !done.contains(tid));
    candidate
        .dispatch_seq
        .retain(|_, sequence| !done.contains(&sequence.tid));
    Ok(candidate)
}
fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}

pub(super) fn task_timeout_id(tid: &Tid) -> DeadlineId {
    let digest = digest_fields(&[b"task-deadline:v1", tid.0.as_bytes()]);
    DeadlineId(format!("task-{}", hex_of(&digest)))
}
