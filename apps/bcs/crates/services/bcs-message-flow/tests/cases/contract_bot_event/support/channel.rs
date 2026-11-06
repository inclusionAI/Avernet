use super::super::{
    BotTerminalEvent,
    BotTerminalObserverPort,
    ChannelInboundError,
    ChannelService,
    ChannelUseCaseError,
    Mutex,
    Participant,
    SystemMessageEvent,
    SystemMessageService,
    Value
};

#[derive(Default)]
pub(in super::super) struct RecordingSystemMessage {
    pub(in super::super) notifications: Mutex<Vec<RecordingSystemNotification>>,
}

pub(in super::super) struct RecordingSystemNotification {
    pub(in super::super) group_id: String,
    pub(in super::super) event: SystemMessageEvent,
    pub(in super::super) session_id: String,
    pub(in super::super) participants: Vec<Participant>,
}

#[derive(Default)]
pub(in super::super) struct RecordingBotTerminalObserver {
    pub(in super::super) events: Mutex<Vec<BotTerminalEvent>>,
}

#[async_trait::async_trait]
impl BotTerminalObserverPort for RecordingBotTerminalObserver {
    async fn observe(&self, event: BotTerminalEvent) {
        self.events.lock().await.push(event);
    }
}

#[derive(Default)]
pub(in super::super) struct RecordingChannelService {
    outbound: Mutex<Vec<bcs_service_api::application::channel::OutboundMessage>>,
}

impl RecordingChannelService {
    pub(in super::super) async fn outbound(&self) -> Vec<bcs_service_api::application::channel::OutboundMessage> {
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

    async fn list_bindings_by_target(
        &self,
        _target: bcs_domain::BindingTarget,
        _channel_type: Option<bcs_domain::ChannelType>,
    ) -> Result<Vec<bcs_domain::ChannelBinding>, ChannelUseCaseError> {
        Ok(Vec::new())
    }

    async fn list_conversations_by_session(
        &self,
        _bcs_session_id: &str,
        _channel_type: Option<bcs_domain::ChannelType>,
    ) -> Result<Vec<bcs_domain::ConversationSessionMap>, ChannelUseCaseError> {
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
