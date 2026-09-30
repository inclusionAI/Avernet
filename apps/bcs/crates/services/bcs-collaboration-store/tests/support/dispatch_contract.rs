use super::*;
use bcs_service_api::{StateMachineDispatchPayload, StateMachineDispatchTarget, StateMachineDispatchStatus as Status,
    StateMachineDispatchResult as ResultFact, FailStateMachineNodeAttempt, StateMachineFailureAction};

pub(super) async fn dispatch_contract(store: &dyn StateMachineRunRepoPort) {
    let mut run = test_run(); run.run_id = "dispatch-contract".into(); run.session_id = "dispatch-session".into();
    run.status = StateMachineRunStatus::Running;
    let ids = ["accepted", "unsent", "unknown", "rejected", "deadline", "retry", "cancel", "early_event", "ack_race"];
    let nodes = ids.into_iter().map(|id| {
        let mut node = test_node(); node.run_id = run.run_id.clone(); node.node_id = id.into(); node.status = StateMachineNodeStatus::Pending;
        node.attempt = 0; node.assignee_bot_id = Some("bot".into()); node.node_timeout_ms = Some(1000); node.max_attempts = 3; node
    }).collect();
    store.create_run(run.clone(), nodes).await.unwrap();
    for id in ids {
        let delivery = format!("smnode-{}-{id}-0", run.run_id);
        assert!(store.mark_node_running_if_run_active(&run.run_id, id, 0, delivery.clone(), 100).await.unwrap());
        let payload = StateMachineDispatchPayload { run_id: run.run_id.clone(), node_id: id.into(), attempt: 0,
            group_id: run.group_id.clone(), session_id: run.session_id.clone(), assignee_bot_id: "bot".into(), delivery_request_id: delivery.clone(),
            target: StateMachineDispatchTarget::WebSocket, request: json!({"id": delivery, "method": "chat.send", "prompt": "原请求"}),
            started_at_ms: 100, deadline_ms: 1100 };
        let mut wrong = payload.clone(); wrong.session_id = "other".into();
        assert!(!store.save_node_dispatch(wrong).await.unwrap());
        assert!(store.save_node_dispatch(payload.clone()).await.unwrap());
        let mut changed = payload.clone(); changed.request["prompt"] = json!("changed");
        assert!(store.save_node_dispatch(changed).await.is_err());
        assert_eq!(store.get_node_dispatch(&run.run_id, id, 0).await.unwrap().unwrap().payload, payload);
    }
    let r = run.run_id.as_str();
    let expiry = |id: &str, at| FailStateMachineNodeAttempt { run_id: r.into(), node_id: id.into(), attempt: 0,
        error: "original dispatch deadline".into(), completed_at_ms: at, action: StateMachineFailureAction::Retry };
    assert!(!store.expire_node_dispatch(expiry("missing", 1100)).await.unwrap());
    let (a, b) = tokio::join!(store.claim_node_dispatch(r, "accepted", 0, "a".into(), 110, 150),
        store.claim_node_dispatch(r, "accepted", 0, "b".into(), 110, 150));
    let owners = [a.unwrap(), b.unwrap()].into_iter().flatten().collect::<Vec<_>>();
    assert_eq!(owners.len(), 1);
    let old = owners[0].clone();
    let new = store.claim_node_dispatch(r, "accepted", 0, "new".into(), 150, 200).await.unwrap().unwrap();
    assert!(new.token > old.token);
    assert!(!store.begin_node_dispatch_send(&old, 151).await.unwrap());
    assert!(!store.finish_node_dispatch(&old, ResultFact::Accepted, 151).await.unwrap());
    assert!(!store.release_node_dispatch(&old).await.unwrap());
    assert!(store.begin_node_dispatch_send(&new, 151).await.unwrap());
    assert!(!store.begin_node_dispatch_send(&new, 152).await.unwrap());
    assert!(store.finish_node_dispatch(&new, ResultFact::Accepted, 153).await.unwrap());
    assert!(!store.finish_node_dispatch(&new, ResultFact::Rejected { error: "stale".into() }, 154).await.unwrap());
    let saved = store.get_node_dispatch(r, "accepted", 0).await.unwrap().unwrap();
    assert_eq!(saved.status, Status::Delivered); assert_eq!(saved.delivered_at_ms, Some(153)); assert!(saved.lease_owner.is_none());
    assert!(store.claim_node_dispatch(r, "accepted", 0, "again".into(), 201, 250).await.unwrap().is_none());
    // A scanner's stale Delivering read cannot expire an already accepted request.
    assert!(!store.expire_node_dispatch(expiry("accepted", 1100)).await.unwrap());
    // Non-Judge results must still complete from the new waiting_provider phase.
    assert!(store.complete_node_attempt(r, "accepted", 0, "done".into(), "result".into(), None, 154).await.unwrap());

    let unsent = store.claim_node_dispatch(r, "unsent", 0, "owner".into(), 110, 150).await.unwrap().unwrap();
    assert!(store.release_node_dispatch(&unsent).await.unwrap());
    assert!(store.claim_node_dispatch(r, "unsent", 0, "next".into(), 111, 151).await.unwrap().is_some());
    let unknown = store.claim_node_dispatch(r, "unknown", 0, "owner".into(), 110, 150).await.unwrap().unwrap();
    assert!(store.begin_node_dispatch_send(&unknown, 111).await.unwrap());
    assert!(!store.finish_node_dispatch(&unknown, ResultFact::Accepted, 150).await.unwrap());
    assert!(store.claim_node_dispatch(r, "unknown", 0, "takeover".into(), 151, 250).await.unwrap().is_none());
    assert!(store.release_node_dispatch(&unknown).await.unwrap());
    assert!(store.claim_node_dispatch(r, "unknown", 0, "takeover".into(), 152, 250).await.unwrap().is_none());
    assert!(!store.expire_node_dispatch(expiry("unknown", 1099)).await.unwrap());
    assert!(store.expire_node_dispatch(expiry("unknown", 1100)).await.unwrap());
    assert!(!store.expire_node_dispatch(expiry("unknown", 1101)).await.unwrap());
    let expired = store.get_node_run(r, "unknown").await.unwrap().unwrap();
    assert_eq!(expired.status, StateMachineNodeStatus::Failed);
    assert_eq!(expired.completed_at, Some(1100)); assert_eq!(expired.error.as_deref(), Some("original dispatch deadline"));
    assert_eq!(store.get_node_attempt_failure(r, "unknown", 0).await.unwrap().unwrap().action, Some(StateMachineFailureAction::Retry));
    assert!(store.schedule_node_retry(r, "unknown", 0, 1).await.unwrap());

    let rejected = store.claim_node_dispatch(r, "rejected", 0, "owner".into(), 110, 150).await.unwrap().unwrap();
    assert!(store.finish_node_dispatch(&rejected, ResultFact::Rejected { error: "original rejection".into() }, 111).await.unwrap());
    let rejected = store.get_node_dispatch(r, "rejected", 0).await.unwrap().unwrap();
    assert_eq!(rejected.status, Status::Failed); assert_eq!(rejected.error.as_deref(), Some("original rejection"));
    assert!(store.claim_node_dispatch(r, "deadline", 0, "late".into(), 1100, 1200).await.unwrap().is_none());
    assert!(store.expire_node_dispatch(expiry("deadline", 1100)).await.unwrap());
    assert!(!store.expire_node_dispatch(expiry("rejected", 1100)).await.unwrap());

    let stale = store.claim_node_dispatch(r, "retry", 0, "old".into(), 110, 150).await.unwrap().unwrap();
    assert!(store.fail_node_attempt_with_action(FailStateMachineNodeAttempt { run_id: r.into(), node_id: "retry".into(), attempt: 0,
        error: "timeout".into(), completed_at_ms: 111, action: StateMachineFailureAction::Retry }).await.unwrap());
    assert!(store.schedule_node_retry(r, "retry", 0, 1).await.unwrap());
    assert!(!store.begin_node_dispatch_send(&stale, 112).await.unwrap());
    assert!(!store.finish_node_dispatch(&stale, ResultFact::Accepted, 112).await.unwrap());
    store.supersede_inactive_node_dispatches(r).await.unwrap();
    assert_eq!(store.get_node_dispatch(r, "retry", 0).await.unwrap().unwrap().status, Status::Superseded);

    let early = store.claim_node_dispatch(r, "early_event", 0, "owner".into(), 110, 150).await.unwrap().unwrap();
    assert!(store.begin_node_dispatch_send(&early, 111).await.unwrap());
    assert!(store.begin_node_judging(r, "early_event", 0, "early artifact".into(), None).await.unwrap());
    assert!(!store.finish_node_dispatch(&early, ResultFact::Accepted, 112).await.unwrap());
    assert!(!store.expire_node_dispatch(expiry("early_event", 1100)).await.unwrap());
    assert!(store.claim_node_judging(r, "early_event", 0, "judge".into(), 112, 160).await.unwrap().is_some());

    let racing = store.claim_node_dispatch(r, "ack_race", 0, "owner".into(), 110, 1200).await.unwrap().unwrap();
    assert!(store.begin_node_dispatch_send(&racing, 111).await.unwrap());
    let (ack, timeout) = tokio::join!(store.finish_node_dispatch(&racing, ResultFact::Accepted, 1100),
        store.expire_node_dispatch(expiry("ack_race", 1100)));
    let ack = ack.unwrap(); let timeout = timeout.unwrap();
    assert_ne!(ack, timeout, "ACK and expiry must have exactly one winner");
    let saved = store.get_node_dispatch(r, "ack_race", 0).await.unwrap().unwrap();
    let node = store.get_node_run(r, "ack_race").await.unwrap().unwrap();
    assert_eq!(saved.status, if ack { Status::Delivered } else { Status::Superseded });
    assert_eq!(node.status, if ack { StateMachineNodeStatus::Running } else { StateMachineNodeStatus::Failed });
    assert!(saved.lease_owner.is_none());
    let cancel = store.claim_node_dispatch(r, "cancel", 0, "owner".into(), 110, 150).await.unwrap().unwrap();
    assert!(store.update_run_status(r, StateMachineRunStatus::Aborted, None, None, 120, Some(120)).await.unwrap());
    assert!(!store.begin_node_dispatch_send(&cancel, 121).await.unwrap());
    store.supersede_inactive_node_dispatches(r).await.unwrap();
    for id in ["cancel", "unknown", "unsent", "deadline", "early_event"] {
        let saved = store.get_node_dispatch(r, id, 0).await.unwrap().unwrap();
        assert_eq!(saved.status, Status::Superseded); assert!(saved.lease_owner.is_none());
    }
    assert_eq!(store.get_node_dispatch(r, "accepted", 0).await.unwrap().unwrap().status, Status::Delivered);
    assert_eq!(store.get_node_dispatch(r, "rejected", 0).await.unwrap().unwrap().status, Status::Failed);
}

