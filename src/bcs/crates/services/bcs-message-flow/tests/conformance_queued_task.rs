use std::sync::{Arc, atomic::Ordering};
use bcs_config_api::message_delivery::{DeliveryPolicy, BotDeliveryMode};
use bcs_domain::{DeliveryType, GroupStrategy, ParticipantRole, PersistMode, SystemGroupMessage, SystemMessageEventKind};
use bcs_domain::message_delivery::{DeliveryFlowKind, MessageDeliveryStatus as Status, PersistedMessageDelivery};
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore,
    delivery_policy::LiveDeliveryPolicy, managed_delivery::ManagedMessageDelivery, queued_group::QueuedGroupPreparation};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::{GroupCoreService, MessageFlowService, ManagedMessageDeliveryService,
    ManagedDeliveryPreparationService, BotDeliveryPort, CallerContext, HumanActor, TaskDispatchCommand,
    TaskMessageCommand, TaskCompleteCommand, BotEventCommand, ChatEventState, DeliveryTransitionCommand};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::MessageRepoPort;
use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoPort;
use serde_json::json;

#[path = "support/session.rs"]
mod sessions;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod migrations;

const SESSION: &str = "group-1:task";
fn admin() -> CallerContext { CallerContext::Human(HumanActor { actor_id:"human_operator".into(), staff_no:"operator".into() }) }
struct Fixture {
    support: support::FlowTestSupport,
    flow: Arc<BcsMessageFlow>,
    service: Arc<ManagedMessageDelivery>,
    repo: Arc<dyn MessageRepoPort>,
    live: Arc<LiveDeliveryPolicy>,
}

impl Fixture {
    async fn wait_frames(&self, count: usize) {
        tokio::time::timeout(std::time::Duration::from_secs(3), async {
            while self.support.bot_delivery.frames().await.len() < count {
                tokio::time::sleep(std::time::Duration::from_millis(5)).await;
            }
        }).await.expect("scheduler must make progress");
    }
    async fn transition(&self, row: &PersistedMessageDelivery, event: Event) -> PersistedMessageDelivery {
        self.service.transition(DeliveryTransitionCommand { delivery_id:row.delivery_id.clone(),
            expected_state_version:row.state.state_version, event, now_ms:chrono::Utc::now().timestamp_millis(),
            request_id:row.request_id.clone(), actor_id:None, reply:None, transport_context_json:None, deadline_at_ms:None }).await.unwrap()
    }
    async fn new() -> Self {
        let repo = Arc::new(MemoryMessageRepo::new());
        Self::with_repos(repo.clone(), repo).await
    }

    async fn with_repos(repo: Arc<dyn MessageRepoPort>, deliveries: Arc<dyn MessageDeliveryRepoPort>) -> Self {
        let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let mut group = support.group.get("group-1").await.unwrap();
        group.group_strategy = GroupStrategy::ManagerWorker;
        for p in &mut group.participants {
            if p.bot_uuid == "bot-driver" { p.role = ParticipantRole::Manager; }
            if p.bot_uuid == "bot-observer" { p.role = ParticipantRole::Worker; }
        }
        support.group.upsert(group).await.unwrap();
        let live = Arc::new(LiveDeliveryPolicy::new(deliveries.clone(), Default::default()));
        live.scheduler_available.store(true, Ordering::SeqCst);
        let service = Arc::new(ManagedMessageDelivery::new(deliveries).with_policy(live.clone()));
        let flow = Self::make_flow(&support, &service, &repo, &live).await;
        let mut policy = DeliveryPolicy::default();
        policy.flow_enabled.group = true; policy.flow_enabled.system = true; policy.flow_enabled.task = true;
        policy.defaults.mode = BotDeliveryMode::Enforce;
        policy.queue_ttl_ms = Some(300_000);
        flow.replace_delivery_policy(admin(), 0, policy).await.unwrap();
        Self { support, flow, service, repo, live }
    }

