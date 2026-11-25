//! Independent governance use case. It never fabricates a mutation to read roles.
use super::policy::{validate_principal, validate_scope};
use async_trait::async_trait;
use bcs_service_api::{
    application::agent_invoke::AgentInvokeGovernanceQueryService,
    core::agent_invoke::AgentInvokeQueryPolicyCoreService,
    port::repo::agent_invoke::AgentInvokeQueryRepoPort,
    types::{agent_invoke::*, agent_invoke_query::*},
};
use std::sync::Arc;

pub struct AgentInvokeQueryPolicy;
impl AgentInvokeQueryPolicyCoreService for AgentInvokeQueryPolicy {
    fn validate_query(
        &self,
        scope: &AgentInvokeScope,
        q: &AgentInvokeQuery,
    ) -> AgentInvokeResult<()> {
        validate_scope(scope)?;
        let c = &q.context;
        validate_principal(&c.principal)?;
        if &c.scope != scope || matches!(c.principal, AgentInvokePrincipal::Agent(_)) {
            return Err(AgentInvokeError::Forbidden);
        }
        let bot_valid = |id: &str| {
            !id.trim().is_empty() && !id.starts_with("human_") && id.chars().count() <= 256
        };
        if !bot_valid(&c.target.bot_id)
            || c.principal.id().chars().count() > 256
            || c.target
                .peer_bot_id
                .as_ref()
                .is_some_and(|id| !bot_valid(id))
        {
            return Err(AgentInvokeError::InvalidInput);
        }
        if c.target.peer_bot_id.as_ref() == Some(&c.target.bot_id)
            && !matches!(c.filter, AgentInvokeQueryFilter::Audit { .. })
        {
            return Err(AgentInvokeError::InvalidInput);
        }
        if (matches!(c.principal, AgentInvokePrincipal::Governance(_))
            || matches!(c.filter, AgentInvokeQueryFilter::Audit { .. }))
            && c.target.peer_bot_id.is_none()
        {
            return Err(AgentInvokeError::Forbidden);
        }
        match &c.filter {
            AgentInvokeQueryFilter::Grant { edge_id, .. } if edge_id.get() > i64::MAX as u64 => {
                return Err(AgentInvokeError::InvalidInput);
            }
            AgentInvokeQueryFilter::Request { request_id, .. }
                if uuid::Uuid::parse_str(request_id).is_err() =>
            {
                return Err(AgentInvokeError::InvalidInput);
            }
            _ => {}
        }
        if c.filter.is_page() != q.page.is_some() {
            return Err(AgentInvokeError::InvalidInput);
        }
        if let Some(page) = &q.page {
            if page.limit.get() > 100 {
                return Err(AgentInvokeError::InvalidInput);
            }
            if let Some(cursor) = &page.cursor {
                if cursor.context != *c {
                    return Err(AgentInvokeError::InvalidInput);
                }
                let valid = match (&cursor.position, &c.filter) {
                    (
                        AgentInvokeCursorPosition::RowId(id),
                        AgentInvokeQueryFilter::Grants { .. }
                        | AgentInvokeQueryFilter::Requests { .. },
                    ) => id.get() <= i64::MAX as u64,
                    (
                        AgentInvokeCursorPosition::AuditTime { created_at_ms, id },
                        AgentInvokeQueryFilter::Audit { .. },
                    ) => *created_at_ms <= i64::MAX as u64 && id.get() <= i64::MAX as u64,
                    _ => false,
                };
                if !valid {
                    return Err(AgentInvokeError::InvalidInput);
                }
            }
        }
        Ok(())
    }
    fn authorize_query(
        &self,
        scope: &AgentInvokeScope,
        q: &AgentInvokeQuery,
        f: &AgentInvokeQueryFacts,
    ) -> AgentInvokeResult<()> {
        self.validate_query(scope, q)?;
        if f.context != q.context {
            return Err(AgentInvokeError::InvalidState);
        }
        match &q.context.principal {
            AgentInvokePrincipal::Human(_) => {
                if !f.operator_live || f.anchor_role.is_none() {
                    return Err(AgentInvokeError::Forbidden);
                }
                if !f.anchor_live {
                    return Err(AgentInvokeError::NotFound);
                }
                if f.ownership_version == 0 || f.owner_count != 1 || f.canonical_owner_count != 1 {
                    return Err(AgentInvokeError::InvalidState);
                }
            }
            AgentInvokePrincipal::Governance(_) => {
                if !f.delegated_actions.contains(&AgentInvokeAction::Query)
                    || (matches!(q.context.filter, AgentInvokeQueryFilter::Degrees)
                        && !f
                            .reverse_delegated_actions
                            .contains(&AgentInvokeAction::Query))
                {
                    return Err(AgentInvokeError::Forbidden);
                }
            }
            AgentInvokePrincipal::Agent(_) => return Err(AgentInvokeError::Forbidden),
        }
        Ok(())
    }
}

pub struct AgentInvokeQueryApplicationService {
    scope: AgentInvokeScope,
    policy: Arc<dyn AgentInvokeQueryPolicyCoreService>,
    repo: Arc<dyn AgentInvokeQueryRepoPort>,
}
impl AgentInvokeQueryApplicationService {
    pub fn new(
        scope: AgentInvokeScope,
        policy: Arc<dyn AgentInvokeQueryPolicyCoreService>,
        repo: Arc<dyn AgentInvokeQueryRepoPort>,
    ) -> AgentInvokeResult<Self> {
        validate_scope(&scope)?;
        Ok(Self {
            scope,
            policy,
            repo,
        })
    }
}
#[async_trait]
impl AgentInvokeGovernanceQueryService for AgentInvokeQueryApplicationService {
    async fn read(&self, q: AgentInvokeQuery) -> AgentInvokeResult<AgentInvokeQueryResult> {
        self.policy.validate_query(&self.scope, &q)?;
        let expected = q.context.clone();
        let result = self.repo.read_query(&self.scope, q).await?;
        if result.context != expected {
            return Err(AgentInvokeError::InvalidState);
        }
        let matches = matches!(
            (&expected.filter, &result.data),
            (
                AgentInvokeQueryFilter::Grant { .. },
                AgentInvokeQueryData::Grant(_)
            ) | (
                AgentInvokeQueryFilter::Request { .. },
                AgentInvokeQueryData::Request(_)
            ) | (
                AgentInvokeQueryFilter::Grants { .. },
                AgentInvokeQueryData::Grants(_)
            ) | (
                AgentInvokeQueryFilter::Requests { .. },
                AgentInvokeQueryData::Requests(_)
            ) | (
                AgentInvokeQueryFilter::Degrees,
                AgentInvokeQueryData::Degrees(_)
            ) | (
                AgentInvokeQueryFilter::Audit { .. },
                AgentInvokeQueryData::Audit(_)
            )
        );
        if !matches {
            return Err(AgentInvokeError::InvalidState);
        }
        Ok(result)
    }
}