#[tokio::test]
async fn memory_dispatch_checkpoint_contract() { dispatch_contract(&MemoryCollaborationStore::new()).await; }

#[tokio::test]
async fn sqlite_dispatch_checkpoint_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap()); bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    dispatch_contract(&store).await;
    assert!(MySqlCollaborationStore::sqlite(db.clone(), "other".into()).get_node_dispatch("dispatch-contract", "accepted", 0).await.unwrap().is_none());
    db.execute(DbStatement::new("UPDATE bcs_collaboration_delivery_checkpoints SET payload_json = '{}' WHERE operation_kind = 'bot_dispatch'")).await.unwrap();
    assert!(store.get_node_dispatch("dispatch-contract", "accepted", 0).await.is_err());
}

#[tokio::test]
async fn sqlite_dispatch_acceptance_and_phase_commit_atomically() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap()); bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    let mut run = test_run(); run.status = StateMachineRunStatus::Running;
    let mut node = test_node(); node.status = StateMachineNodeStatus::Pending; node.attempt = 0;
    store.create_run(run.clone(), vec![node.clone()]).await.unwrap();
    let request_id = format!("smnode-{}-{}-0", run.run_id, node.node_id);
    assert!(store.mark_node_running_if_run_active(&run.run_id, &node.node_id, 0, request_id.clone(), 100).await.unwrap());
    assert!(store.save_node_dispatch(StateMachineDispatchPayload { run_id: run.run_id.clone(), node_id: node.node_id.clone(), attempt: 0,
        group_id: run.group_id.clone(), session_id: run.session_id.clone(), assignee_bot_id: node.assignee_bot_id.unwrap(), delivery_request_id: request_id.clone(),
        target: StateMachineDispatchTarget::WebSocket, request: json!({"id": request_id}), started_at_ms: 100, deadline_ms: 120100 }).await.unwrap());
    let claim = store.claim_node_dispatch(&run.run_id, &node.node_id, 0, "owner".into(), 110, 500).await.unwrap().unwrap();
    assert!(store.begin_node_dispatch_send(&claim, 111).await.unwrap());
    db.execute(DbStatement::new("CREATE TRIGGER fail_dispatch_phase BEFORE UPDATE ON bcs_state_machine_node_runs WHEN NEW.runtime_phase = 'waiting_provider' BEGIN SELECT RAISE(ABORT, 'phase failure'); END")).await.unwrap();
    assert!(store.finish_node_dispatch(&claim, ResultFact::Accepted, 112).await.is_err());
    let saved = store.get_node_dispatch(&run.run_id, &node.node_id, 0).await.unwrap().unwrap();
    assert_eq!(saved.status, Status::Delivering); assert_eq!(saved.lease_owner.as_deref(), Some("owner"));
    db.execute(DbStatement::new("DROP TRIGGER fail_dispatch_phase")).await.unwrap();
    assert!(store.finish_node_dispatch(&claim, ResultFact::Accepted, 113).await.unwrap());
    assert_eq!(store.get_node_dispatch(&run.run_id, &node.node_id, 0).await.unwrap().unwrap().status, Status::Delivered);
}

