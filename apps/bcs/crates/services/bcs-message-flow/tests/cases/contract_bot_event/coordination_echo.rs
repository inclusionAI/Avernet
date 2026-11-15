use super::{
    BTreeMap,
    BcsMessageFlow,
    BotDeliveryKind,
    BotEventCommand,
    ChatEventState,
    CoordinationMode,
    CoordinationSurface,
    GroupCoreService,
    MessageFlowService,
    TASK_INTENT_ELIGIBLE_KEY,
    Value,
    agent_tool_result_payload,
    assert_frame_tags,
    configure_provider_v2_target,
    coordination_echo,
    json,
    manager_worker_flow_with_repo,
    record_matching_tool_start,
    request_params,
    support
};

#[tokio::test]
async fn agent_tool_result_coordination_echo_dispatches_task() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let echo = json!({
        "__bcs_coordination__": true,
        "v": 1,
        "tool": "bcs_assign_task",
        "arguments": {
            "target_bot": "bot-observer",
            "message": "review this file"
        },
        "status": "received"
    })
    .to_string();

    record_matching_tool_start(
        &flow,
        "bot-driver",
        "manager-run",
        "group-1",
        "group-1:abcdef12",
        "Bash",
        "tool-1",
    )
    .await;
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-driver".to_string(),
        run_id: "manager-run".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            TASK_INTENT_ELIGIBLE_KEY: true,
            "stream": "tool",
            "data": {
                "phase": "result",
                "toolCallId": "tool-1",
                "name": "Bash",
                "isError": false,
                "result": {
                    "content": [{"type": "text", "text": echo}],
                },
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.first().expect("expected task dispatch frame"));
    assert_eq!(params["session_key"], "group-1:abcdef12");
    assert_eq!(params["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(params["message"]["content"][0]["text"], "[from:Driver] review this file");
}

#[tokio::test]
async fn agent_tool_result_coordination_requires_explicit_success() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );
    let echo = coordination_echo(
        "bcs_assign_task",
        json!({
            "target_bot": "bot-observer",
            "message": "must not dispatch",
        }),
    );

    for (index, is_error) in [
        None,
        Some(Value::String("false".to_string())),
        Some(Value::Bool(true)),
    ]
    .into_iter()
    .enumerate()
    {
        let run_id = format!("failed-tool-run-{index}");
        let tool_call_id = format!("failed-tool-{index}");
        record_matching_tool_start(
            &flow,
            "bot-driver",
            &run_id,
            "group-1",
            "group-1:abcdef12",
            "Bash",
            &tool_call_id,
        )
        .await;
        let mut event_payload = agent_tool_result_payload(
            Some("Bash"),
            &tool_call_id,
            &echo,
            false,
        );
        match is_error {
            Some(value) => event_payload["data"]["isError"] = value,
            None => {
                event_payload["data"]
                    .as_object_mut()
                    .expect("tool result data")
                    .remove("isError");
            }
        }
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-driver".to_string(),
            run_id,
            group_id: "group-1".to_string(),
            event_type: "agent".to_string(),
            event_payload,
            state: ChatEventState::Delta,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .unwrap();
    }

    assert!(support.bot_delivery.kinds().await.is_empty());
}

#[tokio::test]
async fn duplicate_agent_tool_result_coordination_echo_dispatches_once() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );
    let echo = coordination_echo(
        "bcs_assign_task",
        json!({
            "target_bot": "bot-observer",
            "message": "review this file",
        }),
    );
    record_matching_tool_start(
        &flow,
        "bot-driver",
        "manager-run",
        "group-1",
        "group-1:abcdef12",
        "Bash",
        "tool-dup",
    )
    .await;
    let event_payload = agent_tool_result_payload(Some("Bash"), "tool-dup", &echo, false);

    for _ in 0..2 {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-driver".to_string(),
            run_id: "manager-run".to_string(),
            group_id: "group-1".to_string(),
            event_type: "agent".to_string(),
            event_payload: event_payload.clone(),
            state: ChatEventState::Delta,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .unwrap();
    }

    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
}

#[tokio::test]
async fn agent_tool_result_coordination_echo_rejects_unsupported_tool_name() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );
    let echo = coordination_echo(
        "bcs_assign_task",
        json!({
            "target_bot": "bot-observer",
            "message": "review this file",
        }),
    );

    record_matching_tool_start(
        &flow,
        "bot-driver",
        "manager-run",
        "group-1",
        "group-1:abcdef12",
        "read_file",
        "tool-read",
    )
    .await;
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-driver".to_string(),
        run_id: "manager-run".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(Some("read_file"), "tool-read", &echo, false),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    assert!(support.bot_delivery.kinds().await.is_empty());
}

#[tokio::test]
async fn native_mcp_tool_result_coordination_echo_dispatches_from_exact_provider_mapping() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    support
        .registry
        .set_coordination_surface(
            "bot-driver",
            CoordinationSurface {
                mode: CoordinationMode::NativeMcp,
                worker_send_task_message_enabled: true,
                mcp_server: Some("bcs".to_string()),
                mcporter_command: None,
                tool_name_mapping: BTreeMap::from([(
                    "mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_assign_task".to_string(),
                    "bcs_assign_task".to_string(),
                )]),
            },
        )
        .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );
    let echo = coordination_echo(
        "bcs_assign_task",
        json!({
            "target_bot": "bot-observer",
            "message": "review this file",
        }),
    );

    record_matching_tool_start(
        &flow,
        "bot-driver",
        "native-manager-run",
        "group-1",
        "group-1:abcdef12",
        "mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_assign_task",
        "native-tool-1",
    )
    .await;
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-driver".to_string(),
        run_id: "native-manager-run".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(
            Some("mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_assign_task"),
            "native-tool-1",
            &echo,
            false,
        ),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
    assert_eq!(
        support
            .registry
            .coordination_surface_resolution_count("bot-driver")
            .await,
        1
    );
}

