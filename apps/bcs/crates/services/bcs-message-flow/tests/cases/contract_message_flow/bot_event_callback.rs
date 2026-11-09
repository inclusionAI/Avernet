use super::*;

#[tokio::test]
async fn bot_final_event_routes_and_publishes_frontend_through_message_flow() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    register_active_run(
        &run_context,
        "run-1",
        "group-1",
        "group-1",
        "bot-observer",
        BotRunTransportOwner::WebSocket,
    )
    .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context.clone());

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-1".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "callback done"}],
                },
            }),
            bcs_session_id: None,
            state: ChatEventState::Final,
        })
        .await
        .unwrap();

    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert!(!outcome.bot_deliveries.is_empty());
    assert_eq!(
        support.routing.route_calls().await,
        vec![(
            "group-1".to_string(),
            "callback done".to_string(),
            Some("bot-observer".to_string())
        )]
    );
    let frames = support.bot_delivery.frames().await;
    let run_id = request_id_for_method(&frames, "chat.send");
    let context = run_context
        .get_context(&run_id)
        .await
        .expect("relay run context");
    assert_eq!(context.bot_id, "bot-driver");
    assert_eq!(context.group_id, "group-1");
    assert!(
        run_context
            .list_active_runs(&BotRunScope {
                group_id: "group-1".to_string(),
                session_id: "group-1".to_string(),
                bot_id: "bot-observer".to_string(),
            })
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
async fn bot_final_event_relay_to_provider_uses_explicit_bcs_session_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .set_delivery_target(
            "bot-driver",
            support::FakeRegistryService::provider_target("bot-driver"),
        )
        .await;
    let session_id = "group-1:abcdef12";
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-provider-relay".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "callback done"}],
            },
        }),
        bcs_session_id: Some(session_id.to_string()),
        state: ChatEventState::Final,
    })
    .await
    .unwrap();

    assert!(support.bot_delivery.targets().await[0].is_http_provider());
    let frames = support.bot_delivery.frames().await;
    let frame = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
        .expect("chat.send frame");
    let BcsFrame::Request(req) = frame else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["bcs_group_id"], "group-1");
    assert_eq!(params["bcs_session_id"], session_id);
    assert_eq!(params["session_key"], "group:group-1");
}

#[tokio::test]
async fn group_callback_command_routes_and_publishes_frontend_through_message_flow() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context.clone());

    let outcome = flow
        .handle_group_callback(GroupCallbackCommand {
            group_id: "group-1".to_string(),
            message: "system callback".to_string(),
            mentions: vec!["bot-driver".to_string()],
            metadata: Some(json!({"source": "contract"})),
            store_message: true,
        })
        .await
        .unwrap();

    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert_eq!(outcome.mentions, vec!["bot-driver".to_string()]);
    assert_eq!(outcome.delivered_count, 2);
    assert_eq!(
        support.group.get("group-1").await.unwrap().messages.len(),
        1
    );
    let frames = support.bot_delivery.frames().await;
    let run_id = request_id_for_method(&frames, "chat.send");
    let context = run_context
        .get_context(&run_id)
        .await
        .expect("callback run context");
    assert_eq!(context.bot_id, "bot-driver");
    assert_eq!(context.group_id, "group-1");
}

#[tokio::test]
async fn group_callback_stops_before_delivery_when_persistence_fails() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.group.fail_add_message().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let error = flow
        .handle_group_callback(GroupCallbackCommand {
            group_id: "group-1".to_string(),
            message: "persistence must succeed".to_string(),
            mentions: vec!["bot-driver".to_string()],
            metadata: Some(json!({"source": "contract"})),
            store_message: true,
        })
        .await
        .expect_err("persistence failure must be returned");

    assert!(error.to_string().contains("add_message failed"));
    assert!(support.bot_delivery.frames().await.is_empty());
    assert!(
        support
            .group
            .get("group-1")
            .await
            .unwrap()
            .messages
            .is_empty()
    );
}

#[tokio::test]
async fn group_callback_continues_when_frontend_publish_fails() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.frontend_delivery.fail_publish().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_group_callback(GroupCallbackCommand {
            group_id: "group-1".to_string(),
            message: "frontend is best effort".to_string(),
            mentions: vec!["bot-driver".to_string()],
            metadata: Some(json!({"source": "contract"})),
            store_message: true,
        })
        .await
        .expect("frontend publication does not change committed callback delivery");

    assert_eq!(outcome.delivered_count, 2);
    assert_eq!(outcome.failed_count, 0);
    assert!(outcome.frontend_deliveries.is_empty());
    assert_eq!(
        support.group.get("group-1").await.unwrap().messages.len(),
        1
    );
}

#[tokio::test]
async fn group_callback_all_mentions_use_routing_service() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_group_callback(GroupCallbackCommand {
        group_id: "group-1".to_string(),
        message: "broadcast".to_string(),
        mentions: vec!["all".to_string()],
        metadata: None,
        store_message: false,
    })
    .await
    .unwrap();

    assert_eq!(
        support.routing.route_calls().await,
        vec![("group-1".to_string(), "@all broadcast".to_string(), None)]
    );
}

fn request_id_for_method(frames: &[BcsFrame], method: &str) -> String {
    frames
        .iter()
        .find_map(|frame| match frame {
            BcsFrame::Request(req) if req.method == method => Some(req.id.clone()),
            _ => None,
        })
        .unwrap_or_else(|| panic!("{method} request frame not found"))
}
