//! Snapshot boundary conformance, independent of a grant-command driver.
//! Synthetic fixture seeding does not prove persistent command semantics.
use async_trait::async_trait;
use bcs_domain::edge_permission::{EdgeStatus, GrantKind};
use bcs_service_api::application::agent_invoke::AgentInvocationService;
use bcs_service_api::port::agent_invoke::AgentInvokeFactsPort;
use bcs_service_api::types::agent_invoke::*;

#[async_trait]
pub trait AgentInvokeSnapshotFixture: Send + Sync {
    fn scope(&self) -> AgentInvokeScope;
    fn facts(&self) -> &dyn AgentInvokeFactsPort;
    fn invocation(&self) -> &dyn AgentInvocationService;
    /// Registered bot-a/b, source/target owners and one canonical approval.
    async fn prepare(&self);
    async fn lose_source_qualification(&self);
}

pub async fn agent_invoke_facts_port_contract_tests(fixture: &dyn AgentInvokeSnapshotFixture) {
    fixture.prepare().await;
    let scope = fixture.scope();
    let pair = AgentInvokePair {
        from_bot_id: "bot-a".into(),
        to_bot_id: "bot-b".into(),
    };
    let input = AgentInvocationInput {
        authentication: AgentInvocationAuthentication::TrustedInProcess,
            principal: AgentInvokePrincipal::Agent("bot-a".into()),
        authenticated_scope: scope.clone(),
        pair: pair.clone(),
        originator: "bot-a".into(),
        operation: AgentInvokeOperation::DirectInvocation,
    };
    let snapshot = fixture
        .facts()
        .runtime_snapshot(&scope, &pair)
        .await
        .unwrap();
    assert_eq!(snapshot.scope, scope);
    assert_eq!(snapshot.pair, pair);
    assert_eq!(snapshot.source.bot_id, "bot-a");
    assert_eq!(snapshot.target.bot_id, "bot-b");
    let approval = snapshot.approval.unwrap();
    assert_eq!(approval.edge.grant_kind, GrantKind::AgentInvoke);
    assert_eq!(approval.edge.status, EdgeStatus::Approved);
    let permit = fixture
        .invocation()
        .authorize_invocation(input.clone())
        .await
        .unwrap();
    assert_eq!(permit.ref_id, approval.edge.edge_id);
    assert_eq!(permit.revision, Some(approval.version.get()));
    let mut c = AgentInvokeCommand {
        principal: AgentInvokePrincipal::Human("human_source".into()),
        authenticated_scope: scope.clone(),
        pair: pair.clone(),
        change: AgentInvokeChange::Request,
        idempotency_key: "snapshot-contract".into(),
        reason: "read-only facts conformance".into(),
    };
    let source = fixture
        .facts()
        .management_snapshot(&scope, &c)
        .await
        .unwrap();
    assert_eq!(source.source_role, Some(AgentInvokeRole::Owner));
    assert_eq!(source.target_role, None);
    assert_eq!(source.request_creator, None);
    assert!(!source.requester_source_authorized);
    assert!(source.delegated_actions.is_empty());
    c.principal = AgentInvokePrincipal::Human("human_target".into());
    c.change = AgentInvokeChange::Grant {
        expected_version: approval.version.get(),
    };
    let target = fixture
        .facts()
        .management_snapshot(&scope, &c)
        .await
        .unwrap();
    assert_eq!(target.target_role, Some(AgentInvokeRole::Owner));
    assert_eq!(target.source_role, None);
    let repeated = fixture
        .facts()
        .management_snapshot(&scope, &c)
        .await
        .unwrap();
    assert_eq!(
        target.fence, repeated.fence,
        "DB clock alone must not change a permission fence"
    );
    fixture.lose_source_qualification().await;
    assert_eq!(
        fixture.invocation().authorize_invocation(input).await,
        Err(AgentInvokeError::Forbidden)
    );
    let lost = fixture
        .facts()
        .management_snapshot(&scope, &c)
        .await
        .unwrap();
    assert!(!lost.source.eligible);
    assert_eq!(lost.target_role, Some(AgentInvokeRole::Owner));
    assert_ne!(target.fence, lost.fence);
    let mut other = scope.clone();
    other.namespace.push_str("-other");
    assert_eq!(
        fixture.facts().runtime_snapshot(&other, &pair).await,
        Err(AgentInvokeError::NotFound)
    );
    assert_eq!(
        fixture.facts().management_snapshot(&other, &c).await,
        Err(AgentInvokeError::NotFound)
    );
}
