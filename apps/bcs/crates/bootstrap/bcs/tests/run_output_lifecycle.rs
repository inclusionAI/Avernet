//! Producer -> WS ingress -> MessageFlow -> Workbench/Internal-sink regressions.
//! Production Run registration and output delivery are wired together here;
//! only Bot network delivery and recipient authorization use test doubles.
use std::sync::Arc;
use bcs_domain::{GroupStrategy, MessageViewScope, ParticipantRole};
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore, TrackedBotRunContext};
use bcs_service_api::*;
use bcs_service_api::application::v1::ProtectedDeliveryBinding;
use bcs_ws::shared::{OutboundFrame, RunChannelManager};
use bcs_ws::web::{WorkbenchConnectionRegistry, WorkbenchFrontendDelivery, WorkbenchOutputAdmission};
use serde_json::json;
use tokio::sync::mpsc;

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
#[path = "../../../services/bcs-message-flow/tests/support/session.rs"]
mod sessions;
#[path = "../../../adapters/ws/bcs-ws/tests/support/mod.rs"]
mod ws_support;

const SESSION: &str = "group-1:task";

struct Fixture {
    support: support::FlowTestSupport,
    flow: Arc<BcsMessageFlow>,
    registry: Arc<WorkbenchConnectionRegistry>,
    resolver: Arc<ws_support::RecordingAdmission>,
    output: Arc<WorkbenchOutputAdmission>,
    contexts: Arc<TrackedBotRunContext>,
}
impl Fixture {
    async fn new() -> Self {
        let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let mut group = support.group.get("group-1").await.unwrap();
        group.group_strategy = GroupStrategy::ManagerWorker;
        for participant in &mut group.participants {
            participant.role = if participant.bot_uuid == "bot-driver" { ParticipantRole::Manager }
                else { ParticipantRole::Worker };
        }
        support.group.upsert(group.clone()).await.unwrap();
        let registry = Arc::new(WorkbenchConnectionRegistry::new());
        let resolver = Arc::new(ws_support::RecordingAdmission::new());
        resolver.set_scope(MessageViewScope::Participant);
        registry.set_delivery_admission(resolver.clone());
        let runs = Arc::new(RunChannelManager::new().with_output_permits(registry.output_permits()));
        let frontend = Arc::new(WorkbenchFrontendDelivery::new(registry.clone(), runs));
        let output = Arc::new(WorkbenchOutputAdmission::new(registry.clone(), resolver.clone()));
        let contexts = Arc::new(TrackedBotRunContext::new(Arc::new(MemoryBotRunContextStore::new())));
        let flow = Arc::new(BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(),
            support.bot_delivery.clone(), frontend)
            .with_message_repo(Arc::new(bcs_message_store::MemoryMessageRepo::new()))
            .with_tracked_run_context(contexts.clone()).with_output_admission(Some(output.clone()))
            .with_session_management(Arc::new(sessions::StaticSessionManagement::new(
                sessions::test_session(SESSION, "group-1", group.participants)))));
        flow.retain_terminal_events();
        Self { support, flow, registry, resolver, output, contexts }
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
}

#[path = "../../../services/bcs-message-flow/tests/support/operation_output.rs"]
mod operation_output;

fn dispatch_state(f: &Fixture) -> Arc<bcs_ws::bot::BotDispatchState> {
    Arc::new(bcs_ws::bot::BotDispatchState {
        expected_actor_id: None,
        bot_runtime: Arc::new(bcs_test_support::NoopBotRuntimeConnectionService),
        message_flow: f.flow.clone(),
        collaboration_runtime: Arc::new(bcs_test_support::NoopCollaborationRuntimeService),
        bot_run_context: f.contexts.clone(),
        bot_connections: Arc::new(bcs_ws::bot::BotConnectionRegistry::new()),
        run_channels: Arc::new(RunChannelManager::new().with_output_permits(f.registry.output_permits())),
        task_callback: None,
        session_management: Arc::new(bcs_session::NoopSessionManagementService),
        group_dispatch: Arc::new(bcs_test_support::NoopGroupDispatchContextPort),
        callback_dispatch: Arc::new(bcs_test_support::NoopSessionCallbackDispatchPort),
        system_message: None,
        coordination_processed: Arc::new(tokio::sync::Mutex::new(Default::default())),
        agent_credential_backfill: None,
        interactions: Arc::new(bcs_test_support::NoopInteractionService),
    })
}

