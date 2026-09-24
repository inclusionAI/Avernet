//! Direct A2A contracts run against memory and real SQLite transaction stores.
use std::{sync::Arc, time::Duration};
use bcs_service_api::*;
use bcs_service_api::port::repo::{SessionRepoPort, NewSessionParams, MessageRepoPort};
use bcs_service_api::port::repo::message_delivery::{MessageDeliveryRepoPort, DeliveryLookup};
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore, a2a_chat::{A2aChat, ChatRunStore}};
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_flow::delivery_policy::LiveDeliveryPolicy;
use bcs_message_flow::queued_group::QueuedGroupPreparation;
use bcs_message_flow::delivery_runtime::{DeliveryRuntime, DeliveryRuntimeConfig};
use serde_json::json;
use bcs_db_api::{DbPlugin, DbStatement};

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod migrations;
#[path = "support/direct_a2a_regressions.rs"]
mod regressions;

struct Harness {
    db: Option<Arc<ObservedDb>>,
    flow: Arc<BcsMessageFlow>,
    direct: Arc<A2aChat>,
    service: Arc<ManagedMessageDelivery>,
    policy: Arc<LiveDeliveryPolicy>,
    sessions: Arc<dyn SessionRepoPort>,
    messages: Arc<dyn MessageRepoPort>,
    support: support::FlowTestSupport,
}

impl Harness {
    async fn new(sql: bool) -> Self {
        Self::with_db(sql, None).await
    }

