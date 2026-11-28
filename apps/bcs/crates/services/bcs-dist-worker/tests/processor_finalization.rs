#![allow(
    clippy::unwrap_used,
    reason = "committed finalization integration assertions"
)]
#[path = "lifecycle_support/dispatch.rs"]
mod board;
mod lifecycle_support;
mod processor_support;
mod support;
use bcs_dist_model::*;
use bcs_dist_worker::supervisor::*;
use bcs_service_api::port::repo::distributed::{
    common::*,
    control::{ControlMode, SessionControlPort},
    dispatch::*,
    ownership::*,
    result::*,
    session::EffectKind,
};
use lifecycle_support::*;
use processor_support::Fixture;
use std::{
    num::NonZeroU32,
    sync::{Arc, atomic::Ordering},
    time::Duration,
};

#[tokio::test(start_paused = true)]
async fn partial_committed_cancel_batch_survives_takeover_without_rerunning_or_changing_intents() {
    let runner = Runner::new(Mode::Parallel);
    let f = Fixture::new(runner.clone()).await;
    let board = board::Board::new(&f);
    let p = processor_with_dispatch(
        &f,
        runner.clone(),
        f.publisher.clone(),
        f.archive.clone(),
        board.clone(),
    );
    let tid = f.admit_for(session(), "partial").await;
    f.warm().await;
    until_status(&f, &p, &tid, TaskStatus::Working).await;
    drive(&f, &p, 10).await;
    board.cancel_fault.store(1, Ordering::SeqCst);
    send(&f, &tid, "cancel", InputBody::Cancel { reason: None }).await;
    for _ in 0..160 {
        f.runtime.tick().await.unwrap();
        let _ = p.tick().await;
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    let requests = board.requests.lock().unwrap().clone();
    assert!(
        !requests.is_empty(),
        "checkpoint {:?}",
        f.checkpoint().await
    );
    assert!(requests.iter().all(|r| r == &requests[0]));
    let first = requests[0].clone();
    assert_eq!(
        f.dispatch
            .get(&first.did)
            .await
            .unwrap()
            .unwrap()
            .view
            .control
            .cancel,
        Some(first.intent.clone())
    );
    let second = [dispatch_id(&tid), Did(format!("{}.second", tid.0))]
        .into_iter()
        .find(|d| d != &first.did)
        .unwrap();
    assert!(
        f.dispatch
            .get(&second)
            .await
            .unwrap()
            .unwrap()
            .view
            .control
            .cancel
            .is_none()
    );
    assert!(
        f.archive
            .get_final(&session().scope, &tid)
            .await
            .unwrap()
            .is_none()
    );
    let frozen = f.checkpoint().await;
    assert_eq!(
        frozen
            .outbox
            .as_ref()
            .unwrap()
            .effects
            .iter()
            .filter(|e| matches!(e.kind, EffectKind::RequestCancel { .. }))
            .count(),
        2
    );
    f.model.advance(Duration::from_secs(10));
    let (runtime, replacement) = f.replacement(runner.clone()).await;
    for _ in 0..4 {
        tokio::time::sleep(Duration::from_secs(1)).await;
        runtime.tick().await.unwrap();
    }
    for _ in 0..100 {
        runtime.tick().await.unwrap();
        replacement.tick().await.unwrap();
        if f.snapshot(&tid).await.content.status == TaskStatus::Canceled {
            break;
        }
    }
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Canceled);
    assert_eq!(runner.calls(), 1);
    for effect in frozen.outbox.unwrap().effects {
        if let EffectKind::RequestCancel { did, intent } = effect.kind {
            assert_eq!(
                f.dispatch
                    .get(&did)
                    .await
                    .unwrap()
                    .unwrap()
                    .view
                    .control
                    .cancel,
                Some(intent)
            );
        }
    }
    assert!(
        f.archive
            .get_final(&session().scope, &tid)
            .await
            .unwrap()
            .is_some()
    );
}

#[tokio::test(start_paused = true)]
async fn conflicting_cancel_effect_blocks_instead_of_publishing_a_false_terminal() {
    let runner = Runner::new(Mode::Wait);
    let f = Fixture::new(runner.clone()).await;
    let board = board::Board::new(&f);
    let p = processor_with_dispatch(
        &f,
        runner.clone(),
        f.publisher.clone(),
        f.archive.clone(),
        board.clone(),
    );
    let tid = f.admit_for(session(), "conflict").await;
    f.warm().await;
    until_status(&f, &p, &tid, TaskStatus::Working).await;
    board.conflict.store(true, Ordering::SeqCst);
    send(&f, &tid, "cancel", InputBody::Cancel { reason: None }).await;
    for _ in 0..60 {
        f.runtime.tick().await.unwrap();
        let _ = p.tick().await;
    }
    assert!(matches!(
        f.coordination
            .load_control(&session().sid)
            .await
            .unwrap()
            .unwrap()
            .control
            .mode,
        ControlMode::Blocked { .. }
    ));
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Working);
    assert!(
        f.archive
            .get_final(&session().scope, &tid)
            .await
            .unwrap()
            .is_none()
    );
    assert_eq!(runner.calls(), 1);
    assert!(!board.requests.lock().unwrap().is_empty());
}