    async fn make_flow(support: &support::FlowTestSupport, service: &Arc<ManagedMessageDelivery>, repo: &Arc<dyn MessageRepoPort>, live: &Arc<LiveDeliveryPolicy>) -> Arc<BcsMessageFlow> {
        let group = support.group.get("group-1").await.unwrap();
        let mut flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(),
            support.bot_delivery.clone(), support.frontend_delivery.clone())
            .with_message_repo(repo.clone()).with_managed_deliveries(service.clone())
            .with_bot_run_context(Arc::new(MemoryBotRunContextStore::default()))
            .with_session_management(Arc::new(sessions::StaticSessionManagement::new(
                sessions::test_session(SESSION, "group-1", group.participants))));
        flow.delivery_policy = Some(live.clone());
        let flow = Arc::new(flow); flow.retain_terminal_events(); flow
    }

    async fn rows(&self) -> Vec<PersistedMessageDelivery> { self.service.snapshot(Some(SESSION)).await.unwrap() }

    async fn context(&self, bot: &str) {
        let group = self.support.group.get("group-1").await.unwrap();
        self.flow.system_queue_port().admit(&group, SESSION, &group.participants, SystemMessageEventKind::SessionContext,
            &[SystemGroupMessage { recipients:vec![bot.into()], message:format!("REQUIRED_CONTEXT_FOR_{bot}"),
                delivery_type:DeliveryType::Inject, persist:PersistMode::PerRecipient }]).await.unwrap().unwrap();
    }

    async fn dispatch(&self) -> (String, PersistedMessageDelivery) {
        let outcome = bcs_test_support::contract::application::queued_task::queued_task_dispatch_contract_tests(
            self.flow.as_ref(), dispatch_command()).await;
        let row = self.rows().await.into_iter().find(|r| r.semantic_projection_json["task"]["task_id"] == outcome.task_id).unwrap();
        (outcome.task_id, row)
    }

    async fn start(&self, row: &PersistedMessageDelivery) -> PersistedMessageDelivery {
        let preparer = QueuedGroupPreparation { flow:Arc::downgrade(&self.flow), deliveries:self.service.clone() };
        let mut prepared = preparer.prepare(row).await.unwrap();
        prepared.transport_context_json["policy_version"] = json!(self.live.snapshot.read().await.version);
        let started = self.service.transition(DeliveryTransitionCommand {
            delivery_id:row.delivery_id.clone(), expected_state_version:row.state.state_version, event:Event::StartSend,
            now_ms:chrono::Utc::now().timestamp_millis(), request_id:None, actor_id:None, reply:None,
            transport_context_json:Some(prepared.transport_context_json), deadline_at_ms:Some(i64::MAX) }).await.unwrap();
        if let bcs_protocol::BcsFrame::Request(frame) = &mut prepared.command.frame { frame.id = started.request_id.clone().unwrap(); }
        preparer.before_send(&started, &prepared.command).await.unwrap();
        self.support.bot_delivery.deliver(prepared.command).await.unwrap();
        started
    }
}

fn dispatch_command() -> TaskDispatchCommand {
    TaskDispatchCommand { driver_bot_id:"bot-driver".into(), group_id:"group-1".into(), target_bot_id:"bot-observer".into(),
        target_bot_name:None, payload:json!({"message":"TASK_BODY", "bcs_session_id":SESSION, "response_mode":"full"}) }
}
fn final_event(row: &PersistedMessageDelivery) -> BotEventCommand {
    BotEventCommand { bot_id:row.target_bot_id.clone(), run_id:row.run_id.clone().unwrap(), group_id:"group-1".into(),
        bcs_session_id:Some(SESSION.into()), state:ChatEventState::Final, event_type:"chat.event".into(),
        event_payload:json!({"message":{"content":[{"type":"text","text":"WORKER_RESULT"}]}}) }
}