    async fn with_db(sql: bool, shared: Option<Arc<ObservedDb>>) -> Self {
        let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
        support.registry.set_visibility("bot-observer", "public").await;
        let mut observed_db = None;
        let (sessions, messages, deliveries, runs): (Arc<dyn SessionRepoPort>, Arc<dyn MessageRepoPort>, Arc<dyn MessageDeliveryRepoPort>, Arc<dyn ChatRunRepoPort>) = if sql {
            let db = shared.unwrap_or_else(|| Arc::new(ObservedDb::new()));
            observed_db = Some(db.clone());
            migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
            let sessions = Arc::new(bcs_session_store::MySqlSessionStore::sqlite(db.clone(), "dev".into()));
            let messages = Arc::new(bcs_message_store::MySqlMessageStore::sqlite(db.clone(), "dev".into()));
            let runs = Arc::new(bcs_chat_run_store::SqlChatRunRepo::new(db, bcs_db_api::DbSqlFlavor::Sqlite,
                Arc::new(bcs_cache_local::InMemoryCachePlugin::new()), "direct-test:".into(), 60_000, "dev".into()));
            (sessions, messages.clone(), messages, runs)
        } else {
            let sessions = Arc::new(bcs_session_store::MemorySessionRepo::new());
            let messages = Arc::new(bcs_message_store::MemoryMessageRepo::new().with_environment("dev".into()).with_session_registry(sessions.session_registry()));
            (sessions, messages.clone(), messages, Arc::new(bcs_chat_run_store::MemoryChatRunRepo::new()))
        };
        let session_service = Arc::new(bcs_session::SessionManagementServiceImpl::new(sessions.clone(), Arc::new(bcs_group_store::MemoryGroupRepo::new())));
        let policy = Arc::new(LiveDeliveryPolicy::new(deliveries.clone(), Default::default()).with_session_registry(Some(session_service.clone())));
        {
            let mut snapshot = policy.snapshot.write().await;
            snapshot.policy.flow_enabled.direct_a2a = true;
            snapshot.policy.defaults.mode = bcs_config_api::message_delivery::BotDeliveryMode::Enforce;
            snapshot.policy.defaults.max_running = 1;
            snapshot.policy.defaults.max_queued = 8;
        }
        policy.scheduler_available.store(true, std::sync::atomic::Ordering::SeqCst);
        let service = Arc::new(ManagedMessageDelivery::new(deliveries).with_policy(policy.clone()));
        let direct = Arc::new(A2aChat::new(support.bot_delivery.clone(), Arc::new(ChatRunStore::with_repo(runs)), 60_000,
            support.registry.clone(), Arc::new(bcs_test_support::NoopFriendCoreService)).with_session_management(session_service.clone()));
        let mut flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(), support.bot_delivery.clone(), support.frontend_delivery.clone())
            .with_message_repo(messages.clone()).with_managed_deliveries(service.clone()).with_session_management(session_service)
            .with_bot_run_context(Arc::new(MemoryBotRunContextStore::new())).with_direct_chat(direct.clone());
        flow.delivery_policy = Some(policy.clone());
        let flow = Arc::new(flow);
        flow.retain_terminal_events();
        Self { db: observed_db, flow, direct, service, policy, sessions, messages, support }
    }

    async fn submit(&self, id: &str, session: &str) -> ServiceResult<AsyncA2aChatAccepted> {
        self.direct.start_async_chat(AsyncA2aChatCommand {
            caller: caller(), target_bot_id: "bot-observer".into(), message: format!("request {id}"), from_actor_id: None,
            run_channel_from: None, authenticated_staff_id: None, run_id: id.into(), session_key: session.into(), timeout_ms: 60_000,
            client: Some("bcs-cli/test".into()), tags: vec![], response_mode: ChatResponseMode::Full,
            caller_wait_mode: Some("detach".into()), organization_code: None, provider_bypass_headers: vec![],
        }).await
    }

    async fn row(&self, run: &str) -> bcs_domain::message_delivery::PersistedMessageDelivery {
        self.service.lookup(DeliveryLookup::Id(run.into())).await.unwrap().remove(0)
    }

    async fn status(&self, run: &str) -> serde_json::Value {
        A2aChatService::get_run(self.direct.as_ref(), caller(), run).await.unwrap().response.unwrap()
    }

    fn runtime(&self) -> DeliveryRuntime {
        DeliveryRuntime { policy: Some(self.policy.clone()), service: self.service.clone(),
            preparation: Arc::new(QueuedGroupPreparation { flow: Arc::downgrade(&self.flow), deliveries: self.service.clone() }),
            transport: self.support.bot_delivery.clone(),
            config: DeliveryRuntimeConfig { max_safe_retries: 0, pause_dispatch: false, bots: Default::default(), tick: Duration::from_millis(10),
                expiry_tick: Duration::from_millis(20), io_timeout: Duration::from_secs(1), run_timeout: Duration::from_secs(30), cancel_timeout: Duration::from_secs(1),
                startup_recovery_grace: Duration::ZERO, max_tasks: 4, max_abort_tasks: 1 },
        }
    }
}

fn caller() -> CallerContext { CallerContext::Bot(BotActor { bot_uuid: "bot-driver".into() }) }

#[tokio::test]
async fn registry_claims_are_atomic_idempotent_and_survive_run_lifecycle() {
    for sql in [false, true] {
        let h = Harness::new(sql).await;
        bcs_test_support::contract::repo::message_delivery::direct_a2a_session_registry_contract_tests(h.sessions.as_ref()).await;
        let group = h.sessions.create("group-1", NewSessionParams { id: Some("group-1:87654321".into()), ..Default::default() }).await.unwrap();
        assert!(matches!(h.submit("collision", &group.id).await, Err(ServiceError::Conflict(_))));
        assert!(h.direct.run_store().get("collision").await.is_none());
        assert!(h.support.bot_delivery.frames().await.is_empty());
    }
}

