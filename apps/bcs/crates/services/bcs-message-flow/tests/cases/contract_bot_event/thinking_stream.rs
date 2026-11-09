use super::{
    BcsMessageFlow,
    BotEventCommand,
    ChatEventState,
    MessageFlowService,
    Value,
    json,
    support
};

#[tokio::test]
async fn agent_thinking_delta_self_accumulates_and_resets_after_tool_start() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    for (delta, upstream_text) in [("AA", "upstream-run-text-AA"), ("A", "upstream-run-text-AAA")] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-thinking-delta".to_string(),
            group_id: "group-1".to_string(),
            event_type: "agent".to_string(),
            event_payload: json!({
                "stream": "thinking",
                "data": {
                    "stream": "thinking",
                    "delta": delta,
                    "text": upstream_text,
                },
            }),
            state: ChatEventState::Delta,
            bcs_session_id: None,
        })
        .await
        .unwrap();
    }

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-delta".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "tool",
            "data": {
                "phase": "start",
                "toolCallId": "tool-1",
                "name": "Bash",
            },
        }),
        state: ChatEventState::ToolCallStart,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-delta".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": {
                "stream": "thinking",
                "delta": "BB",
                "text": "AAABB",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 4);

    let second: Value = serde_json::from_str(&events[1]).unwrap();
    assert_eq!(second["payload"]["data"]["delta"], "A");
    assert_eq!(second["payload"]["data"]["text"], "AAA");

    let after_tool: Value = serde_json::from_str(&events[3]).unwrap();
    assert_eq!(after_tool["payload"]["data"]["delta"], "BB");
    assert_eq!(
        after_tool["payload"]["data"]["text"],
        "BB",
        "thinking text should be rebuilt from the current segment's delta, not upstream run-cumulative text"
    );
}

#[tokio::test]
async fn agent_thinking_delta_resets_after_tool_end() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-tool-end".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": {
                "stream": "thinking",
                "delta": "before",
                "text": "upstream-before",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-tool-end".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "tool",
            "data": {
                "phase": "result",
                "toolCallId": "tool-1",
                "name": "Bash",
                "result": "ok",
            },
        }),
        state: ChatEventState::ToolCallEnd,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-tool-end".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": {
                "stream": "thinking",
                "delta": "after",
                "text": "upstream-beforeafter",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 3);

    let after_tool: Value = serde_json::from_str(&events[2]).unwrap();
    assert_eq!(after_tool["payload"]["data"]["delta"], "after");
    assert_eq!(
        after_tool["payload"]["data"]["text"],
        "after",
        "tool-call end should close the previous thinking segment"
    );
}

#[tokio::test]
async fn agent_thinking_delta_resets_after_non_tool_block() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-approval-boundary".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": {
                "stream": "thinking",
                "delta": "before",
                "text": "before",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-approval-boundary".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "approval",
            "data": {
                "state": "pending",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-approval-boundary".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": {
                "stream": "thinking",
                "delta": "after",
                "text": "beforeafter",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 3);

    let after_boundary: Value = serde_json::from_str(&events[2]).unwrap();
    assert_eq!(after_boundary["payload"]["data"]["delta"], "after");
    assert_eq!(
        after_boundary["payload"]["data"]["text"],
        "after",
        "any non-thinking agent block should close the previous thinking segment"
    );
}

#[tokio::test]
async fn agent_thinking_delta_buffers_are_isolated_by_run_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    for (run_id, delta, upstream_text) in [
        ("run-thinking-a", "A", "A"),
        ("run-thinking-b", "B", "B"),
        ("run-thinking-a", "A2", "AA2"),
        ("run-thinking-b", "B2", "BB2"),
    ] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: run_id.to_string(),
            group_id: "group-1".to_string(),
            event_type: "agent".to_string(),
            event_payload: json!({
                "stream": "thinking",
                "data": {
                    "stream": "thinking",
                    "delta": delta,
                    "text": upstream_text,
                },
            }),
            state: ChatEventState::Delta,
            bcs_session_id: None,
        })
        .await
        .unwrap();
    }

    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 4);

    let run_a_second: Value = serde_json::from_str(&events[2]).unwrap();
    assert_eq!(run_a_second["payload"]["data"]["text"], "AA2");

    let run_b_second: Value = serde_json::from_str(&events[3]).unwrap();
    assert_eq!(run_b_second["payload"]["data"]["text"], "BB2");
}

#[tokio::test]
async fn non_agent_stream_event_does_not_reset_thinking_delta_buffer() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-non-agent-boundary".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": {
                "stream": "thinking",
                "delta": "AA",
                "text": "upstream-AA",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-non-agent-boundary".to_string(),
        group_id: "group-1".to_string(),
        event_type: "diagnostic".to_string(),
        event_payload: json!({
            "stream": "tool",
            "data": {
                "note": "stream-shaped but not an agent stream event",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-non-agent-boundary".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": {
                "stream": "thinking",
                "delta": "BB",
                "text": "upstream-AABB",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 3);

    let after_non_agent: Value = serde_json::from_str(&events[2]).unwrap();
    assert_eq!(after_non_agent["payload"]["data"]["delta"], "BB");
    assert_eq!(
        after_non_agent["payload"]["data"]["text"],
        "AABB",
        "only non-thinking agent stream events should close a thinking segment"
    );
}

#[tokio::test]
async fn terminal_cleanup_clears_thinking_delta_buffer_for_reused_run_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-cleanup".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": {
                "stream": "thinking",
                "delta": "old",
                "text": "upstream-old",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-cleanup".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "error",
            "display_message": "run failed",
        }),
        state: ChatEventState::Error,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-thinking-cleanup".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "thinking",
            "data": {
                "stream": "thinking",
                "delta": "new",
                "text": "upstream-oldnew",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 3);

    let after_cleanup: Value = serde_json::from_str(&events[2]).unwrap();
    assert_eq!(after_cleanup["payload"]["data"]["delta"], "new");
    assert_eq!(
        after_cleanup["payload"]["data"]["text"],
        "new",
        "terminal cleanup should clear any open thinking segment buffer"
    );
}