#[tokio::test]
async fn assignment_and_return_both_carry_system_context_and_duplicate_final_is_idempotent() {
    let f = Fixture::new().await;
    f.context("bot-observer").await;
    f.context("bot-driver").await;
    let (task_id, row) = f.dispatch().await;
    assert_ne!(row.run_id.as_deref(), Some(task_id.as_str()));
    let source = f.repo.get_message_by_id(SESSION, &row.source_message_id).await.unwrap().unwrap();
    assert_eq!(source.content["text"], "TASK_BODY");
    assert_eq!(source.owner_bot_id.as_deref(), Some("bot-observer"));
    assert!(f.support.bot_delivery.frames().await.is_empty());
    let row = f.start(&row).await;
    let wire = serde_json::to_value(&f.support.bot_delivery.frames().await[0]).unwrap();
    assert!(wire.to_string().contains("REQUIRED_CONTEXT_FOR_bot-observer"));
    assert_eq!(wire["params"]["task_id"], task_id);
    assert_eq!(wire["params"]["session_context"]["you_are_mentioned"], true);
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    assert_eq!(f.support.bot_delivery.frames().await.len(), 1, "terminal must enqueue, not directly deliver to Manager");
    let rows = f.rows().await;
    assert_eq!(rows.iter().filter(|r| r.semantic_projection_json["task"]["leg"] == "result").count(), 1);
    assert_eq!(rows.iter().find(|r| r.delivery_id == row.delivery_id).unwrap().state.status, Status::Completed);
    assert_eq!(rows.iter().find(|r| r.state.kind == DeliveryType::Inject && r.target_bot_id == "bot-observer").unwrap().state.status, Status::Consumed);
    let result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
    let source = f.repo.get_message_by_id(SESSION, &result.source_message_id).await.unwrap().unwrap();
    assert_eq!(source.message_type, "run_reply"); assert_eq!(source.content["text"], "WORKER_RESULT");
    f.start(result).await;
    let wire = serde_json::to_value(&f.support.bot_delivery.frames().await[1]).unwrap().to_string();
    assert!(wire.contains("REQUIRED_CONTEXT_FOR_bot-driver") && wire.contains("WORKER_RESULT"));
}

#[tokio::test]
async fn worker_messages_queue_and_task_completion_waits_for_queued_assignments() {
    let f = Fixture::new().await;
    f.dispatch().await;
    let outcome = f.flow.handle_task_message(TaskMessageCommand { worker_bot_id:"bot-observer".into(), group_id:"group-1".into(),
        payload:json!({"bcs_session_id":SESSION, "message":"progress"}) }).await.unwrap();
    assert_eq!(outcome.status, "queued"); assert!(f.support.bot_delivery.frames().await.is_empty());
    let complete = f.flow.handle_task_complete(TaskCompleteCommand { task_id:"group-1".into(), bot_id:"bot-driver".into(), via_echo:true,
        payload:json!({"bcs_session_id":SESSION}) }).await.unwrap();
    assert!(complete.blocked);
    let group_complete = f.flow.handle_task_complete(TaskCompleteCommand { task_id:"group-1".into(), bot_id:"bot-driver".into(), via_echo:true,
        payload:json!({}) }).await.unwrap();
    assert!(group_complete.blocked, "group-wide completion must also see queued Session tasks");
}

#[tokio::test]
async fn restart_recovers_task_mapping_and_dispatch_deadline_does_not_include_queue_wait() {
    let mut f = Fixture::new().await;
    let (task_id, row) = f.dispatch().await;
    assert!(row.run_deadline_at_ms.is_none());
    assert_eq!(f.flow.task_store.get(&task_id).await.unwrap().status, bcs_message_flow::task_store::TaskLedgerStatus::Queued);
    let row = f.start(&row).await;
    f.flow = Fixture::make_flow(&f.support, &f.service, &f.repo, &f.live).await;
    assert!(f.flow.task_store.get(&task_id).await.is_none());
    f.service.recover(chrono::Utc::now().timestamp_millis()).await.unwrap();
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    assert_eq!(f.flow.task_store.get(&task_id).await.unwrap().status, bcs_message_flow::task_store::TaskLedgerStatus::Replied);
    assert!(f.rows().await.iter().any(|r| r.semantic_projection_json["task"]["leg"] == "result"));
}

#[tokio::test]
async fn disabling_task_keeps_old_work_ordered_and_terminal_results_queued() {
    let f = Fixture::new().await;
    let (_, row) = f.dispatch().await;
    let mut policy = f.live.snapshot.read().await.policy.clone(); policy.flow_enabled.task = false;
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    assert!(f.flow.handle_task_dispatch(dispatch_command()).await.unwrap_err().to_string().contains("queue_draining"));
    let row = f.start(&row).await;
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    assert!(f.rows().await.iter().any(|r| r.semantic_projection_json["task"]["leg"] == "result" && r.state.status == Status::Queued));
}

