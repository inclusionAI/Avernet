use super::{
    Arc,
    BTreeMap,
    BcsMessageFlow,
    BotEventCommand,
    ChannelOutboundEventKind,
    ChannelRenderHint,
    ChannelService,
    ChatEventState,
    GroupCoreService,
    MessageFlowService,
    RecordingChannelService,
    Value,
    json,
    session_support,
    state_payload_name,
    support
};

#[tokio::test]
async fn unmanaged_final_preserves_publication_and_legacy_group_fallback() {
    use bcs_service_api::ManagedMessageDeliveryService;
    for failure in ["read", "missing", "empty", "no_id", "no_service"] {
        let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
        let service = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()));
        let group = support.group.get("group-1").await.unwrap();
        let mut session = session_support::test_session("group-1:final", "group-1", group.participants);
        if failure == "missing" { session.id = "different-session".into(); }
        if failure == "empty" { session.participants.clear(); }
        let sessions = session_support::StaticSessionManagement::new(session);
        let sessions = if failure == "read" { sessions.with_get_failure() } else { sessions };
        let mut flow = BcsMessageFlow::new(support.group, support.routing, support.registry,
            support.bot_delivery.clone(), support.frontend_delivery.clone())
            .with_message_repo(repo).with_managed_deliveries(service.clone());
        // Queue admission requires a session ID; legacy sessionless routing does not.
        if failure != "no_id" {
            flow = flow.with_group_delivery_limits(BTreeMap::from([("bot-observer".into(), 100)]));
        }
        if failure != "no_service" { flow = flow.with_session_management(Arc::new(sessions)); }
        let channel = Arc::new(RecordingChannelService::default());
        assert!(flow.channel_slot().set(channel.clone()).is_ok());
        let payload = json!({"message":{"role":"assistant","content":[{"type":"text","text":"original final"}]}});
        let result = flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-driver".into(), run_id: "session-failure".into(), group_id: "group-1".into(),
            bcs_session_id: if failure == "no_id" { None } else { Some("group-1:final".into()) }, state: ChatEventState::Final,
            event_type: "chat.event".into(), event_payload: payload.clone(),
        }).await;
        result.unwrap();
        let events = support.frontend_delivery.events().await;
        assert_eq!(events.len(), 1);
        let wire: Value = serde_json::from_str(&events[0]).unwrap();
        assert_eq!(wire["payload"]["message"], payload["message"]);
        let outbound = channel.outbound().await;
        assert_eq!(outbound.len(), 1);
        assert_eq!(outbound[0].raw_payload, payload);
        let queued = service.snapshot(None).await.unwrap();
        let direct = support.bot_delivery.frames().await;
        assert!(!queued.is_empty() || !direct.is_empty(), "group routing must deliver the reply for {failure}");
    }
}

#[tokio::test]
async fn queue_reconstruction_never_rewrites_channel_or_frontend_final() {
    use bcs_service_api::ManagedMessageDeliveryService;
    use bcs_service_api::port::repo::MessageRepoPort;
    for queued in [false, true] {
        for final_text in ["工具前工具后", "工具后补充", "重写后的回答", ""] {
            let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
            let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
            let service = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()));
            let mut flow = BcsMessageFlow::new(support.group.clone(), support.routing, support.registry,
                support.bot_delivery, support.frontend_delivery.clone())
                .with_message_repo(repo.clone()).with_managed_deliveries(service.clone());
            if queued {
                flow = flow.with_group_delivery_limits(BTreeMap::from([("bot-driver".into(), 100), ("bot-observer".into(), 100)]));
            }
            let channel = Arc::new(RecordingChannelService::default());
            assert!(flow.channel_slot().set(channel.clone()).is_ok());
            let event = |state, event_type: &str, payload| BotEventCommand {
                bot_id: "bot-driver".into(), run_id: "raw-final-run".into(), group_id: "group-1".into(),
                bcs_session_id: Some("group-1:raw-final".into()), state, event_type: event_type.into(), event_payload: payload,
            };
            for (state, kind, payload) in [
                (ChatEventState::Delta, "chat.event", json!({"message":{"content":[{"type":"text","text":"工具前"}]}})),
                (ChatEventState::Delta, "agent", json!({"stream":"tool","data":{"phase":"result","name":"search","toolCallId":"tool","result":"done"}})),
                (ChatEventState::Delta, "chat.event", json!({"message":{"content":[{"type":"text","text":"工具后"}]}})),
            ] { flow.handle_bot_event(event(state, kind, payload)).await.unwrap(); }
            let payload = json!({"state":"final","message":{"role":"assistant","content":[{"type":"text","text":final_text}]},"custom":"preserved"});
            let before = support.frontend_delivery.events().await.len();
            flow.handle_bot_event(event(ChatEventState::Final, "chat.event", payload.clone())).await.unwrap();
            let outbound = channel.outbound().await;
            let finals: Vec<_> = outbound.iter().filter(|m| m.kind == ChannelOutboundEventKind::ChatFinal).collect();
            assert_eq!(finals.len(), 1);
            assert_eq!(finals[0].raw_payload, payload);
            assert_eq!(finals[0].text.as_deref().unwrap_or(""), final_text);
            let events = support.frontend_delivery.events().await;
            let final_frame: Value = serde_json::from_str(&events[before]).unwrap();
            assert_eq!(final_frame["payload"]["message"], payload["message"]);
            assert_eq!(final_frame["payload"]["custom"], "preserved");
            let deliveries = service.snapshot(Some("group-1:raw-final")).await.unwrap();
            if queued {
                assert!(!deliveries.is_empty());
                let message = repo.get_message_by_id("group-1:raw-final", &deliveries[0].source_message_id).await.unwrap().unwrap();
                assert_eq!(message.message_type, "run_reply");
                let expected = match final_text {
                    "工具前工具后" | "" => "工具前工具后",
                    "工具后补充" => "工具前工具后补充",
                    _ => "工具前工具后重写后的回答",
                };
                assert_eq!(message.content["text"], expected);
                assert_eq!(message.content["normalization"]["raw_final"], final_text);
            } else { assert!(deliveries.is_empty()); }
        }
    }
}

