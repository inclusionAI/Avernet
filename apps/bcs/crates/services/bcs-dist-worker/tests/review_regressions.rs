#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "runtime assertions"
)]
mod support;
use bcs_dist_model::*;
use bcs_dist_worker::WorkerConfig;
use bcs_service_api::{
    port::{dist_runtime::DistRuntimeControlPort, repo::distributed::ownership::*},
    types::dist_runtime::*,
};
use std::{sync::atomic::Ordering, time::Duration};
use support::*;

#[tokio::test(start_paused = true)]
async fn missing_specs_do_not_block_healthy_sessions_or_drain() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    world.claim("a").await;
    for n in 0..70 {
        world
            .data
            .pending
            .lock()
            .unwrap()
            .push(Sid(format!("s0042.bad{n:03}")));
    }
    let healthy = Sid("s0042.healthy".into());
    world.pending(&healthy).await;
    for _ in 0..8 {
        tokio::time::advance(Duration::from_secs(1)).await;
        let _ = a.tick().await;
        let _ = b.tick().await;
    }
    assert_eq!(a.acquired_sessions().unwrap().len(), 1);
    assert_eq!(a.snapshot().await.unwrap().active_work, 0);
    a.recovery_completed(&a.acquired_sessions().unwrap()[0].ownership)
        .await
        .unwrap();
    assert_eq!(
        a.drain(Duration::from_secs(5)).await.unwrap(),
        DrainOutcome::Drained
    );
}

#[tokio::test(start_paused = true)]
async fn definite_failures_release_capacity_but_prior_unknown_remains() {
    let world = World::new();
    let a = world.worker_with_capacity("a", 2).await;
    world.claim("a").await;
    world.pending(&sid()).await;
    world
        .sessions
        .failed_acquires
        .lock()
        .unwrap()
        .insert(sid(), false);
    for _ in 0..3 {
        tokio::time::advance(Duration::from_secs(1)).await;
        let _ = a.tick().await;
    }
    assert_eq!(a.snapshot().await.unwrap().active_work, 0);
    world
        .sessions
        .failed_acquires
        .lock()
        .unwrap()
        .insert(sid(), true);
    tokio::time::advance(Duration::from_secs(1)).await;
    let _ = a.tick().await;
    assert_eq!(a.snapshot().await.unwrap().active_work, 1);
    world
        .sessions
        .failed_acquires
        .lock()
        .unwrap()
        .insert(sid(), false);
    tokio::time::advance(Duration::from_secs(1)).await;
    let _ = a.tick().await;
    assert_eq!(a.snapshot().await.unwrap().active_work, 1);
    world.sessions.failed_acquires.lock().unwrap().clear();
    tokio::time::advance(Duration::from_secs(1)).await;
    a.tick().await.unwrap();
    assert_eq!(a.acquired_sessions().unwrap().len(), 1);
}

#[tokio::test(start_paused = true)]
async fn retry_backlog_cannot_spend_entire_fresh_recovery_budget() {
    let world = World::new();
    let a = world.worker("a").await;
    world.claim("a").await;
    for n in 0..70 {
        let sid = Sid(format!("s0042.bad{n:03}"));
        world.pending(&sid).await;
        world
            .sessions
            .failed_acquires
            .lock()
            .unwrap()
            .insert(sid, true);
    }
    world.pending(&Sid("s0042.healthy".into())).await;
    for _ in 0..12 {
        tokio::time::advance(Duration::from_secs(1)).await;
        let _ = a.tick().await;
    }
    assert_eq!(a.acquired_sessions().unwrap().len(), 1);
}

