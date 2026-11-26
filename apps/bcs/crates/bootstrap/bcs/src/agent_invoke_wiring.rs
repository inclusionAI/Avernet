//! Explicit host assembly, NOT activation or an identity/eligibility producer.
//! Default startup supplies None. No config label/header can select this seam.
use std::{collections::BTreeMap, num::NonZeroU64, sync::Arc};
use bcs_api_http::{ApiState, v1::agent_invoke::{AgentInvokeDirectHttpServices, AgentInvokeHttpServices}};
use bcs_db_api::{DbPlugin, DbSqlFlavor};
use bcs_edge_permission::agent_invoke::{AgentInvokeApplicationService, AgentInvokeInterceptor,
    AgentInvokePolicy, AgentInvokeQueryApplicationService, AgentInvokeQueryPolicy};
use bcs_edge_permission_store::agent_invoke::{DbAgentInvokeStore, DbAgentInvokeCommandStore, DbAgentInvokeQueryStore};
use bcs_service_api::{A2aChatRunService, application::agent_invoke::AgentInvokeDirectLimits,
    interceptor::InterceptorChain, types::agent_invoke::{AgentInvokeLimits, AgentInvokeScope}};

/// Deployment-owned assembly parameters. This type does not attest external
/// consumer compatibility or authorize opening durable writer/runtime gates.
/// Hosts must validate readers, SDK/runtime and their durable inventory feed
/// separately before explicit trusted activation through the existing store.
#[derive(Clone)]
pub struct AgentInvokeRegistration {
    pub scope: AgentInvokeScope,
    pub tenant: Option<String>,
    pub governance_apps: BTreeMap<i64, String>,
    pub limits: AgentInvokeLimits,
    pub max_fact_ttl_ms: NonZeroU64,
    pub direct_limits: AgentInvokeDirectLimits,
}
impl AgentInvokeRegistration {
    fn validate(&self, env: &str) -> crate::Result<()> {
        if self.scope.env != env
            || self.tenant.as_ref().is_some_and(|v| v.trim().is_empty() || v.contains('*'))
            || self.governance_apps.iter().any(|(app, id)| *app <= 0 || id.trim().is_empty()
                || id.chars().count() > 256 || id.contains('*'))
            || self.direct_limits.default_timeout_ms > self.direct_limits.max_timeout_ms
            || self.direct_limits.max_timeout_ms.get() > 24 * 60 * 60 * 1_000
        {
            return Err(crate::BcsError::InvalidConfig("invalid AgentInvoke host registration".into()));
        }
        Ok(())
    }
}

pub(crate) struct AgentInvokeWiring {
    registration: AgentInvokeRegistration,
    store: Arc<DbAgentInvokeStore>,
    management: Arc<AgentInvokeApplicationService>,
    queries: Arc<AgentInvokeQueryApplicationService>,
}
fn invalid(error: bcs_service_api::types::agent_invoke::AgentInvokeError) -> crate::BcsError {
    crate::BcsError::StorageInitError(format!("AgentInvoke assembly: {error}"))
}
impl AgentInvokeWiring {
    /// No registration = no SQL and no hooks. An explicit registration checks
    /// exact physical env/domain/limits and initializes only a CLOSED scope;
    /// reopening preserves durable gates. Never publish facts or set_enabled.
    pub(crate) async fn select(
        registration: Option<AgentInvokeRegistration>, db: Arc<dyn DbPlugin>,
        flavor: DbSqlFlavor, env: &str, chain: &mut Arc<InterceptorChain>,
    ) -> crate::Result<Option<Self>> {
        let Some(registration) = registration else { return Ok(None); };
        registration.validate(env)?;
        // Fail before DB effects if a caller tries to replace an already shared
        // chain. Preserve every pre-existing interceptor and its instrumentation.
        let chain = Arc::get_mut(chain).ok_or_else(|| crate::BcsError::InvalidConfig(
            "AgentInvoke must be assembled before the interceptor chain is shared".into()))?;
        let store = Arc::new(DbAgentInvokeStore::new(db, flavor, registration.scope.clone(),
            registration.limits, registration.max_fact_ttl_ms).map_err(invalid)?);
        store.initialize_scope().await.map_err(invalid)?;
        let policy = Arc::new(AgentInvokePolicy);
        let commands = Arc::new(DbAgentInvokeCommandStore::new(store.clone(), policy.clone()));
        let management = Arc::new(AgentInvokeApplicationService::new(registration.scope.clone(),
            policy, store.clone(), commands).map_err(invalid)?);
        let query_policy = Arc::new(AgentInvokeQueryPolicy);
        let query_repo = Arc::new(DbAgentInvokeQueryStore::new(store.clone(), query_policy.clone()));
        let queries = Arc::new(AgentInvokeQueryApplicationService::new(registration.scope.clone(),
            query_policy, query_repo).map_err(invalid)?);
        chain.push(AgentInvokeInterceptor::new(management.clone()));
        Ok(Some(Self { registration, store, management, queries }))
    }

    /// The exact run facade (including metrics wrappers) used by normal Direct
    /// A2A is reused; it has the SAME chain registered above, including queued
    /// pickup and final before_send. HTTP state still exposes only application.
    pub(crate) fn mount(self, state: ApiState, runs: Arc<dyn A2aChatRunService>) -> crate::Result<ApiState> {
        let direct = Arc::new(bcs_message_flow::agent_invoke_ingress::AgentInvokeDirectApplicationService::new(
            self.registration.scope.clone(), self.store, runs, self.registration.direct_limits).map_err(invalid)?);
        Ok(state.with_agent_invoke(AgentInvokeHttpServices {
            scope: self.registration.scope.clone(), tenant: self.registration.tenant.clone(),
            governance_apps: self.registration.governance_apps, management: self.management, queries: self.queries,
        }).with_agent_invoke_direct(AgentInvokeDirectHttpServices {
            scope: self.registration.scope, tenant: self.registration.tenant, service: direct,
        }))
    }
}

#[cfg(test)]
#[path = "../tests/agent_invoke_wiring/mod.rs"]
mod tests;
