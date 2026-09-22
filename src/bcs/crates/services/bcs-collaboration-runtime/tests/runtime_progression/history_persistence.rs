use super::*;
use bcs_service_api::port::repo::collaboration_history::StateMachineHistoryIdentity;
use std::sync::atomic::{AtomicU8, AtomicUsize, Ordering};

#[derive(Default)]
struct HistoryMessages {
    inner: MemoryMessageRepo,
    fail: AtomicU8,
    writes: AtomicUsize,
}
#[async_trait]
impl MessageRepoPort for HistoryMessages {
    async fn append_message(&self, m: NewMessage) -> Result<PersistedMessage, MessageRepoError> {
        self.inner.append_message(m).await
    }
    async fn append_message_with_id(
        &self,
        id: String,
        m: NewMessage,
    ) -> Result<PersistedMessage, MessageRepoError> {
        self.writes.fetch_add(1, Ordering::SeqCst);
        let mode = self.fail.load(Ordering::SeqCst);
        if (mode == 1 && m.message_type == "state_machine_output") || (mode == 5 && m.message_type == "chat") {
            return Err(MessageRepoError::StorageError(
                "injected history write failure".into(),
            ));
        }
        let row = self.inner.append_message_with_id(id, m).await?;
        if mode == 2 && row.message_type == "state_machine_output" {
            return Err(MessageRepoError::StorageError(
                "injected response lost after commit".into(),
            ));
        }
        if mode == 3 && row.message_type == "state_machine_human_input_prompt" {
            return Err(MessageRepoError::StorageError(
                "injected prompt response lost".into(),
            ));
        }
        Ok(row)
    }
    async fn query_messages(&self, q: MessageQuery) -> Result<MessagePage, MessageRepoError> {
        self.inner.query_messages(q).await
    }
    async fn list_state_machine_history(&self, g: &str, s: &str, v: Option<HumanMessageView>, b: Option<(u64, i64)>, l: u32) -> Result<MessagePage, MessageRepoError> {
        if self.fail.load(Ordering::SeqCst) == 4 {
            return Err(MessageRepoError::StorageError("injected history read failure".into()));
        }
        self.inner.list_state_machine_history(g, s, v, b, l).await
    }
    async fn get_message_by_id(
        &self,
        s: &str,
        id: &str,
    ) -> Result<Option<PersistedMessage>, MessageRepoError> {
        self.inner.get_message_by_id(s, id).await
    }
    async fn get_state_machine_messages_by_keys(
        &self,
        s: &str,
        keys: &[String],
    ) -> Result<Vec<PersistedMessage>, MessageRepoError> {
        self.inner.get_state_machine_messages_by_keys(s, keys).await
    }
    async fn get_current_seq(&self, s: &str) -> Result<i64, MessageRepoError> {
        self.inner.get_current_seq(s).await
    }
}
struct HistoryHarness {
    runtime: CollaborationRuntime,
    store: Arc<MemoryCollaborationStore>,
    messages: Arc<HistoryMessages>,
    groups: Arc<GroupStore>,
    sessions: Arc<SessionManagementServiceImpl>,
    delivery: Arc<RecordingDelivery>,
}