#[tokio::test]
async fn bot_delta_channel_outbound_uses_delta_text_not_synthesized_snapshot() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "bot-observer")
        .unwrap()
        .bot_name = None;
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );
    let recording_channel = Arc::new(RecordingChannelService::default());
    let channel: Arc<dyn ChannelService> = recording_channel.clone();
    assert!(flow.channel_slot().set(channel).is_ok());
    flow.message_tracker
        .cache_channel_source_message_id("run-channel-delta", "source-msg-1")
        .await;
    let group_get_count_before = support.group.get_count("group-1").await;

    for delta in ["你", "好"] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-channel-delta".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "state": "delta",
                "delta_text": delta,
            }),
            state: ChatEventState::Delta,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .unwrap();
    }

    let outbound = recording_channel.outbound().await;
    assert_eq!(outbound.len(), 2);
    assert_eq!(outbound[0].sender_label, "Observer");
    assert_eq!(
        outbound[0].source_im_message_id.as_deref(),
        Some("source-msg-1")
    );
    assert_eq!(
        outbound[1].source_im_message_id.as_deref(),
        Some("source-msg-1")
    );
    assert_eq!(
        support
            .registry
            .including_deleted_get_count("bot-observer")
            .await,
        1,
        "streaming deltas should resolve a missing provider bot name only once per run"
    );
    assert_eq!(
        support.group.get_count("group-1").await - group_get_count_before,
        1,
        "streaming deltas should resolve sender role and name from the group only once per run"
    );
    assert_eq!(outbound[0].text.as_deref(), Some("你"));
    assert_eq!(outbound[0].raw_payload["message"]["content"][0]["text"], "你");
    assert_eq!(
        outbound[1].text.as_deref(),
        Some("好"),
        "ChatDelta sent to channel adapters must stay incremental even when BCS synthesizes cumulative message.content for frontend rendering"
    );
    assert_eq!(outbound[1].raw_payload["message"]["content"][0]["text"], "你好");
}

#[tokio::test]
async fn bot_final_channel_outbound_resolves_missing_sender_name() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "bot-observer")
        .unwrap()
        .bot_name = Some("bot-observer".to_string());
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );
    let recording_channel = Arc::new(RecordingChannelService::default());
    let channel: Arc<dyn ChannelService> = recording_channel.clone();
    assert!(flow.channel_slot().set(channel).is_ok());

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-channel-final".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "你好"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let outbound = recording_channel.outbound().await;
    assert_eq!(outbound.len(), 1);
    assert_eq!(outbound[0].kind, ChannelOutboundEventKind::ChatFinal);
    assert_eq!(outbound[0].sender_label, "Observer");
    assert_eq!(outbound[0].text.as_deref(), Some("你好"));
    assert!(
        flow.message_tracker
            .channel_sender_info("run-channel-final")
            .await
            .is_none(),
        "terminal run cleanup should remove cached channel sender metadata"
    );
}

#[tokio::test]
async fn bot_terminal_failures_emit_safe_system_channel_feedback() {
    for (state, run_id, expected_text) in [
        (
            ChatEventState::Error,
            "run-error-1234567890",
            "机器人连接或执行失败，请稍后重试。",
        ),
        (
            ChatEventState::Aborted,
            "run-aborted-1234567890",
            "机器人已中止本次处理，请重新发送。",
        ),
    ] {
        let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let recording_channel = Arc::new(RecordingChannelService::default());
        let flow = BcsMessageFlow::new(
            support.group.clone(),
            support.routing.clone(),
            support.registry.clone(),
            support.bot_delivery.clone(),
            support.frontend_delivery.clone(),
        );
        let channel: Arc<dyn ChannelService> = recording_channel.clone();
        assert!(flow.channel_slot().set(channel).is_ok());

        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: run_id.to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "state": state_payload_name(&state),
                "errorMessage": "provider secret detail",
                "errorKind": "provider_internal_error",
            }),
            state,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .unwrap();

        let outbound = recording_channel.outbound().await;
        assert_eq!(outbound.len(), 1);
        let outbound = &outbound[0];
        assert_eq!(outbound.kind, ChannelOutboundEventKind::System);
        assert_eq!(outbound.render_hint, ChannelRenderHint::Render);
        let text = outbound.text.as_deref().expect("system feedback text");
        assert!(text.contains(expected_text));
        assert!(text.contains("追踪标识"));
        assert!(!text.contains("provider secret detail"));
        assert!(!text.contains("provider_internal_error"));
        assert!(!text.contains(run_id), "trace reference must be shortened");
        let raw_payload = outbound.raw_payload.to_string();
        assert!(!raw_payload.contains("provider secret detail"));
        assert!(!raw_payload.contains("provider_internal_error"));
        let trace = text
            .split("追踪标识: ")
            .nth(1)
            .and_then(|suffix| suffix.strip_suffix(')'))
            .expect("short trace reference");
        assert!(trace.is_ascii());
        assert!(trace.len() <= 12);
    }
}
