use super::*;
use bcs_service_api::StateMachineOpeningPayload;
use bcs_service_api::port::repo::MessageRepoPort;

pub(super) async fn opening_contract(store: &dyn StateMachineRunRepoPort) {
    let mut run = test_run(); run.run_id = "opening-run".into(); run.status = StateMachineRunStatus::Pending;
    run.session_id = "opening-contract-session".into();
    store.create_run(run.clone(), Vec::new()).await.unwrap();
    let payload = StateMachineOpeningPayload { run_id: run.run_id.clone(), group_id: run.group_id.clone(),
        session_id: run.session_id.clone(), client_msg_id: format!("{}:000-panel", run.run_id),
        content: "原始 opening\n{{already rendered}}".into(), component: Some("aixui".into()), created_at_ms: run.created_at };
    let mut wrong = payload.clone(); wrong.session_id = "another-session".into();
    assert!(!store.save_run_opening(wrong).await.unwrap());
    assert!(store.get_run_opening(&run.run_id).await.unwrap().is_none());
    let (a, b) = tokio::join!(store.save_run_opening(payload.clone()), store.save_run_opening(payload.clone()));
    assert!(a.unwrap() && b.unwrap());
    let saved = store.get_run_opening(&run.run_id).await.unwrap().unwrap();
    assert_eq!(saved.payload, payload);
    assert_eq!(saved.delivered_at_ms, None);
    let mut changed = payload.clone(); changed.content.push(' ');
    assert!(store.save_run_opening(changed).await.is_err());
    assert!(store.mark_run_opening_delivered(&run.run_id, 50).await.unwrap());
    assert!(store.mark_run_opening_delivered(&run.run_id, 90).await.unwrap());
    assert_eq!(store.get_run_opening(&run.run_id).await.unwrap().unwrap().delivered_at_ms, Some(50));
    assert!(store.save_run_opening(payload.clone()).await.unwrap());
    store.update_run_status(&run.run_id, StateMachineRunStatus::Aborted, None, None, 100, Some(100)).await.unwrap();
    assert!(store.save_run_opening(payload.clone()).await.unwrap());
    assert_eq!(store.get_run_opening(&run.run_id).await.unwrap().unwrap().payload, payload);
    let mut cancelled = run.clone(); cancelled.run_id = "cancel-opening".into(); cancelled.status = StateMachineRunStatus::Aborted;
    store.create_run(cancelled.clone(), Vec::new()).await.unwrap();
    let mut cancelled_payload = payload; cancelled_payload.run_id = cancelled.run_id.clone();
    cancelled_payload.client_msg_id = format!("{}:000-panel", cancelled.run_id);
    assert!(!store.save_run_opening(cancelled_payload).await.unwrap());
    assert!(!store.mark_run_opening_delivered(&cancelled.run_id, 200).await.unwrap());
    for (id, status) in [("opening-1", StateMachineRunStatus::Pending), ("opening-2", StateMachineRunStatus::Running), ("opening-3", StateMachineRunStatus::Pending)] {
        let mut candidate = run.clone(); candidate.run_id = id.into(); candidate.status = status;
        store.create_run(candidate, Vec::new()).await.unwrap();
    }
    assert!(store.list_pending_runs(None, 0).await.unwrap().is_empty());
    let first = store.list_pending_runs(Some("opening-0"), 1).await.unwrap();
    assert_eq!(first[0].run_id, "opening-1");
    let second = store.list_pending_runs(Some(&first[0].run_id), 1).await.unwrap();
    assert_eq!(second[0].run_id, "opening-3");
    assert!(store.list_pending_runs(Some(&second[0].run_id), 1).await.unwrap().is_empty());
}

pub(super) async fn message_identity_contract(repo: &dyn MessageRepoPort) {
    let msg = bcs_domain::NewMessage { group_id: "opening-group".into(), session_id: "opening-session".into(),
        sender_id: "opening-bot".into(), sender_type: bcs_domain::SenderType::Bot, message_type: "test".into(),
        content: json!({"text": "saved original"}), client_msg_id: None, owner_bot_id: None,
        created_at: 100, run_id: "opening-run".into(), visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None };
    let mut changed = msg.clone(); changed.content = json!({"text": "conflicting original"});
    let (a, b) = tokio::join!(repo.append_message_with_id("fixed-opening-id".into(), msg.clone()),
        repo.append_message_with_id("fixed-opening-id".into(), changed));
    let a = a.unwrap(); let b = b.unwrap();
    assert_eq!(a.message_id, "fixed-opening-id");
    assert_eq!(a.content, b.content);
    assert_eq!(a.session_seq, b.session_seq);
    assert_eq!(repo.get_current_seq("opening-session").await.unwrap(), 1);
    let repeated = repo.append_message_with_id("fixed-opening-id".into(), msg.clone()).await.unwrap();
    assert_eq!(repeated.content, a.content);
    assert_eq!(repo.get_current_seq("opening-session").await.unwrap(), 1);
    // Upgraded writers must reuse a legacy logical message with a generated ID.
    let mut legacy = msg;
    legacy.client_msg_id = Some("legacy-opening-client-id".into());
    let original = repo.append_message(legacy.clone()).await.unwrap();
    let reused = repo.append_message_with_id("new-stable-legacy-id".into(), legacy).await.unwrap();
    assert_eq!(reused.message_id, original.message_id);
    assert_eq!(reused.content, original.content);
    assert_eq!(repo.get_current_seq("opening-session").await.unwrap(), 2);
}

#[tokio::test]
async fn memory_opening_checkpoint_and_message_identity_contract() {
    opening_contract(&MemoryCollaborationStore::new()).await;
    message_identity_contract(&bcs_message_store::MemoryMessageRepo::new()).await;
}

#[tokio::test]
async fn sqlite_opening_checkpoint_and_message_identity_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    opening_contract(&store).await;
    assert!(MySqlCollaborationStore::sqlite(db.clone(), "other".into()).get_run_opening("opening-run").await.unwrap().is_none());
    assert!(MySqlCollaborationStore::sqlite(db.clone(), "other".into()).list_pending_runs(None, 10).await.unwrap().is_empty());
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (env, group_id, session_id, participants) VALUES ('test', 'opening-group', 'opening-session', '[]')")).await.unwrap();
    message_identity_contract(&bcs_message_store::MySqlMessageStore::sqlite(db.clone(), "test".into())).await;
    db.execute(DbStatement::new("UPDATE bcs_collaboration_delivery_checkpoints SET payload_json = '{}' WHERE aggregate_id = 'opening-run'")).await.unwrap();
    assert!(store.get_run_opening("opening-run").await.is_err());
}
