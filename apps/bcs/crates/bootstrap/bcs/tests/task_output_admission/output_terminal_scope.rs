use super::*;

#[tokio::test]
async fn completed_nonqueued_run_cannot_publish_to_late_subscriber() {
    let f = Fixture::new(false, 100).await;
    let (_, mut original) = f.subscribe("bot-driver").await;
    let sent = f.flow.handle_web_send(operation_output::web_send("group-1", "human_1", Some(SESSION), &["bot-driver"])).await.unwrap();
    let run = sent.active_run_ids.first().unwrap();
    let context = f.contexts.get_context(run).await.unwrap();
    f.flow.register_task_run_alias(run, "completed-alias", &context.bot_id).await.unwrap();
    let event = BotEventCommand { bot_id: context.bot_id, run_id: run.clone(), group_id: "group-1".into(),
        bcs_session_id: Some(SESSION.into()), state: ChatEventState::Final, event_type: "chat.event".into(),
        event_payload: json!({"message":{"content":[{"type":"text","text":"original accepted output"}]}}) };
    f.flow.handle_bot_event(event.clone()).await.unwrap();
    assert!(f.contexts.get_context(run).await.unwrap().terminal);
    drain(&mut original);
    let (_, mut late) = f.subscribe("bot-driver").await;
    let before = f.resolver.call_count();
    let state = dispatch_state(&f);
    let (tx, _rx) = mpsc::channel(8);
    let mut registered = Some(event.bot_id.clone());
    let wire = json!({"type":"event", "event":"chat.event", "payload": {
        "run_id":run, "bcs_group_id":"group-1", "bcs_session_id":SESSION,
        "state":"final", "message":{"role":"assistant", "timestamp":0, "content":[{"type":"text", "text":"replayed original output"}]}
    }}).to_string();
    let parsed: serde_json::Value = serde_json::from_str(&wire).unwrap();
    serde_json::from_value::<bcs_protocol::ChatEventPayload>(parsed["payload"].clone()).expect("valid wire chat");
    assert!(bcs_ws::bot::dispatch_frame(&state, &wire, &tx, &mut registered).await.is_err());
    let frames: Vec<_> = std::iter::from_fn(|| late.try_recv().ok()).collect();
    assert!(frames.is_empty(), "terminal replay must not be re-admitted as a fresh independent event");
    assert_eq!(f.resolver.call_count(), before);
    // Calling the application directly must not downgrade a cleaned Run into
    // fresh authorization even without the WS ingress terminal check.
    for run_id in [run.as_str(), "completed-alias"] {
        for state in [ChatEventState::Final, ChatEventState::Delta] {
            let mut replay = event.clone();
            replay.run_id = run_id.into();
            replay.state = state.clone();
            f.flow.handle_bot_event(replay).await.unwrap();
            let mut payload = parsed.clone();
            payload["payload"]["run_id"] = json!(run_id);
            payload["payload"]["state"] = json!(if state == ChatEventState::Final { "final" } else { "delta" });
            let _ = bcs_ws::bot::dispatch_frame(&dispatch_state(&f), &payload.to_string(), &tx, &mut registered).await;
            assert!(late.try_recv().is_err());
            assert!(original.try_recv().is_err());
            assert_eq!(f.resolver.call_count(), before);
        }
    }
}

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
        system_message: None, coordination_processed: Arc::new(tokio::sync::Mutex::new(Default::default())),
        agent_credential_backfill: None, interactions: Arc::new(bcs_test_support::NoopInteractionService),
    })
}

