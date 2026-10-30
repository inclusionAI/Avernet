//! RunResult intent saved by `TerminateRun` (issue #2610): one terminal CAS saves one
//! Pending intent, delivery leases are fenced, and terminal outcomes are never resent.
use super::*;
use bcs_event_store::MemoryEventStore;
use bcs_service_api::port::repo::MessageRepoPort;
use bcs_service_api::{
    ServiceError, StateMachineRunFailedNode, StateMachineRunResultClaim, StateMachineRunResultOutcome as Outcome,
    StateMachineRunResultPayload as Payload, StateMachineRunResultStatus as Status, StateMachineRunResultTerminal,
    StateMachineRunTerminalReason,
};

fn assert_cas_lost(result: bcs_service_api::ServiceResult<bool>) {
    assert!(matches!(result, Ok(false) | Err(ServiceError::Conflict(_))), "{result:?}");
}

fn payload(run: &str) -> Payload {
    Payload {
        run_id: run.into(), group_id: "group-1".into(), session_id: "group-1:result".into(),
        target_bot_id: "referee".into(), workflow: "谁是卧底 第1轮 发言".into(),
        terminal: StateMachineRunResultTerminal::Failed, reason: StateMachineRunTerminalReason::NodeFailed,
        failed_node: Some(StateMachineRunFailedNode { node_id: "speak_3".into(), attempt: 1, max_attempts: 1 }),
        completed_nodes: vec!["speak_1".into(), "speak_2".into()], terminated_at_ms: 1_000, deadline_ms: 601_000,
    }
}

fn terminate(run: &str, result: Option<Payload>, event: Option<AppendEventRecord>) -> StateMachineEventfulTransition {
    StateMachineEventfulTransition::TerminateRun {
        run_id: run.into(), status: StateMachineRunStatus::Failed, error: Some("speak_3 failed".into()),
        terminated_at_ms: 1_000, event, result,
    }
}

async fn running(store: &dyn StateMachineRunRepoPort, run: &str) {
    // Own root and Session, so the Runs left Running here never make a shared fixture Session busy.
    let mut record = test_run();
    record.run_id = run.into();
    record.root_run_id = Some(run.into());
    record.session_id = format!("group-1:{run}");
    record.status = StateMachineRunStatus::Running;
    store.create_run(record, Vec::new()).await.unwrap();
}

async fn saved_status(store: &dyn StateMachineRunRepoPort, run: &str) -> Status {
    store.get_run_result(run).await.unwrap().unwrap().status
}

async fn pending_contains(store: &dyn StateMachineRunRepoPort, run: &str) -> bool {
    store.list_run_results_pending(None, 100).await.unwrap().iter().any(|id| id == run)
}

/// Terminates `run` with an intent, claims it as `worker-a` until 32s and starts the send.
async fn sending(store: &dyn StateMachineRunRepoPort, run: &str) -> StateMachineRunResultClaim {
    running(store, run).await;
    assert!(store.commit_eventful_transition(terminate(run, Some(payload(run)), None)).await.unwrap());
    let claim = store.claim_run_result(run, "worker-a".into(), 2_000, 32_000).await.unwrap().unwrap();
    assert!(store.begin_run_result_send(&claim, 3_000).await.unwrap());
    claim
}

