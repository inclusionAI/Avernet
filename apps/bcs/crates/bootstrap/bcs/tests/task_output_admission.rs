//! Real TaskDispatch -> Worker events -> protected Workbench permit path.
//! Only the recipient resolver is doubled; permission predicates, operation
//! lifetime, queue admission and frontend fanout use production implementations.
use std::sync::{Arc, Mutex, atomic::Ordering};
use bcs_domain::{GroupStrategy, MessageViewScope, ParticipantRole};
use bcs_domain::message_delivery::PersistedMessageDelivery;
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore,
    delivery_policy::LiveDeliveryPolicy, managed_delivery::ManagedMessageDelivery,
    queued_group::QueuedGroupPreparation};
use bcs_service_api::*;
use bcs_service_api::application::v1::{DeliveryVisibility, ProtectedDeliveryBinding};
use bcs_ws::shared::{OutboundFrame, RunChannelManager};
use bcs_ws::web::{WorkbenchConnectionRegistry, WorkbenchFrontendDelivery, WorkbenchOutputAdmission};
use serde_json::json;
use tokio::sync::mpsc;

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
#[path = "../../../services/bcs-message-flow/tests/support/session.rs"]
mod sessions;
#[path = "../../../services/bcs-message-flow/tests/support/failing_delivery.rs"]
mod failing_delivery;
#[path = "../../../adapters/ws/bcs-ws/tests/support/mod.rs"]
mod ws_support;

const SESSION: &str = "group-1:task";

#[derive(Default)]
struct CleanupGate { entered: tokio::sync::Notify, resume: tokio::sync::Notify }

struct OutputProbe {
    inner: WorkbenchOutputAdmission,
    managed: Arc<ManagedMessageDelivery>,
    runs: Mutex<Vec<(String, String)>>,
    release_gate: Mutex<Option<Arc<CleanupGate>>>,
}
#[async_trait::async_trait]
impl OutputAdmissionPort for OutputProbe {
    async fn admit_output_operation(&self, command: OutputOperationAdmission) -> ServiceResult<()> {
        self.inner.admit_output_operation(command).await
    }
    async fn register_operation_runs(&self, operation: &str, runs: Vec<String>) -> ServiceResult<()> {
        let rows = self.managed.snapshot(None).await.unwrap();
        for run in &runs {
            assert!(!rows.iter().any(|row| row.run_id.as_ref() == Some(run)),
                "output registration must precede the durable admission commit");
            self.runs.lock().unwrap().push((operation.into(), run.clone()));
        }
        self.inner.register_operation_runs(operation, runs).await
    }
    async fn release_operation_runs(&self, operation: &str, runs: Vec<String>) -> ServiceResult<()> {
        let gate = self.release_gate.lock().unwrap().take();
        if let Some(gate) = gate {
            gate.entered.notify_one();
            gate.resume.notified().await;
        }
        self.inner.release_operation_runs(operation, runs).await
    }
}