#[tokio::test]
async fn legacy_agent_error_closes_output_permit() {
    let f = Fixture::new(false, 100).await;
    f.resolver.set_scope(MessageViewScope::Full);
    let (conn, mut frames) = f.subscribe("bot-driver").await;
    let sent = f.flow.handle_web_send(operation_output::web_send("group-1", "human_1", Some(SESSION), &["bot-driver"])).await.unwrap();
    let run = sent.active_run_ids.first().unwrap();
    let context = f.contexts.get_context(run).await.unwrap();
    let operation = f.flow.message_tracker.operation_of_run(run).await.unwrap();
    drain(&mut frames);
    let state = dispatch_state(&f);
    let (tx, _rx) = mpsc::channel(8);
    let mut registered = Some(context.bot_id);
    let wire = json!({"type":"event", "event":"agent", "payload": {
        "run_id":run, "bcs_group_id":"group-1", "bcs_session_id":SESSION,
        "stream":"error", "ts":0, "data":{"message":"provider failed"}
    }}).to_string();
    bcs_ws::bot::dispatch_frame(&state, &wire, &tx, &mut registered).await.unwrap();
    let emitted: Vec<_> = std::iter::from_fn(|| frames.try_recv().ok()).collect();
    assert!(!f.permits(&operation, run, conn).await, "a terminal agent/error must close its admitted stream");
    assert!(emitted.iter().any(|frame| matches!(frame, OutboundFrame::Terminal { .. })));
    assert!(f.contexts.get_context(run).await.unwrap().terminal);
    assert!(f.flow.message_tracker.output_binding(run).await.is_none());
}

#[derive(Default)]
struct ReviewEventFactory;
impl bcs_service_api::port::EventRecordFactoryPort for ReviewEventFactory {
    fn prepare(&self, event: bcs_service_api::port::NewEvent) -> Result<Option<bcs_service_api::port::repo::AppendEventRecord>, bcs_service_api::port::EventRecordError> {
        Ok(Some(bcs_service_api::port::repo::AppendEventRecord { event, env:"local".into(),
            recorded_at:"2026-10-26T00:00:00.000Z".into(), retention_until_ms:u64::MAX }))
    }
}
#[tokio::test]
async fn persistent_send_preserves_implicitly_admitted_session_for_output() {
    let f = Fixture::new(false, 100).await;
    let session = "group-1:00000000";
    let (control, _) = mpsc::channel(8);
    let (frames, mut rx) = mpsc::channel(64);
    let conn = f.registry.subscribe_protected_with_shutdown(session.into(), control, Some("bot-driver".into()), None,
        tokio_util::sync::CancellationToken::new(), ProtectedDeliveryBinding {
            tenant:None, user_id:"user".into(), group_id:"group-1".into(), session_id:Some(session.into()),
            view_actor_id:"bot-driver".into(), connection_instance_id:0 }, frames).await.unwrap();
    let group = f.support.group.get("group-1").await.unwrap();
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new().with_environment("local".into())
        .with_event_store(Arc::new(bcs_event_store::MemoryEventStore::new())));
    let sessions = Arc::new(sessions::StaticSessionManagement::new(sessions::test_session(session, "group-1", group.participants)));
    let authority = bcs_test_support::lazy_authority_chain();
    authority.hook.seed_owner("bot-observer", "1");
    let runs = Arc::new(RunChannelManager::new().with_output_permits(f.registry.output_permits()));
    let flow = Arc::new(BcsMessageFlow::new(f.support.group.clone(), f.support.routing.clone(), f.support.registry.clone(),
        f.support.bot_delivery.clone(), Arc::new(WorkbenchFrontendDelivery::new(f.registry.clone(), runs)))
        .with_message_repo(repo).with_event_record_factory(Arc::new(ReviewEventFactory))
        .with_bot_run_context(f.contexts.clone()).with_session_management(sessions)
        .with_bot_authority_hook(Some(authority.hook)).with_output_admission(Some(f.output.clone())));
    flow.handle_persistent_group_send(operation_output::persistent_cmd("bot-observer", 100)).await.unwrap();
    let (op, run) = f.latest();
    assert!(f.permits(&op, &run, conn).await, "subscriber was admitted to the resolved Session");
    let context = f.contexts.get_context(&run).await.unwrap();
    assert_eq!(context.bcs_session_id.as_deref(), Some(session));
    drain(&mut rx);
    let mut state = dispatch_state(&f);
    Arc::get_mut(&mut state).unwrap().message_flow = flow;
    let (tx, _) = mpsc::channel(8);
    let mut bot = Some(context.bot_id.clone());
    let wire = json!({"type":"event", "event":"chat.event", "payload": {
        "run_id":run, "bcs_group_id":"group-1", "state":"delta", "delta_text":"reply" }}).to_string();
    let parsed: serde_json::Value = serde_json::from_str(&wire).unwrap();
    serde_json::from_value::<bcs_protocol::ChatEventPayload>(parsed["payload"].clone()).unwrap();
    bcs_ws::bot::dispatch_frame(&state, &wire, &tx, &mut bot).await.unwrap();
    let received:Vec<_> = std::iter::from_fn(|| rx.try_recv().ok()).collect();
    assert!(!received.is_empty(), "the admitted Session subscriber must receive the Bot reply");
}