#[path = "history_read.rs"]
mod history_read;
impl HistoryHarness {
    async fn new(judge: Arc<dyn JudgeEvaluatorPort>) -> Self {
        let messages = Arc::new(HistoryMessages::default());
        let groups = Arc::new(GroupStore::new());
        groups.upsert(state_machine_test_group()).await.unwrap();
        let session_repo = Arc::new(MemorySessionRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            session_repo.clone(),
            Arc::new(MemoryGroupRepo::new()),
        ));
        let store = Arc::new(MemoryCollaborationStore::new().with_session_repo(session_repo));
        let delivery = Arc::new(RecordingDelivery::default());
        let runtime = CollaborationRuntime::new(
            store.clone(),
            store.clone(),
            store.clone(),
            store.clone(),
            groups.clone(),
            sessions.clone(),
            delivery.clone(),
            judge,
        )
        .with_message_repo(messages.clone())
        .with_history_persistence(true)
        .with_loop_execution();
        Self {
            runtime,
            store,
            messages,
            groups,
            sessions,
            delivery,
        }
    }
    fn restart(&mut self, enabled: bool) {
        self.runtime = CollaborationRuntime::new(
            self.store.clone(),
            self.store.clone(),
            self.store.clone(),
            self.store.clone(),
            self.groups.clone(),
            self.sessions.clone(),
            self.delivery.clone(),
            noop_judge(),
        )
        .with_message_repo(self.messages.clone())
        .with_history_persistence(enabled)
        .with_loop_execution();
    }
    async fn start(&self, yaml: String, human: bool) -> StateMachineRun {
        self.runtime
            .start_state_machine_run(start(yaml, human))
            .await
            .unwrap()
            .view
            .run
    }
}
fn start(yaml: String, human: bool) -> StartStateMachineRunCommand {
    StartStateMachineRunCommand {
        group_id: "group-1".into(),
        session_id: None,
        definition_yaml: Some(yaml),
        definition: None,
        definition_ref: None,
        participant_bindings: None,
        opening_message_override: None,
        input: json!({}),
        caller_id: human.then(|| "human_1001".into()),
        authenticated_human: human.then(|| AuthenticatedHumanCaller {
            actor_id: "human_1001".into(),
            display_name: Some("Reviewer".into()),
        }),
    }
}
fn response(run: &StateMachineRun, text: &str) -> RespondHumanNodeCommand {
    RespondHumanNodeCommand {
        run_id: run.run_id.clone(),
        node_id: "review".into(),
        caller_actor_id: "human_1001".into(),
        content: text.into(),
        source: HumanResponseSource::Http,
    }
}
fn identity(
    run: &StateMachineRun,
    node: &str,
    attempt: i32,
    event: &str,
) -> StateMachineHistoryIdentity {
    StateMachineHistoryIdentity {
        session_id: run.session_id.clone(),
        run_id: run.run_id.clone(),
        node_id: node.into(),
        attempt,
        event: event.into(),
    }
}
async fn output(
    h: &HistoryHarness,
    run: &StateMachineRun,
    node: &str,
) -> bcs_service_api::port::repo::StateMachineHistoryCheckpoint {
    h.store
        .get_history_message(&identity(run, node, 0, "output"))
        .await
        .unwrap()
        .unwrap()
}
async fn pending(h: &HistoryHarness) -> usize {
    h.store
        .list_history_messages_pending(None, 100)
        .await
        .unwrap()
        .checkpoints
        .len()
}

#[tokio::test]
async fn human_prompt_audience_is_frozen_and_full_view_keeps_existing_shape() {
    let h = HistoryHarness::new(noop_judge()).await;
    let run = h.start(frontend_human_input_yaml(), true).await;
    h.sessions
        .add_participant(
            &run.session_id,
            Participant::human("human_late", ParticipantRole::Observer),
        )
        .await
        .unwrap();
    h.runtime
        .respond_human_node(response(&run, "accepted human text"))
        .await
        .unwrap();
    assert_eq!(pending(&h).await, 0);
    let cp = output(&h, &run, "review").await;
    assert!(cp.delivered_at_ms.is_some());
    assert_eq!(cp.payload.message.content["text"], "accepted human text");
    let full = h
        .runtime
        .get_state_machine_session_history(&run.session_id, 20, None)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(full.messages.len(), 2);
    assert!(full.messages.iter().all(|m|m.metadata.as_ref().unwrap()["state_machine"]["event"]!="human_input_prompt"));
    for (actor, expected) in [("human_1001", 3), ("human_late", 1)] {
        let page = h
            .runtime
            .get_state_machine_session_history_for_view(
                &run.session_id,
                20,
                None,
                HumanMessageView {
                    actor_id: actor.into(),
                    scope: MessageViewScope::Participant,
                    allow_legacy_unclassified_chat: false,
                },
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(page.messages.len(), expected);
    }
}

#[tokio::test]
async fn accepted_human_output_survives_write_failure_and_lost_ack_then_recovers_with_switch_off() {
    for mode in [1, 2] {
        let mut h = HistoryHarness::new(noop_judge()).await;
        let run = h.start(human_input_yaml(), true).await;
        h.messages.fail.store(mode, Ordering::SeqCst);
        assert!(
            h.runtime
                .respond_human_node(response(&run, "accepted once"))
                .await
                .is_err()
        );
        let node = h
            .store
            .get_node_run(&run.run_id, "review")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(node.status, StateMachineNodeStatus::Running);
        assert_eq!(node.artifact_text.as_deref(), Some("accepted once"));
        assert_eq!(pending(&h).await, 1);
        for _ in 0..3 {
            let page = h
                .runtime
                .recover_state_machine_history(None, 100)
                .await
                .unwrap();
            assert_eq!(page.failures.len(), 1);
        }
        let before = h.messages.get_current_seq(&run.session_id).await.unwrap();
        h.messages.fail.store(0, Ordering::SeqCst);
        h.restart(false);
        let page = h
            .runtime
            .recover_state_machine_history(None, 100)
            .await
            .unwrap();
        assert_eq!(page.reconciled, 1);
        assert!(page.failures.is_empty());
        assert_eq!(
            h.messages.get_current_seq(&run.session_id).await.unwrap(),
            before + if mode == 1 { 1 } else { 0 }
        );
        let page = h
            .runtime
            .recover_state_machine_progression(None, 100)
            .await
            .unwrap();
        assert!(page.failures.is_empty(), "{:?}", page.failures);
        assert_eq!(
            h.store.get_run(&run.run_id).await.unwrap().unwrap().status,
            StateMachineRunStatus::Completed
        );
        assert_eq!(pending(&h).await, 0);
        assert!(h.delivery.commands.lock().await.is_empty());
        let full = h
            .runtime
            .get_state_machine_session_history(&run.session_id, 20, None)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            full.messages
                .iter()
                .filter(|m| m.content == "accepted once")
                .count(),
            1
        );
    }
}

