use super::super::{
    AppendEventRecord,
    EventRecordError,
    EventRecordFactoryPort,
    HumanInputReadyEvent,
    Mutex,
    NewEvent,
    ServiceError,
    ServiceResult,
    SessionChannelDeliveryOutcome,
    SessionChannelOutboundPort,
    StdMutex,
    async_trait
};

#[derive(Default)]
pub(in super::super) struct RecordingSessionChannelOutbound {
    pub(in super::super) fail_human_publish: std::sync::atomic::AtomicBool,
    pub(in super::super) human_publish_calls: std::sync::atomic::AtomicUsize,
    pub(in super::super) events: Mutex<Vec<HumanInputReadyEvent>>,
    pub(in super::super) terminal_events: Mutex<Vec<bcs_service_api::StateMachineTerminalEvent>>,
    pub(in super::super) validation_calls: Mutex<Vec<(String, String)>>,
    pub(in super::super) validation_error: Mutex<Option<String>>,
}

#[derive(Default)]
pub(in super::super) struct RecordingEventFactory {
    pub(in super::super) events: StdMutex<Vec<NewEvent>>,
}

impl EventRecordFactoryPort for RecordingEventFactory {
    fn prepare(&self, event: NewEvent) -> Result<Option<AppendEventRecord>, EventRecordError> {
        self.events
            .lock()
            .expect("event recording lock")
            .push(event);
        Ok(None)
    }
}

#[async_trait]
impl SessionChannelOutboundPort for RecordingSessionChannelOutbound {
    async fn finish_state_machine_terminal(&self, event: &bcs_service_api::StateMachineTerminalEvent) -> ServiceResult<()> {
        self.terminal_events.lock().await.push(event.clone()); Ok(())
    }
    async fn publish_state_machine_terminal(
        &self,
        event: bcs_service_api::StateMachineTerminalEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        self.terminal_events.lock().await.push(event);
        Ok(SessionChannelDeliveryOutcome::Delivered)
    }

    async fn validate_human_input_channel(
        &self,
        group_id: &str,
        channel_type: &str,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        self.validation_calls
            .lock()
            .await
            .push((group_id.to_string(), channel_type.to_string()));
        if let Some(message) = self.validation_error.lock().await.clone() {
            return Err(ServiceError::InvalidOperation {
                message,
                request_id: None,
            });
        }
        Ok(SessionChannelDeliveryOutcome::NotApplicable)
    }

    async fn publish_human_input_ready(
        &self,
        event: HumanInputReadyEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        self.human_publish_calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        if self.fail_human_publish.swap(false, std::sync::atomic::Ordering::SeqCst) {
            return Err(ServiceError::InternalError("injected human notification failure".into()));
        }
        // The outbound contract replays the same interaction without resending.
        let mut events = self.events.lock().await;
        if !events.iter().any(|saved| saved.event_id == event.event_id) { events.push(event); }
        Ok(SessionChannelDeliveryOutcome::Delivered)
    }
}