#[tokio::test]
async fn agent_error_ends_only_its_worker_stream_in_direct_and_queued_modes() {
    for queued in [false, true] {
        let f = Fixture::new(queued, 100).await;
        f.resolver.set_scope(MessageViewScope::Full);
        let (conn, mut frames) = f.subscribe("bot-driver").await;
        f.dispatch().await.unwrap();
        let (operation, run) = f.latest();
        if queued { f.start().await; }
        drain(&mut frames);
        let state = dispatch_state(&f);
        let (tx, _) = mpsc::channel(8);
        let mut bot = Some("bot-observer".into());
        let wire = json!({"type":"event", "event":"agent", "payload": {
            "run_id":run, "bcs_group_id":"group-1", "bcs_session_id":SESSION,
            "stream":"error", "ts":0, "data":{"errorMessage":"worker failed"}
        }}).to_string();
        bcs_ws::bot::dispatch_frame(&state, &wire, &tx, &mut bot).await.unwrap();
        assert!(!f.permits(&operation, &run, conn).await, "queued={queued}");
        assert!(std::iter::from_fn(|| frames.try_recv().ok()).any(|frame| matches!(frame, OutboundFrame::Terminal { .. })), "queued={queued}");
        assert!(f.contexts.get_context(&run).await.unwrap().terminal);
    }
}

#[tokio::test]
async fn agent_error_preserves_a_running_sibling() {
    let f = Fixture::new(false, 100).await;
    let mut group = f.support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::Chat;
    f.support.group.upsert(group).await.unwrap();
    f.resolver.set_scope(MessageViewScope::Full);
    let (conn, mut frames) = f.subscribe("bot-driver").await;
    let sent = f.flow.handle_web_send(operation_output::web_send("group-1", "human_1", Some(SESSION), &["bot-driver", "bot-observer"])).await.unwrap();
    assert_eq!(sent.active_run_ids.len(), 2);
    let run = &sent.active_run_ids[0];
    let sibling = &sent.active_run_ids[1];
    let operation = f.flow.message_tracker.operation_of_run(run).await.unwrap();
    let mut bot = Some(f.contexts.get_context(run).await.unwrap().bot_id);
    let (tx, _) = mpsc::channel(8);
    drain(&mut frames);
    let wire = json!({"type":"event", "event":"agent", "payload": {
        "run_id":run, "bcs_group_id":"group-1", "bcs_session_id":SESSION,
        "stream":"error", "ts":0, "data":{"errorMessage":"failed"}
    }}).to_string();
    bcs_ws::bot::dispatch_frame(&dispatch_state(&f), &wire, &tx, &mut bot).await.unwrap();
    assert!(!f.permits(&operation, run, conn).await);
    assert!(f.permits(&operation, sibling, conn).await);
}