#[tokio::test]
async fn sqlite_dispatch_expiry_rolls_back_node_failure_when_checkpoint_write_fails() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap()); bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    let mut run = test_run(); run.status = StateMachineRunStatus::Running;
    let mut node = test_node(); node.status = StateMachineNodeStatus::Pending; node.attempt = 0; node.max_attempts = 2;
    store.create_run(run.clone(), vec![node.clone()]).await.unwrap();
    let request_id = format!("smnode-{}-{}-0", run.run_id, node.node_id);
    assert!(store.mark_node_running_if_run_active(&run.run_id, &node.node_id, 0, request_id.clone(), 100).await.unwrap());
    assert!(store.save_node_dispatch(StateMachineDispatchPayload { run_id: run.run_id.clone(), node_id: node.node_id.clone(), attempt: 0,
        group_id: run.group_id.clone(), session_id: run.session_id.clone(), assignee_bot_id: node.assignee_bot_id.unwrap(), delivery_request_id: request_id.clone(),
        target: StateMachineDispatchTarget::WebSocket, request: json!({"id": request_id}), started_at_ms: 100, deadline_ms: 120100 }).await.unwrap());
    let claim = store.claim_node_dispatch(&run.run_id, &node.node_id, 0, "owner".into(), 110, 500).await.unwrap().unwrap();
    assert!(store.begin_node_dispatch_send(&claim, 111).await.unwrap());
    let expiry = FailStateMachineNodeAttempt { run_id: run.run_id.clone(), node_id: node.node_id.clone(), attempt: 0,
        error: "original timeout".into(), completed_at_ms: 120100, action: StateMachineFailureAction::Retry };
    db.execute(DbStatement::new("CREATE TRIGGER fail_dispatch_expiry BEFORE UPDATE ON bcs_collaboration_delivery_checkpoints WHEN NEW.status = 'superseded' BEGIN SELECT RAISE(ABORT, 'expiry failure'); END")).await.unwrap();
    assert!(store.expire_node_dispatch(expiry.clone()).await.is_err());
    let saved = store.get_node_dispatch(&run.run_id, &node.node_id, 0).await.unwrap().unwrap();
    assert_eq!(saved.status, Status::Delivering); assert_eq!(saved.lease_owner.as_deref(), Some("owner"));
    let saved_node = store.get_node_run(&run.run_id, &node.node_id).await.unwrap().unwrap();
    assert_eq!(saved_node.status, StateMachineNodeStatus::Running); assert_eq!(saved_node.timeout_deadline_ms, Some(120100));
    assert!(store.get_node_attempt_failure(&run.run_id, &node.node_id, 0).await.unwrap().is_none());
    db.execute(DbStatement::new("DROP TRIGGER fail_dispatch_expiry")).await.unwrap();
    assert!(store.expire_node_dispatch(expiry).await.unwrap());
    assert_eq!(store.get_node_dispatch(&run.run_id, &node.node_id, 0).await.unwrap().unwrap().status, Status::Superseded);
    assert_eq!(store.get_node_attempt_failure(&run.run_id, &node.node_id, 0).await.unwrap().unwrap().action, Some(StateMachineFailureAction::Retry));
}
