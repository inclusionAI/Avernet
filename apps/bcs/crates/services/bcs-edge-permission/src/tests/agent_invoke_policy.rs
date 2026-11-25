//! Consumer ↔ trusted-facts/atomic-command contract tests. These are NOT SQL
//! transaction, host inventory or registered-router integration evidence.
use std::num::NonZeroU64;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_domain::edge_permission::{
    EdgeGrant, EdgeStatus, GrantKind, GrantSource, OriginatorPolicyType,
};
use bcs_service_api::application::agent_invoke::{
    AgentInvocationService, AgentInvokeGrantManagementService,
};
use bcs_service_api::core::agent_invoke::AgentInvokePolicyCoreService;
use bcs_service_api::port::agent_invoke::AgentInvokeFactsPort;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeCommandRepoPort;
use bcs_service_api::types::agent_invoke::*;

use crate::agent_invoke::{AgentInvokeApplicationService, AgentInvokePolicy};

fn n(value: u64) -> NonZeroU64 {
    NonZeroU64::new(value).unwrap()
}
fn scope() -> AgentInvokeScope {
    AgentInvokeScope {
        namespace: "synthetic-tenant".into(),
        env: "synthetic-production".into(),
    }
}
fn pair() -> AgentInvokePair {
    AgentInvokePair {
        from_bot_id: "bot-a".into(),
        to_bot_id: "bot-b".into(),
    }
}
fn state(id: &str) -> AgentInvokeBotState {
    AgentInvokeBotState {
        bot_id: id.into(),
        binding_generation: n(2),
        eligibility_generation: n(3),
        eligible: true,
        active: true,
        hidden: false,
    }
}
fn input() -> AgentInvocationInput {
    AgentInvocationInput {
        authentication: AgentInvocationAuthentication::TrustedInProcess,
            principal: AgentInvokePrincipal::Agent("bot-a".into()),
        authenticated_scope: scope(),
        pair: pair(),
        originator: "bot-a".into(),
        operation: AgentInvokeOperation::DirectInvocation,
    }
}
fn runtime() -> AgentInvokeRuntimeSnapshot {
    AgentInvokeRuntimeSnapshot {
        source_registration: AgentInvokeRegistrationBinding { registration_id: NonZeroU64::MIN, identity_revision: NonZeroU64::MIN },
        scope: scope(),
        pair: pair(),
        source: state("bot-a"),
        target: state("bot-b"),
        approval: Some(AgentInvokeApproval {
            edge: EdgeGrant {
                edge_id: 42,
                env: scope().env,
                from_id: "bot-a".into(),
                to_id: "bot-b".into(),
                grant_kind: GrantKind::AgentInvoke,
                grant_ref_id: 0,
                rules: None,
                status: EdgeStatus::Approved,
                originator_policy_type: OriginatorPolicyType::SameAsFrom,
                originator_policy_data: None,
            },
            version: n(7),
            from_binding_generation: n(2),
            to_binding_generation: n(2),
            from_eligibility_generation: n(3),
            to_eligibility_generation: n(3),
        }),
    }
}
fn command(change: AgentInvokeChange) -> AgentInvokeCommand {
    AgentInvokeCommand {
        principal: AgentInvokePrincipal::Human("human_owner".into()),
        authenticated_scope: scope(),
        pair: pair(),
        change,
        idempotency_key: "unique-client-key".into(),
        reason: "synthetic test".into(),
    }
}
fn management() -> AgentInvokeManagementSnapshot {
    AgentInvokeManagementSnapshot {
        scope: scope(),
        pair: pair(),
        principal: AgentInvokePrincipal::Human("human_owner".into()),
        source: state("bot-a"),
        target: state("bot-b"),
        source_role: None,
        target_role: None,
        delegated_actions: Vec::new(),
        request_creator: None,
        requester_source_authorized: true,
        fence: "durable-fact-version".into(),
    }
}
fn approve() -> AgentInvokeChange {
    AgentInvokeChange::Approve {
        request_id: "032f7065-9ef5-4c2c-a80d-591d50119270".into(),
        expected_version: n(1),
    }
}
fn cancel() -> AgentInvokeChange {
    AgentInvokeChange::Cancel {
        request_id: "032f7065-9ef5-4c2c-a80d-591d50119270".into(),
        expected_version: n(1),
    }
}
fn revoke() -> AgentInvokeChange {
    AgentInvokeChange::Revoke {
        edge_id: n(42),
        expected_version: n(7),
    }
}

