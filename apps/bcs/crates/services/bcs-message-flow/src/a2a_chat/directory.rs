//! Direct A2A target validation and immutable configuration for the Gateway.
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::dist_agent::{
    DirectoryError, DistSessionAuthorizerPort, DistSessionDirectoryPort, FrozenSessionProposal,
};
use std::sync::Arc;

pub struct DirectSessionDirectory {
    authorizer: Arc<dyn DistSessionAuthorizerPort>,
    tenant: TenantId,
    pool: PoolId,
    limits: SessionLimits,
}

impl DirectSessionDirectory {
    pub fn new(
        authorizer: Arc<dyn DistSessionAuthorizerPort>,
        tenant: TenantId,
        pool: PoolId,
        limits: SessionLimits,
    ) -> Self {
        Self { authorizer, tenant, pool, limits }
    }

    fn agent<'a>(&self, scope: &TaskScope, target: &'a SessionTarget) -> Result<&'a AgentId, DirectoryError> {
        match target {
            SessionTarget::DirectAgent { agent }
                if self.tenant == scope.tenant && !agent.0.is_empty() => Ok(agent),
            _ => Err(DirectoryError::NotFound),
        }
    }
}

#[async_trait]
impl DistSessionDirectoryPort for DirectSessionDirectory {
    async fn authorize(&self, scope: &TaskScope, target: &SessionTarget) -> Result<(), DirectoryError> {
        self.agent(scope, target)?;
        self.authorizer.authorize(scope, target).await
    }

    async fn resolve(&self, scope: &TaskScope, target: &SessionTarget) -> Result<FrozenSessionProposal, DirectoryError> {
        let agent = self.agent(scope, target)?;
        let config = serde_json::json!({ "agent": agent });
        Ok(FrozenSessionProposal {
            target: target.clone(),
            mode_envelope: ModeConfigEnvelope {
                mode: CollaborationMode::DirectA2a,
                mode_version: 1,
                revision: "1".into(),
                digest: bcs_dist_encoding::digest_json(&config),
                payload: Payload::Inline(config),
            },
            authorized_agents: vec![AuthorizedAgent { agent: agent.clone(), pool: self.pool.clone() }],
            limits: self.limits.clone(),
        })
    }
}
