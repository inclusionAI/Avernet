#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "supervisor integration assertions"
)]
#[allow(
    unused_imports,
    reason = "shared fixture re-exports other test runners"
)]
mod processor_support;
mod supervisor_support;
mod support;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, dispatch::*};
use std::sync::atomic::Ordering;
use supervisor_support::*;

/// Fixture send proof: the durable proof written with a send intent. The
/// store persists and returns it; contents only matter to connector
/// takeover decisions, which construct their own via the rules.
fn proof(operation: &SendOperation) -> SendProof {
    SendProof {
        dedupe_domain: "fixture-domain".into(),
        guarantee_deadline: TimestampMillis(i64::MAX),
        operation: match operation {
            SendOperation::Start { request_id } => ProofOperation::Start { request_id: request_id.clone() },
            SendOperation::Input { seq, request_id } => ProofOperation::Input { seq: *seq, request_id: request_id.clone() },
        },
        body_digest: Digest("sha256:fixture-body".into()),
        config_digest: Digest("sha256:fixture-config".into()),
    }
}
#[tokio::test(start_paused = true)]
async fn creation_is_notified_and_retains_the_remote_obligation() {
    let h = Harness::new().await;
    let mut s = h.supervisor();
    h.drive(&mut s, 12).await;
    assert_eq!(
        h.board.notify_calls.lock().unwrap().as_slice(),
        std::slice::from_ref(&h.did)
    );
    assert!(h.inbox.requests.lock().unwrap().is_empty());
    assert_eq!(
        h.f.dispatch
            .get(&h.did)
            .await
            .unwrap()
            .unwrap()
            .view
            .execution
            .phase,
        DispatchPhase::Created
    );
}
#[tokio::test(start_paused = true)]
async fn terminal_return_replays_the_same_identity_after_unknown_admission() {
    let h = Harness::new().await;
    h.finish(FinalDispatchOutcome::Canceled).await;
    h.inbox.lost.store(true, Ordering::SeqCst);
    let mut s = h.supervisor();
    h.drive(&mut s, 20).await;
    let requests = h.inbox.requests.lock().unwrap().clone();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0], requests[1]);
    let InputBody::Agent { event } = &requests[0].input.body else {
        panic!("agent")
    };
    assert_eq!(event.kind, AgentEventKind::Final);
    assert_eq!(
        requests[0].input.input_id,
        bcs_dist_encoding::critical_agent_input_id(&h.did, event.remote_event_id.as_ref().unwrap())
    );
    assert!(
        h.f.dispatch
            .scan_due(
                ShardId(1),
                TimestampMillis(1000),
                PageRequest {
                    after: None,
                    limit: std::num::NonZeroU32::new(128).unwrap()
                }
            )
            .await
            .unwrap()
            .items
            .is_empty()
    );
}
#[tokio::test(start_paused = true)]
async fn unknown_holder_is_neither_taken_over_nor_notified() {
    let h = Harness::new().await;
    let held = h.held().await;
    let holder = held.holder().clone();
    h.members.unknown.store(true, Ordering::SeqCst);
    let mut s = h.supervisor();
    h.drive(&mut s, 16).await;
    assert!(h.board.notify_calls.lock().unwrap().is_empty());
    assert_eq!(
        h.f.dispatch
            .get(&h.did)
            .await
            .unwrap()
            .unwrap()
            .view
            .execution
            .holder,
        Some(holder)
    );
    assert!(h.inbox.requests.lock().unwrap().is_empty());
}
#[tokio::test(start_paused = true)]
async fn missing_terminal_evidence_is_not_invented_and_old_revision_is_reloaded() {
    let h = Harness::new().await;
    h.finish(FinalDispatchOutcome::Failed).await;
    h.board.fake_evidence.store(true, Ordering::SeqCst);
    let mut s = h.supervisor();
    h.drive(&mut s, 8).await;
    assert!(h.inbox.requests.lock().unwrap().is_empty());
    assert!(h.board.reschedule_calls.lock().unwrap().is_empty());
    h.board.fake_evidence.store(false, Ordering::SeqCst);
    h.board.old_revision.store(true, Ordering::SeqCst);
    h.drive(&mut s, 24).await;
    assert_eq!(h.inbox.requests.lock().unwrap().len(), 2);
    let requests = h.inbox.requests.lock().unwrap().clone();
    assert_eq!(requests[0].input.input_id, requests[1].input.input_id);
}
#[tokio::test(start_paused = true)]
async fn shared_budget_denial_keeps_pending_work_for_a_later_visit() {
    let h = Harness::new().await;
    h.finish(FinalDispatchOutcome::Completed).await;
    let mut s = h.supervisor();
    s.tick().await;
    h.budget.reject.store(true, Ordering::SeqCst);
    h.drive(&mut s, 10).await;
    assert_eq!(s.pending(), 1);
    assert!(h.inbox.requests.lock().unwrap().is_empty());
    h.budget.reject.store(false, Ordering::SeqCst);
    h.drive(&mut s, 20).await;
    assert_eq!(h.inbox.requests.lock().unwrap().len(), 1);
}