#[test]
fn direct_invocation_projects_edge_id_and_approval_generation_not_ref_zero() {
    let permit = AgentInvokePolicy
        .authorize_invocation(&scope(), &input(), &runtime())
        .unwrap();
    assert_eq!(permit.kind, GrantKind::AgentInvoke);
    assert_eq!(permit.ref_id, 42);
    assert_eq!(permit.revision, Some(7));
    assert_eq!(permit.source, GrantSource::EdgeGrant);
    assert_eq!(permit.digest, None);
}

#[test]
fn caller_originator_domain_and_operation_are_checked_before_any_allow() {
    let mut cases = Vec::new();
    let mut v = input();
    v.principal = AgentInvokePrincipal::Agent("BOT-A".into());
    cases.push(v);
    let mut v = input();
    v.principal = AgentInvokePrincipal::Human("human_owner".into());
    cases.push(v);
    let mut v = input();
    v.principal = AgentInvokePrincipal::Governance("service".into());
    cases.push(v);
    let mut v = input();
    v.originator = "upstream-agent".into();
    cases.push(v);
    let mut v = input();
    v.authenticated_scope.namespace = "other-tenant".into();
    cases.push(v);
    let mut v = input();
    v.authenticated_scope.env = "other-env".into();
    cases.push(v);
    for operation in [
        AgentInvokeOperation::DelegatedInvocation,
        AgentInvokeOperation::ToolInvocation,
        AgentInvokeOperation::GroupOperation,
    ] {
        let mut v = input();
        v.operation = operation;
        cases.push(v);
    }
    for bad in cases {
        assert_eq!(
            AgentInvokePolicy.authorize_invocation(&scope(), &bad, &runtime()),
            Err(AgentInvokeError::Forbidden)
        );
    }
}

#[test]
fn direction_and_byte_exact_binding_never_inherit_reverse_or_transitive_edges() {
    for (from, to) in [
        ("bot-b", "bot-a"),
        ("bot-a", "bot-c"),
        ("bot-b", "bot-c"),
        ("BOT-A", "bot-b"),
        ("bot-a ", "bot-b"),
    ] {
        let mut v = input();
        v.pair = AgentInvokePair {
            from_bot_id: from.into(),
            to_bot_id: to.into(),
        };
        v.principal = AgentInvokePrincipal::Agent(from.into());
        v.originator = from.into();
        assert_eq!(
            AgentInvokePolicy.authorize_invocation(&scope(), &v, &runtime()),
            Err(AgentInvokeError::InvalidState)
        );
    }
}

#[test]
fn eligibility_loss_and_restoration_never_revive_old_approval() {
    let mut cases = Vec::new();
    let mut s = runtime();
    s.source.eligible = false;
    cases.push(s);
    let mut s = runtime();
    s.target.eligible = false;
    cases.push(s);
    let mut s = runtime();
    s.source.active = false;
    cases.push(s);
    let mut s = runtime();
    s.target.active = false;
    cases.push(s);
    let mut s = runtime();
    s.source.hidden = true;
    cases.push(s);
    let mut s = runtime();
    s.target.hidden = true;
    cases.push(s);
    let mut s = runtime();
    s.source.binding_generation = n(4);
    cases.push(s);
    let mut s = runtime();
    s.target.binding_generation = n(4);
    cases.push(s);
    let mut s = runtime();
    s.source.eligibility_generation = n(4);
    cases.push(s);
    let mut s = runtime();
    s.target.eligibility_generation = n(4);
    cases.push(s);
    let mut s = runtime();
    s.approval = None;
    cases.push(s);
    let mut s = runtime();
    s.approval.as_mut().unwrap().edge.status = EdgeStatus::Revoked;
    cases.push(s);
    for snapshot in cases {
        assert_eq!(
            AgentInvokePolicy.authorize_invocation(&scope(), &input(), &snapshot),
            Err(AgentInvokeError::Forbidden)
        );
    }
}

