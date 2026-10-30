use super::*;
use bcs_domain::{NewMessage, MessageAudience, MessageVisibilityDomain, SenderType, DeliveryType};
use bcs_domain::message_delivery::{DeliveryFlowKind, MessageDeliveryStatus};
use bcs_message_store::{MemoryMessageRepo, MySqlMessageStore};
use bcs_event_store::MemoryEventStore;
use bcs_service_api::port::repo::{CommitStateMachineTerminal as Command, StateMachineRunResultV1, StateMachineRunReason as Reason, MessageRepoPort};
use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget, DeliveryLookup, MessageDeliveryRepoPort};

fn command(run: &StateMachineRun, capacity: u32, status: StateMachineRunStatus) -> Command {
    let result = StateMachineRunResultV1 { schema_version: 1, run_id: run.run_id.clone(), workflow_name: "safe workflow".into(), status,
        failed_node_id: (status == StateMachineRunStatus::Failed).then(|| "answer".into()),
        attempt: (status == StateMachineRunStatus::Failed).then_some(0), reason_category: Some(if status == StateMachineRunStatus::Aborted { Reason::UserAborted } else { Reason::NodeFailed }), completed_node_ids: vec![] };
    let id = result.message_id();
    let admission = AdmitMessageDeliveries { message_id: id.clone(), message: NewMessage {
        group_id: run.group_id.clone(), session_id: run.session_id.clone(), sender_id: "system".into(), sender_type: SenderType::System,
        message_type: "system".into(), content: json!({"text": result.text().unwrap()}), client_msg_id: Some(id), owner_bot_id: run.created_by.clone(),
        visibility_domain: MessageVisibilityDomain::ManagerWorker, audience: Some(MessageAudience::directed(["tester"]).unwrap()),
        created_at: 100, run_id: run.run_id.clone() },
        targets: vec![DeliveryAdmissionTarget { target_bot_id: "tester".into(), kind: DeliveryType::Send, max_queued: capacity,
            rejection: None, semantic_projection_json: json!({"version":1}), run_id: None }],
        flow_kind: DeliveryFlowKind::System, now_ms: 100, expire_at_ms: None, display_message: None, event: None };
    let mut event = public_event(&format!("terminal:{}", run.run_id), if status == StateMachineRunStatus::Aborted { "state_machine.run.aborted" } else { "state_machine.run.failed" }, None);
    event.event.subject.id = run.run_id.clone(); event.event.scope.run_id = Some(run.run_id.clone());
    event.event.stream_key = format!("state-machine-run:{}", run.run_id);
    event.event.data.insert("result".into(), serde_json::to_value(&result).unwrap());
    event.event.data.insert("completed_at".into(), json!("2026-08-19T00:00:00.100Z"));
    event.event.data.insert("duration_ms".into(), json!(99));
    event.event.data.insert("scope_invalid".into(), json!(false));
    Command { run_id: run.run_id.clone(), result, admission: Some(admission), event: Some(event), completed_at_ms: 100,
        error: Some("SECRET_WORD private artifact and stacktrace".into()), startup: None }
}

pub(super) async fn contract(store: &dyn StateMachineRunRepoPort, messages: &dyn MessageRepoPort,
    deliveries: &dyn MessageDeliveryRepoPort, events: &dyn EventRepoPort) {
    for capacity in [0, 100] {
        let mut run = test_run(); run.run_id = format!("terminal-capacity-{capacity}");
        store.create_run(run.clone(), vec![]).await.unwrap();
        let failed = command(&run, capacity, StateMachineRunStatus::Failed);
        let aborted = command(&run, capacity, StateMachineRunStatus::Aborted);
        let (a, b) = tokio::join!(store.commit_terminal(failed.clone()), store.commit_terminal(aborted));
        assert_ne!(a.unwrap(), b.unwrap());
        assert!(!store.commit_terminal(failed.clone()).await.unwrap());
        let result = store.get_terminal_result(&run.run_id).await.unwrap().unwrap();
        assert_eq!(store.get_run(&run.run_id).await.unwrap().unwrap().status, result.status);
        let msg = messages.get_message_by_id(&run.session_id, &result.message_id()).await.unwrap().unwrap();
        assert_eq!(msg.content["text"], result.text().unwrap());
        let rows = deliveries.lookup(DeliveryLookup::Message(result.message_id())).await.unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].state.status, if capacity == 0 { MessageDeliveryStatus::RejectedCapacity } else { MessageDeliveryStatus::Queued });
        let public = events.get_event(&failed.event.unwrap().event.event_id, "test").await.unwrap().unwrap();
        assert!(!format!("{public:?}").contains("SECRET_WORD"));
        assert!(!msg.content.to_string().contains("SECRET_WORD"));
        assert!(!store.mark_node_running_if_run_active(&run.run_id, "answer", 0, "late".into(), 110).await.unwrap());
    }
}