pub(crate) async fn contract(store: &dyn StateMachineRunRepoPort, events: Option<&dyn EventRepoPort>) {
    // One terminal CAS saves one Pending intent and one Event; a racing or replayed transition saves neither.
    running(store, "rr-delivered").await;
    let event = |id: &str| events.map(|_| public_event(id, "state_machine.run.failed", None));
    let first = terminate("rr-delivered", Some(payload("rr-delivered")), event("rr-failed"));
    assert!(store.commit_eventful_transition(first).await.unwrap());
    let replay = terminate("rr-delivered", Some(payload("rr-delivered")), event("rr-failed-again"));
    assert_cas_lost(store.commit_eventful_transition(replay).await);
    assert_eq!(store.get_run("rr-delivered").await.unwrap().unwrap().status, StateMachineRunStatus::Failed);
    let saved = store.get_run_result("rr-delivered").await.unwrap().unwrap();
    assert_eq!((saved.status, saved.lease_token, saved.lease_owner), (Status::Pending, 0, None));
    assert_eq!(saved.payload, payload("rr-delivered"));
    if let Some(events) = events {
        assert!(events.get_event("rr-failed", "test").await.unwrap().is_some());
        assert!(events.get_event("rr-failed-again", "test").await.unwrap().is_none());
    }
    assert!(pending_contains(store, "rr-delivered").await);
    assert!(store.list_run_results_pending(None, 0).await.unwrap().is_empty());

    // Leases are fenced: a second worker waits, Delivering never restarts, a spent claim writes nothing.
    let claim = store.claim_run_result("rr-delivered", "worker-a".into(), 2_000, 32_000).await.unwrap().unwrap();
    assert!(store.claim_run_result("rr-delivered", "worker-b".into(), 3_000, 33_000).await.unwrap().is_none());
    assert!(store.begin_run_result_send(&claim, 3_000).await.unwrap());
    assert!(!store.begin_run_result_send(&claim, 3_500).await.unwrap());
    assert!(store.finish_run_result(&claim, Outcome::Delivered, 4_000).await.unwrap());
    let delivered = store.get_run_result("rr-delivered").await.unwrap().unwrap();
    assert_eq!((delivered.status, delivered.delivered_at_ms, delivered.lease_owner), (Status::Delivered, Some(4_000), None));
    assert!(!store.finish_run_result(&claim, Outcome::Delivered, 5_000).await.unwrap());
    assert!(store.claim_run_result("rr-delivered", "worker-b".into(), 5_000, 35_000).await.unwrap().is_none());
    assert!(!store.expire_run_result("rr-delivered", 700_000).await.unwrap());
    assert!(!pending_contains(store, "rr-delivered").await);

    // Retry returns the intent to Pending; the next claim gets a new token and the old claim is fenced off.
    let first = sending(store, "rr-retry").await;
    assert!(store.finish_run_result(&first, Outcome::Retry { error: "transport busy".into() }, 4_000).await.unwrap());
    let retried = store.get_run_result("rr-retry").await.unwrap().unwrap();
    assert_eq!((retried.status, retried.error.as_deref(), retried.lease_owner), (Status::Pending, Some("transport busy"), None));
    let second = store.claim_run_result("rr-retry", "worker-b".into(), 5_000, 35_000).await.unwrap().unwrap();
    assert_eq!(second.token, first.token + 1);
    assert!(!store.begin_run_result_send(&first, 5_500).await.unwrap());
    assert!(store.begin_run_result_send(&second, 6_000).await.unwrap());
    let skip = Outcome::Skipped { reason: "session is not running".into() };
    assert!(store.finish_run_result(&second, skip, 7_000).await.unwrap());
    let skipped = store.get_run_result("rr-retry").await.unwrap().unwrap();
    assert_eq!((skipped.status, skipped.error.as_deref()), (Status::Skipped, Some("session is not running")));

    // An uncertain send is terminal and never resent.
    let claim = sending(store, "rr-uncertain").await;
    assert!(store.finish_run_result(&claim, Outcome::Uncertain { error: "transport timeout".into() }, 4_000).await.unwrap());
    assert_eq!(saved_status(store, "rr-uncertain").await, Status::Failed);
    assert!(store.claim_run_result("rr-uncertain", "worker-b".into(), 5_000, 35_000).await.unwrap().is_none());

    // A lost sender: Delivering past its lease is listed for expiry and becomes Failed, never Pending again.
    sending(store, "rr-lost").await;
    assert!(pending_contains(store, "rr-lost").await);
    assert!(!store.expire_run_result("rr-lost", 31_999).await.unwrap());
    assert!(store.expire_run_result("rr-lost", 32_000).await.unwrap());
    assert_eq!(saved_status(store, "rr-lost").await, Status::Failed);
    assert!(store.claim_run_result("rr-lost", "worker-b".into(), 33_000, 63_000).await.unwrap().is_none());

    // A Pending intent cannot be leased past its deadline and expires there.
    running(store, "rr-late").await;
    assert!(store.commit_eventful_transition(terminate("rr-late", Some(payload("rr-late")), None)).await.unwrap());
    assert!(store.claim_run_result("rr-late", "worker-a".into(), 590_000, 601_001).await.unwrap().is_none());
    assert!(!store.expire_run_result("rr-late", 600_999).await.unwrap());
    assert!(store.expire_run_result("rr-late", 601_000).await.unwrap());
    assert_eq!(saved_status(store, "rr-late").await, Status::Expired);

    // Without a result the CAS still terminates the Run. Completed and a foreign payload are rejected untouched.
    running(store, "rr-plain").await;
    assert!(store.commit_eventful_transition(terminate("rr-plain", None, None)).await.unwrap());
    assert!(store.get_run_result("rr-plain").await.unwrap().is_none());
    running(store, "rr-invalid").await;
    let mut completed = terminate("rr-invalid", Some(payload("rr-invalid")), None);
    if let StateMachineEventfulTransition::TerminateRun { status, .. } = &mut completed {
        *status = StateMachineRunStatus::Completed;
    }
    assert!(store.commit_eventful_transition(completed).await.is_err());
    assert!(store.commit_eventful_transition(terminate("rr-invalid", Some(payload("another-run")), None)).await.is_err());
    assert_eq!(store.get_run("rr-invalid").await.unwrap().unwrap().status, StateMachineRunStatus::Running);
    assert!(store.get_run_result("rr-invalid").await.unwrap().is_none());
}

