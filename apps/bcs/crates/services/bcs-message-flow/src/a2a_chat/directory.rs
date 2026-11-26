//! Tenant-bound Direct A2A admission using the same access rules as local chat.
use super::DirectChatAccess;
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::ServiceError;
use bcs_service_api::port::dist_agent::{
    DirectoryError, DistSessionDirectoryPort, FrozenSessionProposal, SessionAccessContext,
};

pub struct DirectSessionDirectory {
    access: DirectChatAccess,
    tenant: TenantId,
    pool: PoolId,
    limits: SessionLimits,
}

impl DirectSessionDirectory {
    /// The access services must be configured for this tenant's stores.
    pub fn new(
        access: DirectChatAccess,
        tenant: TenantId,
        pool: PoolId,
        limits: SessionLimits,
    ) -> Self {
        Self {
            access,
            tenant,
            pool,
            limits,
        }
    }

    fn agent<'a>(
        &self,
        scope: &TaskScope,
        target: &'a SessionTarget,
    ) -> Result<&'a AgentId, DirectoryError> {
        match target {
            SessionTarget::DirectAgent { agent }
                if self.tenant == scope.tenant && !agent.0.is_empty() =>
            {
                Ok(agent)
            }
            _ => Err(DirectoryError::NotFound),
        }
    }
}

#[async_trait]
impl DistSessionDirectoryPort for DirectSessionDirectory {
    async fn authorize(
        &self,
        scope: &TaskScope,
        target: &SessionTarget,
        access: &SessionAccessContext,
    ) -> Result<(), DirectoryError> {
        let agent = self.agent(scope, target)?;
        let (kind, id) = scope
            .principal
            .0
            .split_once(':')
            .ok_or(DirectoryError::AccessDenied)?;
        if id.is_empty() {
            return Err(DirectoryError::AccessDenied);
        }
        let (source, staff) = match (kind, access.acting_agent.as_ref()) {
            ("bot" | "integration", None) => (id, None),
            ("human", Some(source)) if !source.0.is_empty() => (source.0.as_str(), Some(id)),
            _ => return Err(DirectoryError::AccessDenied),
        };
        self.access
            .authorize(source, &agent.0, staff, access.organization.as_deref())
            .await
            .map_err(|error| match error {
                ServiceError::BotNotFound(_) | ServiceError::BotNotRegistered(_) => {
                    DirectoryError::NotFound
                }
                ServiceError::NotFriends(_)
                | ServiceError::BotHidden(_)
                | ServiceError::Unauthorized(_)
                | ServiceError::Forbidden(_) => DirectoryError::AccessDenied,
                // The organization service reports unknown organizations and
                // malformed member IDs as invalid operations. Only a missing
                // service or a failed lookup should invite a client retry.
                ServiceError::InvalidOperation { .. }
                    if access.organization.is_some() && self.access.organization.is_some() =>
                {
                    DirectoryError::AccessDenied
                }
                _ => DirectoryError::Unavailable,
            })
    }

    async fn resolve(
        &self,
        scope: &TaskScope,
        target: &SessionTarget,
    ) -> Result<FrozenSessionProposal, DirectoryError> {
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
            authorized_agents: vec![AuthorizedAgent {
                agent: agent.clone(),
                pool: self.pool.clone(),
            }],
            limits: self.limits.clone(),
        })
    }
}
