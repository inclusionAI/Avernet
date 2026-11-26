//! Explicit memory-only Gateway/Worker assembly for the external process story.
use super::{assembly::Host, workload};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::{
    application::{
        dist_gateway::GatewayCaller,
        principal::{BotActor, CallerContext},
    },
    port::dist_agent::*,
};
use std::sync::Arc;
struct Groups;
#[async_trait]
impl DistGroupDirectoryPort for Groups {
    async fn resolve(
        &self,
        _: &TaskScope,
        group: &GroupId,
    ) -> Result<FrozenGroupDefinition, GroupDirectoryError> {
        let s = workload::specification(Sid("s0001.template".into()));
        Ok(FrozenGroupDefinition {
            group: group.clone(),
            workflow: s.workflow,
            participants: s.participants,
            limits: s.limits,
        })
    }
    async fn load_workflow(
        &self,
        _: &TaskScope,
        _: &WorkflowRef,
    ) -> Result<bcs_domain::collaboration::StateMachineDefinition, GroupDirectoryError> {
        Err(GroupDirectoryError::NotFound)
    }
}
impl Host {
    pub async fn gateway_listen(&self) -> anyhow::Result<String> {
        use bcs_dist_store::inbox::{Clock, SystemClock};
        let admission = bcs_dist_worker::WorkerTaskAdmission::new(
            self.inbox.clone(),
            Arc::new(|| SystemClock.now()),
        );
        let output = bcs_dist_worker::WorkerOutputReader::new(
            self.feed.clone(),
            self.archive.clone(),
            Arc::new(bcs_dist_a2a_output::A2aOutputAdapter),
        );
        let core = bcs_dist_gateway::DistGatewayCore::new(
            Arc::new(admission),
            Arc::new(output),
            self.coordination.clone(),
        );
        let gateway = bcs_app_dist_gateway::DistGateway::new(
            Arc::new(core),
            Arc::new(Groups),
            Arc::new(bcs_app_dist_gateway::SystemClock),
            bcs_app_dist_gateway::DistGatewayConfig::new(2),
        )?;
        let caller = GatewayCaller {
            tenant: workload::scope().tenant,
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "gateway-story".into(),
            }),
        };
        let router = bcs_dist_gateway_http::router(
            Arc::new(gateway),
            bcs_dist_gateway_http::GatewayHttpConfig::new("http://127.0.0.1"),
        )
        .layer(axum::extract::Extension(caller));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
        let address = listener.local_addr()?;
        tokio::spawn(async move {
            if let Err(error) = axum::serve(listener, router).await {
                tracing::error!(%error,"test Gateway listener failed");
            }
        });
        Ok(format!("http://{address}/a2a/gateway-story"))
    }
}
