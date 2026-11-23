#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "processor behavior tests"
)]
use super::decision::*;
use bcs_dist_model::*;
use bcs_dist_projection::OutputProjectionService;
use bcs_service_api::{core::dist_runner::*, port::repo::distributed::session::*};
use std::collections::BTreeMap;

pub(super) fn task() -> TaskProgress {
    TaskProgress {
        steps: 0,
        tid: Tid("s0001.session.task".into()),
        phase: TaskPhase::Running,
        input: Payload::Inline(serde_json::Value::Null),
        result_ref: None,
        output_limits: PHASE_ONE_OUTPUT_LIMITS,
        output_usage: OutputUsage::default(),
        publication: SnapshotProgress::default(),
        finalization: None,
    }
}
pub(super) fn spec() -> SessionSpec {
    SessionSpec {
        schema_version: 1,
        sid: Sid("s0001.session".into()),
        shard: ShardId(1),
        scope: TaskScope {
            tenant: TenantId("tenant".into()),
            principal: PrincipalId("principal".into()),
        },
        kind: SessionKind::FsmGroup,
        group: GroupId("group".into()),
        workflow: WorkflowRef {
            definition_id: "wf".into(),
            revision: "1".into(),
            digest: Digest("digest".into()),
        },
        participants: vec![],
        limits: SessionLimits {
            max_queued_tasks: 32,
            max_task_steps: 100,
            task_timeout_millis: 60000,
            max_inline_bytes: 65536,
        },
        digest: Digest("spec".into()),
        created_at: TimestampMillis(0),
    }
}
fn decision(actions: Vec<ActionIntent>) -> Decision {
    Decision {
        next_state: RunnerState {
            format_version: 1,
            value: serde_json::json!({"next":1}),
        },
        actions,
        next_deadlines: vec![],
    }
}
fn show(text: String) -> ActionIntent {
    ActionIntent::Show {
        tid: task().tid,
        node: NodeId("node".into()),
        artifact_id: ArtifactId("answer".into()),
        class: OutputClass::Output,
        update: ArtifactUpdate::Upsert {
            content: Payload::Inline(serde_json::json!(text)),
        },
    }
}
fn check(decision: Decision) -> Result<ValidatedDecision, DecisionFailure> {
    validate_decision(
        DecisionContext {
            spec: &spec(),
            task: &task(),
            display: &DisplaySnapshot {
                format_version: 1,
                units: vec![],
            },
            dispatches: &BTreeMap::new(),
            registered_deadlines: &[],
            deadline_history: &BTreeMap::new(),
        },
        decision,
        &OutputProjectionService,
    )
}

#[test]
fn whole_decision_rejects_terminal_status_after_an_otherwise_valid_show() {
    assert!(matches!(
        check(decision(vec![
            show("hello".into()),
            ActionIntent::SetStatus {
                tid: task().tid,
                status: TaskStatus::Completed,
                interactions: vec![],
            }
        ])),
        Err(DecisionFailure::Invalid("terminal_set_status"))
    ));
}

#[test]
fn large_output_is_accepted_while_private_runner_state_remains_bounded() {
    let text = "x".repeat(9 * 1024 * 1024);
    let output = check(decision(vec![show(text.clone())])).unwrap();
    assert_eq!(output.display.units[0].content, serde_json::json!(text));
    let mut candidate = decision(vec![]);
    candidate.next_state.value = serde_json::json!("x".repeat(65_536));
    assert!(matches!(
        check(candidate),
        Err(DecisionFailure::UnsafeState("runner_state_bound"))
    ));
}

#[test]
fn validated_output_retains_the_original_operation_and_full_display() {
    let valid = check(decision(vec![show("hello".into())])).unwrap();
    assert_eq!(valid.display.units[0].content, serde_json::json!("hello"));
    assert_eq!(valid.usage.cumulative_bytes, 7);
    assert_eq!(valid.events.len(), 1);
    assert!(matches!(valid.events[0], OutputBody::Artifact { .. }));
}

