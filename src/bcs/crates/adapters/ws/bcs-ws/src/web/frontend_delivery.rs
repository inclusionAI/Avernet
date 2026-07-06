use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort,
    FrontendDeliveryResult, FrontendDeliveryTarget, RunFallbackDelivery, ServiceResult,
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