struct Fixture {
    support: support::FlowTestSupport,
    flow: Arc<BcsMessageFlow>,
    managed: Arc<ManagedMessageDelivery>,
    fault: Arc<failing_delivery::FailingDelivery>,
    registry: Arc<WorkbenchConnectionRegistry>,
    resolver: Arc<ws_support::RecordingAdmission>,
    output: Arc<OutputProbe>,
    contexts: Arc<MemoryBotRunContextStore>,
    live: Arc<LiveDeliveryPolicy>,
}
impl Fixture {
    async fn new(queued: bool, capacity: u32) -> Self {
        let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let mut group = support.group.get("group-1").await.unwrap();
        group.group_strategy = GroupStrategy::ManagerWorker;
        for participant in &mut group.participants {
            if participant.bot_uuid == "bot-driver" { participant.role = ParticipantRole::Manager; }
            if participant.bot_uuid == "bot-observer" { participant.role = ParticipantRole::Worker; }
        }
        support.group.upsert(group.clone()).await.unwrap();
        let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
        let live = Arc::new(LiveDeliveryPolicy::new(repo.clone(), Default::default()));
        live.scheduler_available.store(true, Ordering::SeqCst);
        let managed = Arc::new(ManagedMessageDelivery::new(repo.clone()).with_policy(live.clone()));
        let fault = Arc::new(failing_delivery::FailingDelivery::new(managed.clone()));
        let registry = Arc::new(WorkbenchConnectionRegistry::new());
        let resolver = Arc::new(ws_support::RecordingAdmission::new());
        resolver.set_scope(MessageViewScope::Participant);
        registry.set_delivery_admission(resolver.clone());
        let runs = Arc::new(RunChannelManager::new().with_output_permits(registry.output_permits()));
        let frontend = Arc::new(WorkbenchFrontendDelivery::new(registry.clone(), runs));
        let output = Arc::new(OutputProbe {
            inner: WorkbenchOutputAdmission::new(registry.clone(), resolver.clone()),
            managed: managed.clone(), runs: Mutex::new(Vec::new()), release_gate: Mutex::new(None),
        });
        let contexts = Arc::new(MemoryBotRunContextStore::default());
        let mut flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(),
            support.bot_delivery.clone(), frontend)
            .with_message_repo(repo).with_managed_deliveries(fault.clone())
            .with_bot_run_context(contexts.clone())
            .with_output_admission(Some(output.clone()))
            .with_session_management(Arc::new(sessions::StaticSessionManagement::new(
                sessions::test_session(SESSION, "group-1", group.participants))));
        flow.delivery_policy = Some(live.clone());
        let flow = Arc::new(flow);
        flow.retain_terminal_events();
        managed.bind_output_cleanup(Arc::downgrade(&flow)).unwrap();
        let mut policy = bcs_config_api::message_delivery::DeliveryPolicy::default();
        policy.flow_enabled.task = queued;
        policy.defaults.mode = bcs_config_api::message_delivery::BotDeliveryMode::Enforce;
        policy.defaults.max_queued = capacity;
        flow.replace_delivery_policy(CallerContext::Human(HumanActor {
            actor_id: "human_operator".into(), staff_no: "operator".into(),
        }), 0, policy).await.unwrap();
        Self { support, flow, managed, fault, registry, resolver, output, contexts, live }
    }
    async fn subscribe(&self, view: &str) -> (u64, mpsc::Receiver<OutboundFrame>) {
        let (control, _) = mpsc::channel(8);
        let (frames, rx) = mpsc::channel(256);
        let id = self.registry.subscribe_protected_with_shutdown(SESSION.into(), control,
            Some(view.into()), None, tokio_util::sync::CancellationToken::new(),
            ProtectedDeliveryBinding { tenant: None, user_id: "user".into(), group_id: "group-1".into(),
                session_id: Some(SESSION.into()), view_actor_id: view.into(), connection_instance_id: 0 }, frames,
        ).await.unwrap();
        (id, rx)
    }
    fn latest(&self) -> (String, String) { self.output.runs.lock().unwrap().last().unwrap().clone() }
    async fn dispatch(&self) -> ServiceResult<TaskDispatchOutcome> {
        self.flow.handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".into(), group_id: "group-1".into(), target_bot_id: "bot-observer".into(),
            target_bot_name: None, payload: json!({"message":"analyse", "bcs_session_id":SESSION, "response_mode":"full"}),
        }).await
    }
    async fn row(&self) -> PersistedMessageDelivery {
        let (_, run) = self.latest();
        self.managed.snapshot(Some(SESSION)).await.unwrap().into_iter()
            .find(|row| row.run_id.as_deref() == Some(run.as_str())).unwrap()
    }
    async fn start(&self) {
        let row = self.row().await;
        let prep = QueuedGroupPreparation { flow: Arc::downgrade(&self.flow), deliveries: self.fault.clone() };
        let mut prepared = prep.prepare(&row).await.unwrap();
        prepared.transport_context_json["policy_version"] = json!(self.live.snapshot.read().await.version);
        let row = self.managed.transition(DeliveryTransitionCommand {
            delivery_id: row.delivery_id, expected_state_version: row.state.state_version,
            event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::StartSend,
            now_ms: chrono::Utc::now().timestamp_millis(), request_id: None, actor_id: None, reply: None,
            transport_context_json: Some(prepared.transport_context_json), deadline_at_ms: Some(i64::MAX),
        }).await.unwrap();
        if let bcs_protocol::BcsFrame::Request(frame) = &mut prepared.command.frame {
            frame.id = row.request_id.clone().unwrap();
        }
        prep.before_send(&row, &prepared.command).await.unwrap();
        self.support.bot_delivery.deliver(prepared.command).await.unwrap();
    }
    async fn emit(&self, run: &str, state: ChatEventState) {
        self.flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".into(), run_id: run.into(), group_id: "group-1".into(), bcs_session_id: Some(SESSION.into()),
            state, event_type: "chat.event".into(), event_payload: json!({"message":{"content":[{"type":"text","text":"worker answer"}]}}),
        }).await.unwrap();
    }
    async fn permits(&self, operation: &str, run: &str, conn: u64) -> bool {
        self.registry.output_permits().check_frame(operation, conn, &DeliveryVisibility {
            domain: bcs_domain::MessageVisibilityDomain::ManagerWorker,
            audience: Some(bcs_domain::MessageAudience::directed(["bot-driver", "bot-observer"]).unwrap()),
        }, Some(run)).await
    }
}
fn drain(rx: &mut mpsc::Receiver<OutboundFrame>) { while rx.try_recv().is_ok() {} }