#[tokio::test]
async fn system_context_reply_reaches_workbench() {
    let f = Fixture::new().await;
    f.resolver.set_scope(MessageViewScope::Full);
    let (_, mut rx) = f.subscribe("bot-driver").await;
    let group = f.support.group.get("group-1").await.unwrap();
    let dispatcher = bcs_system_message::SystemMessageDispatcherImpl::builder()
        .with_registry(f.support.registry.clone())
        .with_delivery(f.support.bot_delivery.clone())
        .with_frontend_delivery(f.support.frontend_delivery.clone())
        .with_bot_run_context(f.contexts.clone())
        .register(bcs_system_message::SessionContextMessageProducer)
        .build().unwrap();
    let outcome = dispatcher.dispatch(SystemMessageEvent::SessionContext {
        group_id:group.id.clone(), session_id:SESSION.into(), reason:"new task".into(),
        session_input:Some(json!("analyse")), task_ledger:None, driver_delivery:None,
    }, &group, SESSION, &group.participants).await.unwrap();
    assert!(outcome.successful_deliveries > 0);
    let frames = f.support.bot_delivery.frames().await;
    let run = frames.iter().find_map(|frame| match frame {
        bcs_protocol::BcsFrame::Request(request) if request.method == "chat.send" => Some(request.id.clone()), _=>None,
    }).expect("real system dispatcher creates reply-producing Send");
    let context = f.contexts.get_context(&run).await.unwrap();
    assert!(!context.terminal);
    let wire = json!({"type":"event", "event":"chat.event", "payload":{
        "run_id":run, "bcs_group_id":"group-1", "bcs_session_id":SESSION,
        "state":"delta", "delta_text":"manager is analysing"
    }}).to_string();
    let mut bot = Some(context.bot_id);
    let (tx, _) = mpsc::channel(8);
    bcs_ws::bot::dispatch_frame(&dispatch_state(&f), &wire, &tx, &mut bot).await.unwrap();
    let received:Vec<_> = std::iter::from_fn(||rx.try_recv().ok()).collect();
    println!("system binding={:?}; frames={received:?}", f.flow.message_tracker.output_binding(&run).await);
    assert!(!received.is_empty(), "legitimate SessionContext reply must not be dropped as unknown Run");
}

struct InternalRunPort(Arc<RunChannelManager>);
#[async_trait::async_trait]
impl ChatRunEventPort for InternalRunPort {
    async fn register(&self, run:String, session:String, tx:mpsc::Sender<String>, source:Option<String>, from:Option<String>) {
        self.0.register_internal(run, session, tx, source, from).await;
    }
    async fn unregister(&self, run:&str) { self.0.unregister(run).await; }
}
#[async_trait::async_trait]
impl ChatRunCleanupPort for InternalRunPort {
    async fn unregister(&self, run:&str) { self.0.unregister(run).await; }
}