#[test]
fn corrupt_snapshot_and_legacy_kinds_cannot_produce_agent_permits() {
    let mut cases = Vec::new();
    let mut s = runtime();
    s.scope.namespace = "other".into();
    cases.push(s);
    let mut s = runtime();
    s.source.bot_id = "other".into();
    cases.push(s);
    let mut s = runtime();
    s.target.bot_id = "other".into();
    cases.push(s);
    let mut s = runtime();
    s.approval.as_mut().unwrap().edge.edge_id = 0;
    cases.push(s);
    let mut s = runtime();
    s.approval.as_mut().unwrap().edge.env = "other".into();
    cases.push(s);
    let mut s = runtime();
    s.approval.as_mut().unwrap().edge.from_id = "other".into();
    cases.push(s);
    let mut s = runtime();
    s.approval.as_mut().unwrap().edge.to_id = "other".into();
    cases.push(s);
    let mut s = runtime();
    s.approval.as_mut().unwrap().edge.grant_ref_id = 4;
    cases.push(s);
    let mut s = runtime();
    s.approval.as_mut().unwrap().edge.rules = Some(serde_json::Value::Null);
    cases.push(s);
    let mut s = runtime();
    s.approval.as_mut().unwrap().edge.originator_policy_type = OriginatorPolicyType::Any;
    cases.push(s);
    for kind in [
        GrantKind::Owner,
        GrantKind::Manager,
        GrantKind::PermissionProfile,
        GrantKind::Rules,
    ] {
        let mut s = runtime();
        s.approval.as_mut().unwrap().edge.grant_kind = kind;
        cases.push(s);
    }
    for snapshot in cases {
        assert_eq!(
            AgentInvokePolicy.authorize_invocation(&scope(), &input(), &snapshot),
            Err(AgentInvokeError::InvalidState)
        );
    }
}

#[test]
fn source_management_can_request_but_never_self_approve_or_grant() {
    for role in [AgentInvokeRole::Owner, AgentInvokeRole::Manager] {
        let mut s = management();
        s.source_role = Some(role);
        assert!(AgentInvokePolicy
            .authorize_change(&scope(), &command(AgentInvokeChange::Request), &s)
            .is_ok());
        for change in [
            approve(),
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
        ] {
            assert_eq!(
                AgentInvokePolicy.authorize_change(&scope(), &command(change), &s),
                Err(AgentInvokeError::Forbidden)
            );
        }
    }
}

#[test]
fn only_target_owner_or_explicit_scoped_governance_can_approve() {
    for change in [
        approve(),
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    ] {
        let mut s = management();
        s.target_role = Some(AgentInvokeRole::Manager);
        assert_eq!(
            AgentInvokePolicy.authorize_change(&scope(), &command(change.clone()), &s),
            Err(AgentInvokeError::Forbidden)
        );
        s.target_role = Some(AgentInvokeRole::Owner);
        assert!(AgentInvokePolicy
            .authorize_change(&scope(), &command(change), &s)
            .is_ok());
    }
    let mut s = management();
    s.target_role = Some(AgentInvokeRole::Owner);
    let mut c = command(approve());
    c.principal = AgentInvokePrincipal::Agent("bot-b".into());
    s.principal = c.principal.clone();
    s.delegated_actions.push(AgentInvokeAction::Approve);
    assert_eq!(
        AgentInvokePolicy.authorize_change(&scope(), &c, &s),
        Err(AgentInvokeError::Forbidden)
    );
    c.principal = AgentInvokePrincipal::Governance("governance".into());
    s.principal = c.principal.clone();
    assert!(AgentInvokePolicy.authorize_change(&scope(), &c, &s).is_ok());
    s.delegated_actions.clear();
    assert_eq!(
        AgentInvokePolicy.authorize_change(&scope(), &c, &s),
        Err(AgentInvokeError::Forbidden)
    );
}

#[test]
fn cancelling_requires_source_owner_or_still_authorized_original_requester() {
    let c = command(cancel());
    let mut s = management();
    s.source_role = Some(AgentInvokeRole::Manager);
    assert_eq!(
        AgentInvokePolicy.authorize_change(&scope(), &c, &s),
        Err(AgentInvokeError::Forbidden)
    );
    s.request_creator = Some(c.principal.clone());
    assert!(AgentInvokePolicy.authorize_change(&scope(), &c, &s).is_ok());
    s.source_role = None;
    assert_eq!(
        AgentInvokePolicy.authorize_change(&scope(), &c, &s),
        Err(AgentInvokeError::Forbidden)
    );
    s.source_role = Some(AgentInvokeRole::Owner);
    s.request_creator = None;
    assert!(AgentInvokePolicy.authorize_change(&scope(), &c, &s).is_ok());
}

#[test]
fn qualification_loss_does_not_block_authorized_revoke_or_receipt_recovery() {
    let mut s = management();
    s.source_role = Some(AgentInvokeRole::Manager);
    s.source.eligible = false;
    s.target.active = false;
    s.target.hidden = true;
    assert!(AgentInvokePolicy
        .authorize_change(&scope(), &command(revoke()), &s)
        .is_ok());
    s.source_role = None;
    s.target_role = Some(AgentInvokeRole::Manager);
    assert_eq!(
        AgentInvokePolicy.authorize_change(&scope(), &command(revoke()), &s),
        Err(AgentInvokeError::Forbidden)
    );
    s.target_role = Some(AgentInvokeRole::Owner);
    assert!(AgentInvokePolicy
        .authorize_change(&scope(), &command(revoke()), &s)
        .is_ok());
    // A precheck must not block retrieval of the original grant receipt after
    // qualification loss. The atomic store MUST reject a NEW grant here.
    assert!(AgentInvokePolicy
        .authorize_change(
            &scope(),
            &command(AgentInvokeChange::Grant {
                expected_version: 7
            }),
            &s
        )
        .is_ok());
}

