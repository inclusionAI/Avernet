use std::sync::Arc;

use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore};
use bcs_message_flow::task_store::{new_task_entry, TaskLedgerStatus, TASK_TTL_MS};
use bcs_protocol::BcsFrame;
use bcs_service_api::{
    ActorKind, BotDeliveryKind, BotDeliveryTarget, BotEventCommand, BotRegistryCoreService,
    BotRunContextPort, BotTerminalEvent, BotTerminalObserverPort, BotTerminalState,
    ChannelOutboundEventKind, ChannelRenderHint, ChatEventState,
    ChatResponseMode, DefaultDelivery,
    FrontendDeliveryTarget, GroupCoreService, GroupKind, GroupStatus, GroupStrategy,
    MessageFlowService, Participant, ParticipantMode, ParticipantRole,
    ProviderStreamGrayList, ProviderTransportPreference,
    RoutingMode, RoutingPolicy, ServiceError, ServiceSpec, Session, SessionKind,
    SessionManagementService, SessionStatus, SessionUseCaseError, SystemMessageEvent,
    SystemMessageService, TaskCompleteCommand, TaskDispatchCommand, TaskMessageCommand,
    TaskRunAliasRegistration, ChannelInboundError, ChannelService, ChannelUseCaseError,
    interceptor::{BlockReason, InterceptorDecision, MessageInterceptor, OutboundMessage},
};
use serde_json::{Value, json};
use tokio::sync::{Barrier, Mutex, Notify, RwLock};
use tokio::time::{Duration, timeout};

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

#[derive(Default)]
struct RecordingSystemMessage {
    notifications: Mutex<Vec<RecordingSystemNotification>>,
}

struct RecordingSystemNotification {
    group_id: String,
    event: SystemMessageEvent,
    session_id: String,
    participants: Vec<Participant>,
}

#[derive(Default)]
struct RecordingBotTerminalObserver {
    events: Mutex<Vec<BotTerminalEvent>>,
}

#[async_trait::async_trait]
impl BotTerminalObserverPort for RecordingBotTerminalObserver {
    async fn observe(&self, event: BotTerminalEvent) {
        self.events.lock().await.push(event);
    }
}

#[derive(Default)]
struct RecordingChannelService {
    outbound: Mutex<Vec<bcs_service_api::application::channel::OutboundMessage>>,
}

impl RecordingChannelService {
    async fn outbound(&self) -> Vec<bcs_service_api::application::channel::OutboundMessage> {
        self.outbound.lock().await.clone()
    }
}

#[async_trait::async_trait]
impl ChannelService for RecordingChannelService {
    async fn handle_inbound(
        &self,
        _msg: bcs_service_api::application::channel::InboundMessage,
    ) -> Result<(), ChannelInboundError> {
        Ok(())
    }

    async fn try_outbound(
        &self,
        msg: bcs_service_api::application::channel::OutboundMessage,
    ) -> Result<(), ChannelUseCaseError> {
        self.outbound.lock().await.push(msg);
        Ok(())
    }

    async fn create_binding(
        &self,
        _cmd: bcs_service_api::application::channel::CreateBindingCommand,
    ) -> Result<bcs_domain::ChannelBinding, ChannelUseCaseError> {
        Err(ChannelUseCaseError::InvalidParams(
            "not implemented in message-flow test channel".to_string(),
        ))
    }

    async fn list_bindings(&self) -> Result<Vec<bcs_domain::ChannelBinding>, ChannelUseCaseError> {
        Ok(Vec::new())
    }

    async fn set_binding_status(
        &self,
        _id: &str,
        _active: bool,
    ) -> Result<(), ChannelUseCaseError> {
        Ok(())
    }

    async fn update_binding_config(
        &self,
        _id: &str,
        _config: Value,
    ) -> Result<(), ChannelUseCaseError> {
        Ok(())
    }

    async fn delete_binding(&self, _id: &str) -> Result<(), ChannelUseCaseError> {
        Ok(())
    }
}

#[async_trait::async_trait]
impl SystemMessageService for RecordingSystemMessage {
    async fn notify(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        session_participants: &[Participant],
    ) -> bcs_service_api::ServiceResult<usize> {
        self.notifications.lock().await.push(RecordingSystemNotification {
            group_id: group_id.to_string(),
            event,
            session_id: session_id.to_string(),
            participants: session_participants.to_vec(),
        });
        Ok(1)
    }
}

#[tokio::test]
async fn websocket_chat_events_notify_terminal_observer_only_for_terminal_states() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let observer = Arc::new(RecordingBotTerminalObserver::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_terminal_observer(observer.clone());

    for (run_id, state, payload) in [
        (
            "run-websocket-final",
            ChatEventState::Final,
            json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "websocket result"}],
                },
            }),
        ),
        (
            "run-websocket-error",
            ChatEventState::Error,
            json!({"state": "error", "errorMessage": "websocket failed"}),
        ),
        (
            "run-websocket-aborted",
            ChatEventState::Aborted,
            json!({"state": "aborted", "error_message": "websocket aborted"}),
        ),
        (
            "run-websocket-delta",
            ChatEventState::Delta,
            json!({"state": "delta", "message": {"content": "partial"}}),
        ),
    ] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: run_id.to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: payload,
            state,
            bcs_session_id: None,
        })
        .await
        .unwrap();
    }

    let events = observer.events.lock().await;
    assert_eq!(events.len(), 3);
    assert_eq!(events[0].run_id, "run-websocket-final");
    assert_eq!(events[0].bot_uuid, "bot-observer");
    assert_eq!(events[0].state, BotTerminalState::Final);
    assert_eq!(events[0].text, "websocket result");
    assert_eq!(events[1].state, BotTerminalState::Error);
    assert_eq!(events[1].text, "websocket failed");
    assert_eq!(events[2].state, BotTerminalState::Aborted);
    assert_eq!(events[2].text, "websocket aborted");
}

#[tokio::test]
async fn bot_event_publish_preserves_workbench_event_names() {
    let cases = [
        ("chat.event", ChatEventState::Delta, "chat"),
        ("chat.event", ChatEventState::Final, "chat"),
        ("chat.event", ChatEventState::Error, "chat"),
        ("chat.event", ChatEventState::Aborted, "chat"),
        ("chat.event", ChatEventState::ToolCallStart, "agent"),
        ("chat.event", ChatEventState::ToolCallEnd, "agent"),
        ("chat", ChatEventState::Final, "chat"),
        ("agent", ChatEventState::Delta, "agent"),
    ];

    for (event_type, state, expected_event) in cases {
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
            run_id: format!("run-{event_type}-{state:?}"),
            group_id: "group-1".to_string(),
            event_type: event_type.to_string(),
            event_payload: json!({
                "state": state_payload_name(&state),
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
            }),
            state,
            bcs_session_id: None,
        })
        .await
        .unwrap();

        let events = support.frontend_delivery.events().await;
        let frame: Value = serde_json::from_str(events.first().unwrap()).unwrap();
        assert_eq!(
            frame["event"], expected_event,
            "wire event {event_type} should publish stable workbench event name"
        );
    }
}

#[tokio::test]
async fn bot_event_publish_carries_legacy_run_fallback() {
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
        run_id: "run-1".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "run_id": "run-1",
            "bcs_group_id": "group-1",
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "partial"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let commands = support.frontend_delivery.commands().await;
    assert_eq!(commands.len(), 1);
    let fallback = commands[0]
        .run_fallback
        .as_ref()
        .expect("bot event should carry run fallback");
    assert_eq!(fallback.run_id, "run-1");
    assert_eq!(fallback.session_id, "group-1");

    let frame: BcsFrame = serde_json::from_str(&fallback.event_json).unwrap();
    let BcsFrame::Event(event) = frame else {
        panic!("run fallback should use raw event frame");
    };
    assert_eq!(event.event, "chat.event");
    let payload = event.payload.expect("event payload");
    assert_eq!(payload["run_id"], "run-1");
    assert_eq!(payload["bcs_group_id"], "group-1");
    assert_eq!(payload["message"]["content"][0]["text"], "partial");
}

#[tokio::test]
async fn bot_event_with_bcs_session_id_publishes_to_session_target() {
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
        run_id: "run-1".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "run_id": "run-1",
            "bcs_group_id": "group-1:abcdef12",
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "partial"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let commands = support.frontend_delivery.commands().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(
        commands[0].target,
        FrontendDeliveryTarget::Session {
            session_id: "group-1:abcdef12".to_string()
        }
    );
    let fallback = commands[0]
        .run_fallback
        .as_ref()
        .expect("bot event should carry run fallback");
    assert_eq!(fallback.session_id, "group-1:abcdef12");
}


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

#[tokio::test]
async fn bot_delta_channel_outbound_uses_delta_text_not_synthesized_snapshot() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
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

#[tokio::test]
async fn bot_final_event_for_synthetic_session_skips_group_relay() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-1".to_string(),
            group_id: "bcs-cli:caller:12345678".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": "run-1",
                "bcs_group_id": "bcs-cli:caller:12345678",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.unregistered_run_ids, vec!["run-1".to_string()]);
    assert!(outcome.bot_deliveries.is_empty());
    assert!(support.bot_delivery.frames().await.is_empty());
    let commands = support.frontend_delivery.commands().await;
    assert_eq!(
        commands[0].target,
        FrontendDeliveryTarget::Group {
            group_id: "bcs-cli:caller:12345678".to_string()
        }
    );
    assert!(commands[0].run_fallback.is_some());
}

