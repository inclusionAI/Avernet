use super::*;
use bcs_channel::BcsChannelService;
use bcs_channel_api::{ChannelProvider, ChannelProviderRegistry, ChannelProviderResult};
use bcs_channel_store::{DbHumanInputRequestStore, MemoryChannelBindingRepo, MemoryConversationSessionRepo, MemoryImParticipantRepo};
use bcs_domain::{BindingStatus, BindingTarget, ChannelBinding, HumanInputRequestStatus as Status, Visibility};
use bcs_service_api::port::channel_delivery::{ChannelBindingRef, ChannelDeliveryPort, ChannelDeliveryResult, ChannelOutboundEvent};
use bcs_service_api::port::repo::{ChannelBindingRepoPort, HumanInputRequestRepoPort};
use bcs_db_api::DbValue;
use bcs_test_support::{NoopBotRegistryCoreService, NoopGroupCoreService, NoopMessageFlowService, NoopSystemMessageService};

struct Delivery { db: Arc<LocalSqliteDbPlugin>, case: String, prepare: bool, entered: Notify }
#[async_trait]
impl ChannelDeliveryPort for Delivery {
    async fn is_available(&self, _: &ChannelBindingRef) -> bool { !(self.prepare && matches!(self.case.as_str(), "pending" | "expired")) }
    async fn deliver_event(&self, event: ChannelOutboundEvent) -> ServiceResult<ChannelDeliveryResult> {
        self.db.execute(DbStatement::with_params("INSERT INTO test_human_sends (request_id, text) VALUES (?, ?)",
            vec![DbValue::from(event.run_id), DbValue::from(event.text.unwrap())])).await.map_err(|e| ServiceError::InternalError(e.to_string()))?;
        if self.prepare && self.case == "unknown" { self.entered.notify_one(); std::future::pending::<()>().await; }
        Ok(ChannelDeliveryResult { delivered: true, provider_message_ref: Some("original-provider-ref".into()), error: None })
    }
}
struct Provider(Arc<Delivery>);
#[async_trait]
impl ChannelProvider for Provider {
    fn channel_type(&self) -> &'static str { "dingtalk" }
    fn validate_config(&self, _: &Value) -> ChannelProviderResult<()> { Ok(()) }
    fn redact_config(&self, config: &Value) -> Value { config.clone() }
    fn resolve_direct_recipient(&self, _: &str) -> ChannelProviderResult<Option<String>> { Ok(Some("saved-user".into())) }
    fn delivery(&self) -> Arc<dyn ChannelDeliveryPort> { self.0.clone() }
    fn http_ingress(&self) -> Option<Arc<dyn bcs_channel_api::ChannelHttpIngressPort>> { None }
    fn stream_lifecycle(&self, _: Arc<dyn bcs_channel_api::ChannelInboundSink>) -> Option<Arc<dyn bcs_service_api::lifecycle::ServiceLifecycle>> { None }
}
struct Outbound { channel: Arc<BcsChannelService>, missing: bool }
#[async_trait]
impl SessionChannelOutboundPort for Outbound {
    async fn publish_human_input_ready(&self, event: HumanInputReadyEvent) -> ServiceResult<SessionChannelDeliveryOutcome> {
        if self.missing { return Err(ServiceError::InternalError("interrupted before request save".into())); }
        self.channel.publish_human_input_ready(event).await
    }
    async fn recover_human_input_requests(&self, run: &str, session: &str) -> ServiceResult<Vec<String>> { self.channel.recover_human_input_requests(run, session).await }
}

