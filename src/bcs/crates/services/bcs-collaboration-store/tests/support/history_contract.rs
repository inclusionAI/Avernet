use super::*;
use bcs_domain::{MessageAudience, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_service_api::port::repo::collaboration_history::*;

fn payload(
    run: &StateMachineRun,
    node: &str,
    attempt: i32,
    event: &str,
) -> StateMachineHistoryPayload {
    let key = if event == "output" {
        bcs_domain::state_machine_history::output_message_key(&run.run_id, node, attempt)
    } else {
        format!("{}:{node}:{attempt}:human-input-prompt", run.run_id)
    };
    StateMachineHistoryPayload {
        schema_version: 1,
        message_id: bcs_domain::state_machine_history::physical_message_id(&key),
        message: NewMessage {
            group_id: run.group_id.clone(),
            session_id: run.session_id.clone(),
            run_id: run.run_id.clone(),
            sender_id: "human".into(),
            sender_type: SenderType::Human,
            message_type: if event == "output" {
                "state_machine_output"
            } else {
                "state_machine_human_input_prompt"
            }
            .into(),
            content: json!({"text":"original", "metadata":{"state_machine":{"history_schema_version":1,"run_id":run.run_id,"node_id":node,"attempt":attempt,"event":event}}}),
            client_msg_id: Some(key),
            owner_bot_id: None,
            visibility_domain: MessageVisibilityDomain::StateMachine,
            audience: Some(MessageAudience::Directed {
                actor_ids: vec!["human".into()],
            }),
            created_at: 100,
        },
    }
}
fn id(p: &StateMachineHistoryPayload) -> StateMachineHistoryIdentity {
    let meta = &p.message.content["metadata"]["state_machine"];
    StateMachineHistoryIdentity {
        session_id: p.message.session_id.clone(),
        run_id: p.message.run_id.clone(),
        node_id: meta["node_id"].as_str().unwrap().into(),
        attempt: meta["attempt"].as_i64().unwrap() as i32,
        event: meta["event"].as_str().unwrap().into(),
    }
}
fn accept(p: StateMachineHistoryPayload, judging: bool) -> StateMachineEventfulTransition {
    StateMachineEventfulTransition::AcceptHistory(AcceptStateMachineHistory {
        payload: p,
        mutation: StateMachineHistoryMutation::AcceptOutput { judging },
        event: None,
    })
}
async fn seed(store: &dyn StateMachineRunRepoPort, name: &str, count: usize) -> StateMachineRun {
    let mut run = test_run();
    run.run_id = name.into();
    store.create_run(run.clone(), (0..count).map(|n|serde_json::from_value(json!({"run_id":name,"node_id":format!("node-{n:03}"),"status":"running","attempt":0,"max_attempts":2})).unwrap()).collect()).await.unwrap();
    run
}

pub(super) async fn contract(store: &dyn StateMachineRunRepoPort) {
    let run = seed(store, "history-contract", 103).await;
    let p = payload(&run, "node-000", 0, "output");
    assert!(
        store
            .commit_eventful_transition(accept(p.clone(), true))
            .await
            .unwrap()
    );
    let saved = store.get_history_message(&id(&p)).await.unwrap().unwrap();
    assert!(saved.delivered_at_ms.is_none());
    assert_eq!(
        store
            .get_node_run(&run.run_id, "node-000")
            .await
            .unwrap()
            .unwrap()
            .artifact_text
            .as_deref(),
        Some("original")
    );
    // History acceptance also establishes the original Judge input lease.
    assert!(
        store
            .claim_node_judging(&run.run_id, "node-000", 0, "judge".into(), 101, 200)
            .await
            .unwrap()
            .is_some()
    );
    let mut changed = p.clone();
    changed.message.content["text"] = json!("different");
    assert!(!matches!(
        store
            .commit_eventful_transition(accept(changed, true))
            .await,
        Ok(true)
    ));
    let wrong = payload(&run, "node-001", 1, "output");
    assert!(!matches!(
        store
            .commit_eventful_transition(accept(wrong.clone(), false))
            .await,
        Ok(true)
    ));
    assert!(
        store
            .get_history_message(&id(&wrong))
            .await
            .unwrap()
            .is_none()
    );
    for n in 1..103 {
        assert!(
            store
                .commit_eventful_transition(accept(
                    payload(&run, &format!("node-{n:03}"), 0, "output"),
                    false
                ))
                .await
                .unwrap()
        );
    }
    store
        .update_run_status(
            &run.run_id,
            StateMachineRunStatus::Aborted,
            None,
            None,
            150,
            Some(150),
        )
        .await
        .unwrap();
    let first = store
        .list_history_messages_pending(None, usize::MAX)
        .await
        .unwrap();
    assert_eq!(first.checkpoints.len(), 100);
    let second = store
        .list_history_messages_pending(first.next.as_ref(), 100)
        .await
        .unwrap();
    assert_eq!(
        second.checkpoints.len(),
        3,
        "cursor must include the key inside one Run"
    );
    let mut keys = first
        .checkpoints
        .iter()
        .chain(&second.checkpoints)
        .map(|c| c.operation_key.clone())
        .collect::<Vec<_>>();
    keys.sort();
    keys.dedup();
    assert_eq!(keys.len(), 103);
    let (a, b) = tokio::join!(
        store.confirm_history_message(&saved, 200),
        store.confirm_history_message(&saved, 201)
    );
    a.unwrap();
    b.unwrap();
    let confirmed = store.get_history_message(&id(&p)).await.unwrap().unwrap();
    assert!(matches!(confirmed.delivered_at_ms, Some(200 | 201)));
    let mut conflict = confirmed.clone();
    conflict.payload.message.content["text"] = json!("tampered");
    assert!(store.confirm_history_message(&conflict, 202).await.is_err());
    let rejected = payload(&run, "node-001", 2, "output");
    assert!(!matches!(
        store
            .commit_eventful_transition(accept(rejected.clone(), false))
            .await,
        Ok(true)
    ));
    assert!(
        store
            .get_history_message(&id(&rejected))
            .await
            .unwrap()
            .is_none()
    );
}

#[tokio::test]
async fn memory_history_acceptance_and_terminal_replay_contract() {
    contract(&MemoryCollaborationStore::new()).await;
}
#[tokio::test]
async fn sqlite_history_acceptance_and_terminal_replay_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    contract(&MySqlCollaborationStore::sqlite(db, "test".into())).await;
}
#[tokio::test]
async fn sqlite_checkpoint_and_node_rollback_together_and_bad_payload_does_not_starve_page() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    let run = seed(&store, "rollback-history", 2).await;
    let p = payload(&run, "node-000", 0, "output");
    db.execute(DbStatement::new("CREATE TRIGGER reject_history BEFORE INSERT ON bcs_collaboration_delivery_checkpoints BEGIN SELECT RAISE(ABORT,'injected failure'); END")).await.unwrap();
    assert!(
        store
            .commit_eventful_transition(accept(p.clone(), false))
            .await
            .is_err()
    );
    assert!(
        store
            .get_node_run(&run.run_id, "node-000")
            .await
            .unwrap()
            .unwrap()
            .artifact_text
            .is_none()
    );
    assert!(store.get_history_message(&id(&p)).await.unwrap().is_none());
    db.execute(DbStatement::new("DROP TRIGGER reject_history"))
        .await
        .unwrap();
    assert!(
        store
            .commit_eventful_transition(accept(p.clone(), false))
            .await
            .unwrap()
    );
    assert!(
        store
            .commit_eventful_transition(accept(payload(&run, "node-001", 0, "output"), false))
            .await
            .unwrap()
    );
    db.execute(DbStatement::new("UPDATE bcs_collaboration_delivery_checkpoints SET payload_json='{}' WHERE node_id='node-000'")).await.unwrap();
    let page = store
        .list_history_messages_pending(None, 100)
        .await
        .unwrap();
    assert_eq!(page.failures.len(), 1);
    assert_eq!(page.checkpoints.len(), 1);
    assert!(page.next.is_some());
    assert!(store.get_history_message(&id(&p)).await.is_err());
    assert!(
        MySqlCollaborationStore::sqlite(db, "other".into())
            .list_history_messages_pending(None, 100)
            .await
            .unwrap()
            .checkpoints
            .is_empty()
    );
}
#[tokio::test]
async fn sqlite_activation_and_event_are_atomic_with_prompt() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    let mut run = test_run();
    run.run_id = "prompt-history".into();
    store
        .create_run(
            run.clone(),
            vec![
                serde_json::from_value(
                    json!({"run_id":run.run_id,"node_id":"review","status":"pending","attempt":0}),
                )
                .unwrap(),
            ],
        )
        .await
        .unwrap();
    let p = payload(&run, "review", 0, "human_input_prompt");
    let command = StateMachineEventfulTransition::AcceptHistory(AcceptStateMachineHistory {
        payload: p.clone(),
        mutation: StateMachineHistoryMutation::ActivateHuman(MarkHumanNodeRunningCommand {
            run_id: run.run_id.clone(),
            node_id: "review".into(),
            attempt: 0,
            started_at_ms: 100,
            timeout_deadline_ms: 1000,
        }),
        event: Some(public_event(
            "prompt-accepted",
            "state_machine.node.started",
            None,
        )),
    });
    db.execute(DbStatement::new("CREATE TRIGGER reject_public_history BEFORE INSERT ON bcs_events BEGIN SELECT RAISE(ABORT,'event failure'); END")).await.unwrap();
    assert!(
        store
            .commit_eventful_transition(command.clone())
            .await
            .is_err()
    );
    assert_eq!(
        store
            .get_node_run(&run.run_id, "review")
            .await
            .unwrap()
            .unwrap()
            .status,
        StateMachineNodeStatus::Pending
    );
    assert!(store.get_history_message(&id(&p)).await.unwrap().is_none());
    db.execute(DbStatement::new("DROP TRIGGER reject_public_history"))
        .await
        .unwrap();
    assert!(store.commit_eventful_transition(command).await.unwrap());
    assert_eq!(
        store
            .get_node_run(&run.run_id, "review")
            .await
            .unwrap()
            .unwrap()
            .started_at,
        Some(100)
    );
}