#[tokio::test]
async fn prompt_acceptance_survives_restart_without_new_write_permission() {
    let mut h = HistoryHarness::new(noop_judge()).await;
    h.messages.fail.store(3, Ordering::SeqCst);
    assert!(
        h.runtime
            .start_state_machine_run(start(human_input_yaml(), true))
            .await
            .is_err()
    );
    assert_eq!(pending(&h).await, 1);
    let run = h.store.list_running_runs(None, 10).await.unwrap().remove(0);
    assert_eq!(
        h.store
            .get_node_run(&run.run_id, "review")
            .await
            .unwrap()
            .unwrap()
            .status,
        StateMachineNodeStatus::Running
    );
    let seq = h.messages.get_current_seq(&run.session_id).await.unwrap();
    h.messages.fail.store(0, Ordering::SeqCst);
    h.restart(false);
    assert_eq!(
        h.runtime
            .recover_state_machine_history(None, 100)
            .await
            .unwrap()
            .reconciled,
        1
    );
    assert_eq!(
        h.messages.get_current_seq(&run.session_id).await.unwrap(),
        seq
    );
}

#[tokio::test]
async fn judging_input_is_already_in_messages_before_judge_and_survives_cancel() {
    let judge = Arc::new(BlockingJudge::new("approved"));
    let h = HistoryHarness::new(judge.clone()).await;
    let run = h.start(judged_human_input_yaml(), true).await;
    let future = h
        .runtime
        .respond_human_node(response(&run, "frozen judge input"));
    tokio::pin!(future);
    tokio::select! {result=&mut future=>panic!("unexpected early result {result:?}"), _=judge.started.notified()=>{}}
    let cp = output(&h, &run, "review").await;
    assert!(cp.delivered_at_ms.is_some());
    assert!(
        h.messages
            .get_message_by_id(&run.session_id, &cp.payload.message_id)
            .await
            .unwrap()
            .is_some()
    );
    h.runtime
        .cancel_state_machine_run(bcs_service_api::CancelStateMachineRunCommand {
            run_id: run.run_id.clone(),
            reason: None,
        })
        .await
        .unwrap();
    judge.release.notify_one();
    let _ = future.await;
    assert_eq!(
        output(&h, &run, "review").await.payload.message.created_at,
        cp.payload.message.created_at
    );
    assert_eq!(judge.requests.lock().await.len(), 1);
}

#[tokio::test]
async fn terminal_cancel_does_not_discard_pending_output_or_replay_delivery() {
    let mut h = HistoryHarness::new(noop_judge()).await;
    let run = h.start(single_node_yaml(), false).await;
    let delivery = h.delivery.commands.lock().await[0].run_id.clone();
    h.messages.fail.store(1, Ordering::SeqCst);
    let cmd = bcs_service_api::HandleBotTerminalEventCommand {
        bot_id: "driver-bot".into(),
        run_id: delivery,
        event_type: "chat.event".into(),
        state: ChatEventState::Final,
        bcs_session_id: Some(run.session_id.clone()),
        event_payload: json!({"message":{"content":[{"type":"text","text":"accepted Bot output"}]}}),
    };
    assert!(
        h.runtime
            .handle_bot_terminal_event(cmd.clone())
            .await
            .is_err()
    );
    assert_eq!(pending(&h).await, 1);
    h.runtime
        .cancel_state_machine_run(bcs_service_api::CancelStateMachineRunCommand {
            run_id: run.run_id.clone(),
            reason: None,
        })
        .await
        .unwrap();
    h.messages.fail.store(0, Ordering::SeqCst);
    let cleanup = h.runtime.cleanup_state_machine_terminal_work(None, 32).await.unwrap();
    assert!(cleanup.failures.is_empty());
    assert!(h.store.list_unrepaired_history_runs(&[run.run_id.clone()]).await.unwrap().is_empty());
    assert_eq!(pending(&h).await, 1, "opening repair never confirms pending node output");
    h.restart(false);
    assert_eq!(
        h.runtime
            .recover_state_machine_history(None, 100)
            .await
            .unwrap()
            .reconciled,
        1
    );
    h.runtime.handle_bot_terminal_event(cmd).await.unwrap();
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(pending(&h).await, 0);
}