#[tokio::test]
async fn durable_admission_sequences_capacity_cancel_and_legacy_drain() {
    for sql in [false, true] {
        let h = Harness::new(sql).await;
        h.policy.snapshot.write().await.policy.defaults.max_queued = 2;
        let (one, two) = tokio::join!(h.submit("one", "shared"), h.submit("two", "shared"));
        assert_eq!(one.unwrap().status, "pending"); assert_eq!(two.unwrap().status, "pending");
        assert!(h.support.bot_delivery.frames().await.is_empty());
        let mut seqs = vec![h.row("one").await.source_session_seq, h.row("two").await.source_session_seq]; seqs.sort();
        assert_eq!(seqs, vec![1,2]);
        let rejected = h.submit("three", "shared").await.unwrap();
        assert_eq!(rejected.status, "failed");
        assert_eq!(h.status("three").await["error_message"], "queue_capacity_exceeded");
        assert_eq!(h.sessions.session_registration("shared").await.unwrap().unwrap().current_msg_seq, Some(3));
        let row = h.row("one").await;
        let message = h.messages.get_message_by_id("shared", &row.source_message_id).await.unwrap().unwrap();
        assert!(message.group_id.is_empty()); assert_eq!(message.run_id, "one");
        assert_eq!(message.visibility_domain, Some(bcs_domain::MessageVisibilityDomain::DirectA2a));
        h.policy.snapshot.write().await.policy.flow_enabled.direct_a2a = false;
        assert!(matches!(h.submit("bypass", "shared").await, Err(ServiceError::Conflict(_))));
        let cancelled = A2aChatService::cancel_run(h.direct.as_ref(), caller(), "one").await.unwrap();
        assert_eq!(cancelled.status, "cancelled");
        assert!(h.support.bot_delivery.aborts().await.is_empty());
        assert_eq!(A2aChatService::cancel_run(h.direct.as_ref(), caller(), "one").await.unwrap().status, "cancelled");
    }
}

#[tokio::test]
async fn scheduler_preserves_session_fifo_and_projects_scoped_terminal_without_group_reply() {
    for sql in [false, true] {
        let h = Harness::new(sql).await;
        h.submit("first", "direct:stable").await.unwrap();
        h.submit("second", "direct:stable").await.unwrap();
        let (stop, receiver) = tokio::sync::watch::channel(false);
        let worker = tokio::spawn(h.runtime().run(receiver));
        tokio::time::timeout(Duration::from_secs(3), async {
            while h.support.bot_delivery.frames().await.is_empty() { tokio::time::sleep(Duration::from_millis(10)).await; }
        }).await.unwrap();
        assert_eq!(h.support.bot_delivery.frames().await.len(), 1);
        let row = h.row("first").await;
        assert_eq!(row.session_id, "direct:stable");
        assert_eq!(row.transport_context_json.as_ref().unwrap()["downstream_session_key"], "direct:stable");
        let event = BotEventCommand { bot_id: "bot-observer".into(), run_id: "first".into(), group_id: String::new(),
            event_type: "chat".into(), state: ChatEventState::Final, bcs_session_id: Some("direct:stable".into()),
            event_payload: json!({"message":{"role":"assistant","content":[{"type":"text","text":"answer"}]}}) };
        h.flow.handle_bot_event(event.clone()).await.unwrap();
        h.flow.handle_bot_event(event).await.unwrap();
        assert_eq!(h.status("first").await["state"], "completed");
        assert_eq!(h.status("first").await["content"], "answer");
        regressions::assert_context_cleaned(&h, &row).await;
        tokio::time::timeout(Duration::from_secs(3), async {
            while h.support.bot_delivery.frames().await.len() < 2 { tokio::time::sleep(Duration::from_millis(10)).await; }
        }).await.unwrap();
        assert!(h.support.frontend_delivery.events().await.is_empty());
        assert_eq!(h.service.snapshot(None).await.unwrap().len(), 2);
        stop.send(true).unwrap(); worker.await.unwrap().unwrap();
    }
}