#[test]
fn sealed_commit_digest_covers_checkpoint_and_rejects_corrupted_late_effects() {
    use super::{
        commit::{seal_candidate, verify_checkpoint},
        effects::{EffectFailure, validate_batch},
    };
    use bcs_service_api::port::repo::distributed::common::*;
    use std::collections::VecDeque;
    let owner = SessionOwnership {
        sid: spec().sid,
        shard: ShardId(1),
        owner: ShardOwner {
            instance: Instance {
                node: "worker".into(),
                incarnation: "one".into(),
            },
            epoch: WorkerEpoch(1),
        },
    };
    let candidate = Checkpoint {
        format_version: 1,
        commit_seq: CommitSeq(1),
        consumed: None,
        tasks: VecDeque::from([task()]),
        runner_state: None,
        dispatch_seq: BTreeMap::new(),
        deadlines: vec![],
        deadline_history: Default::default(),
        view_drafts: BTreeMap::new(),
        last_event_id: None,
        outbox: Some(CommittedBatch {
            created_at: None,
            commit_id: CommitId(String::new()),
            source_commit: CommitSeq(0),
            content_digest: Digest(String::new()),
            effects: vec![],
        }),
    };
    let request = seal_candidate(
        owner.clone(),
        CommitSeq(0),
        CommitId("frozen".into()),
        candidate.clone(),
        false,
        64 * 1024,
    )
    .unwrap();
    let same = seal_candidate(
        owner.clone(),
        CommitSeq(0),
        CommitId("frozen".into()),
        candidate.clone(),
        false,
        64 * 1024,
    )
    .unwrap();
    assert_eq!(request.content_digest, same.content_digest);
    let mut changed = candidate;
    changed.deadlines.push(Deadline {
        id: DeadlineId("timeout-one".into()),
        at: TimestampMillis(10),
        kind: DeadlineKind::Task { tid: task().tid },
    });
    let changed = seal_candidate(
        owner.clone(),
        CommitSeq(0),
        CommitId("frozen".into()),
        changed,
        false,
        64 * 1024,
    )
    .unwrap();
    assert_ne!(request.content_digest, changed.content_digest);
    let receipt = CommitReceipt {
        sid: owner.sid,
        owner: owner.owner,
        commit_id: request.commit_id,
        content_digest: request.content_digest,
        sequence: CommitSeq(1),
        consumed: None,
        proof: "provider-proof".into(),
    };
    verify_checkpoint(&request.candidate, &receipt).unwrap();
    let mut corrupted = request.candidate.clone();
    corrupted.tasks[0].output_usage.cumulative_bytes = 123;
    assert!(verify_checkpoint(&corrupted, &receipt).is_err());
    let mut batch = request.candidate.outbox.unwrap();
    validate_batch(&batch, &receipt).unwrap();
    batch.effects.push(Effect {
        id: EffectId {
            sid: receipt.sid.clone(),
            commit: CommitSeq(1),
            index: std::num::NonZeroU32::MIN,
        },
        content_digest: Digest("forged".into()),
        kind: EffectKind::AppendEvents {
            previous: None,
            events: vec![],
        },
    });
    assert!(matches!(
        validate_batch(&batch, &receipt),
        Err(EffectFailure::Blocked("outbox_effect_identity"))
    ));
}

fn initial() -> Checkpoint {
    Checkpoint {
        format_version: 1,
        commit_seq: CommitSeq(0),
        consumed: None,
        tasks: std::collections::VecDeque::from([task()]),
        runner_state: None,
        dispatch_seq: BTreeMap::new(),
        deadlines: vec![],
        deadline_history: Default::default(),
        view_drafts: BTreeMap::new(),
        last_event_id: None,
        outbox: None,
    }
}
fn metadata(status: TaskStatus) -> TaskSnapshot {
    TaskSnapshot {
        schema_version: 1,
        scope: spec().scope,
        sid: spec().sid,
        tid: task().tid,
        content: TaskContent {
            status,
            interactions: vec![],
            snapshot: Payload::Inline(serde_json::json!({"format_version":1,"units":[]})),
            result_ref: None,
        },
        output_limits: PHASE_ONE_OUTPUT_LIMITS,
        output_usage: OutputUsage::default(),
        snapshot_event_id: None,
        source_commit: CommitSeq(0),
        created_at: TimestampMillis(0),
        updated_at: TimestampMillis(0),
    }
}
fn completed(checkpoint: &Checkpoint) -> super::effects::CompletedEffects {
    let batch = checkpoint.outbox.as_ref().unwrap();
    super::effects::CompletedEffects {
        commit_id: batch.commit_id.clone(),
        sequence: batch.source_commit,
        digest: batch.content_digest.clone(),
        saved: BTreeMap::new(),
    }
}