#[tokio::test]
async fn native_mcp_assign_task_echo_forwards_provider_participant_tags() {
    let (support, _, flow) = manager_worker_flow_with_repo().await;
    configure_provider_v2_target(&support, "bot-worker", "worker-owner").await;
    support
        .registry
        .set_coordination_surface(
            "bot-manager",
            CoordinationSurface {
                mode: CoordinationMode::NativeMcp,
                worker_send_task_message_enabled: true,
                mcp_server: Some("bcs".to_string()),
                mcporter_command: None,
                tool_name_mapping: BTreeMap::from([(
                    "mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_assign_task".to_string(),
                    "bcs_assign_task".to_string(),
                )]),
            },
        )
        .await;
    let echo = coordination_echo(
        "bcs_assign_task",
        json!({
            "target_bot": "bot-worker",
            "message": "review this file",
        }),
    );

    record_matching_tool_start(
        &flow,
        "bot-manager",
        "native-manager-provider-run",
        "group-1",
        "group-1:abcdef12",
        "mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_assign_task",
        "native-provider-tool-1",
    )
    .await;
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-manager".to_string(),
        run_id: "native-manager-provider-run".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(
            Some("mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_assign_task"),
            "native-provider-tool-1",
            &echo,
            false,
        ),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .expect("native MCP echo should dispatch to provider worker");

    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
    assert!(support.bot_delivery.targets().await[0].is_http_provider());
    assert_frame_tags(&support.bot_delivery.frames().await[0], &["online"]);
}

#[tokio::test]
async fn native_mcp_coordination_rejects_unmapped_or_mismatched_results_and_resolves_once_per_run() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    support
        .registry
        .set_coordination_surface(
            "bot-driver",
            CoordinationSurface {
                mode: CoordinationMode::NativeMcp,
                worker_send_task_message_enabled: true,
                mcp_server: Some("bcs".to_string()),
                mcporter_command: None,
                tool_name_mapping: BTreeMap::from([(
                    "provider_assign_task".to_string(),
                    "bcs_assign_task".to_string(),
                )]),
            },
        )
        .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    for (tool_name, tool_call_id, canonical_tool) in [
        (Some("unmapped_provider_tool"), "native-tool-unmapped", "bcs_assign_task"),
        (Some("provider_assign_task"), "native-tool-mismatch", "bcs_send_task_message"),
        (None, "native-tool-missing-name", "bcs_assign_task"),
    ] {
        let echo = coordination_echo(
            canonical_tool,
            json!({
                "target_bot": "bot-observer",
                "message": "must not dispatch",
            }),
        );
        if let Some(tool_name) = tool_name {
            record_matching_tool_start(
                &flow,
                "bot-driver",
                "native-rejected-run",
                "group-1",
                "group-1:abcdef12",
                tool_name,
                tool_call_id,
            )
            .await;
        }
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-driver".to_string(),
            run_id: "native-rejected-run".to_string(),
            group_id: "group-1".to_string(),
            event_type: "agent".to_string(),
            event_payload: agent_tool_result_payload(
                tool_name,
                tool_call_id,
                &echo,
                false,
            ),
            state: ChatEventState::Delta,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .unwrap();
    }

    assert!(support.bot_delivery.kinds().await.is_empty());
    assert_eq!(
        support
            .registry
            .coordination_surface_resolution_count("bot-driver")
            .await,
        1
    );
}

#[tokio::test]
async fn ordinary_tool_result_does_not_resolve_coordination_surface() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-driver".to_string(),
        run_id: "ordinary-tool-run".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(
            Some("read_file"),
            "ordinary-tool-1",
            "plain tool output",
            false,
        ),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    assert_eq!(
        support
            .registry
            .coordination_surface_resolution_count("bot-driver")
            .await,
        0
    );
}

#[tokio::test]
async fn coordination_surface_resolution_failure_is_cached_and_fails_closed() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );
    let echo = coordination_echo(
        "bcs_assign_task",
        json!({
            "target_bot": "bot-observer",
            "message": "must not dispatch",
        }),
    );

    for tool_call_id in ["unknown-bot-tool-1", "unknown-bot-tool-2"] {
        record_matching_tool_start(
            &flow,
            "unknown-bot",
            "unknown-bot-run",
            "group-1",
            "group-1:abcdef12",
            "Bash",
            tool_call_id,
        )
        .await;
        flow.handle_bot_event(BotEventCommand {
            bot_id: "unknown-bot".to_string(),
            run_id: "unknown-bot-run".to_string(),
            group_id: "group-1".to_string(),
            event_type: "agent".to_string(),
            event_payload: agent_tool_result_payload(Some("Bash"), tool_call_id, &echo, false),
            state: ChatEventState::Delta,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .unwrap();
    }

    assert!(support.bot_delivery.kinds().await.is_empty());
    assert_eq!(
        support
            .registry
            .coordination_surface_resolution_count("unknown-bot")
            .await,
        1
    );
}