#[tokio::test]
async fn bot_final_event_in_human_bot_dm_does_not_self_relay_to_sender_bot() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_kind = GroupKind::Dm;
    group.driver_bot = "bot-observer".to_string();
    group.dm_pair_key = Some("bot-observer|human_1".to_string());
    group.participants = vec![
        Participant {
            bot_uuid: "human_1".to_string(),
            bot_name: Some("Human One".to_string()),
            kind: None,
            role: ParticipantRole::Observer,
            actor_kind: ActorKind::Human,
            mode: Some(ParticipantMode::Present),
        },
        Participant {
            bot_uuid: "bot-observer".to_string(),
            bot_name: Some("Observer".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        },
    ];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-dm".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
                "routing": {
                    "responders": [{"type": "bot", "value": "bot-observer"}],
                    "reason": "ignored for dm"
                }
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert!(outcome.bot_deliveries.is_empty());
    assert!(support.bot_delivery.frames().await.is_empty());
    assert_eq!(
        support.routing.dm_route_calls().await,
        vec![(
            "group-1".to_string(),
            "done".to_string(),
            "bot-observer".to_string()
        )]
    );
}

fn state_payload_name(state: &ChatEventState) -> &'static str {
    match state {
        ChatEventState::Delta => "delta",
        ChatEventState::Final => "final",
        ChatEventState::Aborted => "aborted",
        ChatEventState::Error => "error",
        ChatEventState::ToolCallStart => "tool_call_start",
        ChatEventState::ToolCallEnd => "tool_call_end",
    }
}

#[tokio::test]
async fn bot_final_event_relays_through_bot_delivery_port() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        ..RoutingPolicy::default()
    });
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

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
                    "content": [{"type": "text", "text": "done"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.unregistered_run_ids, vec!["run-1".to_string()]);
    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert_eq!(support.group.message_count("group-1").await.unwrap(), 2);
    assert!(support
        .bot_delivery
        .kinds()
        .await
        .contains(&BotDeliveryKind::Send));
    assert!(support
        .bot_delivery
        .frames()
        .await
        .into_iter()
        .any(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send")));
}

#[tokio::test]
async fn bot_final_event_relay_preserves_session_id_for_legacy_target() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        ..RoutingPolicy::default()
    });
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-session-relay".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done in session"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.first().expect("expected relay frame"));
    assert_eq!(params["bcs_group_id"], "group-1:abcdef12");
    assert_eq!(params["channel"]["actor_id"], "bot-observer");
    assert_eq!(params["channel"]["actor_name"], "Observer");
    assert!(
        params.get("bcs_session_id").is_none(),
        "v2 targets should receive session id through bcs_group_id"
    );
    assert_eq!(params["session_context"]["session_id"], "group-1:abcdef12");
}

#[tokio::test]
async fn bot_final_event_relay_keeps_group_id_for_legacy_default_session_target() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        ..RoutingPolicy::default()
    });
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-legacy-default-session-relay".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done in legacy default session"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:00000000".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.first().expect("expected relay frame"));
    assert_eq!(params["bcs_group_id"], "group-1");
    assert!(
        params.get("bcs_session_id").is_none(),
        "v2 targets should not receive bcs_session_id"
    );
    assert_eq!(params["session_context"]["session_id"], "group-1:00000000");
}

#[tokio::test]
async fn private_bot_final_event_relays_without_hidden_prompt() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .set_visibility("bot-observer", "private")
        .await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        ..RoutingPolicy::default()
    });
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-private-sender".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "private sender done"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert_eq!(outcome.delivered_count, 1);
    assert_eq!(outcome.failed_count, 0);
    assert!(support
        .frontend_delivery
        .events()
        .await
        .iter()
        .all(|event| !event.contains("隐身") && !event.contains("无法看到消息")));
}

#[tokio::test]
async fn bot_final_event_relays_to_private_group_targets() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.set_visibility("bot-driver", "private").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        ..RoutingPolicy::default()
    });
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-private-target".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "private target done"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert_eq!(outcome.delivered_count, 1);
    assert_eq!(outcome.failed_count, 0);
    assert!(outcome
        .delivery_results
        .iter()
        .any(|result| result.bot_uuid == "bot-driver" && result.success));
    assert!(support
        .bot_delivery
        .frames()
        .await
        .into_iter()
        .any(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send")));
}

#[tokio::test]
async fn relay_limit_marks_group_inactive_and_publishes_frontend_event() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_relay_turn_limit(1);

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
                    "content": [{"type": "text", "text": "limit"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(support.group.get("group-1").await.unwrap().status, GroupStatus::Inactive);
    assert!(outcome.bot_deliveries.is_empty());
    assert_eq!(outcome.frontend_deliveries.len(), 2);
    assert!(support
        .frontend_delivery
        .events()
        .await
        .iter()
        .any(|event| event.contains("群聊消息数量已达上限")));
}

#[tokio::test]
async fn task_bot_final_event_is_forwarded_to_driver_and_task_replied() {
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

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    assert_eq!(
        flow.task_store.pending_targets("group-1", None).await,
        vec!["Observer".to_string()]
    );
    assert_eq!(
        flow.task_store.get(&dispatch.task_id).await.unwrap().status,
        TaskLedgerStatus::Dispatched
    );

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "task done"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert_eq!(
        flow.task_store.resolve_task_id(&dispatch.task_id).await,
        Some(dispatch.task_id.clone())
    );
    assert_eq!(
        flow.task_store.get(&dispatch.task_id).await.unwrap().status,
        TaskLedgerStatus::Replied
    );
    assert!(flow.task_store.pending_targets("group-1", None).await.is_empty());
    let ledger = flow.task_store.ledger_summary("group-1", None).await;
    assert_eq!(ledger.pending, Vec::<String>::new());
    assert_eq!(ledger.replied, vec!["Observer".to_string()]);
    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::TaskDispatch, BotDeliveryKind::TaskResult]
    );
    assert!(support
        .bot_delivery
        .frames()
        .await
        .into_iter()
        .any(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send" && req.id == dispatch.task_id)));
}

#[tokio::test]
async fn duplicate_terminal_task_bot_event_is_ignored_after_reply() {
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

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    let first = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "task done"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();
    let duplicate = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "duplicate"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert_eq!(first.bot_deliveries.len(), 1);
    assert!(duplicate.bot_deliveries.is_empty());
    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::TaskDispatch, BotDeliveryKind::TaskResult]
    );
    assert_eq!(
        flow.task_store.get(&dispatch.task_id).await.unwrap().status,
        TaskLedgerStatus::Replied
    );
}

#[tokio::test]
async fn terminal_task_bot_event_from_non_target_bot_is_ignored() {
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

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-driver".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "manager follow-up"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert!(outcome.bot_deliveries.is_empty());
    assert_eq!(
        flow.task_store.get(&dispatch.task_id).await.unwrap().status,
        TaskLedgerStatus::Dispatched
    );
    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
}

#[tokio::test]
async fn task_run_alias_requires_target_bot_and_dispatched_task() {
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

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    let manager_alias = flow
        .register_task_run_alias(&dispatch.task_id, "manager-run", "bot-driver")
        .await
        .unwrap();
    assert_eq!(manager_alias, TaskRunAliasRegistration::Rejected);
    assert_eq!(flow.task_store.resolve_task_id("manager-run").await, None);

    let worker_alias = flow
        .register_task_run_alias(&dispatch.task_id, "worker-run", "bot-observer")
        .await
        .unwrap();
    assert_eq!(worker_alias, TaskRunAliasRegistration::Registered);
    assert_eq!(
        flow.task_store.resolve_task_id("worker-run").await,
        Some(dispatch.task_id.clone())
    );

    flow.task_store.mark_replied(&dispatch.task_id).await;
    let late_alias = flow
        .register_task_run_alias(&dispatch.task_id, "late-worker-run", "bot-observer")
        .await
        .unwrap();
    assert_eq!(late_alias, TaskRunAliasRegistration::Rejected);
    assert_eq!(flow.task_store.resolve_task_id("late-worker-run").await, None);
}

#[tokio::test]
async fn task_ledger_notifications_are_sent_to_driver_after_dispatch_and_reply() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let system_message = Arc::new(RecordingSystemMessage::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_system_message(system_message.clone());

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work", "bcs_session_id": "group-1:abcdef12"}),
        })
        .await
        .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "task done"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let notifications = system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 2);
    assert_eq!(notifications[0].group_id, "group-1");
    assert_eq!(notifications[0].session_id, "group-1:abcdef12");
    assert_eq!(notifications[0].participants.len(), 3);
    assert_ledger_notification(
        &notifications[0].event,
        "待回复: Observer",
        "已回复: -",
    );
    assert_ledger_notification(
        &notifications[1].event,
        "待回复: -",
        "已回复: Observer",
    );
}

#[tokio::test]
async fn dispatch_delivery_failure_marks_failed_not_removed() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    support.bot_delivery.fail_for("bot-observer").await;
    let system_message = Arc::new(RecordingSystemMessage::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_system_message(system_message.clone());

    let Err(err) = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
    else {
        panic!("dispatch should return the delivery failure");
    };

    assert!(matches!(err, ServiceError::BotNotConnected(bot) if bot == "bot-observer"));
    let ledger = flow.task_store.ledger_summary("group-1", None).await;
    assert!(ledger.pending.is_empty());
    assert_eq!(ledger.failed, vec!["Observer".to_string()]);
    assert!(ledger.replied.is_empty());
    assert!(ledger.timed_out.is_empty());

    let notifications = system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 1);
    match &notifications[0].event {
        SystemMessageEvent::GenericNotification { message, receivers, .. } => {
            assert!(message.contains("待回复: -"));
            assert!(message.contains("失败: Observer"));
            assert!(message.contains("超时: -"));
            assert_eq!(receivers.len(), 1);
            assert_eq!(receivers[0].bot_uuid, "bot-driver");
        }
        other => panic!("expected GenericNotification, got {other:?}"),
    }
}

fn assert_ledger_notification(event: &SystemMessageEvent, pending: &str, replied: &str) {
    match event {
        SystemMessageEvent::GenericNotification {
            group_id,
            message,
            receivers,
        } => {
            assert_eq!(group_id, "group-1");
            assert!(message.contains("[任务状态]"));
            assert!(message.contains(pending));
            assert!(message.contains(replied));
            assert!(message.contains("失败: -"));
            assert!(message.contains("超时: -"));
            assert_eq!(receivers.len(), 1);
            assert_eq!(receivers[0].bot_uuid, "bot-driver");
        }
        other => panic!("expected GenericNotification, got {other:?}"),
    }
}