#[tokio::test(start_paused = true)]
async fn bulk_handoff_is_not_limited_to_one_request_timeout_per_step() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    world.claim("a").await;
    for n in 0..300 {
        world.pending(&Sid(format!("s0042.session{n:03}"))).await;
    }
    warm(&[a.clone(), b.clone()]).await;
    for _ in 0..12 {
        a.tick().await.unwrap();
    }
    let sessions = a.acquired_sessions().unwrap();
    assert_eq!(sessions.len(), 300);
    for session in sessions {
        a.recovery_completed(&session.ownership).await.unwrap();
    }
    *world.sessions.transfer_delay.lock().unwrap() = Duration::from_millis(20);
    let started = tokio::time::Instant::now();
    assert_eq!(
        a.drain(Duration::from_secs(10)).await.unwrap(),
        DrainOutcome::Drained
    );
    assert!(started.elapsed() < Duration::from_secs(9));
    assert!(world.data.watched.lock().unwrap().is_empty());
    assert_eq!(world.data.open_feeds.load(Ordering::SeqCst), 0);
}

#[test]
fn heartbeat_round_trip_requires_headroom_and_checked_arithmetic() {
    let mut config = WorkerConfig {
        heartbeat_interval: Duration::from_secs(2),
        request_timeout: Duration::from_secs(1),
        lease_ttl: Duration::from_secs(3),
        ..WorkerConfig::default()
    };
    assert!(config.validate().is_err());
    config.heartbeat_interval = Duration::MAX;
    config.lease_ttl = Duration::MAX;
    assert!(config.validate().is_err());
}

#[tokio::test(start_paused = true)]
async fn member_pages_advance_past_orphans_without_removing_them() {
    use bcs_dist_store::layout::KeyLayout;
    use bcs_store_api::zset::{ZAddMode, ZSetEntry, ZSetStorePlugin};
    let world = World::new();
    let key = KeyLayout::new("test", "runtime")
        .unwrap()
        .members("worker")
        .unwrap();
    for n in 0..8 {
        world
            .model
            .add(
                &key,
                &[ZSetEntry {
                    member: serde_json::to_string(&instance(&format!("a{n}"))).unwrap(),
                    score: 0.0,
                }],
                ZAddMode::InsertOnly,
            )
            .await
            .unwrap();
    }
    let a = world
        .worker_with_config(
            "z",
            WorkerConfig {
                shards: vec![ShardId(42)],
                page_size: 2,
                max_member_pages: 1,
                ..WorkerConfig::default()
            },
        )
        .await;
    for _ in 0..12 {
        tokio::time::advance(Duration::from_secs(1)).await;
        a.tick().await.unwrap();
    }
    assert_eq!(
        world
            .store
            .current(ShardId(42))
            .await
            .unwrap()
            .unwrap()
            .claim
            .owner
            .instance,
        *a.instance()
    );
}

#[tokio::test(start_paused = true)]
async fn deferred_retry_respects_hint_but_source_done_wakes_it() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    let claim = world.claim("a").await;
    world.pending(&sid()).await;
    warm(&[a, b.clone()]).await;
    let request = HandoffRequest {
        request_id: RequestId("deferred".into()),
        from: claim,
        target: b.instance().clone(),
        grace: Duration::from_secs(60),
    };
    let HandoffOutcome::Accepted(record) = b.accept_handoff(request.clone()).await.unwrap() else {
        panic!()
    };
    b.tick().await.unwrap();
    let before = world.sessions.acquire_calls.lock().unwrap().len();
    b.tick().await.unwrap();
    assert_eq!(world.sessions.acquire_calls.lock().unwrap().len(), before);
    assert!(
        b.handoff_done(world.instance("a"), record.claim, request.request_id)
            .await
            .unwrap()
    );
    b.tick().await.unwrap();
    assert_eq!(b.acquired_sessions().unwrap().len(), 1);
}

