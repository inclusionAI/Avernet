use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort,
    FrontendDeliveryResult, FrontendDeliveryTarget, InteractionFrontendEvent,
    InteractionFrontendPort, RunFallbackDelivery, ServiceError, ServiceResult,
};

use crate::shared::RunChannelManager;
use crate::web::WorkbenchConnectionRegistry;

#[derive(Debug)]
pub struct WorkbenchFrontendDelivery {
    connections: Arc<WorkbenchConnectionRegistry>,
    run_channels: Arc<RunChannelManager>,
}

impl WorkbenchFrontendDelivery {
    pub fn new(
        connections: Arc<WorkbenchConnectionRegistry>,
        run_channels: Arc<RunChannelManager>,
    ) -> Self {
        Self {
            connections,
            run_channels,
        }
    }
}

/// Serializes typed interaction events into the Workbench WebSocket envelope.
pub struct WorkbenchInteractionDelivery {
    frontend: Arc<dyn FrontendDeliveryPort>,
}

impl WorkbenchInteractionDelivery {
    pub fn new(frontend: Arc<dyn FrontendDeliveryPort>) -> Self {
        Self { frontend }
    }
}

pub(crate) fn interaction_event_json(event: &InteractionFrontendEvent) -> ServiceResult<String> {
    serde_json::to_string(&serde_json::json!({
        "type": "event",
        "event": "interaction",
        "group_id": event.group_id,
        "bot_uuid": event.bot_id,
        "bcsRunId": event.bcs_run_id,
        "bcsSessionId": event.bcs_session_id,
        "payload": event.payload,
    }))
    .map_err(|error| ServiceError::InternalError(format!(
        "serialize interaction Workbench event: {error}"
    )))
}

#[async_trait]
impl InteractionFrontendPort for WorkbenchInteractionDelivery {
    async fn publish_interaction(&self, event: InteractionFrontendEvent) -> ServiceResult<()> {
        self.frontend
            .publish(FrontendDeliveryCommand {
                target: FrontendDeliveryTarget::Session {
                    session_id: event.bcs_session_id.clone(),
                },
                event_json: interaction_event_json(&event)?,
                delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
                run_fallback: None,
                exclude_conn_id: None,
            })
            .await?;
        Ok(())
    }
}

#[async_trait]
impl FrontendDeliveryPort for WorkbenchFrontendDelivery {
    async fn publish(
        &self,
        cmd: FrontendDeliveryCommand,
    ) -> ServiceResult<FrontendDeliveryResult> {
        let delivered = match &cmd.target {
            FrontendDeliveryTarget::Group { group_id } => {
                self.publish_group_or_fallback(group_id, &cmd).await
            }
            FrontendDeliveryTarget::Session { session_id } => {
                self.publish_group_or_fallback(session_id, &cmd).await
            }
            FrontendDeliveryTarget::Run { run_id } => {
                let sent = match cmd.delivery_kind {
                    FrontendDeliveryKind::RunEvent | FrontendDeliveryKind::WorkbenchEvent => {
                        self.run_channels.send_event(run_id, cmd.event_json.clone()).await
                    }
                };
                usize::from(sent)
            }
        };

        Ok(FrontendDeliveryResult {
            target: cmd.target,
            delivered,
        })
    }

    async fn unregister_run(&self, run_id: &str) -> ServiceResult<()> {
        self.run_channels.unregister(run_id).await;
        Ok(())
    }
}

impl WorkbenchFrontendDelivery {
    async fn publish_group_or_fallback(
        &self,
        session_id: &str,
        cmd: &FrontendDeliveryCommand,
    ) -> usize {
        let bound = self.connections.connection_count(session_id).await;
        let delivered = self
            .connections
            .broadcast_excluding(session_id, &cmd.event_json, cmd.exclude_conn_id)
            .await;
        if bound == 0 {
            delivered + self.publish_run_fallback(cmd.run_fallback.as_ref()).await
        } else {
            delivered
        }
    }

    async fn publish_run_fallback(&self, fallback: Option<&RunFallbackDelivery>) -> usize {
        let Some(fallback) = fallback else {
            return 0;
        };

        let delivered_by_run = self
            .run_channels
            .send_event(&fallback.run_id, fallback.event_json.clone())
            .await;
        if delivered_by_run {
            return 1;
        }

        let delivered_by_session = self
            .run_channels
            .send_event_by_session(&fallback.session_id, fallback.event_json.clone())
            .await;
        usize::from(delivered_by_session)
    }
}