struct HistoryCostDb {
    inner: Arc<dyn DbPlugin>,
    calls: Mutex<Vec<(&'static str, usize)>>,
}
#[async_trait]
impl DbPlugin for HistoryCostDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let rows = self.inner.query(statement).await?;
        self.calls.lock().await.push(("query", rows.len()));
        Ok(rows)
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.calls.lock().await.push(("execute", 1));
        self.inner.execute(statement).await
    }
    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.calls.lock().await.push(("transaction", steps.len()));
        self.inner.transaction(steps).await
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

#[tokio::test]
async fn history_normal_retry_and_pending_scan_have_bounded_database_cost() {
    use bcs_service_api::port::repo::MessageRepoPort;
    let inner = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(inner.as_ref())
        .await
        .unwrap();
    inner.execute(DbStatement::new("INSERT INTO bcs_group_sessions (env,session_id,group_id,participants,current_msg_seq) VALUES ('test','group-1:abcdef12','group-1','[]',0)")).await.unwrap();
    let db = Arc::new(HistoryCostDb {
        inner: inner.clone(),
        calls: Mutex::new(Vec::new()),
    });
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    let messages = bcs_message_store::MySqlMessageStore::sqlite(db.clone(), "test".into());
    let run = seed(&store, "history-cost", 1).await;
    db.calls.lock().await.clear();
    let p = payload(&run, "node-000", 0, "output");
    let checkpoint = store
        .accept_history_message(AcceptStateMachineHistory {
            payload: p.clone(),
            mutation: StateMachineHistoryMutation::AcceptOutput { judging: false },
            event: None,
        })
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        &*db.calls.lock().await,
        &[("transaction", 3)],
        "normal acceptance returns committed payload without a read"
    );
    db.calls.lock().await.clear();
    messages
        .append_message_with_id(p.message_id.clone(), p.message.clone())
        .await
        .unwrap();
    store
        .confirm_history_message(&checkpoint, 200)
        .await
        .unwrap();
    assert_eq!(
        &*db.calls.lock().await,
        &[("query", 0), ("transaction", 3), ("execute", 1)]
    );
    db.calls.lock().await.clear();
    messages
        .append_message_with_id(p.message_id.clone(), p.message.clone())
        .await
        .unwrap();
    store
        .confirm_history_message(&checkpoint, 201)
        .await
        .unwrap();
    assert_eq!(
        &*db.calls.lock().await,
        &[("query", 1), ("query", 1), ("execute", 1), ("query", 1)]
    );
    assert_eq!(messages.get_current_seq(&run.session_id).await.unwrap(), 1);
    // Mark pending to model a committed message whose confirmation was lost.
    inner.execute(DbStatement::new("UPDATE bcs_collaboration_delivery_checkpoints SET status='pending',delivered_at_ms=NULL WHERE operation_kind='history_message'")).await.unwrap();
    db.calls.lock().await.clear();
    let page = store
        .list_history_messages_pending(None, 100)
        .await
        .unwrap();
    assert_eq!(page.checkpoints.len(), 1);
    assert_eq!(
        &*db.calls.lock().await,
        &[("query", 1)],
        "scan never loads Run/Node/Definition per checkpoint"
    );
}