#[test]
fn mismatched_management_fact_scope_pair_principal_or_fence_is_an_error() {
    let c = command(AgentInvokeChange::Request);
    let mut original = management();
    original.source_role = Some(AgentInvokeRole::Owner);
    let mut cases = Vec::new();
    let mut s = original.clone();
    s.scope.namespace = "other".into();
    cases.push(s);
    let mut s = original.clone();
    s.pair.to_bot_id = "other".into();
    cases.push(s);
    let mut s = original.clone();
    s.principal = AgentInvokePrincipal::Governance("other".into());
    cases.push(s);
    let mut s = original;
    s.fence.clear();
    cases.push(s);
    for snapshot in cases {
        assert_eq!(
            AgentInvokePolicy.authorize_change(&scope(), &c, &snapshot),
            Err(AgentInvokeError::InvalidState)
        );
    }
}

struct RecordingFacts {
    runtime: Mutex<AgentInvokeResult<AgentInvokeRuntimeSnapshot>>,
    management: Mutex<AgentInvokeResult<AgentInvokeManagementSnapshot>>,
    runtime_calls: AtomicUsize,
    management_calls: AtomicUsize,
}

#[async_trait]
impl AgentInvokeFactsPort for RecordingFacts {
    async fn runtime_snapshot(
        &self,
        requested: &AgentInvokeScope,
        requested_pair: &AgentInvokePair,
    ) -> AgentInvokeResult<AgentInvokeRuntimeSnapshot> {
        assert_eq!(requested, &scope());
        assert_eq!(requested_pair, &pair());
        self.runtime_calls.fetch_add(1, Ordering::SeqCst);
        self.runtime.lock().unwrap().clone()
    }
    async fn management_snapshot(
        &self,
        requested: &AgentInvokeScope,
        c: &AgentInvokeCommand,
    ) -> AgentInvokeResult<AgentInvokeManagementSnapshot> {
        assert_eq!(requested, &scope());
        assert_eq!(c.pair, pair());
        self.management_calls.fetch_add(1, Ordering::SeqCst);
        self.management.lock().unwrap().clone()
    }
}

struct RecordingCommands {
    calls: Mutex<
        Vec<(
            AgentInvokeScope,
            AgentInvokeCommand,
            AgentInvokeManagementSnapshot,
        )>,
    >,
    result: Mutex<AgentInvokeResult<AgentInvokeReceipt>>,
}

#[async_trait]
impl AgentInvokeCommandRepoPort for RecordingCommands {
    async fn execute(
        &self,
        requested: &AgentInvokeScope,
        c: AgentInvokeCommand,
        snapshot: AgentInvokeManagementSnapshot,
    ) -> AgentInvokeResult<AgentInvokeReceipt> {
        self.calls
            .lock()
            .unwrap()
            .push((requested.clone(), c, snapshot));
        self.result.lock().unwrap().clone()
    }
}

fn service() -> (
    AgentInvokeApplicationService,
    Arc<RecordingFacts>,
    Arc<RecordingCommands>,
) {
    let mut management = management();
    management.target_role = Some(AgentInvokeRole::Owner);
    let facts = Arc::new(RecordingFacts {
        runtime: Mutex::new(Ok(runtime())),
        management: Mutex::new(Ok(management)),
        runtime_calls: AtomicUsize::new(0),
        management_calls: AtomicUsize::new(0),
    });
    let commands = Arc::new(RecordingCommands {
        calls: Mutex::new(Vec::new()),
        result: Mutex::new(Err(AgentInvokeError::Unavailable)),
    });
    let service = AgentInvokeApplicationService::new(
        scope(),
        Arc::new(AgentInvokePolicy),
        facts.clone(),
        commands.clone(),
    )
    .unwrap();
    (service, facts, commands)
}