async fn worker_stream(queued: bool) {
    let f = Fixture::new(queued, 100).await;
    let (conn, mut frames) = f.subscribe("bot-driver").await;
    let (_, mut other) = f.subscribe("unrelated-bot").await;
    let task = f.dispatch().await.unwrap();
    let (operation, run) = f.latest();
    assert_eq!(f.flow.message_tracker.operation_of_run(&run).await.as_deref(), Some(operation.as_str()));
    let facts = f.flow.message_tracker.bot_event_run_info(&run, "group-1", "bot-observer").await.unwrap();
    assert_eq!(facts.participant.unwrap().role, ParticipantRole::Worker);
    assert!(f.permits(&operation, &run, conn).await);
    if queued {
        assert_ne!(run, task.task_id, "Task ID must not substitute for the preassigned Worker Run");
        assert_eq!(f.row().await.run_id.as_deref(), Some(run.as_str()));
        f.start().await;
    } else { assert_eq!(run, task.task_id); }
    drain(&mut frames); drain(&mut other);
    // Simulate authority revocation after acceptance, before the first delta.
    f.resolver.admit_only_instances(&[]);
    let (_, mut reconnected) = f.subscribe("bot-driver").await;
    let baseline = f.resolver.call_count();
    for _ in 0..100 {
        f.emit(&run, ChatEventState::Delta).await;
        assert!(matches!(frames.try_recv().unwrap(), OutboundFrame::Data { .. }));
        assert!(other.try_recv().is_err(), "Worker audience must remain private");
        assert!(reconnected.try_recv().is_err(), "a new connection cannot inherit a permit");
    }
    assert_eq!(f.resolver.call_count(), baseline, "100 Worker frames must cause zero authorization resolutions");
    f.emit(&run, ChatEventState::Final).await;
    let mut terminal = false;
    while let Ok(frame) = frames.try_recv() { terminal |= matches!(frame, OutboundFrame::Terminal { .. }); }
    assert!(terminal, "the original connection receives the Worker terminal after revocation");
    assert!(!f.permits(&operation, &run, conn).await);
    // A separate Task is freshly admitted, never attached to the parent's permit.
    f.dispatch().await.unwrap();
    let (next_operation, next_run) = f.latest();
    assert_ne!(next_operation, operation);
    assert!(!f.permits(&next_operation, &next_run, conn).await);
    if queued { f.start().await; }
    drain(&mut frames);
    let baseline = f.resolver.call_count();
    f.emit(&next_run, ChatEventState::Delta).await;
    assert!(frames.try_recv().is_err());
    assert_eq!(f.resolver.call_count(), baseline, "denied operation must not retry authorization per frame");
}
#[tokio::test]
async fn legacy_worker_stream_has_one_admission_not_per_frame() { worker_stream(false).await; }
#[tokio::test]
async fn queued_worker_stream_has_one_admission_not_per_frame() { worker_stream(true).await; }

