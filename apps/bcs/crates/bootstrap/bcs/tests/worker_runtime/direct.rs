//! Production Direct directory and role wiring with an explicit test authorization stub.
#[path = "../../../../test-support/message_flow_contract/registry_routing_fakes.rs"]
mod registry;

use super::assembly::Host;
use bcs_app_dist_gateway::DistGatewayConfig;
use bcs_dist_connector::ConnectorConfig;
use bcs_dist_gateway_http::GatewayHttpConfig;
use bcs_dist_model::*;
use bcs_dist_worker::WorkerRuntime;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{
    BotCapabilities, BotDeliveryTarget, BotRegistryCoreService, RedactedToken,
    application::{
        dist_gateway::GatewayCaller,
        principal::{BotActor, CallerContext},
    },
    port::{
        dist_agent::{DirectoryError, DistSessionAuthorizerPort},
        dist_runtime::DistRuntimeControlPort,
    },
    types::a2a_bot::{A2aBinding, A2aInterface, A2aUpdateMode},
};
use registry::FakeRegistryService;
use std::{sync::Arc, time::Duration};

/// A fixed fixture decision, not a production authorization policy.
struct StoryAuthorizer {
    agent: AgentId,
}

#[async_trait::async_trait]
impl DistSessionAuthorizerPort for StoryAuthorizer {
    async fn authorize(&self, _: &TaskScope, target: &SessionTarget) -> Result<(), DirectoryError> {
        if target == &(SessionTarget::DirectAgent { agent: self.agent.clone() }) {
            Ok(())
        } else {
            Err(DirectoryError::AccessDenied)
        }
    }
}

pub async fn listen(host: &Host, agent_url: &str) -> anyhow::Result<String> {
    let registry = Arc::new(FakeRegistryService::default());
    let agent = "analyst";
    registry.register(agent.into(), BotCapabilities {
        name: Some(agent.into()),
        visibility: "public".into(),
        ..Default::default()
    }).await?;
    registry.set_delivery_target(agent, BotDeliveryTarget::A2a {
        bot_id: agent.into(),
        provider_id: "singlebox".into(),
        provider_bot_ref: agent.into(),
        interface: A2aInterface {
            url: agent_url.into(),
            tenant: None,
            protocol_version: "1.0".into(),
            binding: A2aBinding::JsonRpc,
            update_mode: A2aUpdateMode::Stream,
        },
        credential_kind: "anonymous".into(),
        bearer: RedactedToken::new(""),
        card_revision: 1,
        extensions: vec![],
    }).await;

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
    let base = format!("http://{}", listener.local_addr()?);
    let stores = host.direct_stores();
    let pool = PoolId("direct-pool".into());
    let tenant = TenantId("singlebox".into());
    let caller = GatewayCaller {
        tenant: tenant.clone(),
        caller: CallerContext::Bot(BotActor { bot_uuid: "gateway-story".into() }),
    };
    let router = stores.gateway(
        Arc::new(StoryAuthorizer { agent: AgentId(agent.into()) }),
        tenant, pool.clone(),
        SessionLimits {
            max_queued_tasks: 32,
            max_task_steps: 100,
            task_timeout_millis: 60_000,
            max_inline_bytes: 65_536,
        },
        DistGatewayConfig::new(1),
        GatewayHttpConfig::new(&base),
    )?.layer(axum::extract::Extension(caller));

    let instance = WorkerRuntime::fresh_instance("singlebox-connector".into());
    let mut config = ConnectorConfig::new(instance, pool);
    config.control_address = base.clone();
    config.poll_interval = Duration::from_millis(20);
    config.receive_timeout = Duration::from_millis(20);
    let connector = stores.connector(
        config, Arc::new(host.queue.clone()), registry, OutboundUrlGuard::new(true, true), None,
    )?;
    let running = connector.clone();
    let mut task = tokio::spawn(async move { running.run().await });
    let ready = async {
        while !connector.snapshot().await?.ready {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        Ok::<_, anyhow::Error>(())
    };
    tokio::select! {
        result = &mut task => {
            result??;
            anyhow::bail!("singlebox Connector exited during startup");
        },
        result = tokio::time::timeout(Duration::from_secs(10), ready) => result??,
    }
    tokio::spawn(async move {
        if let Err(error) = axum::serve(listener, router).await {
            tracing::error!(%error, "singlebox Direct Gateway listener failed");
        }
    });
    Ok(format!("{base}/a2a/agents/{agent}"))
}