pub(super) async fn provider_output_contract(store: &dyn StateMachineRunRepoPort, db: &dyn DbPlugin) {
    for judging in [false, true] {
        let run = seed(store, &format!("history-provider-{judging}"), 1).await;
        // Match the durable phase written by confirm_node_dispatch, not a
        // display sub-status or the legacy unmarked Running test fixture.
        db.execute(DbStatement::with_params(
            "UPDATE bcs_state_machine_node_runs SET runtime_phase='waiting_provider', assignee_bot_id='provider-bot' WHERE env='test' AND run_id=? AND node_id='node-000'",
            vec![run.run_id.clone().into()],
        )).await.unwrap();
        let mut p = payload(&run, "node-000", 0, "output");
        p.message.sender_id = "provider-bot".into();
        p.message.sender_type = SenderType::Bot;
        p.message.audience = Some(MessageAudience::FullOnly);
        let saved = store.accept_history_message(AcceptStateMachineHistory {
            payload: p.clone(), mutation: StateMachineHistoryMutation::AcceptOutput { judging }, event: None,
        }).await.unwrap().unwrap();
        assert_eq!(saved.payload.message.content["text"], "original");
        let node = store.get_node_run(&run.run_id, "node-000").await.unwrap().unwrap();
        assert_eq!(node.artifact_text.as_deref(), Some("original"));
        assert!(node.responded_by.is_none());
        let rows = db.query(DbStatement::with_params(
            "SELECT runtime_phase FROM bcs_state_machine_node_runs WHERE env='test' AND run_id=? AND node_id='node-000'",
            vec![run.run_id.clone().into()],
        )).await.unwrap();
        assert_eq!(bcs_db_api::db_get_column::<String>(&rows[0], "runtime_phase").unwrap(), if judging { "judging" } else { "waiting_provider" });
        store.confirm_history_message(&saved, 201).await.unwrap();
        store.update_run_status(&run.run_id, StateMachineRunStatus::Aborted, None, None, 202, Some(202)).await.unwrap();
    }
}