#[tokio::test]
async fn non_terminal_bot_event_does_not_mark_replied() {
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

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "working"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    assert_eq!(
        flow.task_store.get(&dispatch.task_id).await.unwrap().status,
        TaskLedgerStatus::Dispatched
    );
    assert_eq!(
        flow.task_store.pending_targets("group-1", None).await,
        vec!["Observer".to_string()]
    );
    let ledger = flow.task_store.ledger_summary("group-1", None).await;
    assert_eq!(ledger.pending, vec!["Observer".to_string()]);
    assert!(ledger.replied.is_empty());
    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
}

#[tokio::test]
async fn task_dispatch_uses_manager_session_for_worker_context() {
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

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.first().expect("expected task dispatch frame"));
    assert_eq!(params["session_key"], "group-1:abcdef12");
    assert_eq!(params["bcs_group_id"], "group-1:abcdef12");
    assert_eq!(params["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(params["channel"]["user_id"], "bot-driver");
    assert_eq!(params["channel"]["actor_id"], "bot-driver");
    assert_eq!(params["channel"]["actor_name"], "Driver");
    assert_eq!(params["channel"]["thread_id"], "group-1");
    assert_eq!(params["session_context"]["session_id"], "group-1:abcdef12");
    assert_eq!(params["session_context"]["recipient_role"], "worker");

    let entry = flow.task_store.get(&dispatch.task_id).await.unwrap();
    assert_eq!(entry.group_id, "group-1");
    assert_eq!(entry.session_id.as_deref(), Some("group-1:abcdef12"));
}

#[tokio::test]
async fn manager_worker_task_dispatch_authorizes_manager_role_not_driver_bot_field() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .expect("manager role should be allowed to dispatch tasks");

    assert_eq!(dispatch.status, "dispatched");
    assert_eq!(dispatch.bot_deliveries[0].target_bot_id, "bot-worker");
}

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

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-driver".to_string(),
        run_id: "manager-run".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "stream": "tool",
            "data": {
                "phase": "result",
                "toolCallId": "tool-1",
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
    let event_payload = agent_tool_result_payload(None, "tool-dup", &echo, false);

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
async fn task_dispatch_rejects_muted_target_without_delivery() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    let mut worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    worker.mode = Some(ParticipantMode::Muted);
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        worker,
    ];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let err = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .expect_err("muted target should not receive task dispatch");

    match err {
        ServiceError::InvalidOperation { message, .. } => {
            assert_eq!(message, "target bot is muted");
        }
        other => panic!("expected InvalidOperation for muted target, got {other:?}"),
    }
    assert!(support.bot_delivery.kinds().await.is_empty());
}

#[tokio::test]
async fn task_dispatch_rejects_target_muted_in_session_participants() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();

    let mut session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    let mut muted_worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    muted_worker.mode = Some(ParticipantMode::Muted);
    session.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        muted_worker,
    ];
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management);

    let err = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .expect_err("session-muted target should not receive task dispatch");

    match err {
        ServiceError::InvalidOperation { message, .. } => {
            assert_eq!(message, "target bot is muted");
        }
        other => panic!("expected InvalidOperation for muted target, got {other:?}"),
    }
    assert!(support.bot_delivery.kinds().await.is_empty());
}

#[tokio::test]
async fn task_dispatch_uses_legacy_wire_group_id_to_load_session_participants() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();

    let mut session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    let mut muted_worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    muted_worker.mode = Some(ParticipantMode::Muted);
    session.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        muted_worker,
    ];
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management);

    let err = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1:abcdef12".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .expect_err("legacy wire group id should resolve session participants");

    match err {
        ServiceError::InvalidOperation { message, .. } => {
            assert_eq!(message, "target bot is muted");
        }
        other => panic!("expected InvalidOperation for muted target, got {other:?}"),
    }
    assert!(support.bot_delivery.kinds().await.is_empty());
}

#[tokio::test]
async fn task_result_is_forwarded_back_to_manager_session() {
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

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "task done"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.get(1).expect("expected task result frame"));
    assert_eq!(params["session_key"], "group-1:abcdef12");
    assert_eq!(params["bcs_group_id"], "group-1:abcdef12");
    assert_eq!(params["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(params["channel"]["user_id"], "Observer");
    assert_eq!(params["channel"]["actor_id"], "bot-observer");
    assert_eq!(params["channel"]["actor_name"], "Observer");
    assert_eq!(params["channel"]["thread_id"], "group-1");
    assert_eq!(params["session_context"]["session_id"], "group-1:abcdef12");
}

#[tokio::test]
async fn task_message_from_worker_is_forwarded_to_manager_session() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();

    let mut session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    session.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management);

    let outcome = flow
        .handle_task_message(TaskMessageCommand {
            worker_bot_id: "bot-worker".to_string(),
            group_id: "group-1".to_string(),
            payload: json!({
                "message": "blocked on missing schema",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .expect("worker should be able to send task-scoped message to manager");

    assert_eq!(outcome.status, "sent");
    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert_eq!(outcome.bot_deliveries[0].target_bot_id, "bot-manager");
    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert_eq!(
        outcome.frontend_deliveries[0].target,
        FrontendDeliveryTarget::Session {
            session_id: "group-1:abcdef12".to_string(),
        }
    );
    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskMessage]);

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.first().expect("expected task message frame"));
    assert_eq!(params["session_key"], "group-1:abcdef12");
    assert_eq!(params["bcs_group_id"], "group-1:abcdef12");
    assert_eq!(params["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(params["channel"]["user_id"], "Worker");
    assert_eq!(params["channel"]["actor_id"], "bot-worker");
    assert_eq!(params["channel"]["actor_name"], "Worker");
    assert_eq!(params["channel"]["thread_id"], "group-1");
    assert_eq!(params["session_context"]["session_id"], "group-1:abcdef12");
    assert_eq!(params["session_context"]["recipient_role"], "manager");
    assert_eq!(params["session_context"]["from_bot_id"], "bot-worker");
    let text = params["message"]["content"][0]["text"].as_str().unwrap();
    assert!(text.contains("blocked on missing schema"));

    let frontend_commands = support.frontend_delivery.commands().await;
    assert_eq!(frontend_commands.len(), 1);
    assert_eq!(
        frontend_commands[0].target,
        FrontendDeliveryTarget::Session {
            session_id: "group-1:abcdef12".to_string(),
        }
    );
    let event: Value = serde_json::from_str(&frontend_commands[0].event_json).unwrap();
    assert_eq!(event["event"], "chat");
    assert_eq!(event["group_id"], "group-1");
    assert_eq!(event["bot_uuid"], "bot-worker");
    assert_eq!(event["bot_name"], "Worker");
    assert_eq!(event["payload"]["session_key"], "group-1:abcdef12");
    assert_eq!(event["payload"]["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(event["payload"]["message"]["role"], "assistant");
    assert_eq!(event["payload"]["message"]["from"], "bot-worker");
    assert_eq!(event["payload"]["message"]["from_name"], "Worker");
    assert_eq!(
        event["payload"]["message"]["content"][0]["text"],
        "blocked on missing schema"
    );
}

#[tokio::test]
async fn task_message_requires_session_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let err = flow
        .handle_task_message(TaskMessageCommand {
            worker_bot_id: "bot-worker".to_string(),
            group_id: "group-1".to_string(),
            payload: json!({"message": "progress"}),
        })
        .await
        .expect_err("task.message must be scoped to a session");

    match err {
        ServiceError::InvalidOperation { message, .. } => {
            assert_eq!(message, "task.message requires bcs_session_id");
        }
        other => panic!("expected InvalidOperation for missing session id, got {other:?}"),
    }
    assert!(support.bot_delivery.kinds().await.is_empty());
}

#[tokio::test]
async fn manager_worker_task_result_preserves_manager_session_context() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "task done"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.get(1).expect("expected task result frame"));
    assert_eq!(params["session_key"], "group-1:abcdef12");
    assert_eq!(params["session_context"]["session_id"], "group-1:abcdef12");
    assert_eq!(params["session_context"]["group_type"], "manager_worker");
    assert_eq!(params["session_context"]["recipient_role"], "manager");
}

#[tokio::test]
async fn manager_worker_task_result_records_independent_manager_run_context() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context.clone());

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .unwrap();

    let dispatch_context = run_context
        .get_context(&dispatch.task_id)
        .await
        .expect("task dispatch run context");
    assert_eq!(dispatch_context.bot_id, "bot-worker");
    assert_eq!(dispatch_context.bcs_session_id.as_deref(), Some("group-1:abcdef12"));

    let worker_alias = "worker-provider-run";
    assert_eq!(
        flow.register_task_run_alias(&dispatch.task_id, worker_alias, "bot-worker")
            .await
            .unwrap(),
        TaskRunAliasRegistration::Registered
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: worker_alias.to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "task done"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let result_frame = frames.get(1).expect("expected task result frame");
    let params = request_params(result_frame);
    assert_eq!(params["task_id"], dispatch.task_id.as_str());
    let manager_result_run_id = request_id(result_frame);
    assert_ne!(manager_result_run_id, dispatch.task_id);
    assert_ne!(manager_result_run_id, worker_alias);

    let manager_context = run_context
        .get_context(&manager_result_run_id)
        .await
        .expect("manager result run context");
    assert_eq!(manager_context.bot_id, "bot-manager");
    assert_eq!(manager_context.group_id, "group-1");
    assert_eq!(manager_context.bcs_session_id.as_deref(), Some("group-1:abcdef12"));
    assert!(!manager_context.terminal);

    let original_context = run_context
        .get_context(&dispatch.task_id)
        .await
        .expect("original worker run context should be retained");
    assert_eq!(original_context.bot_id, "bot-worker");
}