async fn child(path: &str, case: &str, prepare: bool) {
    let db = Arc::new(LocalSqliteDbPlugin::new_file(path).unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new("CREATE TABLE IF NOT EXISTS test_human_sends (request_id TEXT PRIMARY KEY, text TEXT NOT NULL)")).await.unwrap();
    let store = Arc::new(MySqlCollaborationStore::sqlite(db.clone(), "test".into()));
    let session_repo = Arc::new(MySqlSessionStore::sqlite(db.clone(), "test".into()));
    let sessions = Arc::new(SessionManagementServiceImpl::new(session_repo.clone(), Arc::new(MemoryGroupRepo::new())));
    let group = Arc::new(GroupStore::new()); group.upsert(test_group()).await.unwrap();
    let bot_delivery = Arc::new(RecordingDelivery::default());
    let make_runtime = || CollaborationRuntime::new(store.clone(), store.clone(), store.clone(), store.clone(),
        group.clone(), sessions.clone(), bot_delivery.clone(), noop_judge())
        .with_message_repo(Arc::new(MySqlMessageStore::sqlite(db.clone(), "test".into()))).with_experimental_fixed_loop_execution();
    let bindings = Arc::new(MemoryChannelBindingRepo::new("test"));
    bindings.create(ChannelBinding { id: "human-binding".into(), channel_type: "dingtalk".into(), account_ref: "test-account".into(),
        target: BindingTarget::Group { group_id: "group-1".into() }, group_chat_scope: None, outbound_visibility: Visibility::FullTranscript,
        env: "test".into(), status: BindingStatus::Active, created_by: None, config: json!({}) }).await.unwrap();
    let requests = Arc::new(DbHumanInputRequestStore::sqlite(db.clone()));
    let delivery = Arc::new(Delivery { db: db.clone(), case: case.into(), prepare, entered: Notify::new() });
    let channel = Arc::new(BcsChannelService::new(bindings, Arc::new(MemoryConversationSessionRepo::new()), Arc::new(MemoryImParticipantRepo::new()),
        requests.clone(), session_repo, Arc::new(NoopMessageFlowService), Arc::new(NoopSystemMessageService), Arc::new(make_runtime()),
        Arc::new(NoopGroupCoreService), Arc::new(NoopBotRegistryCoreService),
        Arc::new(ChannelProviderRegistry::new(vec![Arc::new(Provider(delivery.clone()))]).unwrap()), "test", Arc::new(bcs_protocol::now_ms), Arc::new(|| "unused".into())));
    let runtime = make_runtime().with_session_channel_outbound(Arc::new(Outbound { channel: channel.clone(), missing: prepare && case == "missing" }));
    if prepare {
        if case == "ack" { db.execute(DbStatement::new("CREATE TRIGGER reject_ack BEFORE UPDATE ON bcs_human_input_requests WHEN NEW.status = 'active' BEGIN SELECT RAISE(ABORT, 'injected ACK failure'); END")).await.unwrap(); }
        if case == "unknown" {
            tokio::select! {
                result = runtime.start_state_machine_run(command(loop_yaml(2, false, true, 1), true)) => panic!("send was not interrupted: {result:?}"),
                () = delivery.entered.notified() => {}
            }
        } else { runtime.start_state_machine_run(command(loop_yaml(2, false, true, 1), true)).await.unwrap(); }
        if case == "ack" { db.execute(DbStatement::new("DROP TRIGGER reject_ack")).await.unwrap(); }
        if case == "expired" {
            db.execute(DbStatement::new("UPDATE bcs_human_input_requests SET deadline_ms = 1")).await.unwrap();
            db.execute(DbStatement::new("UPDATE bcs_state_machine_node_runs SET timeout_deadline_ms = 1 WHERE status = 'running'")).await.unwrap();
        }
        db.execute(DbStatement::new("DELETE FROM bcs_collaboration_definitions WHERE env = 'test'")).await.unwrap();
    } else {
        let run_id = bcs_db_api::db_get_column::<String>(&db.query(DbStatement::new("SELECT run_id FROM bcs_state_machine_runs")).await.unwrap()[0], "run_id").unwrap();
        let before = requests.list_by_run(&run_id).await.unwrap();
        // Two independent application instances use the same persisted barrier.
        let competitor = make_runtime().with_session_channel_outbound(Arc::new(Outbound { channel, missing: false }));
        let (a,b) = tokio::join!(runtime.recover_state_machine_progression(None, 32), competitor.recover_state_machine_progression(None, 32));
        for page in [a.unwrap(), b.unwrap()] { assert!(page.failures.is_empty(), "{case}: {:?}", page.failures); }
        let saved = requests.list_by_run(&run_id).await.unwrap(); assert_eq!(saved.len(), 1);
        assert_eq!(saved[0].status, match case { "unknown" | "ack" => Status::Notifying, "expired" => Status::Expired, _ => Status::Active });
        if let Some(before) = before.first() {
            assert_eq!(before.notification_text, saved[0].notification_text); assert_eq!(before.deadline_ms, saved[0].deadline_ms);
            assert_eq!(before.request_id, saved[0].request_id); assert_eq!(before.im_user_id, saved[0].im_user_id);
        }
        let sent = db.query(DbStatement::new("SELECT text FROM test_human_sends")).await.unwrap();
        assert_eq!(sent.len(), usize::from(case != "expired"));
        if let Some(sent) = sent.first() { assert_eq!(bcs_db_api::db_get_column::<String>(sent, "text").unwrap(), saved[0].notification_text); }
        assert!(bot_delivery.commands.lock().await.is_empty());
        assert_eq!(store.get_run(&run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
    }
}

#[tokio::test]
async fn human_notification_scanner_recovers_across_process_restart() {
    if let Ok(path) = std::env::var("BCS_HUMAN_NOTIFICATION_TEST_DB") {
        child(&path, &std::env::var("BCS_HUMAN_NOTIFICATION_TEST_CASE").unwrap(), std::env::var("BCS_HUMAN_NOTIFICATION_TEST_PHASE").unwrap() == "prepare").await; return;
    }
    let directory = TempDatabaseDirectory(std::env::temp_dir().join(format!("bcs-human-notification-{}", uuid::Uuid::new_v4())));
    for case in ["missing", "pending", "unknown", "ack", "active", "expired"] {
        for phase in ["prepare", "recover"] {
            let output = std::process::Command::new(std::env::current_exe().unwrap()).args(["--exact",
                "fixed_loop_tests::recovery_tests::sqlite_restart_tests::human_notification_tests::human_notification_scanner_recovers_across_process_restart", "--nocapture"])
                .env("BCS_HUMAN_NOTIFICATION_TEST_DB", directory.0.join(format!("{case}.sqlite")))
                .env("BCS_HUMAN_NOTIFICATION_TEST_CASE", case).env("BCS_HUMAN_NOTIFICATION_TEST_PHASE", phase).output().unwrap();
            assert!(output.status.success(), "{case}/{phase}: {} {}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        }
    }
}