#[test]
fn finish_requires_archive_confirmation_and_a_separate_terminal_commit() {
    use super::transitions::*;
    let decision = check(decision(vec![
        show("hello".into()),
        ActionIntent::FinishTask {
            tid: task().tid,
            status: TerminalStatus::Completed,
            result: Payload::Inline(serde_json::json!({"done":true})),
        },
    ]))
    .unwrap();
    let metadata = metadata(TaskStatus::Submitted);
    let first = apply_decision(&initial(), &metadata, decision, TimestampMillis(10), None).unwrap();
    assert!(matches!(
        first.tasks[0].phase,
        TaskPhase::AwaitingResult {
            status: TerminalStatus::Completed
        }
    ));
    assert!(
        first
            .outbox
            .as_ref()
            .unwrap()
            .effects
            .iter()
            .any(|effect| matches!(effect.kind, EffectKind::SaveResult { .. }))
    );
    assert!(first.outbox.as_ref().unwrap().effects.iter().all(|effect| !matches!(&effect.kind,
        EffectKind::PublishSnapshot { snapshot } if snapshot.content.status == TaskStatus::Completed)));
    assert!(next_checkpoint(&first, None).is_err());
    let mut proof = completed(&first);
    assert!(archive_confirmed(&first, &metadata, TimestampMillis(11), &proof).is_err());
    proof.saved.insert(
        task().tid,
        BlobRef {
            key: "archive".into(),
            digest: Digest("archive-digest".into()),
        },
    );
    let second = archive_confirmed(&first, &metadata, TimestampMillis(11), &proof).unwrap();
    assert_eq!(second.commit_seq, CommitSeq(2));
    assert_eq!(second.tasks[0].phase, TaskPhase::Done);
    assert_eq!(
        second.tasks[0].finalization.as_ref().unwrap().record,
        first.tasks[0].finalization.as_ref().unwrap().record
    );
    assert!(
        !second.tasks[0]
            .finalization
            .as_ref()
            .unwrap()
            .terminal_published
    );
    assert!(next_checkpoint(&second, None).is_err());
}

#[test]
fn a_persisted_snapshot_deadline_flushes_the_last_small_delta_without_new_output() {
    use super::transitions::apply_decision;
    let metadata = metadata(TaskStatus::Working);
    let first = apply_decision(
        &initial(),
        &metadata,
        check(decision(vec![show("hello".into())])).unwrap(),
        TimestampMillis(10),
        None,
    )
    .unwrap();
    assert_eq!(first.tasks[0].publication.pending_events, 1);
    assert_eq!(
        first.tasks[0]
            .publication
            .flush_deadline
            .as_ref()
            .unwrap()
            .at,
        TimestampMillis(1010)
    );
    assert!(
        first
            .outbox
            .as_ref()
            .unwrap()
            .effects
            .iter()
            .all(|effect| !matches!(effect.kind, EffectKind::PublishSnapshot { .. }))
    );
    let Payload::Inline(value) = &first.view_drafts[&task().tid].snapshot else {
        panic!()
    };
    let display = serde_json::from_value(value.clone()).unwrap();
    let quiet = validate_decision(
        DecisionContext {
            spec: &spec(),
            task: &first.tasks[0],
            display: &display,
            dispatches: &BTreeMap::new(),
            registered_deadlines: &[],
            deadline_history: &BTreeMap::new(),
        },
        decision(vec![]),
        &OutputProjectionService,
    )
    .unwrap();
    let flushed = apply_decision(
        &first,
        &metadata,
        quiet,
        TimestampMillis(1010),
        Some(&completed(&first)),
    )
    .unwrap();
    assert_eq!(flushed.tasks[0].publication.pending_events, 0);
    assert!(flushed.tasks[0].publication.flush_deadline.is_none());
    assert!(flushed.outbox.as_ref().unwrap().effects.iter().any(|effect| matches!(&effect.kind,
        EffectKind::PublishSnapshot { snapshot } if snapshot.snapshot_event_id == first.last_event_id)));
}

#[test]
fn independent_queue_rejection_preserves_the_running_task_and_its_state() {
    use super::{lifecycle::*, transitions::*};
    let mut before = initial();
    before.runner_state = Some(TaskRunnerState {
        tid: task().tid,
        state: decision(vec![]).next_state,
    });
    let mut rejected = metadata(TaskStatus::Submitted);
    rejected.tid = Tid("s0001.session.overflow".into());
    before.tasks.push_back(TaskProgress {
        steps: 0,
        tid: rejected.tid.clone(),
        phase: TaskPhase::Queued,
        ..task()
    });
    let first = finalize_independently(
        &before,
        &rejected,
        TerminalStatus::Rejected,
        "QueueCapacityExceeded",
        TimestampMillis(10),
        None,
    )
    .unwrap();
    assert_eq!(first.runner_state, before.runner_state);
    assert_eq!(first.tasks[0].phase, TaskPhase::Running);
    assert!(matches!(
        first.tasks[1].phase,
        TaskPhase::AwaitingResult {
            status: TerminalStatus::Rejected
        }
    ));
    let mut proof = completed(&first);
    proof.saved.insert(
        rejected.tid.clone(),
        BlobRef {
            key: "archive".into(),
            digest: Digest("result".into()),
        },
    );
    let second = archive_confirmed(&first, &rejected, TimestampMillis(11), &proof).unwrap();
    assert_eq!(second.runner_state, before.runner_state);
    assert_eq!(second.tasks[1].phase, TaskPhase::Done);
    assert!(retire_published(&second, None).is_err());
    let third = retire_published(&second, Some(&completed(&second))).unwrap();
    assert_eq!(third.tasks.len(), 1);
    assert_eq!(third.runner_state, before.runner_state);
    assert!(!third.view_drafts.contains_key(&rejected.tid));
}