#[tokio::test]
async fn memory_terminal_result_is_atomic_unique_and_capacity_bounded() {
    let events = Arc::new(MemoryEventStore::new());
    let messages = Arc::new(MemoryMessageRepo::new().with_environment("test".into()).with_event_store(events.clone()));
    let store = MemoryCollaborationStore::new().with_event_store(events.clone()).with_message_store(messages.clone());
    contract(&store, messages.as_ref(), messages.as_ref(), events.as_ref()).await;
}

#[tokio::test]
async fn preexisting_message_cannot_commit_an_unrelated_run_transition() {
    let events = Arc::new(MemoryEventStore::new());
    let messages = Arc::new(MemoryMessageRepo::new().with_environment("test".into()).with_event_store(events.clone()));
    let store = MemoryCollaborationStore::new().with_event_store(events.clone()).with_message_store(messages.clone());
    let run = test_run(); store.create_run(run.clone(), vec![]).await.unwrap();
    let c = command(&run, 10, StateMachineRunStatus::Failed);
    messages.admit_batch(vec![c.admission.clone().unwrap()]).await.unwrap();
    assert!(store.commit_terminal(c.clone()).await.is_err());
    assert_eq!(store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
    assert!(store.get_terminal_result(&run.run_id).await.unwrap().is_none());
    assert!(events.get_event(&c.event.unwrap().event.event_id, "test").await.unwrap().is_none());
}

#[tokio::test]
async fn sqlite_terminal_result_rollback_race_replay_and_restart() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (env, group_id, session_id, session_kind, status, participants) VALUES ('test','group-1','group-1:abcdef12','chat','running','[]')")).await.unwrap();
    let messages = Arc::new(MySqlMessageStore::sqlite(db.clone(), "test".into()));
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into()).with_message_store(messages.clone());
    let events = DbEventStore::sqlite(db.clone());
    contract(&store, messages.as_ref(), messages.as_ref(), &events).await;
    let restarted = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    assert!(restarted.get_terminal_result("terminal-capacity-100").await.unwrap().is_some());
    // A completion concurrent with summary construction must force a retry;
    // the committed message/event may never omit that completed node.
    let mut fenced = test_run(); fenced.run_id = "terminal-node-fence".into();
    let mut node = test_node(); node.run_id = fenced.run_id.clone(); node.status = StateMachineNodeStatus::Completed;
    store.create_run(fenced.clone(), vec![node]).await.unwrap();
    let mut stale = command(&fenced, 100, StateMachineRunStatus::Failed);
    assert!(store.commit_terminal(stale.clone()).await.is_err());
    assert_eq!(store.get_run(&fenced.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
    stale.result.completed_node_ids = store.completed_node_ids(&fenced.run_id).await.unwrap();
    stale.admission.as_mut().unwrap().message.content["text"] = json!(stale.result.text().unwrap());
    stale.event.as_mut().unwrap().event.data.insert("result".into(), serde_json::to_value(&stale.result).unwrap());
    assert!(store.commit_terminal(stale).await.unwrap());
    for table in ["bcs_messages", "bcs_events"] {
        let mut run = test_run(); run.run_id = format!("rollback-{table}");
        store.create_run(run.clone(), vec![]).await.unwrap();
        let c = command(&run, 100, StateMachineRunStatus::Failed);
        db.execute(DbStatement::new(format!("CREATE TRIGGER reject_terminal BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'injected'); END"))).await.unwrap();
        assert!(store.commit_terminal(c.clone()).await.is_err());
        assert_eq!(store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
        assert!(store.get_terminal_result(&run.run_id).await.unwrap().is_none());
        assert!(messages.get_message_by_id(&run.session_id, &c.result.message_id()).await.unwrap().is_none());
        assert!(messages.lookup(DeliveryLookup::Message(c.result.message_id())).await.unwrap().is_empty());
        assert!(events.get_event(&c.event.as_ref().unwrap().event.event_id, "test").await.unwrap().is_none());
        db.execute(DbStatement::new("DROP TRIGGER reject_terminal")).await.unwrap();
        assert!(store.commit_terminal(c).await.unwrap());
    }
}

#[tokio::test]
async fn memory_event_failure_rolls_back_run_and_message_and_invalid_target_is_rejected() {
    let messages = Arc::new(MemoryMessageRepo::new().with_environment("test".into()));
    let store = MemoryCollaborationStore::new().with_message_store(messages.clone());
    let run = test_run(); store.create_run(run.clone(), vec![]).await.unwrap();
    let c = command(&run, 10, StateMachineRunStatus::Failed);
    assert!(store.commit_terminal(c.clone()).await.is_err()); // No event journal configured.
    assert_eq!(store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
    assert!(messages.get_message_by_id(&run.session_id, &c.result.message_id()).await.unwrap().is_none());
    let mut invalid = c; invalid.admission.as_mut().unwrap().targets[0].target_bot_id = "worker".into();
    assert!(store.commit_terminal(invalid).await.is_err());
    let mut leaked = command(&run, 10, StateMachineRunStatus::Failed);
    leaked.event.as_mut().unwrap().event.data.insert("error".into(), json!("SECRET_WORD"));
    assert!(store.commit_terminal(leaked).await.is_err());
    assert_eq!(store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
}

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing to a disposable migrated database"]
async fn real_mysql_terminal_transaction_and_rollback() {
    use bcs_config_api::{MysqlDbConfig, StatementProtocol};
    use bcs_config_api::mysql::MysqlConnectionConfig;
    use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
    let url = std::env::var("BCS_TEST_MYSQL_URL").expect("test database URL");
    let options = mysql_async::Opts::from_url(&url).unwrap();
    let config = MysqlDbConfig::new().with_database(options.db_name().unwrap()).with_connection(MysqlConnectionConfig {
        connection_type: "direct".into(), host: Some(options.ip_or_hostname().into()), port: Some(options.tcp_port()),
        user: options.user().map(str::to_owned), password: options.pass().map(str::to_owned), extra: BTreeMap::new(),
    }).with_statement_protocol(StatementProtocol::Prepared);
    let manager = MysqlDbManager::new(config).await.unwrap();
    let db = Arc::new(MysqlDbPlugin::new(manager.clone(), "bcs"));
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (env, group_id, session_id, session_kind, status, activation_count, participants) VALUES ('test','group-1','group-1:abcdef12','chat','running',1,'[]')")).await.unwrap();
    let messages = Arc::new(MySqlMessageStore::new(db.clone(), "test".into()));
    let store = MySqlCollaborationStore::new(db.clone(), "test".into()).with_message_store(messages.clone());
    let events = DbEventStore::mysql(db.clone());
    contract(&store, messages.as_ref(), messages.as_ref(), &events).await;
    let mut fenced = test_run(); fenced.run_id = "mysql-terminal-node-fence".into();
    let mut node = test_node(); node.run_id = fenced.run_id.clone(); node.status = StateMachineNodeStatus::Completed;
    store.create_run(fenced.clone(), vec![node]).await.unwrap();
    let mut stale = command(&fenced, 100, StateMachineRunStatus::Failed);
    assert!(store.commit_terminal(stale.clone()).await.is_err());
    stale.result.completed_node_ids = store.completed_node_ids(&fenced.run_id).await.unwrap();
    stale.admission.as_mut().unwrap().message.content["text"] = json!(stale.result.text().unwrap());
    stale.event.as_mut().unwrap().event.data.insert("result".into(), serde_json::to_value(&stale.result).unwrap());
    assert!(store.commit_terminal(stale).await.unwrap());
    for table in ["bcs_messages", "bcs_events"] {
        let mut run = test_run(); run.run_id = format!("rollback-{table}");
        store.create_run(run.clone(), vec![]).await.unwrap();
        let c = command(&run, 100, StateMachineRunStatus::Failed);
        let field = if table == "bcs_messages" { "message_id" } else { "event_id" };
        db.execute(DbStatement::new(format!("ALTER TABLE {table} ADD CONSTRAINT reject_terminal CHECK ({field} NOT LIKE '%rollback-{table}%')"))).await.unwrap();
        assert!(store.commit_terminal(c.clone()).await.is_err());
        assert_eq!(store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
        assert!(store.get_terminal_result(&run.run_id).await.unwrap().is_none());
        assert!(messages.get_message_by_id(&run.session_id, &c.result.message_id()).await.unwrap().is_none());
        assert!(events.get_event(&c.event.as_ref().unwrap().event.event_id, "test").await.unwrap().is_none());
        db.execute(DbStatement::new(format!("ALTER TABLE {table} DROP CHECK reject_terminal"))).await.unwrap();
        assert!(store.commit_terminal(c).await.unwrap());
    }
    let mut run = test_run(); run.run_id = "mysql-disconnected-worker".into();
    let mut node = test_node(); node.run_id = run.run_id.clone();
    store.create_run(run, vec![node.clone()]).await.unwrap();
    assert!(store.disconnected_bot_nodes("bot_sm_e2e_driver", 999, None, 64).await.unwrap().is_empty());
    let candidates = store.disconnected_bot_nodes("bot_sm_e2e_driver", 1001, None, 64).await.unwrap();
    assert_eq!(candidates.len(), 1);
    let mut stale = candidates[0].clone(); stale.delivery_request_id = Some("old".into());
    assert!(!store.fail_disconnected_bot_node(&stale, 1001).await.unwrap());
    assert!(store.fail_disconnected_bot_node(&candidates[0], 1001).await.unwrap());
    assert!(!store.fail_disconnected_bot_node(&candidates[0], 1001).await.unwrap());
    assert_eq!(store.get_node_attempt_failure(&node.run_id, &node.node_id, node.attempt).await.unwrap().unwrap().reason_category, Some(bcs_service_api::port::repo::StateMachineRunReason::NodeFailed));
    manager.close().await;
}