#[tokio::test]
async fn queued_uncertain_commit_retains_registered_worker_output() {
    for committed in [false, true] {
        let f = Fixture::new(true, 100).await;
        let (conn, mut frames) = f.subscribe("bot-driver").await;
        f.fault.arm(if committed { "admit_after_commit" } else { "admit" }, 1);
        assert!(f.dispatch().await.is_err());
        assert_eq!(f.fault.remaining(), 0);
        let (operation, run) = f.latest();
        assert!(f.permits(&operation, &run, conn).await, "an unknown outcome is not rollback proof");
        assert_eq!(f.flow.message_tracker.operation_of_run(&run).await.as_deref(), Some(operation.as_str()));
        if committed {
            f.start().await;
            let baseline = f.resolver.call_count();
            f.emit(&run, ChatEventState::Delta).await;
            assert!(matches!(frames.try_recv().unwrap(), OutboundFrame::Data { .. }));
            assert_eq!(f.resolver.call_count(), baseline);
        }
    }
}

#[tokio::test]
async fn explicit_queue_rejections_release_unused_worker_output() {
    for capacity in [1, 100] {
        let f = Fixture::new(true, capacity).await;
        let (conn, _) = f.subscribe("bot-driver").await;
        if capacity == 1 { f.dispatch().await.unwrap(); }
        else { f.managed.set_admission_available(false); }
        assert!(f.dispatch().await.is_err());
        let (operation, run) = f.latest();
        assert!(!f.permits(&operation, &run, conn).await);
        assert!(f.flow.message_tracker.operation_of_run(&run).await.is_none());
        assert!(f.support.bot_delivery.frames().await.is_empty());
    }
}

#[tokio::test]
async fn legacy_transport_uncertainty_keeps_ingress_but_explicit_rejection_releases_it() {
    for unknown in [false, true] {
        let f = Fixture::new(false, 100).await;
        let (conn, mut frames) = f.subscribe("bot-driver").await;
        if unknown { f.support.bot_delivery.fail_for("bot-observer").await; }
        else { f.support.bot_delivery.not_delivered_for("bot-observer").await; }
        assert!(f.dispatch().await.is_err());
        let (operation, run) = f.latest();
        assert_eq!(f.permits(&operation, &run, conn).await, unknown);
        assert_eq!(f.contexts.find_active_run(&run).await.unwrap().is_some(), unknown);
        if unknown {
            assert!(!f.contexts.get_context(&run).await.unwrap().terminal);
            drain(&mut frames);
            let baseline = f.resolver.call_count();
            f.emit(&run, ChatEventState::Delta).await;
            assert!(matches!(frames.try_recv().unwrap(), OutboundFrame::Data { .. }));
            assert_eq!(f.resolver.call_count(), baseline);
        }
    }
}
#[tokio::test]
async fn legacy_accepted_alias_preserves_frozen_admission() {
    let f = Fixture::new(false, 100).await;
    let (conn, mut frames) = f.subscribe("bot-driver").await;
    let task = f.dispatch().await.unwrap();
    let (operation, run) = f.latest();
    let alias = "engine-accepted-run";
    f.flow.message_tracker.cache_channel_source_message_id(&run, "im-source").await;
    assert!(f.flow.rebind_channel_source_message(&run, alias).await.unwrap());
    assert_eq!(f.flow.message_tracker.channel_source_message_id(&run).await.as_deref(), Some("im-source"));
    f.contexts.bind_downstream_run_id(&run, alias).await.unwrap();
    assert_eq!(f.flow.register_task_run_alias(&task.task_id, alias, "bot-observer").await.unwrap(), TaskRunAliasRegistration::Registered);
    drain(&mut frames);
    f.resolver.admit_only_instances(&[]);
    let baseline = f.resolver.call_count();
    for _ in 0..100 {
        f.emit(alias, ChatEventState::Delta).await;
        assert!(matches!(frames.try_recv().unwrap(), OutboundFrame::Data { .. }));
    }
    assert_eq!(f.resolver.call_count(), baseline, "accepted alias must retain original operation, not reauthorize each delta");
    f.emit(alias, ChatEventState::Final).await;
    let mut terminal = false;
    while let Ok(frame) = frames.try_recv() { terminal |= matches!(frame, OutboundFrame::Terminal { .. }); }
    assert!(terminal);
    assert!(!f.permits(&operation, &run, conn).await, "alias final closes the canonical stream");
    assert!(f.contexts.get_context(&run).await.unwrap().terminal);
    assert!(f.flow.message_tracker.channel_source_message_id(&run).await.is_none());
}