#[tokio::test]
async fn application_rechecks_the_fact_port_for_every_new_invocation() {
    let (service, facts, commands) = service();
    assert_eq!(
        service
            .authorize_invocation(input())
            .await
            .unwrap()
            .revision,
        Some(7)
    );
    facts
        .runtime
        .lock()
        .unwrap()
        .as_mut()
        .unwrap()
        .approval
        .as_mut()
        .unwrap()
        .edge
        .status = EdgeStatus::Revoked;
    assert_eq!(
        service.authorize_invocation(input()).await,
        Err(AgentInvokeError::Forbidden)
    );
    assert_eq!(facts.runtime_calls.load(Ordering::SeqCst), 2);
    assert!(commands.calls.lock().unwrap().is_empty());
}

#[tokio::test]
async fn malformed_or_unauthorized_ingress_never_calls_dependencies() {
    let (service, facts, commands) = service();
    let mut bad = input();
    bad.originator = "upstream".into();
    assert_eq!(
        service.authorize_invocation(bad).await,
        Err(AgentInvokeError::Forbidden)
    );
    let mut c = command(approve());
    c.authenticated_scope.namespace = "other".into();
    assert_eq!(service.change(c).await, Err(AgentInvokeError::Forbidden));
    let mut c = command(approve());
    c.idempotency_key = "bad key".into();
    assert_eq!(service.change(c).await, Err(AgentInvokeError::InvalidInput));
    assert_eq!(facts.runtime_calls.load(Ordering::SeqCst), 0);
    assert_eq!(facts.management_calls.load(Ordering::SeqCst), 0);
    assert!(commands.calls.lock().unwrap().is_empty());
}

#[tokio::test]
async fn fact_outages_propagate_without_default_allow_or_any_command_write() {
    let (service, facts, commands) = service();
    *facts.runtime.lock().unwrap() = Err(AgentInvokeError::Unavailable);
    assert_eq!(
        service.authorize_invocation(input()).await,
        Err(AgentInvokeError::Unavailable)
    );
    *facts.management.lock().unwrap() = Err(AgentInvokeError::Unavailable);
    assert_eq!(
        service.change(command(approve())).await,
        Err(AgentInvokeError::Unavailable)
    );
    assert_eq!(facts.runtime_calls.load(Ordering::SeqCst), 1);
    assert_eq!(facts.management_calls.load(Ordering::SeqCst), 1);
    assert!(commands.calls.lock().unwrap().is_empty());
}

#[tokio::test]
async fn management_denial_never_reaches_atomic_command_port() {
    let (service, facts, commands) = service();
    facts
        .management
        .lock()
        .unwrap()
        .as_mut()
        .unwrap()
        .target_role = Some(AgentInvokeRole::Manager);
    assert_eq!(
        service.change(command(approve())).await,
        Err(AgentInvokeError::Forbidden)
    );
    assert_eq!(facts.management_calls.load(Ordering::SeqCst), 1);
    assert!(commands.calls.lock().unwrap().is_empty());
}

#[tokio::test]
async fn approved_operator_passes_exact_command_and_fence_to_one_atomic_write() {
    let (service, facts, commands) = service();
    let receipt = AgentInvokeReceipt {
        action_id: "original-audit-id".into(),
        replayed: true,
        original_grant: Some(AgentInvokeGrantView {
            edge_id: n(42),
            status: EdgeStatus::Approved,
            version: n(7),
        }),
        current_grant: Some(AgentInvokeGrantView {
            edge_id: n(42),
            status: EdgeStatus::Revoked,
            version: n(8),
        }),
        original_request: None,
        current_request: None,
    };
    *commands.result.lock().unwrap() = Ok(receipt.clone());
    let c = command(AgentInvokeChange::Grant {
        expected_version: 0,
    });
    assert_eq!(service.change(c.clone()).await, Ok(receipt));
    assert_eq!(facts.management_calls.load(Ordering::SeqCst), 1);
    let calls = commands.calls.lock().unwrap();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].0, scope());
    assert_eq!(calls[0].1, c);
    assert_eq!(
        calls[0].2,
        facts.management.lock().unwrap().as_ref().unwrap().clone()
    );
}

#[tokio::test]
async fn atomic_write_failures_and_unknown_commit_are_never_retried_or_hidden() {
    let (service, facts, commands) = service();
    for error in [
        AgentInvokeError::Conflict,
        AgentInvokeError::LimitExceeded,
        AgentInvokeError::Unavailable,
        AgentInvokeError::CommitUnknown,
    ] {
        *commands.result.lock().unwrap() = Err(error);
        assert_eq!(service.change(command(approve())).await, Err(error));
    }
    assert_eq!(facts.management_calls.load(Ordering::SeqCst), 4);
    assert_eq!(commands.calls.lock().unwrap().len(), 4);
}