#[tokio::test]
async fn manager_worker_task_final_persists_worker_final_and_manager_result_history() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "analysis before tool. "}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "run_id": dispatch.task_id.clone(),
            "bcs_group_id": "group-1",
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "lookup",
                "phase": "result",
                "toolCallId": "tool-1",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "answer after tool"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "analysis before tool. answer after tool"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::TaskDispatch, BotDeliveryKind::TaskResult]
    );
    let appended = repo.appended().await;
    assert_eq!(appended.len(), 5);
    assert_eq!(appended[0].content, json!("do work"));
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));

    let worker_final = &appended[3];
    assert_eq!(worker_final.group_id, "group-1");
    assert_eq!(worker_final.session_id, "group-1:abcdef12");
    assert_eq!(worker_final.sender_id, "bot-worker");
    assert_eq!(worker_final.sender_type, bcs_domain::SenderType::Bot);
    assert_eq!(worker_final.message_type, "chat");
    assert_eq!(worker_final.content, json!("answer after tool"));
    assert_eq!(worker_final.owner_bot_id.as_deref(), Some("bot-worker"));
    assert_eq!(worker_final.run_id, dispatch.task_id);

    let manager_result = &appended[4];
    assert_eq!(manager_result.group_id, "group-1");
    assert_eq!(manager_result.session_id, "group-1:abcdef12");
    assert_eq!(manager_result.sender_id, "bot-worker");
    assert_eq!(manager_result.sender_type, bcs_domain::SenderType::Bot);
    assert_eq!(manager_result.message_type, "chat");
    assert_eq!(manager_result.content, json!("answer after tool"));
    assert_eq!(manager_result.owner_bot_id, None);
    assert_eq!(manager_result.run_id, dispatch.task_id);
}

#[tokio::test]
async fn duplicate_manager_worker_task_final_does_not_append_duplicate_result_history() {
    let (_support, repo, flow) = manager_worker_flow_with_repo().await;

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .unwrap();

    let final_cmd = |text: &str| BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": text}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    };

    flow.handle_bot_event(final_cmd("task done")).await.unwrap();
    flow.handle_bot_event(final_cmd("duplicate")).await.unwrap();

    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        3,
        "dispatch, worker final, and manager result should each persist once"
    );
    assert_eq!(appended[1].content, json!("task done"));
    assert_eq!(appended[1].owner_bot_id.as_deref(), Some("bot-worker"));
    assert_eq!(appended[2].content, json!("task done"));
    assert_eq!(appended[2].owner_bot_id, None);
    assert_eq!(appended[2].run_id, dispatch.task_id);
}

#[tokio::test]
async fn manager_worker_task_final_from_non_target_bot_does_not_append_result_history() {
    let (_support, repo, flow) = manager_worker_flow_with_repo().await;

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-manager".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": "not worker"}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1, "only the original dispatch should persist");
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));
}

#[tokio::test]
async fn manager_worker_task_result_not_delivered_defers_history_until_retry_success() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;
    support.bot_delivery.not_delivered_for("bot-manager").await;
    register_manager_worker_task(
        &flow,
        "task-result-not-delivered",
        ChatResponseMode::AfterLastToolCall,
    )
    .await;

    let final_cmd = BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-not-delivered".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": "task done"}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    };

    let first = flow.handle_bot_event(final_cmd.clone()).await.unwrap();

    assert_eq!(first.bot_deliveries.len(), 1);
    assert!(!first.bot_deliveries[0].delivered);
    assert!(
        repo.appended().await.is_empty(),
        "not-delivered manager result must not persist worker final or manager result history"
    );
    assert_eq!(
        flow.task_store
            .get("task-result-not-delivered")
            .await
            .unwrap()
            .status,
        TaskLedgerStatus::Dispatched
    );

    let retry_delivery = Arc::new(support::RecordingBotDelivery::default());
    let retry_flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        retry_delivery,
        support.frontend_delivery.clone(),
    )
    .with_task_store(flow.task_store.clone())
    .with_message_repo(repo.clone());

    let retry = retry_flow.handle_bot_event(final_cmd).await.unwrap();

    assert_eq!(retry.bot_deliveries.len(), 1);
    assert!(retry.bot_deliveries[0].delivered);
    assert_eq!(
        retry_flow
            .task_store
            .get("task-result-not-delivered")
            .await
            .unwrap()
            .status,
        TaskLedgerStatus::Replied
    );
    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        2,
        "retry success should persist exactly worker final and manager result"
    );
    assert_eq!(appended[0].content, json!("task done"));
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));
    assert_eq!(appended[1].content, json!("task done"));
    assert_eq!(appended[1].owner_bot_id, None);
    assert_eq!(appended[1].run_id, "task-result-not-delivered");
}

#[tokio::test]
async fn manager_worker_task_result_retry_uses_only_successful_terminal_text() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;
    support.bot_delivery.not_delivered_for("bot-manager").await;
    register_manager_worker_task(
        &flow,
        "task-result-retry-text",
        ChatResponseMode::AfterLastToolCall,
    )
    .await;

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-retry-text".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "tool_call_start",
            "tool_call_id": "tool-1",
            "tool_name": "lookup",
        }),
        state: ChatEventState::ToolCallStart,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let terminal_cmd = |text: &str| BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-retry-text".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": text}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    };

    let first = flow.handle_bot_event(terminal_cmd("first")).await.unwrap();

    assert_eq!(first.bot_deliveries.len(), 1);
    assert!(!first.bot_deliveries[0].delivered);
    assert!(
        repo.appended().await.is_empty(),
        "failed first attempt must not persist terminal history"
    );
    assert_eq!(
        flow.task_store
            .get("task-result-retry-text")
            .await
            .unwrap()
            .status,
        TaskLedgerStatus::Dispatched
    );

    let retry_delivery = Arc::new(support::RecordingBotDelivery::default());
    let retry_flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        retry_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_task_store(flow.task_store.clone())
    .with_message_repo(repo.clone());

    let retry = retry_flow
        .handle_bot_event(terminal_cmd("second"))
        .await
        .unwrap();

    assert_eq!(retry.bot_deliveries.len(), 1);
    assert!(retry.bot_deliveries[0].delivered);
    let frames = retry_delivery.frames().await;
    let params = request_params(frames.first().expect("expected retry task result frame"));
    assert_eq!(params["message"]["content"][0]["text"], "[from:Worker] second");
    assert_eq!(params["session_context"]["message"], "second");

    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        2,
        "only the successful retry should persist terminal history"
    );
    assert_eq!(appended[0].content, json!("second"));
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));
    assert_eq!(appended[1].content, json!("second"));
    assert_eq!(appended[1].owner_bot_id, None);
    assert_eq!(appended[1].run_id, "task-result-retry-text");
}

#[tokio::test]
async fn manager_worker_task_result_delivery_error_does_not_persist_or_mark_replied() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;
    support.bot_delivery.fail_for("bot-manager").await;
    register_manager_worker_task(
        &flow,
        "task-result-delivery-error",
        ChatResponseMode::AfterLastToolCall,
    )
    .await;

    let err = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-worker".to_string(),
            run_id: "task-result-delivery-error".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": { "role": "assistant", "content": [{"type": "text", "text": "task done"}] },
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .expect_err("manager delivery error should propagate");

    match err {
        ServiceError::BotNotConnected(bot_id) => assert_eq!(bot_id, "bot-manager"),
        other => panic!("expected BotNotConnected for manager delivery failure, got {other:?}"),
    }
    assert!(
        repo.appended().await.is_empty(),
        "delivery error must not persist worker final or manager result history"
    );
    assert_eq!(
        flow.task_store
            .get("task-result-delivery-error")
            .await
            .unwrap()
            .status,
        TaskLedgerStatus::Dispatched
    );
}

#[tokio::test]
async fn manager_worker_task_final_delta_mode_flushes_only_open_worker_segment() {
    let (_support, repo, flow) = manager_worker_flow_with_repo().await;
    register_manager_worker_task(&flow, "task-result-delta-full", ChatResponseMode::Full).await;

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-delta-full".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({ "state": "delta", "delta_text": "intro before tool. " }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-delta-full".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "run_id": "task-result-delta-full",
            "bcs_group_id": "group-1",
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "lookup",
                "phase": "result",
                "toolCallId": "tool-1",
                "result": { "content": [{"type": "text", "text": "tool output"}] },
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-delta-full".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({ "state": "delta", "delta_text": "answer after tool" }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "task-result-delta-full".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "intro before tool. answer after tool"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    let worker_chat: Vec<_> = appended
        .iter()
        .filter(|msg| {
            msg.message_type == "chat" && msg.owner_bot_id.as_deref() == Some("bot-worker")
        })
        .map(|msg| msg.content.clone())
        .collect();
    assert_eq!(
        worker_chat,
        vec![json!("intro before tool. "), json!("answer after tool")],
        "worker final history must not duplicate the already-flushed delta segment"
    );
    let manager_result = appended
        .iter()
        .find(|msg| {
            msg.sender_id == "bot-worker"
                && msg.message_type == "chat"
                && msg.owner_bot_id.is_none()
        })
        .expect("manager result history");
    assert_eq!(
        manager_result.content,
        json!("intro before tool. answer after tool")
    );
}

#[tokio::test]
async fn manager_worker_task_result_defaults_to_text_after_last_tool_call() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .unwrap();

    for (state, text) in [
        (ChatEventState::Delta, Some("analysis before tool. ")),
        (ChatEventState::ToolCallStart, None),
        (
            ChatEventState::Delta,
            Some("analysis before tool. tool detail. "),
        ),
        (ChatEventState::ToolCallEnd, None),
        (
            ChatEventState::Final,
            Some("analysis before tool. tool detail. answer after tool"),
        ),
    ] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-worker".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: match text {
                Some(text) => json!({
                    "state": state_payload_name(&state),
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": text}],
                    },
                }),
                None => json!({
                    "state": state_payload_name(&state),
                    "tool_call_id": "tool-1",
                    "tool_name": "lookup",
                }),
            },
            state,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .unwrap();
    }

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.get(1).expect("expected task result frame"));
    assert_eq!(
        params["message"]["content"][0]["text"],
        "[from:Worker] answer after tool"
    );
    assert_eq!(params["session_context"]["message"], "answer after tool");
}