struct ObservedDb {
    inner: bcs_db_local::LocalSqliteDbPlugin,
    queries: std::sync::Mutex<Vec<(String, usize)>>,
    fail_checkpoint_once: std::sync::atomic::AtomicBool,
    checkpoint_gate: std::sync::Mutex<Option<Arc<regressions::Pause>>>,
    delivery_read_gate: std::sync::Mutex<Option<Arc<regressions::Pause>>>,
    sequence_barrier: std::sync::Mutex<Option<(usize, Arc<tokio::sync::Barrier>)>>,
    admission_attempts: std::sync::atomic::AtomicUsize,
    admission_conflicts: std::sync::atomic::AtomicUsize,
    fail_admissions: std::sync::atomic::AtomicBool,
}
impl ObservedDb {
    fn new() -> Self {
        Self { inner: bcs_db_local::LocalSqliteDbPlugin::new().unwrap(), queries: Default::default(),
            fail_checkpoint_once: Default::default(), checkpoint_gate: Default::default(), delivery_read_gate: Default::default(),
            sequence_barrier: Default::default(), admission_attempts: Default::default(), admission_conflicts: Default::default(), fail_admissions: Default::default() }
    }
}
#[async_trait::async_trait]
impl DbPlugin for ObservedDb {
    async fn query(&self, stmt: DbStatement) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbRow>> {
        let rows = self.inner.query(stmt.clone()).await?;
        self.queries.lock().unwrap().push((stmt.sql().into(), rows.len()));
        if stmt.sql().starts_with("SELECT * FROM bcs_message_deliveries WHERE env = ? AND delivery_id = ?") {
            let gate = self.delivery_read_gate.lock().unwrap().take();
            if let Some(gate) = gate { gate.pause().await; }
        }
        Ok(rows)
    }
    async fn execute(&self, stmt: DbStatement) -> bcs_db_api::DbResult<bcs_db_api::DbExecuteResult> {
        if stmt.sql().starts_with("UPDATE bcs_chat_runs SET state")
            && self.fail_checkpoint_once.swap(false, std::sync::atomic::Ordering::SeqCst) {
            return Err(bcs_db_api::DbError::Backend("injected checkpoint outage".into()));
        }
        if stmt.sql().starts_with("UPDATE bcs_chat_runs SET state") {
            let gate = self.checkpoint_gate.lock().unwrap().take();
            if let Some(gate) = gate { gate.pause().await; }
        }
        self.inner.execute(stmt).await
    }
    async fn transaction(&self, steps: Vec<bcs_db_api::DbTransactionStep>) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbTransactionStepResult>> {
        use bcs_db_api::{DbTransactionStep as Step, DbError};
        use std::sync::atomic::Ordering::SeqCst;
        let sequence = matches!(steps.first(), Some(Step::Query(stmt)) if stmt.sql().starts_with("SELECT current_msg_seq FROM bcs_session_registry"));
        let admission = matches!(steps.first(), Some(Step::ExecuteChecked { statement, .. }) if statement.sql().starts_with("UPDATE bcs_session_registry SET current_msg_seq"));
        if admission {
            self.admission_attempts.fetch_add(1, SeqCst);
            if self.fail_admissions.load(SeqCst) { return Err(DbError::ConditionFailed { expected: 1, actual: 0 }); }
        }
        let result = self.inner.transaction(steps).await;
        if admission && matches!(&result, Err(DbError::ConditionFailed { .. })) { self.admission_conflicts.fetch_add(1, SeqCst); }
        if sequence {
            // Two independent stores both finish their primary sequence read
            // before either can commit admission. The loser must retry CAS.
            let barrier = {
                let mut slot = self.sequence_barrier.lock().unwrap();
                slot.as_mut().and_then(|(remaining, barrier)| {
                    if *remaining == 0 { None } else { *remaining -= 1; Some(barrier.clone()) }
                })
            };
            if let Some(barrier) = barrier { tokio::time::timeout(Duration::from_secs(5), barrier.wait()).await.unwrap(); }
        }
        result
    }
    async fn health_check(&self) -> bcs_db_api::DbResult<bcs_db_api::DbHealth> { self.inner.health_check().await }
}