#[tokio::test]
async fn terminal_history_repair_is_batched_durable_and_retries_failed_confirmation() {
    let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let path = std::env::temp_dir().join(format!("bcs-history-repair-{}-{stamp}.sqlite", std::process::id()));
    let ids = (0..32).map(|i| format!("repair-{i:02}")).collect::<Vec<_>>();
    {
        let inner = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
        bootstrap_migrations::run_sqlite_migrations(inner.as_ref()).await.unwrap();
        let db = Arc::new(HistoryCostDb { inner: inner.clone(), calls: Mutex::new(Vec::new()) });
        let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
        for id in &ids {
            let mut run = test_run(); run.run_id = id.clone(); run.status = StateMachineRunStatus::Completed;
            store.create_run(run, Vec::new()).await.unwrap();
        }
        db.calls.lock().await.clear();
        assert_eq!(store.list_unrepaired_history_runs(&ids).await.unwrap(), ids);
        assert_eq!(&*db.calls.lock().await, &[("query", 0)]);
        for id in &ids[..16] { store.confirm_terminal_history_repair(id, 200).await.unwrap(); }
        db.calls.lock().await.clear();
        assert_eq!(store.list_unrepaired_history_runs(&ids).await.unwrap(), ids[16..]);
        assert_eq!(&*db.calls.lock().await, &[("query", 16)], "one primary-key batch, no Run or producer reads");
        inner.execute(DbStatement::new("CREATE TRIGGER reject_history_repair BEFORE INSERT ON bcs_collaboration_delivery_checkpoints WHEN NEW.operation_kind='history_repair' BEGIN SELECT RAISE(ABORT,'injected repair confirmation failure'); END")).await.unwrap();
        assert!(store.confirm_terminal_history_repair(&ids[16], 200).await.is_err());
        assert_eq!(store.list_unrepaired_history_runs(&ids).await.unwrap(), ids[16..]);
        inner.execute(DbStatement::new("DROP TRIGGER reject_history_repair")).await.unwrap();
        for id in &ids[16..] { store.confirm_terminal_history_repair(id, 200).await.unwrap(); }
    }
    {
        let inner = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
        let db = Arc::new(HistoryCostDb { inner, calls: Mutex::new(Vec::new()) });
        let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
        assert!(store.list_unrepaired_history_runs(&ids).await.unwrap().is_empty());
        assert_eq!(&*db.calls.lock().await, &[("query", 32)], "completed Runs need one bounded marker lookup after restart");
        db.calls.lock().await.clear();
        assert!(store.list_unrepaired_history_runs(&[]).await.unwrap().is_empty());
        assert!(store.list_unrepaired_history_runs(&vec!["run".into(); 33]).await.is_err());
        assert!(db.calls.lock().await.is_empty());
        let other = MySqlCollaborationStore::sqlite(db, "other".into());
        assert_eq!(other.list_unrepaired_history_runs(&ids).await.unwrap(), ids);
        assert!(other.confirm_terminal_history_repair(&ids[0], 300).await.is_err());
    }
    std::fs::remove_file(path).unwrap();
}

#[tokio::test]
async fn sqlite_provider_outputs_accept_the_persisted_dispatch_phase() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    provider_output_contract(&store, db.as_ref()).await;
}