#[tokio::test]
async fn enabling_does_not_overwrite_legacy_accepted_input_without_timestamp() {
    let mut h = HistoryHarness::new(noop_judge()).await;
    h.restart(false);
    let run = h.start(human_input_yaml(), true).await;
    h.store
        .record_human_response_if_running(
            &run.run_id,
            "review",
            0,
            "legacy accepted".into(),
            "human_1001".into(),
        )
        .await
        .unwrap();
    h.restart(true);
    let error = h
        .runtime
        .respond_human_node(response(&run, "replacement"))
        .await
        .unwrap_err();
    assert!(error.to_string().contains("missing_source_evidence"));
    assert_eq!(
        h.store
            .get_node_run(&run.run_id, "review")
            .await
            .unwrap()
            .unwrap()
            .artifact_text
            .as_deref(),
        Some("legacy accepted")
    );
    assert!(
        h.store
            .get_history_message(&identity(&run, "review", 0, "output"))
            .await
            .unwrap()
            .is_none()
    );
}

#[path = "../../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

#[tokio::test]
async fn sqlite_reopen_recovers_accepted_output_after_message_transaction_failure() {
    use bcs_collaboration_store::MySqlCollaborationStore;
    use bcs_db_api::{DbPlugin, DbStatement};
    use bcs_db_local::LocalSqliteDbPlugin;
    use bcs_message_store::MySqlMessageStore;
    use bcs_session_store::MySqlSessionStore;
    let path = std::env::temp_dir().join(format!("bcs-history-{}.sqlite", uuid::Uuid::new_v4()));
    let groups = Arc::new(GroupStore::new());
    groups.upsert(state_machine_test_group()).await.unwrap();
    let delivery = Arc::new(RecordingDelivery::default());
    let run = {
        let db = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
        bootstrap_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        let store = Arc::new(MySqlCollaborationStore::sqlite(db.clone(), "test".into()));
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            Arc::new(MySqlSessionStore::sqlite(db.clone(), "test".into())),
            Arc::new(MemoryGroupRepo::new()),
        ));
        let runtime = CollaborationRuntime::new(
            store.clone(),
            store.clone(),
            store.clone(),
            store,
            groups.clone(),
            sessions,
            delivery.clone(),
            noop_judge(),
        )
        .with_message_repo(Arc::new(MySqlMessageStore::sqlite(
            db.clone(),
            "test".into(),
        )))
        .with_history_persistence(true);
        let run = runtime
            .start_state_machine_run(start(human_input_yaml(), true))
            .await
            .unwrap()
            .view
            .run;
        db.execute(DbStatement::new("CREATE TRIGGER reject_output BEFORE INSERT ON bcs_messages WHEN NEW.message_type='state_machine_output' BEGIN SELECT RAISE(ABORT,'injected output failure'); END")).await.unwrap();
        assert!(
            runtime
                .respond_human_node(response(&run, "durable input"))
                .await
                .is_err()
        );
        run
    };
    {
        let db = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
        db.execute(DbStatement::new("DROP TRIGGER reject_output"))
            .await
            .unwrap();
        let store = Arc::new(MySqlCollaborationStore::sqlite(db.clone(), "test".into()));
        let messages = Arc::new(MySqlMessageStore::sqlite(db.clone(), "test".into()));
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            Arc::new(MySqlSessionStore::sqlite(db, "test".into())),
            Arc::new(MemoryGroupRepo::new()),
        ));
        let runtime = CollaborationRuntime::new(
            store.clone(),
            store.clone(),
            store.clone(),
            store.clone(),
            groups,
            sessions,
            delivery.clone(),
            noop_judge(),
        )
        .with_message_repo(messages.clone());
        assert_eq!(
            runtime
                .recover_state_machine_history(None, 100)
                .await
                .unwrap()
                .reconciled,
            1
        );
        let recovered = runtime
            .recover_state_machine_progression(None, 100)
            .await
            .unwrap();
        assert!(recovered.failures.is_empty(), "{:?}", recovered.failures);
        assert_eq!(
            store.get_run(&run.run_id).await.unwrap().unwrap().status,
            StateMachineRunStatus::Completed
        );
        assert_eq!(messages.get_current_seq(&run.session_id).await.unwrap(), 3);
            let history = runtime
            .get_state_machine_session_history(&run.session_id, 20, None)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            history
                .messages
                .iter()
                .filter(|m| m.content == "durable input")
                .count(),
            1
        );
        assert!(delivery.commands.lock().await.is_empty());
    }
    std::fs::remove_file(path).unwrap();
}

