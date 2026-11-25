//! Shared lifecycle contract against the real application + synthetic local
//! facts/atomic store driver. No SQL/HTTP/deployment coverage is claimed.
use std::num::NonZeroU32;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_edge_permission::agent_invoke::{AgentInvokeApplicationService, AgentInvokePolicy};
use bcs_edge_permission_store::local_agent_invoke::LocalAgentInvokeStore;
use bcs_service_api::application::agent_invoke::{
    AgentInvocationService, AgentInvokeGrantManagementService,
};
use bcs_service_api::port::agent_invoke::AgentInvokeFactsPort;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeCommandRepoPort;
use bcs_service_api::types::agent_invoke::*;
use bcs_test_support::contract::agent_invoke::*;

struct Driver {
    scope: AgentInvokeScope,
    store: Arc<LocalAgentInvokeStore>,
    service: AgentInvokeApplicationService,
}
impl Driver {
    fn new() -> Self {
        let scope = AgentInvokeScope {
            namespace: "isolated-local".into(),
            env: "synthetic-production".into(),
        };
        let one = NonZeroU32::new(1).unwrap();
        let limits = AgentInvokeLimits {
            max_in_degree: one,
            max_out_degree: one,
            max_pending_in: one,
            max_pending_out: one,
        };
        let policy = Arc::new(AgentInvokePolicy);
        let store =
            Arc::new(LocalAgentInvokeStore::new(scope.clone(), limits, policy.clone()).unwrap());
        let service =
            AgentInvokeApplicationService::new(scope.clone(), policy, store.clone(), store.clone())
                .unwrap();
        Self {
            scope,
            store,
            service,
        }
    }
}
#[async_trait]
impl AgentInvokeContractDriver for Driver {
    fn scope(&self) -> AgentInvokeScope {
        self.scope.clone()
    }
    fn invocation(&self) -> &dyn AgentInvocationService {
        &self.service
    }
    fn management(&self) -> &dyn AgentInvokeGrantManagementService {
        &self.service
    }
    fn facts(&self) -> &dyn AgentInvokeFactsPort {
        self.store.as_ref()
    }
    fn commands(&self) -> &dyn AgentInvokeCommandRepoPort {
        self.store.as_ref()
    }
    async fn register(&self, id: &str) {
        self.store.register_agent(id).await.unwrap();
    }
    async fn role(&self, human: &str, bot: &str, role: Option<AgentInvokeRole>) {
        self.store.set_role(human, bot, role).await.unwrap();
    }
    async fn publish(&self, id: &str, eligible: bool, rebind: bool) {
        self.store
            .publish_agent_state(id, eligible, true, false, rebind)
            .await
            .unwrap();
    }
    async fn audit_count(&self) -> usize {
        self.store.audit().await.len()
    }
}

#[tokio::test]
async fn local_lifecycle() {
    agent_invoke_grant_management_service_contract_tests(&Driver::new()).await;
}
#[tokio::test]
async fn local_replay_and_regrant() {
    run_agent_invoke_replay_contract(&Driver::new()).await;
}
#[tokio::test]
async fn local_invalidation() {
    agent_invocation_service_contract_tests(&Driver::new()).await;
}
#[tokio::test]
async fn local_request_terminals() {
    run_agent_invoke_request_terminals_contract(&Driver::new()).await;
}
#[tokio::test]
async fn local_fencing() {
    agent_invoke_command_repo_port_contract_tests(&Driver::new()).await;
}
#[tokio::test]
async fn local_requester_authority() {
    agent_invoke_facts_port_contract_tests(&Driver::new()).await;
}
#[tokio::test]
async fn local_limits() {
    run_agent_invoke_limits_contract(&Driver::new()).await;
}

#[tokio::test]
async fn local_approve_before_revoke() {
    run_agent_invoke_approve_revoke_contract(&Driver::new(), true).await;
}
#[tokio::test]
async fn local_revoke_before_approve() {
    run_agent_invoke_approve_revoke_contract(&Driver::new(), false).await;
}
#[tokio::test]
async fn local_atomic_limits() {
    run_agent_invoke_atomic_limits_contract(&Driver::new()).await;
}

#[tokio::test]
async fn local_invalidation_audit_retains_before_and_after_generations() {
    use bcs_domain::edge_permission::EdgeStatus;
    use bcs_edge_permission_store::local_agent_invoke::LocalAgentInvokeAuditAction;
    let driver = Driver::new();
    agent_invocation_service_contract_tests(&driver).await;
    let audits = driver.store.audit().await;
    let invalidations: Vec<_> = audits
        .iter()
        .filter(|a| a.action == LocalAgentInvokeAuditAction::QualificationInvalidated)
        .collect();
    assert_eq!(invalidations.len(), 2);
    for audit in invalidations {
        let before = audit.before_grant.as_ref().unwrap();
        let after = audit.after_grant.as_ref().unwrap();
        assert_eq!(before.edge_id, after.edge_id);
        assert_eq!(before.status, EdgeStatus::Approved);
        assert_eq!(after.status, EdgeStatus::Revoked);
        assert_eq!(after.version.get(), before.version.get() + 1);
    }
    let ids: std::collections::HashSet<_> = audits.iter().map(|a| &a.action_id).collect();
    assert_eq!(ids.len(), audits.len());
}

#[tokio::test]
async fn local_governance_delegation_is_exact_principal_pair_and_action() {
    let d = Driver::new();
    for bot in ["bot-a", "bot-b", "bot-c"] {
        d.register(bot).await;
    }
    let pair = AgentInvokePair {
        from_bot_id: "bot-a".into(),
        to_bot_id: "bot-b".into(),
    };
    d.store
        .set_delegation("service-one", pair.clone(), vec![AgentInvokeAction::Grant])
        .await
        .unwrap();
    let c = AgentInvokeCommand {
        principal: AgentInvokePrincipal::Governance("service-one".into()),
        authenticated_scope: d.scope(),
        pair: pair.clone(),
        change: AgentInvokeChange::Grant {
            expected_version: 0,
        },
        idempotency_key: "governance-key".into(),
        reason: "synthetic delegation".into(),
    };
    assert!(d.management().change(c.clone()).await.is_ok());
    let mut wrong_pair = c.clone();
    wrong_pair.pair.to_bot_id = "bot-c".into();
    assert_eq!(
        d.management().change(wrong_pair).await,
        Err(AgentInvokeError::Forbidden)
    );
    let mut wrong_principal = c.clone();
    wrong_principal.principal = AgentInvokePrincipal::Governance("service-two".into());
    assert_eq!(
        d.management().change(wrong_principal).await,
        Err(AgentInvokeError::Forbidden)
    );
    let mut wrong_scope = c.clone();
    wrong_scope.authenticated_scope.namespace = "other-tenant".into();
    assert_eq!(
        d.management().change(wrong_scope).await,
        Err(AgentInvokeError::Forbidden)
    );
    d.store
        .set_delegation("service-one", pair, Vec::new())
        .await
        .unwrap();
    assert_eq!(
        d.management().change(c).await,
        Err(AgentInvokeError::Forbidden),
        "history replay still requires current operator authority"
    );
    assert_eq!(d.audit_count().await, 1);
}

#[test]
fn pure_policy_conformance() {
    bcs_test_support::contract::agent_invoke_facts::agent_invoke_policy_core_service_contract_tests(&AgentInvokePolicy);
}
