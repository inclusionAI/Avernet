use super::super::{
    BcsFrame,
    BotDeliveryCommand,
    BotDeliveryPort,
    BotDeliveryResult,
    BotDeliveryTarget,
    BotRunContext,
    BotRunContextPort,
    ChatEventState,
    ChatSendParams,
    CollaborationRuntime,
    CollaborationRuntimeService,
    FrontendDeliveryCommand,
    FrontendDeliveryPort,
    FrontendDeliveryResult,
    HashMap,
    Mutex,
    ProviderRunTransport,
    ServiceError,
    ServiceResult,
    StateMachineResultPublishCommand,
    StateMachineResultPublisherPort,
    async_trait,
    json
};

#[derive(Default)]
pub(in super::super) struct RecordingDelivery {
    pub(in super::super) commands: Mutex<Vec<BotDeliveryCommand>>,
}

#[derive(Default)]
pub(in super::super) struct RecordingBotRunContext {
    contexts: Mutex<HashMap<String, BotRunContext>>,
}

#[async_trait]
impl BotRunContextPort for RecordingBotRunContext {
    async fn put_context(&self, context: BotRunContext) {
        self.contexts
            .lock()
            .await
            .insert(context.run_id.clone(), context);
    }

    async fn get_context(&self, run_id: &str) -> Option<BotRunContext> {
        self.contexts.lock().await.get(run_id).cloned()
    }

    async fn try_begin_terminal(&self, _run_id: &str) -> bool {
        true
    }

    async fn mark_terminal(&self, run_id: &str) -> bool {
        let mut contexts = self.contexts.lock().await;
        let Some(context) = contexts.get_mut(run_id) else {
            return false;
        };
        context.terminal = true;
        true
    }

    async fn release_terminal(&self, _run_id: &str) {}

    async fn begin_provider_transport(&self, _run_id: &str, _deadline_ms: u64) -> bool {
        false
    }

    async fn bind_provider_transport(
        &self,
        _run_id: &str,
        _transport: ProviderRunTransport,
    ) -> bool {
        false
    }

    async fn get_provider_transport(&self, _run_id: &str) -> Option<ProviderRunTransport> {
        None
    }

    async fn mark_provider_transport_terminal(&self, _run_id: &str) {}

    async fn clear_provider_transport(&self, _run_id: &str) {}
}

#[async_trait]
impl BotDeliveryPort for RecordingDelivery {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        self.commands.lock().await.push(cmd.clone());
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            delivered: true,
            error: None,
        })
    }
}

#[derive(Default)]
pub(in super::super) struct RejectingDelivery {
    pub(in super::super) commands: Mutex<Vec<BotDeliveryCommand>>,
}

#[async_trait]
impl BotDeliveryPort for RejectingDelivery {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        self.commands.lock().await.push(cmd.clone());
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            delivered: false,
            error: Some(ServiceError::BotNotConnected(
                cmd.target_bot_id().to_string(),
            )),
        })
    }
}

#[derive(Default)]
pub(in super::super) struct RecordingFrontendDelivery {
    pub(in super::super) commands: Mutex<Vec<FrontendDeliveryCommand>>,
}

#[async_trait]
impl FrontendDeliveryPort for RecordingFrontendDelivery {
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult> {
        let target = cmd.target.clone();
        self.commands.lock().await.push(cmd);
        Ok(FrontendDeliveryResult {
            target,
            delivered: 1,
        })
    }

    async fn unregister_run(&self, _run_id: &str) -> ServiceResult<()> {
        Ok(())
    }
}

#[derive(Default)]
pub(in super::super) struct RecordingResultPublisher {
    pub(in super::super) commands: Mutex<Vec<StateMachineResultPublishCommand>>,
}

#[async_trait]
impl StateMachineResultPublisherPort for RecordingResultPublisher {
    async fn publish_state_machine_result(
        &self,
        cmd: StateMachineResultPublishCommand,
    ) -> ServiceResult<()> {
        self.commands.lock().await.push(cmd);
        Ok(())
    }
}

pub(in super::super) struct FailingResultPublisher;

#[async_trait]
impl StateMachineResultPublisherPort for FailingResultPublisher {
    async fn publish_state_machine_result(
        &self,
        _cmd: StateMachineResultPublishCommand,
    ) -> ServiceResult<()> {
        Err(ServiceError::InternalError(
            "simulated result persistence failure".to_string(),
        ))
    }
}

pub(in super::super) fn chat_send_params(command: &BotDeliveryCommand) -> ChatSendParams {
    match &command.frame {
        BcsFrame::Request(request) => {
            assert_eq!(request.method, "chat.send");
            serde_json::from_value(request.params.clone().expect("chat.send params"))
                .expect("chat.send params decode")
        }
        _ => panic!("expected chat.send request frame"),
    }
}

pub(in super::super) async fn complete_with_text(
    runtime: &CollaborationRuntime,
    delivery_run_id: &str,
    session_id: &str,
    text: &str,
) -> bcs_service_api::HandleBotTerminalEventOutcome {
    runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: delivery_run_id.to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": delivery_run_id,
                "bcs_group_id": "group-1",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}]
                }
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some(session_id.to_string()),
        })
        .await
        .expect("handle final")
}