#[tokio::test]
async fn recovery_reads_one_bounded_page_and_batch_instead_of_one_query_per_run() {
    let h = Harness::new(true).await;
    h.policy.snapshot.write().await.policy.defaults.max_queued = 32;
    for index in 0..17 { h.submit(&format!("run-{index:02}"), "recovery").await.unwrap(); }
    let db = h.db.as_ref().unwrap();
    db.queries.lock().unwrap().clear();
    A2aChatService::cleanup_expired(h.direct.as_ref(), 0, 60_000).await.unwrap();
    let queries = db.queries.lock().unwrap().clone();
    let scans: Vec<_> = queries.iter().filter(|(sql,_)| sql.contains("ORDER BY run_id LIMIT")).collect();
    assert_eq!(scans.len(), 1); assert_eq!(scans[0].1, 8);
    let lookups: Vec<_> = queries.iter().filter(|(sql,_)| sql.contains("delivery_id IN (")).collect();
    assert_eq!(lookups.len(), 1); assert_eq!(lookups[0].1, 8);
    assert_eq!(queries.len(), 3, "page, batch delivery lookup and legacy timeout scan only");
}

#[tokio::test]
async fn final_checkpoint_retries_storage_and_survives_a_fresh_cache() {
    let h = Harness::new(true).await;
    h.submit("restart", "restart-session").await.unwrap();
    let (stop, receiver) = tokio::sync::watch::channel(false);
    let worker = tokio::spawn(h.runtime().run(receiver));
    tokio::time::timeout(Duration::from_secs(3), async {
        while h.support.bot_delivery.frames().await.is_empty() { tokio::time::sleep(Duration::from_millis(10)).await; }
    }).await.unwrap();
    let mut event = BotEventCommand { bot_id:"bot-observer".into(), run_id:"restart".into(), group_id:String::new(),
        bcs_session_id:Some("wrong-session".into()), event_type:"chat".into(), state:ChatEventState::Final,
        event_payload:json!({"message":{"role":"assistant","content":[{"type":"text","text":"durable answer"}]}}) };
    assert!(h.flow.handle_bot_event(event.clone()).await.is_err());
    assert!(!h.row("restart").await.state.status.is_terminal());
    event.bcs_session_id = Some("restart-session".into());
    let db = h.db.as_ref().unwrap();
    db.fail_checkpoint_once.store(true, std::sync::atomic::Ordering::SeqCst);
    h.flow.handle_bot_event(event).await.unwrap();
    let fresh_repo = bcs_chat_run_store::SqlChatRunRepo::new(db.clone(), bcs_db_api::DbSqlFlavor::Sqlite,
        Arc::new(bcs_cache_local::InMemoryCachePlugin::new()), "fresh:".into(), 60_000, "dev".into());
    let record = fresh_repo.get("restart").await.unwrap().unwrap();
    assert_eq!(record.state, ChatRunState::Completed);
    assert_eq!(record.accumulated_content, "durable answer");
    assert_eq!(record.delivery_id.as_deref(), Some("restart"));
    assert_eq!(h.support.bot_delivery.frames().await.len(), 1);
    stop.send(true).unwrap();worker.await.unwrap().unwrap();
}