#[tokio::test(start_paused = true)]
async fn local_terminal_keeps_sending_or_cancel_unknown_supervised_until_definitive_late_evidence()
{
    for accepted in [false, true] {
        let runner = Runner::new(Mode::Wait);
        let f = Fixture::new(runner.clone()).await;
        let tid = f.admit_for(session(), "unknown").await;
        f.warm().await;
        until_status(&f, &f.processor, &tid, TaskStatus::Working).await;
        drive(&f, &f.processor, 10).await;
        let did = dispatch_id(&tid);
        let connector = support::instance("lifecycle-connector");
        f.coordination
            .register(
                connector.clone(),
                RuntimeRole::Connector,
                "http://connector.example.test".into(),
                Duration::from_secs(60),
            )
            .await
            .unwrap();
        let ClaimOutcome::Acquired(mut held) = f
            .queue
            .claim(DispatchClaimRequest {
                did: did.clone(),
                me: connector,
                request_id: RequestId("claim".into()),
                partition: None,
            })
            .await
            .unwrap()
        else {
            panic!()
        };
        let operation = SendOperation::Start {
            request_id: RequestId("send".into()),
        };
        held.begin_send(operation.clone()).await.unwrap();
        if accepted {
            held.record_acceptance(AcceptanceEvidence {
                operation,
                remote: RemoteTaskRef {
                    task_id: "remote-task".into(),
                    context_id: None,
                },
            })
            .await
            .unwrap();
        }
        send(&f, &tid, "cancel", InputBody::Cancel { reason: None }).await;
        until_status(&f, &f.processor, &tid, TaskStatus::Canceled).await;
        drive(&f, &f.processor, 20).await;
        if accepted {
            held.record_cancel(CancelObservation::OutcomeUnknown)
                .await
                .unwrap();
        }
        let final_record = f
            .archive
            .get_final(&session().scope, &tid)
            .await
            .unwrap()
            .unwrap();
        let consumed = f.checkpoint().await.consumed;
        let board = board::Board::new(&f);
        let mut supervisor = DispatchSupervisor::new(
            SupervisorPorts {
                dispatch: board.clone(),
                sessions: f.coordination.clone(),
                inbox: f.inbox.clone(),
                membership: f.coordination.clone(),
                scan_budget: f.scan_budget.clone(),
                now: Arc::new(|| TimestampMillis(999_999)),
            },
            SupervisorConfig {
                shards: vec![ShardId(1)],
                ..Default::default()
            },
        )
        .unwrap();
        for _ in 0..20 {
            supervisor.tick().await;
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
        assert!(
            board.reschedules.lock().unwrap().contains(&did),
            "must execute an actual durable supervisor cycle"
        );
        let unresolved = f.dispatch.get(&did).await.unwrap().unwrap();
        assert_eq!(
            unresolved.view.execution.phase,
            if accepted {
                DispatchPhase::Accepted
            } else {
                DispatchPhase::Sending
            }
        );
        assert!(unresolved.view.execution.outcome.is_none());
        assert!(unresolved.terminal_evidence.is_none());
        if accepted {
            assert_eq!(
                unresolved.view.execution.cancellation,
                CancelObservation::OutcomeUnknown
            );
        }
        assert!(
            f.dispatch
                .scan_due(
                    ShardId(1),
                    TimestampMillis(i64::MAX),
                    PageRequest {
                        after: None,
                        limit: NonZeroU32::new(32).unwrap()
                    }
                )
                .await
                .unwrap()
                .items
                .contains(&did)
        );
        drive(&f, &f.processor, 10).await;
        assert_eq!(
            f.checkpoint().await.consumed,
            consumed,
            "Unknown must not invent terminal inbox evidence"
        );
        held.finish(
            FinalDispatchOutcome::Failed,
            "definite-remote-terminal".into(),
        )
        .await
        .unwrap();
        let mut successor = DispatchSupervisor::new(
            SupervisorPorts {
                dispatch: board.clone(),
                sessions: f.coordination.clone(),
                inbox: f.inbox.clone(),
                membership: f.coordination.clone(),
                scan_budget: f.scan_budget.clone(),
                now: Arc::new(|| TimestampMillis(999_999)),
            },
            SupervisorConfig {
                shards: vec![ShardId(1)],
                ..Default::default()
            },
        )
        .unwrap();
        for _ in 0..20 {
            successor.tick().await;
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
        drive(&f, &f.processor, 100).await;
        assert_ne!(f.checkpoint().await.consumed, consumed);
        assert_eq!(runner.calls(), 1);
        assert_eq!(
            f.archive.get_final(&session().scope, &tid).await.unwrap(),
            Some(final_record)
        );
        assert!(
            f.dispatch
                .scan_due(
                    ShardId(1),
                    TimestampMillis(i64::MAX),
                    PageRequest {
                        after: None,
                        limit: NonZeroU32::new(32).unwrap()
                    }
                )
                .await
                .unwrap()
                .items
                .is_empty()
        );
    }
}