/// SQL only: a failed intent insert rolls back the terminal CAS of the same transaction.
pub(crate) async fn rollback_contract(store: &dyn StateMachineRunRepoPort, db: &dyn DbPlugin) {
    running(store, "rr-rollback").await;
    db.execute(DbStatement::new(
        "INSERT INTO bcs_collaboration_delivery_checkpoints (env, operation_key, aggregate_kind, aggregate_id, operation_kind, \
         payload_json, status, created_at_ms, deadline_ms) VALUES ('test', 'smrun:rr-rollback:run-result', 'state_machine_run', \
         'rr-rollback', 'run_result', '{}', 'pending', 1000, 601000)",
    )).await.unwrap();
    assert!(store.commit_eventful_transition(terminate("rr-rollback", Some(payload("rr-rollback")), None)).await.is_err());
    assert_eq!(store.get_run("rr-rollback").await.unwrap().unwrap().status, StateMachineRunStatus::Running);
}

/// Running Chat Session for `history_contract` in the SQL stores.
pub(crate) const HISTORY_SESSION: &str = "INSERT INTO bcs_group_sessions (env, group_id, session_id, session_kind, status, \
    input, activation_count, participants) VALUES ('test', 'rr-group', 'rr-group:history', 'chat', 'running', '{}', 1, '[]')";

/// A queued delivery persists the RunResult row; neither history read may return it.
pub(crate) async fn history_contract(repo: &dyn MessageRepoPort) {
    let message = |message_type: &str| bcs_domain::NewMessage {
        group_id: "rr-group".into(), session_id: "rr-group:history".into(), sender_id: "bcs_state_machine".into(),
        sender_type: bcs_domain::SenderType::Bot, message_type: message_type.into(), content: json!({"text": message_type}),
        client_msg_id: None, owner_bot_id: None, created_at: 100, run_id: "rr-history".into(),
        visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None,
    };
    repo.append_message(message("text")).await.unwrap();
    repo.append_message(message(bcs_domain::RUN_RESULT_MESSAGE_TYPE)).await.unwrap();
    let query = bcs_domain::MessageQuery {
        group_id: "rr-group".into(), session_id: "rr-group:history".into(), cursor: None, limit: 50, keyword: None,
        sender_id: None, message_type: None, owner_filter: bcs_domain::MessageOwnerFilter::Any, time_range: None,
        visible_from_seq: None, human_view: None,
    };
    let page = repo.query_messages(query).await.unwrap();
    assert_eq!(page.messages.iter().map(|m| m.message_type.as_str()).collect::<Vec<_>>(), ["text"]);
    let history = repo.list_session_history("rr-group:history", bcs_domain::MessageOwnerFilter::Any, None, None, None, 50)
        .await.unwrap();
    assert_eq!(history.messages.iter().map(|m| m.message_type.as_str()).collect::<Vec<_>>(), ["text"]);
}

#[tokio::test]
async fn memory_run_result_contract() {
    let events = Arc::new(MemoryEventStore::new());
    let store = MemoryCollaborationStore::new().with_event_store(events.clone());
    contract(&store, Some(events.as_ref())).await;
    history_contract(&bcs_message_store::MemoryMessageRepo::new()).await;
}

#[tokio::test]
async fn sqlite_run_result_contract_and_intent_rollback() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    contract(&store, Some(&DbEventStore::sqlite(db.clone()))).await;
    rollback_contract(&store, db.as_ref()).await;
    db.execute(DbStatement::new(HISTORY_SESSION)).await.unwrap();
    history_contract(&bcs_message_store::MySqlMessageStore::sqlite(db.clone(), "test".into())).await;
}