#[tokio::test(start_paused = true)]
async fn blocked_session_still_accepts_terminal_supervision_without_business_recovery() {
    use bcs_service_api::port::repo::distributed::control::*;
    let h = Harness::new().await;
    h.finish(FinalDispatchOutcome::Completed).await;
    h.block().await;
    let before = h.f.checkpoint().await;
    let mut s = h.supervisor();
    h.drive(&mut s, 16).await;
    assert_eq!(h.inbox.requests.lock().unwrap().len(), 1);
    assert_eq!(before, h.f.checkpoint().await);
    assert!(matches!(
        h.f.coordination
            .load_control(&processor_support::spec().sid)
            .await
            .unwrap()
            .unwrap()
            .control
            .mode,
        ControlMode::Blocked { .. }
    ));
}
#[tokio::test(start_paused = true)]
async fn completed_task_keeps_its_archive_when_late_dispatch_evidence_arrives() {
    use bcs_service_api::port::repo::distributed::result::ResultStorePort;
    let h = Harness::new().await;
    let owner = h.f.runtime.acquired_sessions().unwrap().remove(0).ownership;
    h.f.runtime.recovery_completed(&owner).await.unwrap();
    h.f.runtime.release_session(&owner).await.unwrap();
    h.f.warm().await;
    for _ in 0..80 {
        h.f.step().await.unwrap();
        if h.f.snapshot(&h.tid).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    let before =
        h.f.archive
            .get_final(&processor_support::spec().scope, &h.tid)
            .await
            .unwrap()
            .unwrap();
    h.finish(FinalDispatchOutcome::Failed).await;
    let mut s = h.supervisor();
    h.drive(&mut s, 16).await;
    assert_eq!(h.inbox.requests.lock().unwrap().len(), 1);
    for _ in 0..16 {
        h.f.step().await.unwrap();
    }
    assert_eq!(
        h.f.archive
            .get_final(&processor_support::spec().scope, &h.tid)
            .await
            .unwrap()
            .unwrap(),
        before
    );
    assert_eq!(
        h.f.snapshot(&h.tid).await.content.status,
        TaskStatus::Completed
    );
}
#[tokio::test(start_paused = true)]
async fn reconstructing_the_supervisor_after_unknown_return_keeps_the_same_business_input() {
    let h = Harness::new().await;
    h.finish(FinalDispatchOutcome::Completed).await;
    h.inbox.lost.store(true, Ordering::SeqCst);
    let mut first = h.supervisor();
    for _ in 0..12 {
        first.tick().await;
        if !h.inbox.requests.lock().unwrap().is_empty() {
            break;
        }
    }
    drop(first);
    let mut replacement = h.supervisor();
    h.drive(&mut replacement, 20).await;
    let requests = h.inbox.requests.lock().unwrap().clone();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0], requests[1]);
}