#[tokio::test]
async fn cleanup_repairs_orphan_without_waiting_for_the_run_deadline() {
    let h = Harness::new(false).await;
    let now = chrono::Utc::now().timestamp_millis() as u64;
    let mut record = ChatRunRecord::new("orphan".into(), "bot-observer".into(), "bot-driver".into(), "orphan-session".into(),
        now - 31_000, now + 3_600_000, None, ChatResponseMode::Full, ChatRunCompletionPolicy::WaitForFinal);
    record.delivery_id = Some("missing".into());record.source_message_id = Some("missing-source".into());
    h.direct.run_store().create(record).await.unwrap();
    A2aChatService::cleanup_expired(h.direct.as_ref(), now, 60_000).await.unwrap();
    let repaired = h.direct.run_store().get("orphan").await.unwrap();
    assert_eq!(repaired.error_message.as_deref(), Some("admission_incomplete"));
    assert!(repaired.state.is_terminal());
    assert!(h.support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn cancellation_requires_confirmed_stop_and_provider_keeps_the_lane() {
    for provider in [false, true] {
        let h = Harness::new(true).await;
        if provider {
            h.support.registry.set_delivery_target("bot-observer", support::FakeRegistryService::provider_target("bot-observer")).await;
        }
        h.submit("cancel-active", "cancel-session").await.unwrap();
        h.submit("cancel-next", "cancel-session").await.unwrap();
        let (stop, receiver) = tokio::sync::watch::channel(false);
        let worker = tokio::spawn(h.runtime().run(receiver));
        tokio::time::timeout(Duration::from_secs(3), async {
            while h.support.bot_delivery.frames().await.is_empty() { tokio::time::sleep(Duration::from_millis(10)).await; }
        }).await.unwrap();
        let stranger = CallerContext::Bot(BotActor { bot_uuid: "unrelated-bot".into() });
        assert!(A2aChatService::cancel_run(h.direct.as_ref(), stranger, "cancel-active").await.is_err());
        A2aChatService::cancel_run(h.direct.as_ref(), caller(), "cancel-active").await.unwrap();
        let expected = if provider { "cancel_unknown" } else { "cancelled" };
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                if h.status("cancel-active").await["delivery"]["status"] == expected { break; }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        }).await.unwrap();
        let cancelled = A2aChatService::cancel_run(h.direct.as_ref(), caller(), "cancel-active").await.unwrap().response.unwrap();
        assert_eq!(cancelled["cancelled"], false);
        if provider {
            assert_eq!(cancelled["is_terminal"], false);
            assert!(h.support.bot_delivery.aborts().await.is_empty());
            assert_eq!(h.support.bot_delivery.frames().await.len(), 1);
            assert_eq!(h.status("cancel-next").await["delivery"]["status"], "queued");
            assert!(h.flow.bot_run_context.as_ref().unwrap().find_active_run("cancel-active").await.unwrap().is_some());
        } else {
            let aborts = h.support.bot_delivery.aborts().await;
            assert_eq!(aborts.len(), 1);
            assert_eq!(aborts[0].session_id, "cancel-session");
            assert_eq!(aborts[0].run_id.as_deref(), Some("cancel-active"));
            regressions::assert_context_cleaned(&h, &h.row("cancel-active").await).await;
        }
        stop.send(true).unwrap(); worker.await.unwrap().unwrap();
    }
}

#[tokio::test]
async fn expired_or_no_longer_authorized_requests_never_reach_transport() {
    for expires in [false, true] {
        let h = Harness::new(true).await;
        if expires { h.policy.snapshot.write().await.policy.queue_ttl_ms = Some(1); }
        let accepted = h.submit("blocked", "blocked-session").await.unwrap();
        assert_eq!(accepted.expires_at_ms, h.direct.run_store().get("blocked").await.unwrap().expires_at_ms);
        if expires { tokio::time::sleep(Duration::from_millis(10)).await; }
        else { h.support.registry.set_visibility("bot-observer", "protected").await; }
        let (stop, receiver) = tokio::sync::watch::channel(false);
        let worker = tokio::spawn(h.runtime().run(receiver));
        tokio::time::timeout(Duration::from_secs(3), async {
            while !h.row("blocked").await.state.status.is_terminal() { tokio::time::sleep(Duration::from_millis(10)).await; }
        }).await.unwrap();
        let status = h.status("blocked").await;
        assert_eq!(status["state"], "failed");
        assert_eq!(status["error_message"], if expires { "queue_expired" } else { "delivery_failed" });
        assert!(h.support.bot_delivery.frames().await.is_empty());
        stop.send(true).unwrap(); worker.await.unwrap().unwrap();
    }
}