#[tokio::test(start_paused = true)]
async fn definite_rejection_prefers_untried_target_even_after_cooldown_expires() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    let c = world.worker("c").await;
    world.claim("a").await;
    warm(&[a.clone(), b, c]).await;
    let ranked = bcs_dist_worker::rank_candidates(
        ShardId(42),
        &[world.instance("b"), world.instance("c")],
        bcs_dist_worker::HrwIdentity::Node,
    );
    world
        .peers
        .rejected_nodes
        .lock()
        .unwrap()
        .push(ranked[0].node.clone());
    assert_eq!(
        a.drain(Duration::from_secs(5)).await.unwrap(),
        DrainOutcome::Drained
    );
    let requests = world.peers.requests.lock().unwrap();
    assert_eq!(requests.len(), 2);
    assert_ne!(requests[0].target, requests[1].target);
}

#[tokio::test(start_paused = true)]
async fn unknown_request_keeps_identity_and_target_even_after_read_only_rejection() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    let c = world.worker("c").await;
    world.claim("a").await;
    warm(&[a.clone(), b, c]).await;
    let ranked = bcs_dist_worker::rank_candidates(
        ShardId(42),
        &[world.instance("b"), world.instance("c")],
        bcs_dist_worker::HrwIdentity::Node,
    );
    world
        .peers
        .unknown_nodes
        .lock()
        .unwrap()
        .push(ranked[0].node.clone());
    let peers = world.peers.clone();
    let first = ranked[0].node.clone();
    let update = tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(100)).await;
        peers.unknown_nodes.lock().unwrap().clear();
        peers.rejected_nodes.lock().unwrap().push(first);
    });
    assert!(matches!(
        a.drain(Duration::from_secs(3)).await.unwrap(),
        DrainOutcome::DeadlineReached { .. }
    ));
    update.await.unwrap();
    let requests = world.peers.requests.lock().unwrap();
    assert!(requests.len() > 1);
    assert!(requests.iter().all(|r| r == &requests[0]));
}

#[tokio::test(start_paused = true)]
async fn transferred_session_retains_cleanup_until_unwatch_succeeds() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    world.claim("a").await;
    world.pending(&sid()).await;
    warm(&[a.clone(), b]).await;
    a.recovery_completed(&a.acquired_sessions().unwrap()[0].ownership)
        .await
        .unwrap();
    world.data.unwatch_unavailable.store(true, Ordering::SeqCst);
    let data = world.data.clone();
    let release = tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(100)).await;
        assert!(!data.watched.lock().unwrap().is_empty());
        data.unwatch_unavailable.store(false, Ordering::SeqCst);
    });
    assert_eq!(
        a.drain(Duration::from_secs(5)).await.unwrap(),
        DrainOutcome::Drained
    );
    release.await.unwrap();
    assert!(world.data.watched.lock().unwrap().is_empty());
    assert_eq!(world.data.open_feeds.load(Ordering::SeqCst), 0);
    assert_eq!(world.sessions.released.lock().unwrap().len(), 1);
}

#[tokio::test(start_paused = true)]
async fn unknown_acquisition_reuses_frozen_initial_state_without_reading_spec_again() {
    let world = World::new();
    let a = world.worker("a").await;
    world.claim("a").await;
    world.pending(&sid()).await;
    world
        .sessions
        .failed_acquires
        .lock()
        .unwrap()
        .insert(sid(), true);
    for _ in 0..3 {
        tokio::time::advance(Duration::from_secs(1)).await;
        let _ = a.tick().await;
    }
    assert_eq!(a.snapshot().await.unwrap().active_work, 1);
    world.sessions.failed_acquires.lock().unwrap().clear();
    world
        .sessions
        .spec_unavailable
        .store(true, Ordering::SeqCst);
    tokio::time::advance(Duration::from_secs(1)).await;
    a.tick().await.unwrap();
    assert_eq!(a.acquired_sessions().unwrap().len(), 1);
}