#[tokio::test]
async fn manager_capacity_rejection_does_not_lose_worker_completion() {
    let f = Fixture::new().await;
    let (_, row) = f.dispatch().await;
    let row = f.start(&row).await;
    let mut policy = f.live.snapshot.read().await.policy.clone(); policy.defaults.max_queued = 1;
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    f.flow.handle_task_message(TaskMessageCommand { worker_bot_id:"bot-observer".into(), group_id:"group-1".into(),
        payload:json!({"bcs_session_id":SESSION, "message":"occupy Manager queue"}) }).await.unwrap();
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    let rows = f.rows().await;
    assert_eq!(rows.iter().find(|r| r.delivery_id == row.delivery_id).unwrap().state.status, Status::Completed);
    let result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
    assert_eq!(result.state.status, Status::RejectedCapacity);
    assert_eq!(f.repo.get_message_by_id(SESSION, &result.source_message_id).await.unwrap().unwrap().content["text"], "WORKER_RESULT");
}

#[tokio::test]
async fn task_policy_off_preserves_legacy_without_retained_context() {
    let f = Fixture::new().await;
    let mut policy = f.live.snapshot.read().await.policy.clone(); policy.flow_enabled.task = false;
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    assert_eq!(f.flow.handle_task_dispatch(dispatch_command()).await.unwrap().status, "dispatched");
    assert!(!f.rows().await.iter().any(|r| r.flow_kind == DeliveryFlowKind::Task));
    assert_eq!(f.support.bot_delivery.frames().await.len(), 1);
}

#[tokio::test]
async fn task_attachments_roundtrip_and_history_hides_capabilities() {
    let f = Fixture::new().await;
    let mut cmd = dispatch_command();
    cmd.payload["attachments"] = json!([{"attachment_id":"att-task", "type":"file", "file_name":"input.txt", "url":"https://example.test/task-input"}]);
    f.flow.handle_task_dispatch(cmd).await.unwrap();
    let row = f.rows().await.remove(0);
    let source = f.repo.get_message_by_id(SESSION, &row.source_message_id).await.unwrap().unwrap();
    assert_eq!(source.content["attachments"][0]["url"], "https://example.test/task-input");
    f.start(&row).await;
    let wire = serde_json::to_value(&f.support.bot_delivery.frames().await[0]).unwrap();
    assert_eq!(wire["params"]["attachments"][0]["url"], "https://example.test/task-input");
    let history = f.repo.list_session_history(SESSION, bcs_domain::MessageOwnerFilter::Any, None, None, None, 100).await.unwrap();
    assert!(!serde_json::to_string(&history.messages).unwrap().contains("https://example.test/task-input"));
}

#[tokio::test]
async fn queued_cancel_and_expiry_release_context_and_survive_ledger_rebuild() {
    for event in [Event::CancelRequested, Event::QueueExpired] {
        let mut f = Fixture::new().await;
        f.context("bot-observer").await;
        let (_, row) = f.dispatch().await;
        let (_, successor) = f.dispatch().await;
        let terminal = f.transition(&row, event).await;
        assert!(matches!(terminal.state.status, Status::Cancelled | Status::Expired));
        let rows = f.rows().await;
        let context = rows.iter().find(|r| r.state.kind == DeliveryType::Inject).unwrap();
        assert_eq!(context.bound_to_delivery_id.as_deref(), Some(successor.delivery_id.as_str()));
        assert!(f.support.bot_delivery.frames().await.is_empty());
        // Completion must consult durable state after restart, not an empty ledger.
        f.flow = Fixture::make_flow(&f.support, &f.service, &f.repo, &f.live).await;
        let complete = f.flow.handle_task_complete(TaskCompleteCommand { task_id:"group-1".into(), bot_id:"bot-driver".into(), via_echo:true,
            payload:json!({"bcs_session_id":SESSION}) }).await.unwrap();
        assert!(complete.blocked);
        assert_eq!(f.flow.task_store.get(&row.semantic_projection_json["task"]["task_id"].as_str().unwrap()).await.unwrap().managed, true);
    }
}

#[tokio::test]
async fn running_abort_and_error_create_one_explicit_failure_result() {
    for state in [ChatEventState::Aborted, ChatEventState::Error] {
        let f = Fixture::new().await;
        let (_, row) = f.dispatch().await;
        let mut row = f.start(&row).await;
        if state == ChatEventState::Aborted { row = f.transition(&row, Event::CancelRequested).await; }
        let mut event = final_event(&row); event.state = state.clone();
        f.flow.handle_bot_event(event.clone()).await.unwrap();
        f.flow.handle_bot_event(event).await.unwrap();
        let rows = f.rows().await;
        assert_eq!(rows.iter().filter(|r| r.semantic_projection_json["task"]["leg"] == "result").count(), 1);
        let result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
        let source = f.repo.get_message_by_id(SESSION, &result.source_message_id).await.unwrap().unwrap();
        assert_eq!(source.content["task_state"], if state == ChatEventState::Aborted { "cancelled" } else { "failed" });
        assert!(source.content["task_result_text"].as_str().unwrap().starts_with("[task "));
    }
}

