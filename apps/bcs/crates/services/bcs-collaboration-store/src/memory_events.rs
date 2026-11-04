use super::*;
#[async_trait]
impl CollaborationEventRepoPort for MemoryCollaborationStore {
    async fn append_event(
        &self,
        state_machine_run_id: &str,
        node_id: Option<&str>,
        attempt: Option<i32>,
        event_type: &str,
        payload: serde_json::Value,
        created_at: u64,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        inner.events.push(CollaborationEventRecord {
            state_machine_run_id: state_machine_run_id.to_string(),
            node_id: node_id.map(str::to_string),
            attempt,
            event_type: event_type.to_string(),
            payload,
            created_at,
        });
        Ok(())
    }

    async fn list_events_by_run_and_type(
        &self,
        state_machine_run_id: &str,
        event_type: &str,
    ) -> ServiceResult<Vec<CollaborationEventRecord>> {
        let inner = self.inner.read().await;
        Ok(inner
            .events
            .iter()
            .filter(|event| {
                event.state_machine_run_id == state_machine_run_id && event.event_type == event_type
            })
            .cloned()
            .collect())
    }

    async fn list_events_by_run_node_and_type(
        &self,
        state_machine_run_id: &str,
        node_id: &str,
        event_type: &str,
    ) -> ServiceResult<Vec<CollaborationEventRecord>> {
        let inner = self.inner.read().await;
        Ok(inner
            .events
            .iter()
            .filter(|event| {
                event.state_machine_run_id == state_machine_run_id
                    && event.node_id.as_deref() == Some(node_id)
                    && event.event_type == event_type
            })
            .cloned()
            .collect())
    }
}

pub(super) fn state_machine_transition_events(
    transition: &StateMachineEventfulTransition,
) -> Vec<bcs_service_api::port::repo::AppendEventRecord> {
    match transition {
        StateMachineEventfulTransition::AcceptHistory(command) => command.event.iter().cloned().collect(),
        StateMachineEventfulTransition::FinishJudge(command) => command.event.iter().cloned().collect(),
        StateMachineEventfulTransition::StartRun { events, .. } => events.clone(),
        StateMachineEventfulTransition::StartBotNode { event, .. }
        | StateMachineEventfulTransition::StartHumanNode { event, .. }
        | StateMachineEventfulTransition::CompleteNode { event, .. }
        | StateMachineEventfulTransition::ScheduleNodeRetry { event, .. }
        | StateMachineEventfulTransition::CompleteRun { event, .. } => vec![event.clone()],
    }
}