#[tokio::test]
async fn agent_error_retires_a_saturated_connection_with_bounded_backpressure() {
    let f = Fixture::new(false, 100).await;
    f.resolver.set_scope(MessageViewScope::Full);
    let (control, _control_rx) = mpsc::channel(8);
    let (frames, mut rx) = mpsc::channel(1);
    let fill = frames.clone();
    let shutdown = tokio_util::sync::CancellationToken::new();
    let conn = f.registry.subscribe_protected_with_shutdown(SESSION.into(), control,
        Some("bot-driver".into()), None, shutdown.clone(), ProtectedDeliveryBinding {
            tenant:None, user_id:"user".into(), group_id:"group-1".into(), session_id:Some(SESSION.into()),
            view_actor_id:"bot-driver".into(), connection_instance_id:0,
        }, frames).await.unwrap();
    let sent = f.flow.handle_web_send(operation_output::web_send("group-1", "human_1", Some(SESSION), &["bot-driver"])).await.unwrap();
    let run = &sent.active_run_ids[0];
    let operation = f.flow.message_tracker.operation_of_run(run).await.unwrap();
    drain(&mut rx);
    fill.send(OutboundFrame::Data { payload:"already queued".into() }).await.unwrap();
    let mut bot = Some("bot-driver".into());
    let (tx, _) = mpsc::channel(8);
    let wire = json!({"type":"event", "event":"agent", "payload": {
        "run_id":run, "bcs_group_id":"group-1", "bcs_session_id":SESSION,
        "stream":"error", "ts":0, "data":{"errorMessage":"failed"}
    }}).to_string();
    tokio::time::timeout(std::time::Duration::from_secs(5),
        bcs_ws::bot::dispatch_frame(&dispatch_state(&f), &wire, &tx, &mut bot)).await.unwrap().unwrap();
    assert!(shutdown.is_cancelled(), "a lost terminal must not leave a live connection");
    assert!(!f.permits(&operation, run, conn).await);
}

#[tokio::test]
async fn explicit_independent_run_keeps_live_alias_output_but_not_terminal_replay() {
    let f = Fixture::new(false, 100).await;
    let (_, mut frames) = f.subscribe("bot-driver").await;
    // Trusted internal/relay send registration, unlike an arbitrary ingress Run.
    let run = "independent-run";
    f.flow.message_tracker.register_independent_output_run(run, u64::MAX).await;
    let scope = BotRunScope { group_id:"group-1".into(), bot_id:"bot-driver".into(), session_id:SESSION.into() };
    f.contexts.put_context(BotRunContext { run_id:run.into(), bot_id:"bot-driver".into(), group_id:"group-1".into(),
        bcs_session_id:Some(SESSION.into()), deadline_ms:u64::MAX, terminal:false }).await;
    f.contexts.register_active_run(ActiveBotRunContext { canonical_run_id:run.into(), downstream_run_id:run.into(),
        downstream_session_key:Some(SESSION.into()), scope, transport_owner:BotRunTransportOwner::WebSocket,
        provider_bypass_headers:Vec::new(), deadline_ms:u64::MAX }).await.unwrap();
    f.flow.register_task_run_alias(run, "independent-alias", "bot-driver").await.unwrap();
    let command = BotEventCommand { bot_id:"bot-driver".into(), run_id:"independent-alias".into(), group_id:"group-1".into(),
        bcs_session_id:Some(SESSION.into()), state:ChatEventState::Delta, event_type:"chat.event".into(), event_payload:json!({"delta_text":"reply"}) };
    f.flow.handle_bot_event(command.clone()).await.unwrap();
    assert!(matches!(frames.try_recv().unwrap(), OutboundFrame::Data { .. }));
    let mut terminal = command.clone();
    terminal.state = ChatEventState::Final;
    f.flow.handle_bot_event(terminal).await.unwrap();
    drain(&mut frames);
    let calls = f.resolver.call_count();
    f.flow.handle_bot_event(command).await.unwrap();
    assert!(frames.try_recv().is_err());
    assert_eq!(f.resolver.call_count(), calls);
}