#[tokio::test]
async fn closed_group_does_not_block_worker_commit_or_authorize_result_send() {
    let f = Fixture::new().await;
    let (_, row) = f.dispatch().await;
    let row = f.start(&row).await;
    let mut group = f.support.group.get("group-1").await.unwrap();
    group.status = bcs_domain::GroupStatus::Closed;
    f.support.group.upsert(group).await.unwrap();
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    let rows = f.rows().await;
    assert_eq!(rows.iter().find(|r| r.delivery_id == row.delivery_id).unwrap().state.status, Status::Completed);
    let result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
    let preparer = QueuedGroupPreparation { flow:Arc::downgrade(&f.flow), deliveries:f.service.clone() };
    assert!(preparer.prepare(result).await.is_err());
}

#[tokio::test]
async fn enabling_task_before_legacy_worker_returns_queues_only_the_result() {
    let f = Fixture::new().await;
    let mut policy = f.live.snapshot.read().await.policy.clone(); policy.flow_enabled.task = false;
    f.flow.replace_delivery_policy(admin(), 1, policy.clone()).await.unwrap();
    let task = f.flow.handle_task_dispatch(dispatch_command()).await.unwrap();
    policy.flow_enabled.task = true;
    f.flow.replace_delivery_policy(admin(), 2, policy).await.unwrap();
    let event = BotEventCommand { bot_id:"bot-observer".into(), run_id:task.task_id,
        group_id:"group-1".into(), bcs_session_id:Some(SESSION.into()), state:ChatEventState::Final,
        event_type:"chat.event".into(), event_payload:json!({"message":{"content":[{"type":"text","text":"legacy result"}]}}) };
    f.flow.handle_bot_event(event.clone()).await.unwrap();
    f.flow.handle_bot_event(event).await.unwrap();
    assert_eq!(f.support.bot_delivery.frames().await.len(), 1);
    let rows = f.rows().await;
    assert_eq!(rows.len(), 1); assert_eq!(rows[0].semantic_projection_json["task"]["leg"], "result");
}

#[tokio::test]
async fn non_chat_terminal_cannot_finish_task_and_missing_session_cannot_enqueue() {
    let f = Fixture::new().await;
    let mut cmd = dispatch_command(); cmd.payload.as_object_mut().unwrap().remove("bcs_session_id");
    assert!(f.flow.handle_task_dispatch(cmd).await.is_err());
    assert!(f.rows().await.is_empty());
    let (_, row) = f.dispatch().await;
    let row = f.start(&row).await;
    let mut event = final_event(&row); event.event_type = "agent".into();
    event.event_payload = json!({"stream":"thinking"});
    f.flow.handle_bot_event(event).await.unwrap();
    assert_eq!(f.rows().await.len(), 1);
    assert_eq!(f.rows().await[0].state.status, Status::Dispatching);
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    assert_eq!(f.rows().await.len(), 2);
}

#[tokio::test]
async fn restarted_after_tool_task_retains_full_run_but_sends_only_result_window() {
    let mut f = Fixture::new().await;
    let mut cmd = dispatch_command(); cmd.payload["response_mode"] = json!("after_last_tool_call");
    f.flow.handle_task_dispatch(cmd).await.unwrap();
    let row = f.start(&f.rows().await[0]).await;
    let mut delta = final_event(&row); delta.state = ChatEventState::Delta;
    delta.event_payload = json!({"delta_text":"BEFORE_TOOL"});
    f.flow.handle_bot_event(delta).await.unwrap();
    let mut tool = final_event(&row); tool.state = ChatEventState::ToolCallEnd; tool.event_type = "agent".into();
    tool.event_payload = json!({"stream":"tool", "data":{"phase":"result", "toolCallId":"call-task", "name":"lookup", "result":"not response text"}});
    f.flow.handle_bot_event(tool).await.unwrap();
    f.flow = Fixture::make_flow(&f.support, &f.service, &f.repo, &f.live).await;
    let mut final_cmd = final_event(&row);
    final_cmd.event_payload = json!({"message":{"content":[{"type":"text","text":"BEFORE_TOOLAFTER_TOOL"}]}});
    f.flow.handle_bot_event(final_cmd).await.unwrap();
    let rows = f.rows().await;
    let result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
    let source = f.repo.get_message_by_id(SESSION, &result.source_message_id).await.unwrap().unwrap();
    assert_eq!(source.content["text"], "BEFORE_TOOLAFTER_TOOL");
    assert_eq!(source.content["task_result_text"], "AFTER_TOOL");
}