#[tokio::test(start_paused = true)]
async fn incompatible_store_capacity_is_rejected_before_registration() {
    use std::sync::Arc;
    let world = World::new();
    let result = bcs_dist_worker::WorkerRuntime::start(
        "oversized".into(),
        "http://worker.example.test".into(),
        WorkerConfig {
            max_active_sessions: 10_001,
            ..WorkerConfig::default()
        },
        world.store.clone(),
        world.store.clone(),
        world.sessions.clone(),
        Arc::new(Inbox(world.data.clone())),
        world.scheduling.clone(),
        world.peers.clone(),
        Arc::new(|_| Ok(initial())),
    )
    .await;
    assert!(matches!(result, Err(e) if e.code == "session_store_capacity_mismatch"));
}

#[tokio::test(start_paused = true)]
async fn initializer_failure_is_not_a_pending_write() {
    use std::sync::Arc;
    let world = World::new();
    let a = bcs_dist_worker::WorkerRuntime::start(
        "init".into(),
        "http://worker.example.test".into(),
        WorkerConfig {
            shards: vec![ShardId(42)],
            ..WorkerConfig::default()
        },
        world.store.clone(),
        world.store.clone(),
        world.sessions.clone(),
        Arc::new(Inbox(world.data.clone())),
        world.scheduling.clone(),
        world.peers.clone(),
        Arc::new(|_| {
            Err(RuntimeError {
                code: "initializer_rejected".into(),
            })
        }),
    )
    .await
    .unwrap();
    world
        .peers
        .workers
        .lock()
        .unwrap()
        .insert("init".into(), a.clone());
    world.claim("init").await;
    world.pending(&sid()).await;
    for _ in 0..3 {
        tokio::time::advance(Duration::from_secs(1)).await;
        let _ = a.tick().await;
    }
    assert_eq!(a.snapshot().await.unwrap().active_work, 0);
    assert!(world.sessions.acquire_calls.lock().unwrap().is_empty());
}

#[tokio::test(start_paused = true)]
async fn single_acquire_budget_alternates_retries_and_new_work() {
    let world = World::new();
    let a = world
        .worker_with_config(
            "a",
            WorkerConfig {
                shards: vec![ShardId(42)],
                max_acquires_per_tick: 1,
                ..WorkerConfig::default()
            },
        )
        .await;
    world.claim("a").await;
    let bad = Sid("s0042.bad".into());
    world.pending(&bad).await;
    world
        .sessions
        .failed_acquires
        .lock()
        .unwrap()
        .insert(bad.clone(), true);
    world.pending(&sid()).await;
    for _ in 0..8 {
        tokio::time::advance(Duration::from_secs(1)).await;
        let _ = a.tick().await;
    }
    assert_eq!(a.acquired_sessions().unwrap().len(), 1);
    assert!(
        world
            .sessions
            .acquire_calls
            .lock()
            .unwrap()
            .iter()
            .filter(|sid| **sid == bad)
            .count()
            > 1
    );
}

#[tokio::test(start_paused = true)]
async fn accepted_handoff_prioritizes_recovery_before_a_full_shard_rotation() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world
        .worker_with_config(
            "b",
            WorkerConfig {
                shards: (0..100).map(ShardId).collect(),
                max_shards_per_tick: 1,
                ..WorkerConfig::default()
            },
        )
        .await;
    let claim = world.claim("a").await;
    world.pending(&sid()).await;
    warm(&[a, b.clone()]).await;
    let request = HandoffRequest {
        request_id: RequestId("priority".into()),
        from: claim,
        target: b.instance().clone(),
        grace: Duration::from_secs(60),
    };
    let HandoffOutcome::Accepted(record) = b.accept_handoff(request.clone()).await.unwrap() else {
        panic!()
    };
    assert!(
        b.handoff_done(world.instance("a"), record.claim, request.request_id)
            .await
            .unwrap()
    );
    b.tick().await.unwrap();
    b.tick().await.unwrap();
    assert!(
        b.acquired_sessions()
            .unwrap()
            .iter()
            .any(|s| s.ownership.sid == sid())
    );
}