#[tokio::test]
async fn manager_worker_task_result_uses_agent_tool_boundary_by_default() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "analysis before tool. "}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "run_id": dispatch.task_id.clone(),
            "bcs_group_id": "group-1",
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "lookup",
                "phase": "result",
                "toolCallId": "tool-1",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "answer after tool"}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "analysis before tool. answer after tool"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.get(1).expect("expected task result frame"));
    assert_eq!(
        params["message"]["content"][0]["text"],
        "[from:Worker] answer after tool"
    );
    assert_eq!(params["session_context"]["message"], "answer after tool");
}

#[tokio::test]
async fn manager_worker_task_result_uses_final_when_agent_tool_has_no_followup_text() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "delta",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "analysis before tool. "}],
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: json!({
            "run_id": dispatch.task_id.clone(),
            "bcs_group_id": "group-1",
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "lookup",
                "phase": "result",
                "toolCallId": "tool-1",
            },
        }),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "analysis before tool."}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let params = request_params(frames.get(1).expect("expected task result frame"));
    assert_eq!(
        params["message"]["content"][0]["text"],
        "[from:Worker] analysis before tool."
    );
    assert_eq!(params["session_context"]["message"], "analysis before tool.");
}

#[tokio::test]
async fn task_complete_updates_group_status_without_legacy_service_group_callback() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    group.service_group_uuid = Some("sg-1".to_string());
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert_eq!(outcome.status, "completed");
    assert!(!outcome.callback_requested);
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Completed
    );
}

#[tokio::test]
async fn ws_task_complete_blocked_returns_err_when_pending() {
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

    flow.handle_task_dispatch(TaskDispatchCommand {
        driver_bot_id: "bot-driver".to_string(),
        group_id: "group-1".to_string(),
        target_bot_id: "bot-observer".to_string(),
        target_bot_name: None,
        payload: json!({"message": "do work"}),
    })
    .await
    .unwrap();

    let err = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .expect_err("legacy task.complete should fail while workers are pending");

    match err {
        ServiceError::InvalidOperation { message, request_id } => {
            assert!(message.contains("pending"));
            assert!(message.contains("Observer"));
            assert_eq!(request_id, Some("group-1".to_string()));
        }
        other => panic!("expected invalid operation, got {other:?}"),
    }
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Active
    );
}

#[tokio::test]
async fn echo_task_complete_blocked_returns_ok_guarded_when_pending() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    group.service_spec = Some(ServiceSpec {
        callback_config: None,
        timeout_seconds: None,
        max_concurrency: None,
    });
    support.group.upsert(group).await.unwrap();
    let session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management.clone());

    flow.handle_task_dispatch(TaskDispatchCommand {
        driver_bot_id: "bot-driver".to_string(),
        group_id: "group-1".to_string(),
        target_bot_id: "bot-observer".to_string(),
        target_bot_name: None,
        payload: json!({"message": "do work"}),
    })
    .await
    .unwrap();

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: true,
            payload: json!({
                "group_id": "group-1",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert!(outcome.blocked);
    assert_eq!(outcome.pending, vec!["Observer".to_string()]);
    assert!(outcome.completed_session.is_none());
    assert!(!outcome.callback_requested);
    assert!(outcome.frontend_deliveries.is_empty());
    assert!(session_management.completed.read().await.is_empty());
    assert!(support.frontend_delivery.events().await.is_empty());
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Active
    );
}

#[tokio::test]
async fn task_complete_allowed_when_no_pending() {
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

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();
    flow.task_store.mark_replied(&dispatch.task_id).await;

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert!(!outcome.blocked);
    assert!(outcome.pending.is_empty());
    assert_eq!(outcome.status, "completed");
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Completed
    );
}

#[tokio::test]
async fn task_complete_allowed_when_dispatched_task_timed_out() {
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
    flow.task_store
        .register(new_task_entry(
            "stale-task".to_string(),
            "group-1".to_string(),
            None,
            "bot-driver".to_string(),
            "bot-observer".to_string(),
            Some("Observer".to_string()),
            0,
            ChatResponseMode::AfterLastToolCall,
        ))
        .await;
    let summary = flow
        .task_store
        .ledger_summary_at("group-1", None, TASK_TTL_MS + 1)
        .await;
    assert!(summary.pending.is_empty());
    assert_eq!(summary.timed_out, vec!["Observer".to_string()]);

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert!(!outcome.blocked);
    assert!(outcome.pending.is_empty());
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Completed
    );
}

#[tokio::test]
async fn task_complete_pending_in_other_session_does_not_block() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    group.service_spec = Some(ServiceSpec {
        callback_config: None,
        timeout_seconds: None,
        max_concurrency: None,
    });
    let participants = group.participants.clone();
    support.group.upsert(group).await.unwrap();
    let mut pending_session =
        test_session("group-1:pending", "group-1", SessionKind::ServiceInvocation);
    pending_session.participants = participants.clone();
    let mut completing_session =
        test_session("group-1:complete", "group-1", SessionKind::ServiceInvocation);
    completing_session.participants = participants;
    let session_management = Arc::new(RecordingSessionManagement::new(vec![
        pending_session,
        completing_session,
    ]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management.clone());

    flow.handle_task_dispatch(TaskDispatchCommand {
        driver_bot_id: "bot-driver".to_string(),
        group_id: "group-1".to_string(),
        target_bot_id: "bot-observer".to_string(),
        target_bot_name: None,
        payload: json!({
            "bcs_session_id": "group-1:pending",
            "message": "do work"
        }),
    })
    .await
    .unwrap();

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1",
                "bcs_session_id": "group-1:complete",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert!(!outcome.blocked);
    assert!(outcome.pending.is_empty());
    assert_eq!(
        outcome.completed_session.as_ref().map(|session| session.id.as_str()),
        Some("group-1:complete")
    );
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Active
    );
    assert_eq!(
        flow.task_store.pending_targets("group-1", Some("group-1:pending")).await,
        vec!["Observer".to_string()]
    );
}

#[tokio::test]
async fn task_complete_with_session_id_completes_service_session_without_closing_group() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    group.service_spec = Some(ServiceSpec {
        callback_config: None,
        timeout_seconds: None,
        max_concurrency: None,
    });
    support.group.upsert(group).await.unwrap();
    let session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management.clone());

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1",
                "bcs_session_id": "group-1:abcdef12",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert_eq!(outcome.status, "completed");
    assert!(outcome.callback_requested);
    assert_eq!(
        outcome.completed_session.as_ref().map(|session| session.id.as_str()),
        Some("group-1:abcdef12")
    );
    let completed = session_management.completed.read().await;
    assert_eq!(completed.len(), 1);
    assert_eq!(completed[0].0, "group-1:abcdef12");
    assert_eq!(completed[0].1, Some(json!("done")));
    assert_eq!(completed[0].2, None);
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Active
    );
}

#[tokio::test]
async fn task_complete_with_legacy_session_group_id_completes_session_without_closing_group() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    group.service_spec = Some(ServiceSpec {
        callback_config: None,
        timeout_seconds: None,
        max_concurrency: None,
    });
    support.group.upsert(group).await.unwrap();
    let session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management.clone());

    let outcome = flow
        .handle_task_complete(TaskCompleteCommand {
            task_id: "group-1:abcdef12".to_string(),
            bot_id: "bot-driver".to_string(),
            via_echo: false,
            payload: json!({
                "group_id": "group-1:abcdef12",
                "summary": "done",
                "status": "completed",
            }),
        })
        .await
        .unwrap();

    assert_eq!(outcome.status, "completed");
    assert!(outcome.callback_requested);
    assert_eq!(
        outcome.completed_session.as_ref().map(|session| session.id.as_str()),
        Some("group-1:abcdef12")
    );
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Active
    );
}

#[tokio::test]
async fn manager_worker_task_complete_authorizes_manager_role_not_driver_bot_field() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![Participant::bot("bot-manager", ParticipantRole::Manager)];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_task_complete(TaskCompleteCommand {
        bot_id: "bot-manager".to_string(),
        task_id: "group-1".to_string(),
        via_echo: false,
        payload: json!({
            "group_id": "group-1",
            "status": "completed"
        }),
    })
    .await
    .expect("manager role should be allowed to complete task groups");

    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Completed
    );
}

fn request_params(frame: &BcsFrame) -> &Value {
    match frame {
        BcsFrame::Request(req) => req.params.as_ref().expect("request params"),
        other => panic!("expected request frame, got {other:?}"),
    }
}

fn request_id(frame: &BcsFrame) -> String {
    match frame {
        BcsFrame::Request(req) => req.id.clone(),
        other => panic!("expected request frame, got {other:?}"),
    }
}

fn coordination_echo(tool: &str, arguments: Value) -> String {
    json!({
        "__bcs_coordination__": true,
        "v": 1,
        "tool": tool,
        "arguments": arguments,
        "status": "received",
    })
    .to_string()
}

fn agent_tool_result_payload(
    tool_name: Option<&str>,
    tool_call_id: &str,
    result_text: &str,
    is_error: bool,
) -> Value {
    let mut data = json!({
        "phase": "result",
        "toolCallId": tool_call_id,
        "isError": is_error,
        "result": {
            "content": [{"type": "text", "text": result_text}],
        },
    });
    if let Some(tool_name) = tool_name {
        data["name"] = Value::String(tool_name.to_string());
    }
    json!({
        "stream": "tool",
        "data": data,
    })
}