#[tokio::test]
async fn sqlite_task_roundtrip_rebuilds_from_durable_delivery_and_keeps_history_single_copy() {
    use bcs_db_api::{DbPlugin, DbStatement};
    let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES ('group-1:task', 'group-1', 'dev', '[]')")).await.unwrap();
    let repo = Arc::new(bcs_message_store::MySqlMessageStore::sqlite(db, "dev".into()));
    let mut f = Fixture::with_repos(repo.clone(), repo).await;
    f.context("bot-observer").await;
    let (task_id, row) = f.dispatch().await;
    let row = f.start(&row).await;
    f.flow = Fixture::make_flow(&f.support, &f.service, &f.repo, &f.live).await;
    f.service.recover(chrono::Utc::now().timestamp_millis()).await.unwrap();
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    let rows = f.rows().await;
    assert_eq!(rows.iter().find(|r| r.delivery_id == row.delivery_id).unwrap().state.status, Status::Completed);
    assert_eq!(rows.iter().filter(|r| r.semantic_projection_json["task"]["leg"] == "result").count(), 1);
    assert_eq!(f.flow.task_store.get(&task_id).await.unwrap().status, bcs_message_flow::task_store::TaskLedgerStatus::Replied);
    let result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
    let source = f.repo.get_message_by_id(SESSION, &result.source_message_id).await.unwrap().unwrap();
    assert_eq!(source.content["text"], "WORKER_RESULT");
    let history = f.repo.list_session_history(SESSION, bcs_domain::MessageOwnerFilter::Any, None, None, None, 100).await.unwrap();
    assert!(history.messages.iter().all(|m| m.message_type != "run_reply"));
    assert_eq!(history.messages.iter().filter(|m| m.run_id == row.run_id.clone().unwrap() && m.message_type == "chat").count(), 1);
    f.start(result).await;
}

#[tokio::test]
async fn task_disabled_with_retained_context_uses_a_carrier_instead_of_native_inject() {
    let f = Fixture::new().await;
    f.context("bot-observer").await;
    let mut policy = f.live.snapshot.read().await.policy.clone(); policy.flow_enabled.task = false;
    f.flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    let (_, row) = f.dispatch().await;
    assert_eq!(row.semantic_projection_json["drain_context"], true);
    f.start(&row).await;
    assert!(serde_json::to_string(&f.support.bot_delivery.frames().await[0]).unwrap().contains("REQUIRED_CONTEXT_FOR_bot-observer"));
}

#[tokio::test]
async fn after_tool_empty_final_does_not_send_pre_tool_analysis_as_result() {
    let f = Fixture::new().await;
    let mut cmd = dispatch_command(); cmd.payload["response_mode"] = json!("after_last_tool_call");
    f.flow.handle_task_dispatch(cmd).await.unwrap();
    let row = f.start(&f.rows().await[0]).await;
    let mut delta = final_event(&row); delta.state = ChatEventState::Delta;
    delta.event_payload = json!({"delta_text":"ANALYSIS_ONLY"});
    f.flow.handle_bot_event(delta).await.unwrap();
    let mut tool = final_event(&row); tool.state = ChatEventState::ToolCallEnd; tool.event_type = "agent".into();
    tool.event_payload = json!({"stream":"tool", "data":{"phase":"result", "toolCallId":"call-task", "name":"lookup"}});
    f.flow.handle_bot_event(tool).await.unwrap();
    let mut terminal = final_event(&row); terminal.event_payload = json!({"message":{"content":[]}});
    f.flow.handle_bot_event(terminal).await.unwrap();
    let rows = f.rows().await;
    let result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
    let source = f.repo.get_message_by_id(SESSION, &result.source_message_id).await.unwrap().unwrap();
    assert_eq!(source.content["text"], "ANALYSIS_ONLY");
    assert_eq!(source.content["task_result_text"], "[no response]");
}

