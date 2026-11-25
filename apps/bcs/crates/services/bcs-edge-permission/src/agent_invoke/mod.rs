//! Agent invocation use-case orchestration. Deliberately not mounted into any
//! production router or legacy admission path until fenced store/providers and
//! reader capabilities are available. Constructor injection is mandatory.
mod interceptor;
pub use interceptor::AgentInvokeInterceptor;
mod policy;
mod query;
pub use query::{AgentInvokeQueryApplicationService, AgentInvokeQueryPolicy};
pub use policy::AgentInvokePolicy;

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::agent_invoke::{
    AgentInvocationService, AgentInvokeGrantManagementService,
};
use bcs_service_api::core::agent_invoke::AgentInvokePolicyCoreService;
use bcs_service_api::port::agent_invoke::AgentInvokeFactsPort;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeCommandRepoPort;
use bcs_service_api::types::agent_invoke::*;

pub struct AgentInvokeApplicationService {
    scope: AgentInvokeScope,
    policy: Arc<dyn AgentInvokePolicyCoreService>,
    facts: Arc<dyn AgentInvokeFactsPort>,
    commands: Arc<dyn AgentInvokeCommandRepoPort>,
}

impl AgentInvokeApplicationService {
    pub fn new(
        scope: AgentInvokeScope,
        policy: Arc<dyn AgentInvokePolicyCoreService>,
        facts: Arc<dyn AgentInvokeFactsPort>,
        commands: Arc<dyn AgentInvokeCommandRepoPort>,
    ) -> AgentInvokeResult<Self> {
        policy::validate_scope(&scope)?;
        Ok(Self {
            scope,
            policy,
            facts,
            commands,
        })
    }
}

#[async_trait]
impl AgentInvocationService for AgentInvokeApplicationService {
    async fn authorize_invocation(
        &self,
        input: AgentInvocationInput,
    ) -> AgentInvokeResult<AgentInvocationPermit> {
        // No fact/store call at all for forged caller, domain or delegation.
        self.policy.validate_invocation(&self.scope, &input)?;
        let snapshot = self
            .facts
            .runtime_snapshot(&self.scope, &input.pair)
            .await?;
        self.policy
            .authorize_invocation(&self.scope, &input, &snapshot)
    }
}

#[async_trait]
impl AgentInvokeGrantManagementService for AgentInvokeApplicationService {
    async fn change(&self, command: AgentInvokeCommand) -> AgentInvokeResult<AgentInvokeReceipt> {
        self.policy.validate_command(&self.scope, &command)?;
        // External facts are resolved BEFORE the atomic store command. The
        // store must revalidate the fence; this precheck is not a commit lock.
        let snapshot = self
            .facts
            .management_snapshot(&self.scope, &command)
            .await?;
        self.policy
            .authorize_change(&self.scope, &command, &snapshot)?;
        self.commands.execute(&self.scope, command, snapshot).await
    }
}