#[test]
fn nested_task_ids_do_not_steal_another_tasks_dispatch_obligations() {
    use super::{lifecycle::*, transitions::*};
    let mut before = initial();
    let child = Tid(format!("{}.child", task().tid.0));
    before.tasks[0].tid = child.clone();
    before.runner_state = Some(TaskRunnerState {
        tid: child.clone(),
        state: decision(vec![]).next_state,
    });
    let parent = metadata(TaskStatus::Submitted);
    before.tasks.push_back(TaskProgress {
        phase: TaskPhase::Queued,
        ..task()
    });
    let did = Did(format!("{}.dispatch", child.0));
    before.dispatch_seq.insert(
        did.clone(),
        DispatchSequence {
            tid: child.clone(),
            holder_epoch: HolderEpoch(1),
            sequence: InputSeq(0),
        },
    );
    let first = finalize_independently(
        &before,
        &parent,
        TerminalStatus::Canceled,
        "CanceledByUser",
        TimestampMillis(1),
        None,
    )
    .unwrap();
    assert!(
        first
            .outbox
            .as_ref()
            .unwrap()
            .effects
            .iter()
            .all(|e| !matches!(e.kind, EffectKind::RequestCancel { .. }))
    );
    let mut proof = completed(&first);
    proof.saved.insert(
        parent.tid.clone(),
        BlobRef {
            key: "parent-result".into(),
            digest: Digest("parent".into()),
        },
    );
    let second = archive_confirmed(&first, &parent, TimestampMillis(2), &proof).unwrap();
    let third = retire_published(&second, Some(&completed(&second))).unwrap();
    assert_eq!(third.dispatch_seq[&did].tid, child);
}

#[test]
fn task_deadlines_replace_runner_owned_entries_and_deduplicate_runtime_timeout() {
    use super::{lifecycle::*, transitions::*};
    let mut before = initial();
    let owned = Deadline {
        id: task_timeout_id(&task().tid),
        at: TimestampMillis(60000),
        kind: DeadlineKind::Task { tid: task().tid },
    };
    before.deadlines.push(owned.clone());
    let custom = Deadline {
        id: DeadlineId("runner-timeout".into()),
        at: TimestampMillis(10000),
        kind: DeadlineKind::Task { tid: task().tid },
    };
    let mut first_decision = check(decision(vec![])).unwrap();
    first_decision.deadlines = vec![owned.clone(), custom.clone()];
    let metadata = metadata(TaskStatus::Working);
    let first =
        apply_decision(&before, &metadata, first_decision, TimestampMillis(1), None).unwrap();
    assert_eq!(first.deadlines, vec![owned.clone(), custom]);
    let mut second_decision = check(decision(vec![])).unwrap();
    second_decision.deadlines = vec![owned.clone()];
    let second = apply_decision(
        &first,
        &metadata,
        second_decision,
        TimestampMillis(2),
        Some(&completed(&first)),
    )
    .unwrap();
    assert_eq!(second.deadlines, vec![owned]);
}

#[test]
fn quiet_snapshot_flush_preserves_custom_task_deadlines_and_private_state() {
    use super::transitions::*;
    let metadata = metadata(TaskStatus::Working);
    let custom = Deadline {
        id: DeadlineId("runner-deadline".into()),
        at: TimestampMillis(20000),
        kind: DeadlineKind::Task { tid: task().tid },
    };
    let mut decision = check(decision(vec![show("last delta".into())])).unwrap();
    decision.deadlines.push(custom.clone());
    let first = apply_decision(&initial(), &metadata, decision, TimestampMillis(10), None).unwrap();
    assert!(first.tasks[0].publication.flush_deadline.is_some());
    let Payload::Inline(value) = &first.view_drafts[&task().tid].snapshot else {
        panic!()
    };
    let display = serde_json::from_value(value.clone()).unwrap();
    let flushed = flush_snapshot(
        &first,
        &metadata,
        display,
        TimestampMillis(1010),
        Some(&completed(&first)),
    )
    .unwrap();
    assert_eq!(flushed.deadlines, vec![custom]);
    assert_eq!(flushed.runner_state, first.runner_state);
    assert!(flushed.tasks[0].publication.flush_deadline.is_none());
    assert_eq!(flushed.tasks[0].steps, first.tasks[0].steps);
}

#[test]
fn repeated_status_actions_are_rejected_before_building_unbounded_status_events() {
    let status = ActionIntent::SetStatus {
        tid: task().tid,
        status: TaskStatus::Working,
        interactions: vec![],
    };
    assert!(matches!(check(decision(vec![status.clone(), status])),
        Err(DecisionFailure::Invalid("multiple_status_actions"))));
}