#[tokio::test]
async fn queued_cancellation_releases_output_permit() {
    let f = Fixture::new(true, 100).await;
    let (conn, mut frames) = f.subscribe("bot-observer").await;
    let changes = f.managed.subscribe();
    let (shutdown, rx) = tokio::sync::watch::channel(false);
    let job = tokio::spawn(bcs_message_flow::delivery_notifications::run(Arc::downgrade(&f.flow), changes, rx));
    f.dispatch().await.unwrap();
    let (op, run) = f.latest();
    let row = f.row().await;
    let cancelled = f.managed.transition(DeliveryTransitionCommand {
        delivery_id: row.delivery_id, expected_state_version: row.state.state_version,
        event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CancelRequested,
        now_ms: chrono::Utc::now().timestamp_millis(), request_id: None, actor_id: Some("human_user".into()),
        reply: None, transport_context_json: None, deadline_at_ms: None,
    }).await.unwrap();
    assert_eq!(cancelled.state.status, bcs_domain::message_delivery::MessageDeliveryStatus::Cancelled);
    // Wait until the actual notification consumer observed the committed terminal.
    tokio::time::timeout(std::time::Duration::from_secs(3), async {
        loop {
            let frame = frames.recv().await.unwrap();
            if let OutboundFrame::Data { payload } = frame {
                if payload.contains("message.delivery.updated") && payload.contains("cancelled") { break; }
            }
        }
    }).await.unwrap();
    shutdown.send(true).unwrap(); job.await.unwrap();
    assert!(!f.permits(&op, &run, conn).await, "a never-sent cancelled Run must no longer retain live output permits");
}

#[tokio::test]
async fn output_alias_rejects_conflicting_run_and_wrong_producer_scope() {
    let f = Fixture::new(false, 100).await;
    let (_, mut frames) = f.subscribe("bot-driver").await;
    let first = f.dispatch().await.unwrap().task_id;
    let second = f.dispatch().await.unwrap().task_id;
    assert_eq!(f.flow.register_task_run_alias(&first, &second, "bot-observer").await.unwrap(), TaskRunAliasRegistration::Rejected);
    assert_eq!(f.flow.register_task_run_alias(&first, "worker-alias", "bot-driver").await.unwrap(), TaskRunAliasRegistration::Rejected);
    assert_eq!(f.flow.register_task_run_alias(&first, "worker-alias", "bot-observer").await.unwrap(), TaskRunAliasRegistration::Registered);
    assert_eq!(f.flow.register_task_run_alias(&second, "worker-alias", "bot-observer").await.unwrap(), TaskRunAliasRegistration::Rejected);
    for (bot, group, session) in [("other", "group-1", SESSION), ("bot-observer", "other", SESSION),
        ("bot-observer", "group-1", "other-session")] {
        drain(&mut frames);
        let baseline = f.resolver.call_count();
        let result = f.flow.handle_bot_event(BotEventCommand {
            bot_id: bot.into(), run_id: "worker-alias".into(), group_id: group.into(), bcs_session_id: Some(session.into()),
            state: ChatEventState::Delta, event_type: "chat.event".into(), event_payload: json!({"delta_text":"forged"}),
        }).await;
        assert!(matches!(result, Err(ServiceError::Forbidden(_))));
        assert!(frames.try_recv().is_err());
        assert_eq!(f.resolver.call_count(), baseline, "scope mismatch must not fall back to independent event authorization");
    }
}

#[path = "../../../services/bcs-message-flow/tests/support/operation_output.rs"]
mod operation_output;