#[tokio::test]
async fn local_opening_and_delivered_publication_repair_never_acknowledges_network_again() {
    use bcs_service_api::{
        StateMachineChatResultOutcome, StateMachineChatResultPayload, StateMachineChatResultStatus,
    };
    let mut h = HistoryHarness::new(noop_judge()).await;
    let mut command = start(single_node_yaml(), false);
    command.caller_id = Some("driver-bot".into());
    let run = h
        .runtime
        .start_state_machine_run(command)
        .await
        .unwrap()
        .view
        .run;
    assert!(
        h.store
            .complete_node_attempt(
                &run.run_id,
                "answer",
                0,
                "complete".into(),
                "published fact".into(),
                None,
                99
            )
            .await
            .unwrap()
    );
    let cmd = StateMachineResultPublishCommand {
        run_id: run.run_id.clone(),
        group_id: run.group_id.clone(),
        session_id: run.session_id.clone(),
        sender_bot_id: "driver-bot".into(),
        content: "published fact".into(),
        created_at_ms: 100,
    };
    assert!(
        h.store
            .save_chat_result(StateMachineChatResultPayload {
                command: cmd,
                deadline_ms: 1000
            })
            .await
            .unwrap()
    );
    let claim = h
        .store
        .claim_chat_result(&run.run_id, "publisher".into(), 100, 200)
        .await
        .unwrap()
        .unwrap();
    assert!(h.store.begin_chat_result_send(&claim, 101).await.unwrap());
    assert!(
        h.store
            .finish_chat_result(&claim, StateMachineChatResultOutcome::Published, 102)
            .await
            .unwrap()
    );
    h.store
        .update_run_status(
            &run.run_id,
            StateMachineRunStatus::Completed,
            Some("published fact".into()),
            None,
            103,
            Some(103),
        )
        .await
        .unwrap();
    let original = h.store.get_chat_result(&run.run_id).await.unwrap().unwrap();
    // A missing local projection is repaired from original producer payloads.
    h.messages = Arc::new(HistoryMessages::default());
    h.restart(true);
    h.messages.fail.store(5, Ordering::SeqCst);
    let failed = h.runtime.cleanup_state_machine_terminal_work(None, 32).await.unwrap();
    assert_eq!(failed.failures.len(), 1);
    assert_eq!(h.store.list_unrepaired_history_runs(&[run.run_id.clone()]).await.unwrap(), vec![run.run_id.clone()]);
    assert_eq!(h.messages.get_current_seq(&run.session_id).await.unwrap(), 1);
    h.messages.fail.store(0, Ordering::SeqCst);
    h.restart(true);
    let page = h
        .runtime
        .cleanup_state_machine_terminal_work(None, 32)
        .await
        .unwrap();
    assert!(page.failures.is_empty(), "{:?}", page.failures);
    let saved = h.store.get_chat_result(&run.run_id).await.unwrap().unwrap();
    assert_eq!(saved.status, StateMachineChatResultStatus::Delivered);
    assert_eq!(saved.delivered_at_ms, original.delivered_at_ms);
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(
        h.messages.get_current_seq(&run.session_id).await.unwrap(),
        2
    );
    let writes = h.messages.writes.load(Ordering::SeqCst);
    h.restart(true);
    for _ in 0..3 {
        let page = h.runtime.cleanup_state_machine_terminal_work(None, 32).await.unwrap();
        assert!(page.failures.is_empty());
    }
    assert_eq!(h.messages.writes.load(Ordering::SeqCst), writes, "completed repair must not probe or append messages on later sweeps");
    assert_eq!(
        h.messages.get_current_seq(&run.session_id).await.unwrap(),
        2
    );
}