async fn direct_result(gated: bool, accepted_alias: bool) {
    let f = Fixture::new().await;
    f.support.registry.set_visibility("bot-observer", "public").await;
    let runs = Arc::new(RunChannelManager::new().with_output_permits(f.registry.output_permits()));
    let flow = Arc::new(BcsMessageFlow::new(f.support.group.clone(), f.support.routing.clone(), f.support.registry.clone(),
        f.support.bot_delivery.clone(), Arc::new(WorkbenchFrontendDelivery::new(f.registry.clone(), runs.clone())))
        .with_tracked_run_context(f.contexts.clone())
        .with_output_admission(if gated { Some(f.output.clone()) } else { None }));
    let port = Arc::new(InternalRunPort(runs.clone()));
    let store = Arc::new(bcs_message_flow::a2a_chat::ChatRunStore::new());
    let a2a = bcs_message_flow::a2a_chat::A2aChat::new_with_run_ports(f.support.bot_delivery.clone(), store.clone(),
        10000, f.support.registry.clone(), Arc::new(bcs_test_support::NoopFriendCoreService), port.clone(), port)
        .with_bot_run_context(f.contexts.clone());
    let run = "direct-run";
    let session = "direct-session";
    a2a.start_async_chat(AsyncA2aChatCommand {
        caller: CallerContext::Bot(BotActor { bot_uuid:"bot-driver".into() }), target_bot_id:"bot-observer".into(),
        message:"hello".into(), from_actor_id:None, run_channel_from:None, authenticated_staff_id:None,
        run_id:run.into(), session_key:session.into(), timeout_ms:10000, client:None, tags:vec![],
        response_mode:ChatResponseMode::Full, caller_wait_mode:None, organization_code:None, provider_bypass_headers:vec![],
    }).await.unwrap();
    assert!(runs.is_registered(run).await);
    assert!(f.contexts.get_context(run).await.is_some());
    let mut state = dispatch_state(&f);
    let state_mut = Arc::get_mut(&mut state).unwrap();
    state_mut.message_flow = flow.clone();
    state_mut.run_channels = runs;
    let (tx, _) = mpsc::channel(8);
    let mut bot = Some("bot-observer".into());
    let event_run = if accepted_alias {
        let ack = json!({"type":"res", "id":run, "ok":true, "payload":{"run_id":"engine-alias"}}).to_string();
        bcs_ws::bot::dispatch_frame(&state, &ack, &tx, &mut bot).await.unwrap();
        assert!(state.run_channels.is_registered("engine-alias").await);
        assert_eq!(flow.message_tracker.output_binding("engine-alias").await, None);
        "engine-alias"
    } else { run };
    let wire = json!({"type":"event", "event":"chat.event", "payload":{
        "run_id":event_run, "bcs_group_id":session, "state":"final", "message":{
            "role":"assistant", "timestamp":0, "content":[{"type":"text", "text":"A2A answer"}]
        }
    }}).to_string();
    bcs_ws::bot::dispatch_frame(&state, &wire, &tx, &mut bot).await.unwrap();
    let record = tokio::time::timeout(std::time::Duration::from_secs(2), async {
        loop {
            let record = store.get(run).await.unwrap();
            if record.state.is_terminal() { break record; }
            tokio::task::yield_now().await;
        }
    }).await.expect("drain should observe final or channel close");
    println!("gated={gated}, binding={:?}, state={:?}, content={:?}, error={:?}",
        flow.message_tracker.output_binding(run).await, record.state, record.accumulated_content, record.error_message);
    assert_eq!(record.accumulated_content, "A2A answer", "a valid final must reach the Internal sink");
    assert_eq!(flow.message_tracker.output_binding(run).await, None);
    let calls = f.resolver.call_count();
    // A projected alias context must not recreate an independent producer.
    let _ = bcs_ws::bot::dispatch_frame(&state, &wire, &tx, &mut bot).await;
    assert_eq!(store.get(run).await.unwrap().version, record.version);
    assert_eq!(f.resolver.call_count(), calls);
}
#[tokio::test]
async fn direct_a2a_response_survives_output_gate() { direct_result(true, false).await; }
#[tokio::test]
async fn control_direct_a2a_without_output_gate() { direct_result(false, false).await; }
#[tokio::test]
async fn direct_a2a_accepted_alias_keeps_its_internal_sink() { direct_result(true, true).await; }