#[tokio::test]
async fn after_tool_window_handles_full_segment_and_delta_final_in_sse_mode() {
    for final_text in ["ABC", "BC", "C"] {
        let f = Fixture::new().await;
        let mut cmd = dispatch_command(); cmd.payload["response_mode"] = json!("after_last_tool_call");
        f.flow.handle_task_dispatch(cmd).await.unwrap();
        let row = f.start(&f.rows().await[0]).await;
        let mut delta = final_event(&row); delta.state = ChatEventState::Delta;
        delta.event_payload = json!({"delta_text":"A"});
        f.flow.handle_bot_event(delta.clone()).await.unwrap();
        let mut tool = final_event(&row); tool.state = ChatEventState::ToolCallEnd; tool.event_type = "agent".into();
        tool.event_payload = json!({"stream":"tool", "data":{"phase":"result", "toolCallId":"call-task", "name":"lookup"}});
        f.flow.handle_bot_event(tool).await.unwrap();
        let mut thinking = delta.clone(); thinking.event_type = "agent".into();
        thinking.event_payload = json!({"stream":"thinking", "delta_text":"NOT_VISIBLE_REPLY"});
        f.flow.handle_bot_event(thinking).await.unwrap();
        delta.event_payload = json!({"delta_text":"B"});
        f.flow.handle_bot_event(delta).await.unwrap();
        let mut terminal = final_event(&row); terminal.event_payload = json!({"message":{"content":[{"type":"text","text":final_text}]}});
        f.flow.handle_bot_event(terminal).await.unwrap();
        let rows = f.rows().await;
        let result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result").unwrap();
        let source = f.repo.get_message_by_id(SESSION, &result.source_message_id).await.unwrap().unwrap();
        assert_eq!(source.content["text"], "ABC");
        assert_eq!(source.content["task_result_text"], "BC");
    }
}

#[tokio::test]
async fn actual_runtime_orders_worker_tasks_and_manager_results_on_independent_lanes() {
    use bcs_message_flow::delivery_runtime::{DeliveryRuntime, DeliveryRuntimeConfig};
    use std::time::Duration;
    let f = Fixture::new().await;
    f.context("bot-observer").await;
    let (first_task, first) = f.dispatch().await;
    let (_, second) = f.dispatch().await;
    let worker = DeliveryRuntime { policy:Some(f.live.clone()), service:f.service.clone(),
        preparation:Arc::new(QueuedGroupPreparation { flow:Arc::downgrade(&f.flow), deliveries:f.service.clone() }),
        transport:f.support.bot_delivery.clone(), config:DeliveryRuntimeConfig {
            max_safe_retries:0, pause_dispatch:false, bots:Default::default(), tick:Duration::from_millis(2),
            expiry_tick:Duration::from_millis(20), io_timeout:Duration::from_secs(1), run_timeout:Duration::from_secs(60),
            cancel_timeout:Duration::from_secs(1), startup_recovery_grace:Duration::ZERO,
            max_tasks:4, max_abort_tasks:1 } };
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let job = tokio::spawn(worker.run(shutdown));
    f.wait_frames(1).await;
    let rows = f.rows().await;
    assert_eq!(rows.iter().find(|r| r.delivery_id == second.delivery_id).unwrap().state.status, Status::Queued);
    let wire = serde_json::to_value(&f.support.bot_delivery.frames().await[0]).unwrap();
    assert_eq!(wire["params"]["task_id"], first_task);
    assert!(wire.to_string().contains("REQUIRED_CONTEXT_FOR_bot-observer"));
    f.flow.handle_bot_event(final_event(&first)).await.unwrap();
    f.wait_frames(3).await; // Worker task 2 and Manager result 1 can both run.
    f.flow.handle_bot_event(final_event(&second)).await.unwrap();
    let rows = f.rows().await;
    let active_result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result"
        && r.semantic_projection_json["task"]["task_id"] == first_task).unwrap();
    let queued_result = rows.iter().find(|r| r.semantic_projection_json["task"]["leg"] == "result"
        && r.semantic_projection_json["task"]["task_id"] != first_task).unwrap();
    assert_eq!(queued_result.state.status, Status::Queued);
    f.flow.handle_bot_event(final_event(active_result)).await.unwrap();
    f.wait_frames(4).await;
    stop.send(true).unwrap();
    job.await.unwrap().unwrap();
}