fn test_session(id: &str, group_id: &str, kind: SessionKind) -> Session {
    Session {
        id: id.to_string(),
        group_id: group_id.to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: kind,
        participants: Vec::new(),
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: Some("pending".to_string()),
        activation_count: 1,
        caller_principal: None,
        created_by: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        current_msg_seq: 0,
        participant_join_seq: None,
    }
}

struct RecordingSessionManagement {
    sessions: RwLock<Vec<Session>>,
    completed: RwLock<Vec<(String, Option<Value>, Option<String>)>>,
}

impl RecordingSessionManagement {
    fn new(sessions: Vec<Session>) -> Self {
        Self {
            sessions: RwLock::new(sessions),
            completed: RwLock::new(Vec::new()),
        }
    }
}

#[async_trait::async_trait]
impl SessionManagementService for RecordingSessionManagement {
    async fn create_or_reactivate(
        &self,
        _cmd: bcs_service_api::CreateOrReactivateCommand,
    ) -> Result<bcs_service_api::CreateOrReactivateOutcome, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn get(&self, session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self
            .sessions
            .read()
            .await
            .iter()
            .find(|session| session.id == session_id)
            .cloned())
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(self
            .sessions
            .read()
            .await
            .iter()
            .any(|s| s.id == session_id && s.group_id == group_id))
    }

    async fn list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(self
            .sessions
            .read()
            .await
            .iter()
            .filter(|session| session.group_id == group_id)
            .filter(|session| status.map_or(true, |status| session.status == status))
            .cloned()
            .collect())
    }

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        Ok(())
    }

    async fn complete_if_running(
        &self,
        session_id: &str,
        output: Option<Value>,
        error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        self.completed
            .write()
            .await
            .push((session_id.to_string(), output.clone(), error.clone()));
        let mut sessions = self.sessions.write().await;
        let Some(session) = sessions.iter_mut().find(|session| session.id == session_id) else {
            return Ok(None);
        };
        if session.status == SessionStatus::Completed {
            return Ok(None);
        }
        session.status = SessionStatus::Completed;
        session.output = output;
        session.error_message = error;
        Ok(Some(session.clone()))
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> { Ok(false) }
}

struct BlockingInterceptor;

#[async_trait::async_trait]
impl MessageInterceptor for BlockingInterceptor {
    async fn on_outbound(&self, _msg: &mut OutboundMessage) -> InterceptorDecision {
        InterceptorDecision::Block(BlockReason {
            interceptor_id: "test-block".to_string(),
            code: "blocked".to_string(),
            message: "task dispatch blocked by test".to_string(),
            user_visible: true,
        })
    }
}

#[tokio::test]
async fn task_dispatch_blocking_interceptor_prevents_bot_delivery() {
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
    )
    .with_interceptor(BlockingInterceptor);

    let result = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await;

    match result {
        Err(ServiceError::Forbidden(message)) => {
            assert_eq!(message, "task dispatch blocked by test");
        }
        other => panic!("expected blocked Forbidden, got {other:?}"),
    }
    // Bot must NOT receive the dispatch frame.
    assert!(support.bot_delivery.frames().await.is_empty());
}

// -- Chat persistence on terminal error/abort --

/// Minimal `MessageRepoPort` that records every appended message so tests can
/// assert what got persisted to history.
#[derive(Default)]
struct RecordingMessageRepo {
    appended: RwLock<Vec<bcs_domain::NewMessage>>,
}

impl RecordingMessageRepo {
    async fn appended(&self) -> Vec<bcs_domain::NewMessage> {
        self.appended.read().await.clone()
    }
}

#[async_trait::async_trait]
impl bcs_service_api::port::repo::MessageRepoPort for RecordingMessageRepo {
    async fn append_message(
        &self,
        msg: bcs_domain::NewMessage,
    ) -> Result<bcs_domain::PersistedMessage, bcs_service_api::port::repo::MessageRepoError> {
        let seq = self.appended.read().await.len() as i64 + 1;
        let persisted = bcs_domain::PersistedMessage {
            message_id: format!("msg-{seq}"),
            group_id: msg.group_id.clone(),
            session_id: msg.session_id.clone(),
            session_seq: seq,
            sender_id: msg.sender_id.clone(),
            sender_type: msg.sender_type,
            message_type: msg.message_type.clone(),
            content: msg.content.clone(),
            client_msg_id: msg.client_msg_id.clone(),
            owner_bot_id: msg.owner_bot_id.clone(),
            status: bcs_domain::PersistedMessageStatus::Normal,
            created_at: msg.created_at,
            run_id: msg.run_id.clone(),
        };
        self.appended.write().await.push(msg);
        Ok(persisted)
    }

    async fn query_messages(
        &self,
        _query: bcs_domain::MessageQuery,
    ) -> Result<bcs_domain::MessagePage, bcs_service_api::port::repo::MessageRepoError> {
        Ok(bcs_domain::MessagePage {
            messages: Vec::new(),
            next_cursor: None,
            has_more: false,
        })
    }

    async fn get_message_by_id(
        &self,
        _session_id: &str,
        _message_id: &str,
    ) -> Result<Option<bcs_domain::PersistedMessage>, bcs_service_api::port::repo::MessageRepoError>
    {
        Ok(None)
    }

    async fn get_current_seq(
        &self,
        _session_id: &str,
    ) -> Result<i64, bcs_service_api::port::repo::MessageRepoError> {
        Ok(self.appended.read().await.len() as i64)
    }
}

struct BlockingMessageRepo {
    appended: RwLock<Vec<bcs_domain::NewMessage>>,
    append_reached: Barrier,
    release_append: Notify,
}

impl BlockingMessageRepo {
    fn new() -> Self {
        Self {
            appended: RwLock::new(Vec::new()),
            append_reached: Barrier::new(2),
            release_append: Notify::new(),
        }
    }

    async fn wait_for_append(&self) {
        timeout(Duration::from_secs(1), self.append_reached.wait())
            .await
            .expect("message append should start");
    }

    fn release_append(&self) {
        self.release_append.notify_one();
    }

    async fn appended(&self) -> Vec<bcs_domain::NewMessage> {
        self.appended.read().await.clone()
    }
}

#[async_trait::async_trait]
impl bcs_service_api::port::repo::MessageRepoPort for BlockingMessageRepo {
    async fn append_message(
        &self,
        msg: bcs_domain::NewMessage,
    ) -> Result<bcs_domain::PersistedMessage, bcs_service_api::port::repo::MessageRepoError> {
        self.append_reached.wait().await;
        self.release_append.notified().await;

        let seq = self.appended.read().await.len() as i64 + 1;
        let persisted = bcs_domain::PersistedMessage {
            message_id: format!("msg-{seq}"),
            group_id: msg.group_id.clone(),
            session_id: msg.session_id.clone(),
            session_seq: seq,
            sender_id: msg.sender_id.clone(),
            sender_type: msg.sender_type,
            message_type: msg.message_type.clone(),
            content: msg.content.clone(),
            client_msg_id: msg.client_msg_id.clone(),
            owner_bot_id: msg.owner_bot_id.clone(),
            status: bcs_domain::PersistedMessageStatus::Normal,
            created_at: msg.created_at,
            run_id: msg.run_id.clone(),
        };
        self.appended.write().await.push(msg);
        Ok(persisted)
    }

    async fn query_messages(
        &self,
        _query: bcs_domain::MessageQuery,
    ) -> Result<bcs_domain::MessagePage, bcs_service_api::port::repo::MessageRepoError> {
        Ok(bcs_domain::MessagePage {
            messages: Vec::new(),
            next_cursor: None,
            has_more: false,
        })
    }

    async fn get_message_by_id(
        &self,
        _session_id: &str,
        _message_id: &str,
    ) -> Result<Option<bcs_domain::PersistedMessage>, bcs_service_api::port::repo::MessageRepoError>
    {
        Ok(None)
    }

    async fn get_current_seq(
        &self,
        _session_id: &str,
    ) -> Result<i64, bcs_service_api::port::repo::MessageRepoError> {
        Ok(self.appended.read().await.len() as i64)
    }
}

async fn manager_worker_flow_with_repo() -> (
    support::FlowTestSupport,
    Arc<RecordingMessageRepo>,
    BcsMessageFlow,
) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());
    (support, repo, flow)
}

async fn configure_provider_v2_target(
    support: &support::FlowTestSupport,
    bot_id: &str,
    created_by: &str,
) {
    support
        .registry
        .save_created_by(bot_id, created_by, true)
        .await
        .unwrap();
    let mut provider_target = support::FakeRegistryService::provider_target(bot_id);
    if let BotDeliveryTarget::HttpProvider {
        protocol_version, ..
    } = &mut provider_target
    {
        *protocol_version = "2.0".to_string();
    }
    support
        .registry
        .set_delivery_target(bot_id, provider_target)
        .await;
}

fn manager_worker_flow_with_provider_stream(
    support: &support::FlowTestSupport,
    repo: Arc<RecordingMessageRepo>,
) -> BcsMessageFlow {
    BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo)
    .with_provider_stream_gray_list(Arc::new(ProviderStreamGrayList::new(vec![
        "gray-user".to_string(),
    ])))
}

async fn manager_worker_flow_with_blocking_repo() -> (
    support::FlowTestSupport,
    Arc<BlockingMessageRepo>,
    Arc<MemoryBotRunContextStore>,
    BcsMessageFlow,
) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(BlockingMessageRepo::new());
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone())
    .with_bot_run_context(run_context.clone());
    (support, repo, run_context, flow)
}

async fn register_manager_worker_task(
    flow: &BcsMessageFlow,
    task_id: &str,
    response_mode: ChatResponseMode,
) {
    flow.task_store
        .register(new_task_entry(
            task_id.to_string(),
            "group-1".to_string(),
            Some("group-1:abcdef12".to_string()),
            "bot-manager".to_string(),
            "bot-worker".to_string(),
            Some("Worker".to_string()),
            0,
            response_mode,
        ))
        .await;
}