#[tokio::test(start_paused = true)]
async fn recovery_rotates_shards_when_operation_budget_is_smaller_than_shard_count() {
    let world = World::new();
    let a = world
        .worker_with_config(
            "a",
            WorkerConfig {
                shards: vec![ShardId(41), ShardId(42)],
                max_acquires_per_tick: 1,
                ..WorkerConfig::default()
            },
        )
        .await;
    world.claim("a").await;
    world
        .store
        .claim(ClaimShard {
            shard: ShardId(41),
            me: a.instance().clone(),
            expected_epoch: None,
            request_id: RequestId("first41".into()),
        })
        .await
        .unwrap();
    for n in 0..20 {
        let bad = Sid(format!("s0041.bad{n:03}"));
        world.pending(&bad).await;
        world
            .sessions
            .failed_acquires
            .lock()
            .unwrap()
            .insert(bad, true);
    }
    world.pending(&sid()).await;
    for _ in 0..10 {
        let before = world.sessions.acquire_calls.lock().unwrap().len();
        tokio::time::advance(Duration::from_secs(1)).await;
        let _ = a.tick().await;
        assert!(world.sessions.acquire_calls.lock().unwrap().len() <= before + 1);
    }
    assert!(
        a.acquired_sessions()
            .unwrap()
            .iter()
            .any(|s| s.ownership.sid == sid())
    );
}

#[tokio::test(start_paused = true)]
async fn foreign_shard_with_more_than_one_cleanup_batch_is_fully_released() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    let claim = world.claim("a").await;
    for n in 0..70 {
        world.pending(&Sid(format!("s0042.cleanup{n:03}"))).await;
    }
    warm(&[a.clone(), b.clone()]).await;
    for _ in 0..3 {
        a.tick().await.unwrap();
    }
    for session in a.acquired_sessions().unwrap() {
        a.recovery_completed(&session.ownership).await.unwrap();
    }
    let request = HandoffRequest {
        request_id: RequestId("externally-driven".into()),
        from: claim,
        target: b.instance().clone(),
        grace: Duration::from_secs(10),
    };
    assert!(matches!(
        b.accept_handoff(request).await.unwrap(),
        HandoffOutcome::Accepted(_)
    ));
    a.tick().await.unwrap();
    assert_eq!(
        a.drain(Duration::from_secs(5)).await.unwrap(),
        DrainOutcome::Drained
    );
    assert_eq!(world.sessions.released.lock().unwrap().len(), 70);
    assert_eq!(world.data.open_feeds.load(Ordering::SeqCst), 0);
    assert!(world.data.watched.lock().unwrap().is_empty());
}

#[tokio::test(start_paused = true)]
async fn repeated_priority_hints_do_not_reset_the_normal_recovery_cursor() {
    let world = World::new();
    let a = world
        .worker_with_config(
            "a",
            WorkerConfig {
                shards: vec![ShardId(41), ShardId(42), ShardId(43)],
                max_acquires_per_tick: 1,
                ..WorkerConfig::default()
            },
        )
        .await;
    for shard in [41, 42, 43] {
        world
            .store
            .claim(ClaimShard {
                shard: ShardId(shard),
                me: a.instance().clone(),
                expected_epoch: None,
                request_id: RequestId(format!("claim{shard}")),
            })
            .await
            .unwrap();
    }
    for shard in [41, 42] {
        for n in 0..20 {
            let sid = Sid(format!("s{shard:04}.busy{n:03}"));
            world.pending(&sid).await;
            world
                .sessions
                .failed_acquires
                .lock()
                .unwrap()
                .insert(sid, true);
        }
    }
    let healthy = Sid("s0043.healthy".into());
    world.pending(&healthy).await;
    for _ in 0..18 {
        a.wake(ShardId(41)).await.unwrap();
        tokio::time::advance(Duration::from_secs(1)).await;
        let _ = a.tick().await;
    }
    assert!(
        a.acquired_sessions()
            .unwrap()
            .iter()
            .any(|s| s.ownership.sid == healthy)
    );
}
