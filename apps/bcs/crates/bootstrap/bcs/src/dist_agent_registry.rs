//! Resolve the current A2A endpoint and active credential via the BCS Bot registry.
use async_trait::async_trait;
use bcs_a2a_http::dist_agent::{DistAgentResolver, ResolvedAgent};
use bcs_dist_model::AgentId;
use bcs_service_api::types::a2a::A2aUpdateMode;
use bcs_service_api::{BotDeliveryTarget, BotRegistryCoreService};
use std::sync::Arc;

pub struct RegisteredAgentResolver {
    registry: Arc<dyn BotRegistryCoreService>,
}

impl RegisteredAgentResolver {
    pub fn new(registry: Arc<dyn BotRegistryCoreService>) -> Self {
        Self { registry }
    }
}

#[async_trait]
impl DistAgentResolver for RegisteredAgentResolver {
    async fn resolve(&self, target: &AgentId) -> Result<ResolvedAgent, String> {
        let route = self.registry
            .resolve_delivery_target(&target.0)
            .await
            .map_err(|_| "agent_route_unavailable".to_owned())?;
        let BotDeliveryTarget::A2a { interface, bearer, extensions, .. } = route else {
            return Err("agent_route_is_not_a2a".to_owned());
        };
        let streaming = interface.update_mode == A2aUpdateMode::Stream;
        let extensions = extensions.into_iter()
            .map(|extension| extension.uri)
            .filter(|uri| {
                uri == bcs_a2a_http::delivery::DEDUP_EXTENSION
                    || (streaming && bcs_a2a_http::tools::is_extension(uri))
            })
            .collect();
        let bearer = bearer.expose_secret();
        Ok(ResolvedAgent {
            interface,
            extensions,
            bearer: (!bearer.is_empty()).then(|| bearer.to_owned()),
        })
    }
}