#[tokio::test]
async fn manager_worker_task_dispatch_uses_sse_for_eligible_provider_worker() {
    let (support, repo, _) = manager_worker_flow_with_repo().await;
    configure_provider_v2_target(&support, "bot-worker", "gray-user").await;
    let flow = manager_worker_flow_with_provider_stream(&support, repo);

    flow.handle_task_dispatch(TaskDispatchCommand {
        driver_bot_id: "bot-manager".to_string(),
        group_id: "group-1".to_string(),
        target_bot_id: "bot-worker".to_string(),
        target_bot_name: None,
        payload: json!({
            "message": "do work",
            "bcs_session_id": "group-1:abcdef12",
        }),
    })
    .await
    .expect("task.dispatch should deliver to provider worker");

    assert_eq!(
        support.bot_delivery.provider_transports().await,
        vec![ProviderTransportPreference::CallbackSse]
    );
}

#[tokio::test]
async fn manager_worker_task_message_uses_sse_for_eligible_provider_manager() {
    let (support, repo, _) = manager_worker_flow_with_repo().await;
    configure_provider_v2_target(&support, "bot-manager", "gray-user").await;
    let flow = manager_worker_flow_with_provider_stream(&support, repo);

    flow.handle_task_message(TaskMessageCommand {
        worker_bot_id: "bot-worker".to_string(),
        group_id: "group-1".to_string(),
        payload: json!({
            "message": "progress update",
            "bcs_session_id": "group-1:abcdef12",
        }),
    })
    .await
    .expect("task.message should deliver to provider manager");

    assert_eq!(
        support.bot_delivery.provider_transports().await,
        vec![ProviderTransportPreference::CallbackSse]
    );
}

#[tokio::test]
async fn manager_worker_task_result_uses_sse_for_eligible_provider_manager() {
    let (support, repo, _) = manager_worker_flow_with_repo().await;
    configure_provider_v2_target(&support, "bot-manager", "gray-user").await;
    let flow = manager_worker_flow_with_provider_stream(&support, repo);

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .expect("task.dispatch should deliver to worker");

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: dispatch.task_id,
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "task done"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .expect("worker final should deliver task result to provider manager");

    assert_eq!(
        support.bot_delivery.provider_transports().await,
        vec![
            ProviderTransportPreference::Callback,
            ProviderTransportPreference::CallbackSse,
        ]
    );
}

#[tokio::test]
async fn manager_worker_task_dispatch_persists_dispatch_to_worker_history_after_delivery() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;

    let outcome = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
        .expect("task.dispatch should deliver to worker");

    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    let msg = &appended[0];
    assert_eq!(msg.group_id, "group-1");
    assert_eq!(msg.session_id, "group-1:abcdef12");
    assert_eq!(msg.sender_id, "bot-manager");
    assert_eq!(msg.sender_type, bcs_domain::SenderType::Bot);
    assert_eq!(msg.message_type, "chat");
    assert_eq!(msg.content, json!("do work"));
    assert_eq!(msg.owner_bot_id.as_deref(), Some("bot-worker"));
    assert_eq!(msg.run_id, outcome.task_id);
}

#[tokio::test]
async fn manager_worker_task_dispatch_records_context_before_history_persistence() {
    let (support, repo, run_context, flow) = manager_worker_flow_with_blocking_repo().await;

    let handle = tokio::spawn(async move {
        flow.handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({
                "message": "do work",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
    });

    repo.wait_for_append().await;
    let frames = support.bot_delivery.frames().await;
    let run_id = request_id(frames.first().expect("expected task.dispatch frame"));
    let context_before_append_finished = run_context.get_context(&run_id).await;
    repo.release_append();
    let outcome = handle
        .await
        .expect("task.dispatch join should complete")
        .expect("task.dispatch should deliver to worker");

    assert_eq!(outcome.task_id, run_id);
    let context = context_before_append_finished
        .expect("run context should be recorded before history append completes");
    assert_eq!(context.bot_id, "bot-worker");
    assert_eq!(context.group_id, "group-1");
    assert_eq!(context.bcs_session_id.as_deref(), Some("group-1:abcdef12"));
    assert_eq!(repo.appended().await.len(), 1);
}

#[tokio::test]
async fn manager_worker_task_dispatch_does_not_persist_when_delivery_is_not_delivered() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;
    support.bot_delivery.not_delivered_for("bot-worker").await;

    let err = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .expect_err("not-delivered task.dispatch should fail");

    match err {
        ServiceError::InvalidOperation { message, .. } => {
            assert_eq!(message, "target bot is not connected");
        }
        other => panic!("expected InvalidOperation for not-delivered dispatch, got {other:?}"),
    }
    assert!(repo.appended().await.is_empty());
}

#[tokio::test]
async fn manager_worker_task_dispatch_does_not_persist_when_delivery_errors() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;
    support.bot_delivery.fail_for("bot-worker").await;

    let err = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-manager".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-worker".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .expect_err("delivery error should fail task.dispatch");

    match err {
        ServiceError::BotNotConnected(bot_id) => assert_eq!(bot_id, "bot-worker"),
        other => panic!("expected BotNotConnected for failed dispatch, got {other:?}"),
    }
    assert!(repo.appended().await.is_empty());
}

#[tokio::test]
async fn non_manager_worker_task_dispatch_does_not_persist_owner_tagged_dispatch_history() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    flow.handle_task_dispatch(TaskDispatchCommand {
        driver_bot_id: "bot-driver".to_string(),
        group_id: "group-1".to_string(),
        target_bot_id: "bot-observer".to_string(),
        target_bot_name: None,
        payload: json!({"message": "do work"}),
    })
    .await
    .expect("legacy master_slave dispatch should remain deliverable");

    assert!(repo.appended().await.is_empty());
}

#[tokio::test]
async fn manager_worker_task_message_persists_worker_message_to_manager_history_after_delivery() {
    let (support, repo, flow) = manager_worker_flow_with_repo().await;

    flow.handle_task_message(TaskMessageCommand {
        worker_bot_id: "bot-worker".to_string(),
        group_id: "group-1".to_string(),
        payload: json!({
            "message": "blocked on missing schema",
            "bcs_session_id": "group-1:abcdef12",
        }),
    })
    .await
    .expect("task.message should deliver to manager");

    let frames = support.bot_delivery.frames().await;
    let delivery_run_id = match frames.first().expect("expected task.message frame") {
        BcsFrame::Request(req) => req.id.clone(),
        other => panic!("expected request frame for task.message, got {other:?}"),
    };
    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    let msg = &appended[0];
    assert_eq!(msg.group_id, "group-1");
    assert_eq!(msg.session_id, "group-1:abcdef12");
    assert_eq!(msg.sender_id, "bot-worker");
    assert_eq!(msg.sender_type, bcs_domain::SenderType::Bot);
    assert_eq!(msg.message_type, "chat");
    assert_eq!(msg.content, json!("blocked on missing schema"));
    assert_eq!(msg.owner_bot_id, None);
    assert!(!msg.run_id.is_empty());
    assert_eq!(msg.run_id, delivery_run_id);
    assert_ne!(msg.run_id, "group-1:abcdef12");
}

#[tokio::test]
async fn bot_error_terminal_with_display_message_only_publishes_frontend() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    let error = "Concurrent request timed out";
    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-error-only".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": "run-error-only",
                "bcs_group_id": "group-1",
                "state": "error",
                "errorMessage": error,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": error}],
                    "timestamp": 1,
                },
            }),
            state: ChatEventState::Error,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    assert!(
        outcome.bot_deliveries.is_empty(),
        "error terminal must not be relayed to other bots"
    );
    assert!(
        support.bot_delivery.frames().await.is_empty(),
        "error terminal must not broadcast through bot delivery"
    );
    assert!(
        repo.appended().await.is_empty(),
        "error message itself must not be persisted as chat history"
    );

    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 1);
    let frame: Value = serde_json::from_str(&events[0]).unwrap();
    assert_eq!(frame["event"], "chat");
    assert_eq!(frame["payload"]["state"], "error");
    assert_eq!(frame["payload"]["errorMessage"], error);
    assert_eq!(frame["payload"]["message"]["content"][0]["text"], error);
}

#[tokio::test]
async fn manager_worker_task_message_records_context_before_history_persistence() {
    let (support, repo, run_context, flow) = manager_worker_flow_with_blocking_repo().await;

    let handle = tokio::spawn(async move {
        flow.handle_task_message(TaskMessageCommand {
            worker_bot_id: "bot-worker".to_string(),
            group_id: "group-1".to_string(),
            payload: json!({
                "message": "blocked on missing schema",
                "bcs_session_id": "group-1:abcdef12",
            }),
        })
        .await
    });

    repo.wait_for_append().await;
    let frames = support.bot_delivery.frames().await;
    let run_id = request_id(frames.first().expect("expected task.message frame"));
    let context_before_append_finished = run_context.get_context(&run_id).await;
    repo.release_append();
    handle
        .await
        .expect("task.message join should complete")
        .expect("task.message should deliver to manager");

    let context = context_before_append_finished
        .expect("run context should be recorded before history append completes");
    assert_eq!(context.bot_id, "bot-manager");
    assert_eq!(context.group_id, "group-1");
    assert_eq!(context.bcs_session_id.as_deref(), Some("group-1:abcdef12"));
    assert_eq!(repo.appended().await.len(), 1);
}