#[tokio::test(start_paused = true)]
async fn a_poisoned_record_cannot_permanently_occupy_the_only_pending_slot() {
    use bcs_dist_worker::supervisor::*;
    let h = Harness::new().await;
    h.finish(FinalDispatchOutcome::Completed).await;
    let healthy = h.another("healthy").await;
    h.board.fake_evidence.store(true, Ordering::SeqCst);
    let mut s = DispatchSupervisor::new(
        SupervisorPorts {
            dispatch: h.board.clone(),
            sessions: h.f.coordination.clone(),
            inbox: h.inbox.clone(),
            membership: h.members.clone(),
            scan_budget: h.budget.clone(),
            now: std::sync::Arc::new(|| TimestampMillis(1000)),
        },
        SupervisorConfig {
            shards: vec![ShardId(1)],
            max_pending: 1,
            max_pending_bytes: 24 * 1024,
            retry_backoff: std::time::Duration::from_millis(1),
            ..Default::default()
        },
    )
    .unwrap();
    h.drive(&mut s, 100).await;
    assert!(h.board.notify_calls.lock().unwrap().contains(&healthy));
    assert!(s.reserved_bytes() <= 24 * 1024);
    assert!(h.inbox.requests.lock().unwrap().is_empty());
}

#[tokio::test(start_paused = true)]
async fn sending_unknown_is_not_reissued_or_failed_when_the_deadline_has_passed() {
    use bcs_dist_worker::supervisor::*;
    let h = Harness::new().await;
    let mut held = h.held().await;
    let operation = SendOperation::Start {
        request_id: RequestId("send-unknown".into()),
    };
    held.begin_send(operation.clone(), proof(&operation))
    .await
    .unwrap();
    let mut s = DispatchSupervisor::new(
        SupervisorPorts {
            dispatch: h.board.clone(),
            sessions: h.f.coordination.clone(),
            inbox: h.inbox.clone(),
            membership: h.members.clone(),
            scan_budget: h.budget.clone(),
            now: std::sync::Arc::new(|| TimestampMillis(999999)),
        },
        SupervisorConfig {
            shards: vec![ShardId(1)],
            ..Default::default()
        },
    )
    .unwrap();
    h.drive(&mut s, 18).await;
    assert!(h.inbox.requests.lock().unwrap().is_empty());
    let record = h.f.dispatch.get(&h.did).await.unwrap().unwrap();
    assert_eq!(record.view.execution.phase, DispatchPhase::Sending);
    assert!(record.view.execution.outcome.is_none());
    assert_eq!(h.board.notify_calls.lock().unwrap().len(), 1);
}
#[tokio::test(start_paused = true)]
async fn slow_shard_cannot_prevent_other_shards_from_getting_a_turn() {
    use bcs_dist_worker::supervisor::*;
    let h = Harness::new().await;
    h.board.slow_scan.store(true, Ordering::SeqCst);
    let mut s = DispatchSupervisor::new(
        SupervisorPorts {
            dispatch: h.board.clone(),
            sessions: h.f.coordination.clone(),
            inbox: h.inbox.clone(),
            membership: h.members.clone(),
            scan_budget: h.budget.clone(),
            now: std::sync::Arc::new(|| TimestampMillis(1000)),
        },
        SupervisorConfig {
            shards: vec![ShardId(99), ShardId(1)],
            io_timeout: std::time::Duration::from_millis(10),
            ..Default::default()
        },
    )
    .unwrap();
    h.drive(&mut s, 16).await;
    let scans = h.board.scan_calls.lock().unwrap();
    assert_eq!(&scans[..2], &[ShardId(99), ShardId(1)]);
    assert_eq!(
        h.board.notify_calls.lock().unwrap().as_slice(),
        std::slice::from_ref(&h.did)
    );
}
#[tokio::test(start_paused = true)]
async fn canceling_a_tick_keeps_the_admission_identity_and_releases_shared_io() {
    use bcs_service_api::port::repo::distributed::budget::ScanBudgetPort;
    let h = Harness::new().await;
    h.finish(FinalDispatchOutcome::Completed).await;
    h.inbox.pause.store(true, Ordering::SeqCst);
    let mut s = h.supervisor();
    for _ in 0..4 {
        s.tick().await;
    }
    // The fourth processing phase is admission; cancellation occurs after its
    // durable write but before its response reaches the supervisor.
    let timed = tokio::time::timeout(std::time::Duration::from_millis(5), s.tick()).await;
    if timed.is_ok() {
        assert!(
            tokio::time::timeout(std::time::Duration::from_millis(5), s.tick())
                .await
                .is_err()
        );
    }
    assert_eq!(s.pending(), 1);
    let permits: Vec<_> = (0..8).map(|_| h.f.scan_budget.try_io().unwrap()).collect();
    drop(permits);
    h.drive(&mut s, 18).await;
    let requests = h.inbox.requests.lock().unwrap().clone();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0], requests[1]);
}
#[tokio::test(start_paused = true)]
async fn independent_budgets_are_rejected_at_construction() {
    use bcs_dist_worker::supervisor::*;
    let h = Harness::new().await;
    let distinct = std::sync::Arc::new(
        bcs_dist_store::budget::SharedScanBudget::new(Default::default()).unwrap(),
    );
    assert!(
        DispatchSupervisor::new(
            SupervisorPorts {
                dispatch: h.board.clone(),
                sessions: h.f.coordination.clone(),
                inbox: h.inbox.clone(),
                membership: h.members.clone(),
                scan_budget: distinct,
                now: std::sync::Arc::new(|| TimestampMillis(1000))
            },
            SupervisorConfig::default()
        )
        .is_err()
    );
}

