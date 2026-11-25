//! Shared trusted publisher contract, usable against real SQLite/MySQL stores.
use std::num::NonZeroU64;
use bcs_service_api::core::agent_invoke::AgentInvokePolicyCoreService;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeFactRepoPort;
use bcs_service_api::types::agent_invoke::*;

pub async fn agent_invoke_fact_repo_port_contract_tests(
    repo: &dyn AgentInvokeFactRepoPort, scope: AgentInvokeScope, valid_until_ms: u64,
) {
    let one = NonZeroU64::new(1).unwrap();
    let p = AgentInvokeFactPublication { state: AgentInvokeBotState { bot_id: "contract-fact-agent".into(),
        binding_generation: one, eligibility_generation: one, eligible: true, active: true, hidden: false },
        expected_revision: 0, valid_until_ms };
    assert_eq!(repo.read_fact(&scope, &p.state.bot_id).await.unwrap(), None);
    let published = repo.publish_fact(&scope, p.clone()).await.unwrap();
    assert_eq!(repo.read_fact(&scope, &p.state.bot_id).await.unwrap(), Some(published.clone()));
    assert_eq!(repo.publish_fact(&scope, p.clone()).await, Err(AgentInvokeError::Conflict));
    let mut changed = p; changed.expected_revision = published.revision.get(); changed.state.eligible = false;
    assert_eq!(repo.publish_fact(&scope, changed.clone()).await, Err(AgentInvokeError::Conflict));
    changed.state.eligibility_generation = NonZeroU64::new(2).unwrap();
    let lost = repo.publish_fact(&scope, changed.clone()).await.unwrap();
    assert!(!lost.state.eligible); assert_eq!(lost.revision.get(), 2);
    changed.expected_revision = 2; changed.state.eligible = true;
    assert_eq!(repo.publish_fact(&scope, changed.clone()).await, Err(AgentInvokeError::Conflict));
    changed.state.eligibility_generation = NonZeroU64::new(3).unwrap();
    let restored = repo.publish_fact(&scope, changed).await.unwrap();
    assert!(restored.state.eligible); assert_eq!(restored.state.eligibility_generation.get(), 3);
    let mut wrong = scope; wrong.namespace.push_str("-other");
    assert_eq!(repo.read_fact(&wrong, "contract-fact-agent").await, Err(AgentInvokeError::NotFound));
}

pub fn agent_invoke_policy_core_service_contract_tests(policy: &dyn AgentInvokePolicyCoreService) {
    let scope = AgentInvokeScope { namespace: "contract".into(), env: "dev".into() };
    let mut input = AgentInvocationInput { principal: AgentInvokePrincipal::Agent("bot-a".into()), authenticated_scope: scope.clone(),
        pair: AgentInvokePair { from_bot_id: "bot-a".into(), to_bot_id: "bot-b".into() }, originator: "bot-a".into(), operation: AgentInvokeOperation::DirectInvocation };
    assert_eq!(policy.validate_invocation(&scope, &input), Ok(()));
    input.operation = AgentInvokeOperation::ToolInvocation;
    assert_eq!(policy.validate_invocation(&scope, &input), Err(AgentInvokeError::Forbidden));
    input.operation = AgentInvokeOperation::DirectInvocation; input.originator = "bot-c".into();
    assert_eq!(policy.validate_invocation(&scope, &input), Err(AgentInvokeError::Forbidden));
    input.originator = "bot-a".into(); input.principal = AgentInvokePrincipal::Human("human_owner".into());
    assert_eq!(policy.validate_invocation(&scope, &input), Err(AgentInvokeError::Forbidden));
}

pub async fn agent_invoke_governance_repo_port_contract_tests(
    repo: &dyn bcs_service_api::port::repo::agent_invoke::AgentInvokeGovernanceRepoPort,
    scope: AgentInvokeScope,
) {
    let mut p = AgentInvokeDelegationPublication {
        principal: AgentInvokePrincipal::Governance("contract-governor".into()),
        pair: AgentInvokePair {from_bot_id:"bot-a".into(),to_bot_id:"bot-b".into()},
        expected_revision:0,actions:vec![AgentInvokeAction::Request],reason:"trusted governance contract".into(),
    };
    assert_eq!(repo.publish_delegation(&scope,p.clone()).await.unwrap().get(),1);
    assert_eq!(repo.publish_delegation(&scope,p.clone()).await,Err(AgentInvokeError::Conflict));
    p.expected_revision=1;p.actions.clear();assert_eq!(repo.publish_delegation(&scope,p.clone()).await.unwrap().get(),2);
    p.expected_revision=2;p.actions=vec![AgentInvokeAction::Request];assert_eq!(repo.publish_delegation(&scope,p.clone()).await.unwrap().get(),3);
    p.expected_revision=3;p.actions.push(AgentInvokeAction::Request);
    assert_eq!(repo.publish_delegation(&scope,p.clone()).await,Err(AgentInvokeError::InvalidInput));
    p.actions.pop();p.principal=AgentInvokePrincipal::Agent("ordinary-agent".into());
    assert_eq!(repo.publish_delegation(&scope,p.clone()).await,Err(AgentInvokeError::InvalidInput));
    let mut other=scope;other.namespace.push_str("-other");
    assert_eq!(repo.publish_delegation(&other,p).await,Err(AgentInvokeError::NotFound));
}