#[tokio::test]
async fn ordinary_nonqueued_send_alias_uses_the_same_canonical_output_path() {
    let f = Fixture::new(false, 100).await;
    let (conn, mut frames) = f.subscribe("bot-driver").await;
    let sent = f.flow.handle_web_send(operation_output::web_send("group-1", "human_1", Some(SESSION), &["bot-driver"])).await.unwrap();
    let run = sent.active_run_ids.iter().find(|run| f.output.runs.lock().unwrap().iter().any(|(_, id)| id == *run)).unwrap();
    let context = f.contexts.get_context(run).await.unwrap();
    let operation = f.flow.message_tracker.operation_of_run(run).await.unwrap();
    assert_eq!(f.flow.register_task_run_alias(run, "chat-alias", &context.bot_id).await.unwrap(), TaskRunAliasRegistration::NotTask);
    drain(&mut frames);
    f.resolver.admit_only_instances(&[]);
    let baseline = f.resolver.call_count();
    f.flow.handle_bot_event(BotEventCommand {
        bot_id: context.bot_id, run_id: "chat-alias".into(), group_id: "group-1".into(), bcs_session_id: Some(SESSION.into()),
        state: ChatEventState::Delta, event_type: "chat.event".into(), event_payload: json!({"delta_text":"normal chat"}),
    }).await.unwrap();
    assert_eq!(f.resolver.call_count(), baseline);
    assert!(matches!(frames.try_recv().unwrap(), OutboundFrame::Data { .. }));
    // A participant view still obeys this producer's original audience.
    assert!(f.permits(&operation, run, conn).await);
}

#[tokio::test]
async fn unsent_terminal_cleanup_does_not_depend_on_status_notification_delivery() {
    use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
    for event in [Event::CancelRequested, Event::PreparationFailed, Event::QueueExpired] {
        let f = Fixture::new(true, 100).await;
        let (conn, _) = f.subscribe("bot-driver").await;
        f.dispatch().await.unwrap();
        let (op, run) = f.latest();
        let row = f.row().await;
        // Deliberately no notification subscriber: this is post-commit cleanup.
        let terminal = f.managed.transition(DeliveryTransitionCommand {
            delivery_id: row.delivery_id, expected_state_version: row.state.state_version, event,
            now_ms: chrono::Utc::now().timestamp_millis(), request_id: None, actor_id: None, reply: None,
            transport_context_json: None, deadline_at_ms: None,
        }).await.unwrap();
        assert!(terminal.state.status.is_terminal());
        assert!(!f.permits(&op, &run, conn).await);
        assert!(f.flow.message_tracker.operation_of_run(&run).await.is_none());
    }
}

#[tokio::test]
async fn cancelling_a_started_run_keeps_output_until_its_callback_terminal() {
    use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
    let f = Fixture::new(true, 100).await;
    let (conn, _) = f.subscribe("bot-driver").await;
    f.dispatch().await.unwrap();
    let (op, run) = f.latest();
    f.start().await;
    let row = f.row().await;
    let pending = f.managed.transition(DeliveryTransitionCommand {
        delivery_id: row.delivery_id, expected_state_version: row.state.state_version, event: Event::CancelRequested,
        now_ms: chrono::Utc::now().timestamp_millis(), request_id: None, actor_id: None, reply: None,
        transport_context_json: None, deadline_at_ms: None,
    }).await.unwrap();
    assert_eq!(pending.state.status, bcs_domain::message_delivery::MessageDeliveryStatus::Cancelling);
    assert!(f.permits(&op, &run, conn).await);
}

#[tokio::test]
async fn cancellation_of_the_transition_caller_does_not_cancel_committed_cleanup() {
    let f = Fixture::new(true, 100).await;
    let (conn, _) = f.subscribe("bot-driver").await;
    f.dispatch().await.unwrap();
    let (op, run) = f.latest();
    let row = f.row().await;
    let gate = Arc::new(CleanupGate::default());
    *f.output.release_gate.lock().unwrap() = Some(gate.clone());
    let service = f.managed.clone();
    let caller = tokio::spawn(async move {
        service.transition(DeliveryTransitionCommand {
            delivery_id: row.delivery_id, expected_state_version: row.state.state_version,
            event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CancelRequested,
            now_ms: chrono::Utc::now().timestamp_millis(), request_id: None, actor_id: None, reply: None,
            transport_context_json: None, deadline_at_ms: None,
        }).await
    });
    tokio::time::timeout(std::time::Duration::from_secs(3), gate.entered.notified()).await.unwrap();
    assert!(f.row().await.state.status.is_terminal(), "cleanup starts only after commit");
    caller.abort();
    assert!(caller.await.unwrap_err().is_cancelled());
    gate.resume.notify_one();
    tokio::time::timeout(std::time::Duration::from_secs(3), async {
        while f.permits(&op, &run, conn).await { tokio::task::yield_now().await; }
    }).await.expect("retained post-commit task must finish cleanup without its caller");
}

#[path = "task_output_admission/output_terminal_scope.rs"]
mod output_terminal_scope;