#[tokio::test]
async fn bot_final_chat_persists_worker_owner_and_public_manager_owner_for_manager_worker() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "run-worker-final".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "worker result"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    assert_eq!(appended[0].sender_id, "bot-worker");
    assert_eq!(appended[0].message_type, "chat");
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-manager".to_string(),
        run_id: "run-manager-final".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "manager result"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 2);
    assert_eq!(appended[1].sender_id, "bot-manager");
    assert_eq!(appended[1].message_type, "chat");
    assert_eq!(appended[1].owner_bot_id, None);

    let chat_support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let chat_repo = Arc::new(RecordingMessageRepo::default());
    let chat_flow = BcsMessageFlow::new(
        chat_support.group.clone(),
        chat_support.routing.clone(),
        chat_support.registry.clone(),
        chat_support.bot_delivery.clone(),
        chat_support.frontend_delivery.clone(),
    )
    .with_message_repo(chat_repo.clone());

    chat_flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-chat-final".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "chat result"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    let chat_appended = chat_repo.appended().await;
    assert_eq!(chat_appended.len(), 1);
    assert_eq!(chat_appended[0].message_type, "chat");
    assert_eq!(chat_appended[0].owner_bot_id, None);
}

#[tokio::test]
async fn bot_self_output_uses_session_worker_role_for_private_owner() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-session-worker", "Session Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![Participant::bot("bot-manager", ParticipantRole::Manager)];
    support.group.upsert(group).await.unwrap();

    let mut session = test_session("group-1:abcdef12", "group-1", SessionKind::ServiceInvocation);
    session.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-session-worker", ParticipantRole::Worker),
    ];
    let session_management = Arc::new(RecordingSessionManagement::new(vec![session]));
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(session_management)
    .with_message_repo(repo.clone());

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-session-worker".to_string(),
        run_id: "run-session-worker-final".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "session worker result"}],
            },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-session-worker".to_string(),
        run_id: "run-session-worker-tool".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(
            Some("exec"),
            "tool-session-worker-1",
            "session worker tool output",
            false,
        ),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 2);
    assert_eq!(appended[0].message_type, "chat");
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-session-worker"));
    assert_eq!(appended[1].message_type, "tool_call");
    assert_eq!(appended[1].owner_bot_id.as_deref(), Some("bot-session-worker"));
}

#[tokio::test]
async fn agent_tool_result_persists_worker_owner_and_public_manager_owner_for_manager_worker() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-worker".to_string(),
        run_id: "run-worker-tool".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(
            Some("exec"),
            "tool-worker-1",
            "worker tool output",
            false,
        ),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    assert_eq!(appended[0].sender_id, "bot-worker");
    assert_eq!(appended[0].message_type, "tool_call");
    assert_eq!(appended[0].content["tool_call_id"], "tool-worker-1");
    assert_eq!(appended[0].run_id, "run-worker-tool");
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-manager".to_string(),
        run_id: "run-manager-tool".to_string(),
        group_id: "group-1".to_string(),
        event_type: "agent".to_string(),
        event_payload: agent_tool_result_payload(
            Some("exec"),
            "tool-manager-1",
            "manager tool output",
            false,
        ),
        state: ChatEventState::Delta,
        bcs_session_id: Some("group-1:abcdef12".to_string()),
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 2);
    assert_eq!(appended[1].sender_id, "bot-manager");
    assert_eq!(appended[1].message_type, "tool_call");
    assert_eq!(appended[1].owner_bot_id, None);

    let chat_support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let chat_repo = Arc::new(RecordingMessageRepo::default());
    let chat_flow = BcsMessageFlow::new(
        chat_support.group.clone(),
        chat_support.routing.clone(),
        chat_support.registry.clone(),
        chat_support.bot_delivery.clone(),
        chat_support.frontend_delivery.clone(),
    )
    .with_message_repo(chat_repo.clone());

    chat_flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-chat-tool".to_string(),
            group_id: "group-1".to_string(),
            event_type: "agent".to_string(),
            event_payload: agent_tool_result_payload(
                Some("exec"),
                "tool-chat-1",
                "chat tool output",
                false,
            ),
            state: ChatEventState::Delta,
            bcs_session_id: Some("group-1:abcdef12".to_string()),
        })
        .await
        .unwrap();

    let chat_appended = chat_repo.appended().await;
    assert_eq!(chat_appended.len(), 1);
    assert_eq!(chat_appended[0].message_type, "tool_call");
    assert_eq!(chat_appended[0].owner_bot_id, None);
}

/// A run that streams chat deltas and then terminates with `error` (never a
/// `final`) must still flush its open chat segment to history. Regression: the
/// terminal error/abort path skipped the flush (only `final` flushed), so the
/// buffered partial reply was silently dropped.
#[tokio::test]
async fn bot_error_terminal_flushes_buffered_chat_segment() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    // Two streaming chat deltas accumulate in the run's open segment. These only
    // buffer in memory — nothing is persisted yet.
    for delta in ["部分回复", "，还没说完"] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-err".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({ "state": "delta", "delta_text": delta }),
            state: ChatEventState::Delta,
            bcs_session_id: None,
        })
        .await
        .unwrap();
    }
    assert!(
        repo.appended().await.is_empty(),
        "streaming deltas must only buffer, not persist"
    );

    // The run dies with an error before any `final` frame.
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-err".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({ "state": "error", "errorMessage": "engine crashed" }),
        state: ChatEventState::Error,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        1,
        "error terminal must flush the buffered chat segment exactly once"
    );
    assert_eq!(appended[0].message_type, "chat");
    assert_eq!(appended[0].run_id, "run-err");
    assert_eq!(
        appended[0].content,
        json!("部分回复，还没说完"),
        "the flushed content must be the accumulated segment text"
    );
}

/// Regression: a TASK run (its run_id resolves to a dispatched task) that streams
/// buffered chat deltas and then terminates with `error` must still flush the
/// buffered segment to history. The terminal flush/cleanup runs BEFORE the task
/// early-return; an earlier ordering placed it after, so task runs took the task
/// branch and returned without flushing, losing the partial reply and leaking the
/// per-run buffer.
#[tokio::test]
async fn task_run_error_terminal_flushes_buffered_chat_segment() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    // Dispatch a task so the run_id below resolves to a task run.
    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    // Stream buffered chat deltas on the task run — memory only, not persisted.
    for delta in ["任务", "进行中"] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({ "state": "delta", "delta_text": delta }),
            state: ChatEventState::Delta,
            bcs_session_id: None,
        })
        .await
        .unwrap();
    }
    assert!(
        repo.appended().await.is_empty(),
        "streaming deltas must only buffer, not persist"
    );

    // The task run dies with an error before any `final` frame.
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: json!({ "state": "error", "errorMessage": "engine crashed" }),
        state: ChatEventState::Error,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        1,
        "task-run error terminal must still flush the buffered chat segment"
    );
    assert_eq!(appended[0].message_type, "chat");
    assert_eq!(appended[0].run_id, dispatch.task_id);
    assert_eq!(
        appended[0].content,
        json!("任务进行中"),
        "the flushed content must be the accumulated segment text"
    );
}

/// Regression: a TASK run that streams buffered chat deltas and then ends with a
/// normal `final` must flush the accumulated segment to history AND still relay
/// the task result to the driver. The terminal flush runs before the task early
/// return; without it, a successful task run's transcript is lost from history
/// and the per-run buffer leaks (the same early-return class as the error case).
#[tokio::test]
async fn task_run_final_terminal_flushes_buffered_chat_segment() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    // Stream buffered chat deltas on the task run — memory only, not persisted.
    for delta in ["任务", "已完成"] {
        flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({ "state": "delta", "delta_text": delta }),
            state: ChatEventState::Delta,
            bcs_session_id: None,
        })
        .await
        .unwrap();
    }
    assert!(
        repo.appended().await.is_empty(),
        "streaming deltas must only buffer, not persist"
    );

    // The task run ends with a normal Final.
    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: dispatch.task_id.clone(),
            group_id: "group-1".to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "任务已完成"}],
                },
            }),
            state: ChatEventState::Final,
            bcs_session_id: None,
        })
        .await
        .unwrap();

    // History flush happened (delta mode → accumulated buffer text).
    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        1,
        "task-run final must flush the buffered chat segment to history"
    );
    assert_eq!(appended[0].message_type, "chat");
    assert_eq!(appended[0].run_id, dispatch.task_id);
    assert_eq!(appended[0].content, json!("任务已完成"));

    // The task result is still relayed to the driver and the task marked replied.
    assert_eq!(outcome.bot_deliveries.len(), 1);
    assert_eq!(
        flow.task_store.get(&dispatch.task_id).await.unwrap().status,
        TaskLedgerStatus::Replied
    );
}

/// Regression: a DUPLICATE task final (after the task is already Replied) must
/// NOT append a second history row. The terminal chat flush is gated by the same
/// status==Dispatched check as task-result delivery, so the second final is
/// rejected before it can flush. (An earlier version flushed before that gate,
/// producing a duplicate history row on retried finals.)
#[tokio::test]
async fn duplicate_task_final_does_not_append_second_history_row() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    let final_cmd = |text: &str| BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": text}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: None,
    };

    flow.handle_bot_event(final_cmd("task done")).await.unwrap();
    // Duplicate/retried final after the task is Replied.
    flow.handle_bot_event(final_cmd("duplicate")).await.unwrap();

    let appended = repo.appended().await;
    assert_eq!(
        appended.len(),
        1,
        "duplicate task final after Replied must not append a second history row"
    );
    assert_eq!(appended[0].content, json!("task done"));
}

/// Regression: a task terminal from a NON-TARGET bot (using the task run_id) must
/// NOT append history. The `target_bot == cmd.bot_id` gate rejects it before the
/// flush, so a misrouted event cannot pollute group history.
#[tokio::test]
async fn task_final_from_non_target_bot_does_not_append_history() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    let dispatch = flow
        .handle_task_dispatch(TaskDispatchCommand {
            driver_bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            target_bot_id: "bot-observer".to_string(),
            target_bot_name: None,
            payload: json!({"message": "do work"}),
        })
        .await
        .unwrap();

    // A different bot emits a terminal event carrying the task's run_id.
    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-intruder".to_string(),
        run_id: dispatch.task_id.clone(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": "intruder"}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: None,
    })
    .await
    .unwrap();

    assert!(
        repo.appended().await.is_empty(),
        "a task terminal from a non-target bot must not append history"
    );
}