#[tokio::test]
async fn completed_alias_cannot_repeat_downstream_work() {
    let f = Fixture::new().await;
    let mut group = f.support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::Chat;
    f.support.group.upsert(group).await.unwrap();
    let (_id, _rx) = f.subscribe("bot-driver").await;
    let sent = f.flow.handle_web_send(operation_output::web_send("group-1", "human_1", Some(SESSION), &["bot-driver"])).await.unwrap();
    let run = &sent.active_run_ids[0];
    let alias = "completed-engine-alias";
    f.flow.register_task_run_alias(run, alias, "bot-driver").await.unwrap();
    f.contexts.bind_downstream_run_id(run, alias).await.unwrap();
    let state = dispatch_state(&f);
    let (tx, _) = mpsc::channel(8);
    let mut bot = Some("bot-driver".into());
    let wire = json!({"type":"event", "event":"chat.event", "payload":{
        "run_id":alias, "bcs_group_id":"group-1", "bcs_session_id":SESSION, "state":"final", "message":{
            "role":"assistant", "timestamp":0, "content":[{"type":"text", "text":"@bot-observer please analyse"}]
        }
    }}).to_string();
    bcs_ws::bot::dispatch_frame(&state, &wire, &tx, &mut bot).await.unwrap();
    assert!(f.contexts.get_context(run).await.unwrap().terminal);
    assert!(f.flow.message_tracker.output_binding(run).await.is_none());
    let before = f.support.bot_delivery.frames().await.len();
    let repo = f.flow.message_repo.as_ref().unwrap();
    let history_before = repo.get_current_seq(SESSION).await.unwrap();
    let result = bcs_ws::bot::dispatch_frame(&state, &wire, &tx, &mut bot).await;
    let all = f.support.bot_delivery.frames().await;
    let kinds:Vec<_> = all[before..].iter().filter_map(|frame| match frame {
        bcs_protocol::BcsFrame::Request(request) => Some(request.method.as_str()), _=>None,
    }).collect();
    println!("alias context={:?}; replay={result:?}; deliveries before={before}, after={}, new methods={kinds:?}",
        f.contexts.get_context(alias).await, all.len());
    assert_eq!(all.len(), before, "completed alias replay must not create another chat.send");
    assert_eq!(repo.get_current_seq(SESSION).await.unwrap(), history_before);
    assert!(f.flow.message_tracker.snapshot_chat_bufs().await.is_empty());
}

#[tokio::test]
async fn unknown_run_cannot_persist_or_relay() {
    let f = Fixture::new().await;
    let (_id, mut rx) = f.subscribe("bot-driver").await;
    let before = f.support.group.message_count("group-1").await.unwrap();
    let repo = f.flow.message_repo.as_ref().unwrap();
    let history_before = repo.get_current_seq(SESSION).await.unwrap();
    let result = f.flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-driver".into(), run_id: "not-registered".into(), group_id: "group-1".into(),
        bcs_session_id: Some(SESSION.into()), state: ChatEventState::Final, event_type: "chat.event".into(),
        event_payload: json!({"message":{"content":[{"type":"text","text":"@bot-observer do work"}]}}),
    }).await.unwrap();
    assert!(result.bot_deliveries.is_empty() && result.frontend_deliveries.is_empty());
    assert!(f.support.bot_delivery.frames().await.is_empty());
    assert!(rx.try_recv().is_err());
    assert_eq!(f.support.group.message_count("group-1").await.unwrap(), before);
    assert_eq!(f.resolver.call_count(), 0);
    assert_eq!(repo.get_current_seq(SESSION).await.unwrap(), history_before);
    assert!(f.flow.message_tracker.snapshot_chat_bufs().await.is_empty());
}