#[tokio::test(start_paused = true)]
async fn repeated_revision_conflicts_yield_the_slot_to_other_durable_dispatches() {
    use bcs_dist_worker::supervisor::*;
    let h = Harness::new().await;
    h.finish(FinalDispatchOutcome::Completed).await;
    let healthy = h.another("healthy").await;
    h.board.always_old_revision.store(true, Ordering::SeqCst);
    let mut s = DispatchSupervisor::new(
        SupervisorPorts {
            dispatch: h.board.clone(),
            sessions: h.f.coordination.clone(),
            inbox: h.inbox.clone(),
            membership: h.members.clone(),
            scan_budget: h.budget.clone(),
            now: std::sync::Arc::new(|| TimestampMillis(1000)),
        },
        SupervisorConfig {
            shards: vec![ShardId(1)],
            max_pending: 1,
            max_pending_bytes: 24 * 1024,
            retry_backoff: std::time::Duration::from_millis(1),
            ..Default::default()
        },
    )
    .unwrap();
    h.drive(&mut s, 100).await;
    assert!(h.board.notify_calls.lock().unwrap().contains(&healthy));
}

#[tokio::test(start_paused = true)]
async fn accepted_dispatch_with_unknown_cancel_keeps_remote_supervision() {
    let h = Harness::new().await;
    let mut held = h.held().await;
    let operation = SendOperation::Start {
        request_id: RequestId("start".into()),
    };
    held.begin_send(operation.clone(), proof(&operation)).await.unwrap();
    held.record_acceptance(AcceptanceEvidence {
        operation,
        remote: RemoteTaskRef {
            task_id: "remote-task".into(),
            context_id: None,
        },
    })
    .await
    .unwrap();
    h.cancel().await;
    held.record_cancel(CancelObservation::OutcomeUnknown)
        .await
        .unwrap();
    let mut s = h.supervisor();
    h.drive(&mut s, 16).await;
    assert_eq!(h.board.notify_calls.lock().unwrap().len(), 1);
    assert!(h.inbox.requests.lock().unwrap().is_empty());
    let record = h.f.dispatch.get(&h.did).await.unwrap().unwrap();
    assert_eq!(record.view.execution.phase, DispatchPhase::Accepted);
    assert_eq!(
        record.view.execution.cancellation,
        CancelObservation::OutcomeUnknown
    );
    assert!(record.view.control.cancel.is_some());
    assert!(record.view.execution.outcome.is_none());
}