#[tokio::test]
async fn simultaneous_canonical_and_alias_finals_do_not_repeat_work() {
    let f = Fixture::new().await;
    let mut group = f.support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::Chat;
    f.support.group.upsert(group).await.unwrap();
    let sent = f.flow.handle_web_send(operation_output::web_send("group-1", "human_1", Some(SESSION), &["bot-driver"])).await.unwrap();
    let run = &sent.active_run_ids[0];
    f.flow.register_task_run_alias(run, "alias", "bot-driver").await.unwrap();
    let before = f.support.bot_delivery.frames().await.len();
    let command = BotEventCommand {
        bot_id: "bot-driver".into(), run_id: run.clone(), group_id: "group-1".into(),
        bcs_session_id: Some(SESSION.into()), state: ChatEventState::Final, event_type: "chat.event".into(),
        event_payload: json!({"message":{"content":[{"type":"text","text":"@bot-observer do work"}]}}),
    };
    let mut alias = command.clone();
    alias.run_id = "alias".into();
    let (a, b) = tokio::join!(f.flow.handle_bot_event(command), f.flow.handle_bot_event(alias));
    a.unwrap(); b.unwrap();
    let frames = f.support.bot_delivery.frames().await;
    let sends = frames[before..].iter().filter(|frame| matches!(frame,
        bcs_protocol::BcsFrame::Request(request) if request.method == "chat.send")).count();
    assert_eq!(sends, 1, "both identities must serialize on the same Run and recheck lineage after waiting");
}

#[tokio::test]
async fn independent_alias_keeps_canonical_im_source() {
    let f = Fixture::new().await;
    let run = "independent";
    f.contexts.put_context(BotRunContext { run_id:run.into(), bot_id:"bot-driver".into(),
        group_id:"group-1".into(), bcs_session_id:Some(SESSION.into()), deadline_ms:u64::MAX, terminal:false }).await;
    f.contexts.register_active_run(ActiveBotRunContext { canonical_run_id:run.into(), downstream_run_id:run.into(),
        downstream_session_key:Some(SESSION.into()), scope:BotRunScope { group_id:"group-1".into(),
            bot_id:"bot-driver".into(), session_id:SESSION.into() }, transport_owner:BotRunTransportOwner::WebSocket,
        provider_bypass_headers:Vec::new(), deadline_ms:u64::MAX }).await.unwrap();
    f.flow.message_tracker.cache_channel_source_message_id(run, "im-message").await;
    f.flow.register_task_run_alias(run, "independent-alias", "bot-driver").await.unwrap();
    assert!(f.flow.rebind_channel_source_message(run, "independent-alias").await.unwrap());
    assert_eq!(f.flow.message_tracker.channel_source_message_id(run).await.as_deref(), Some("im-message"));
    assert_eq!(f.flow.message_tracker.channel_source_message_id("independent-alias").await, None);
}

#[tokio::test]
async fn registered_producer_without_active_index_can_bind_its_own_alias() {
    let f = Fixture::new().await;
    f.contexts.put_context(BotRunContext { run_id:"context-only".into(), bot_id:"bot-driver".into(),
        group_id:"group-1".into(), bcs_session_id:Some(SESSION.into()), deadline_ms:u64::MAX, terminal:false }).await;
    assert_eq!(f.flow.register_task_run_alias("context-only", "wrong-bot-alias", "bot-observer").await.unwrap(),
        TaskRunAliasRegistration::Rejected);
    assert_eq!(f.flow.register_task_run_alias("context-only", "context-alias", "bot-driver").await.unwrap(),
        TaskRunAliasRegistration::NotTask);
    let (_id, mut frames) = f.subscribe("bot-driver").await;
    f.flow.handle_bot_event(BotEventCommand { run_id:"context-alias".into(), bot_id:"bot-driver".into(),
        group_id:"group-1".into(), bcs_session_id:Some(SESSION.into()), state:ChatEventState::Delta,
        event_type:"chat.event".into(), event_payload:json!({"delta_text":"reply"}) }).await.unwrap();
    assert!(matches!(frames.try_recv().unwrap(), OutboundFrame::Data { .. }));
}
