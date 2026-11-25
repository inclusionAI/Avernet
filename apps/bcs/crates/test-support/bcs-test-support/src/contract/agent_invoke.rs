//! Shared Agent invocation lifecycle/consumer contract. Drivers own fact setup
//! and real implementations; this suite calls only declared application/port
//! contracts. A local pass does NOT prove SQL durability or host eligibility.
use std::num::NonZeroU64;

use async_trait::async_trait;
use bcs_domain::edge_permission::{EdgeStatus, RequestStatus};
use bcs_service_api::application::agent_invoke::{
    AgentInvocationService, AgentInvokeGrantManagementService,
};
use bcs_service_api::port::agent_invoke::AgentInvokeFactsPort;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeCommandRepoPort;
use bcs_service_api::types::agent_invoke::*;

#[async_trait]
pub trait AgentInvokeContractDriver: Send + Sync {
    fn scope(&self) -> AgentInvokeScope;
    fn invocation(&self) -> &dyn AgentInvocationService;
    fn management(&self) -> &dyn AgentInvokeGrantManagementService;
    fn facts(&self) -> &dyn AgentInvokeFactsPort;
    fn commands(&self) -> &dyn AgentInvokeCommandRepoPort;
    async fn register(&self, bot_id: &str);
    async fn role(&self, human: &str, bot_id: &str, role: Option<AgentInvokeRole>);
    async fn publish(&self, bot_id: &str, eligible: bool, rebind: bool);
    async fn audit_count(&self) -> usize;
}

fn nz(value: u64) -> NonZeroU64 {
    NonZeroU64::new(value).expect("positive fixture version")
}
fn pair(from: &str, to: &str) -> AgentInvokePair {
    AgentInvokePair {
        from_bot_id: from.into(),
        to_bot_id: to.into(),
    }
}
fn input(driver: &dyn AgentInvokeContractDriver, from: &str, to: &str) -> AgentInvocationInput {
    AgentInvocationInput {
        principal: AgentInvokePrincipal::Agent(from.into()),
        authenticated_scope: driver.scope(),
        pair: pair(from, to),
        originator: from.into(),
        operation: AgentInvokeOperation::DirectInvocation,
    }
}
fn command(
    driver: &dyn AgentInvokeContractDriver,
    human: &str,
    from: &str,
    to: &str,
    key: &str,
    change: AgentInvokeChange,
) -> AgentInvokeCommand {
    AgentInvokeCommand {
        principal: AgentInvokePrincipal::Human(human.into()),
        authenticated_scope: driver.scope(),
        pair: pair(from, to),
        change,
        idempotency_key: key.into(),
        reason: "shared conformance fixture".into(),
    }
}
async fn setup(driver: &dyn AgentInvokeContractDriver) {
    for bot in ["bot-a", "bot-b", "bot-c", "bot-d", "bot-e"] {
        driver.register(bot).await;
    }
    for bot in ["bot-a", "bot-d", "bot-e"] {
        driver
            .role("human_source", bot, Some(AgentInvokeRole::Owner))
            .await;
    }
    for bot in ["bot-b", "bot-c"] {
        driver
            .role("human_target", bot, Some(AgentInvokeRole::Owner))
            .await;
    }
}
async fn grant(
    driver: &dyn AgentInvokeContractDriver,
    key: &str,
    version: u64,
) -> AgentInvokeReceipt {
    driver
        .management()
        .change(command(
            driver,
            "human_target",
            "bot-a",
            "bot-b",
            key,
            AgentInvokeChange::Grant {
                expected_version: version,
            },
        ))
        .await
        .expect("target owner grant")
}
async fn request(driver: &dyn AgentInvokeContractDriver, key: &str) -> AgentInvokeRequestView {
    driver
        .management()
        .change(command(
            driver,
            "human_source",
            "bot-a",
            "bot-b",
            key,
            AgentInvokeChange::Request,
        ))
        .await
        .expect("source owner request")
        .original_request
        .expect("request view")
}

pub async fn agent_invoke_grant_management_service_contract_tests(driver: &dyn AgentInvokeContractDriver) {
    setup(driver).await;
    assert_eq!(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-a", "bot-b"))
            .await,
        Err(AgentInvokeError::Forbidden)
    );
    let request = request(driver, "request-1").await;
    assert_eq!(request.status, RequestStatus::Pending);
    assert_eq!(request.edge_id, None);
    let approve = AgentInvokeChange::Approve {
        request_id: request.request_id.clone(),
        expected_version: request.version,
    };
    assert_eq!(
        driver
            .management()
            .change(command(
                driver,
                "human_source",
                "bot-a",
                "bot-b",
                "self-approve",
                approve.clone()
            ))
            .await,
        Err(AgentInvokeError::Forbidden)
    );
    let approval = driver
        .management()
        .change(command(
            driver,
            "human_target",
            "bot-a",
            "bot-b",
            "approve-1",
            approve,
        ))
        .await
        .unwrap();
    let grant = approval.original_grant.unwrap();
    assert_eq!(
        approval.original_request.unwrap().status,
        RequestStatus::Approved
    );
    let permit = driver
        .invocation()
        .authorize_invocation(input(driver, "bot-a", "bot-b"))
        .await
        .unwrap();
    assert_eq!(permit.ref_id, grant.edge_id.get());
    assert_eq!(permit.revision, Some(grant.version.get()));
    assert_eq!(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-b", "bot-a"))
            .await,
        Err(AgentInvokeError::Forbidden)
    );
    assert_eq!(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-a", "bot-c"))
            .await,
        Err(AgentInvokeError::Forbidden)
    );
    let mut delegated = input(driver, "bot-b", "bot-c");
    delegated.originator = "bot-a".into();
    assert_eq!(
        driver.invocation().authorize_invocation(delegated).await,
        Err(AgentInvokeError::Forbidden)
    );
    assert_eq!(
        driver.audit_count().await,
        2,
        "request + approve only, no reverse, self-approve or runtime writes"
    );
}

pub async fn run_agent_invoke_replay_contract(driver: &dyn AgentInvokeContractDriver) {
    setup(driver).await;
    let original = grant(driver, "grant-1", 0).await;
    let first = original.original_grant.as_ref().unwrap();
    let revoke = command(
        driver,
        "human_source",
        "bot-a",
        "bot-b",
        "revoke-1",
        AgentInvokeChange::Revoke {
            edge_id: first.edge_id,
            expected_version: first.version,
        },
    );
    let revoked = driver
        .management()
        .change(revoke.clone())
        .await
        .unwrap()
        .original_grant
        .unwrap();
    assert_eq!(revoked.status, EdgeStatus::Revoked);
    assert!(revoked.version > first.version);
    let replay = grant(driver, "grant-1", 0).await;
    assert!(replay.replayed);
    assert_eq!(replay.action_id, original.action_id);
    assert_eq!(replay.original_grant, original.original_grant);
    assert_eq!(replay.current_grant, Some(revoked.clone()));
    assert_eq!(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-a", "bot-b"))
            .await,
        Err(AgentInvokeError::Forbidden)
    );
    assert_eq!(
        driver.audit_count().await,
        2,
        "replay cannot write audit/edge"
    );
    let mut altered = command(
        driver,
        "human_target",
        "bot-a",
        "bot-b",
        "grant-1",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    );
    altered.reason = "different payload".into();
    assert_eq!(
        driver.management().change(altered).await,
        Err(AgentInvokeError::Conflict)
    );
    let fresh = grant(driver, "grant-2", revoked.version.get())
        .await
        .original_grant
        .unwrap();
    assert_eq!(fresh.edge_id, first.edge_id);
    assert!(fresh.version > revoked.version);
    assert_eq!(
        driver
            .management()
            .change(revoke)
            .await
            .unwrap()
            .current_grant,
        Some(fresh.clone())
    );
    let mut stale_revoke = command(
        driver,
        "human_source",
        "bot-a",
        "bot-b",
        "new-stale-key",
        AgentInvokeChange::Revoke {
            edge_id: first.edge_id,
            expected_version: first.version,
        },
    );
    assert_eq!(
        driver.management().change(stale_revoke.clone()).await,
        Err(AgentInvokeError::Conflict)
    );
    stale_revoke.change = AgentInvokeChange::Revoke {
        edge_id: nz(first.edge_id.get() + 100),
        expected_version: fresh.version,
    };
    assert_eq!(
        driver.management().change(stale_revoke).await,
        Err(AgentInvokeError::NotFound)
    );
    assert_eq!(
        driver.audit_count().await,
        3,
        "failed/stale commands leave no partial writes"
    );
    assert_eq!(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-a", "bot-b"))
            .await
            .unwrap()
            .revision,
        Some(fresh.version.get())
    );
}

pub async fn agent_invocation_service_contract_tests(driver: &dyn AgentInvokeContractDriver) {
    setup(driver).await;
    let first = grant(driver, "grant-before-loss", 0)
        .await
        .original_grant
        .unwrap();
    driver.publish("bot-a", false, false).await;
    assert_eq!(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-a", "bot-b"))
            .await,
        Err(AgentInvokeError::Forbidden)
    );
    driver.publish("bot-a", true, false).await;
    assert_eq!(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-a", "bot-b"))
            .await,
        Err(AgentInvokeError::Forbidden)
    );
    let snapshot = driver
        .facts()
        .runtime_snapshot(&driver.scope(), &pair("bot-a", "bot-b"))
        .await
        .unwrap();
    let old = snapshot.approval.unwrap();
    assert_eq!(old.edge.status, EdgeStatus::Revoked);
    assert!(old.version > first.version);
    let fresh = grant(driver, "grant-after-restoration", old.version.get())
        .await
        .original_grant
        .unwrap();
    assert_eq!(fresh.edge_id, first.edge_id);
    assert_eq!(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-a", "bot-b"))
            .await
            .unwrap()
            .revision,
        Some(fresh.version.get())
    );
    driver.publish("bot-b", true, true).await;
    assert_eq!(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-a", "bot-b"))
            .await,
        Err(AgentInvokeError::Forbidden)
    );
    // Replaying the old key still reports history; it cannot repair/reapprove.
    let replay = grant(driver, "grant-before-loss", 0).await;
    assert!(replay.replayed);
    assert_eq!(replay.current_grant.unwrap().status, EdgeStatus::Revoked);
}

pub async fn run_agent_invoke_request_terminals_contract(driver: &dyn AgentInvokeContractDriver) {
    setup(driver).await;
    let pending = request(driver, "cancel-request").await;
    let cancel = command(
        driver,
        "human_source",
        "bot-a",
        "bot-b",
        "cancel-key",
        AgentInvokeChange::Cancel {
            request_id: pending.request_id.clone(),
            expected_version: pending.version,
        },
    );
    let cancelled = driver.management().change(cancel.clone()).await.unwrap();
    assert_eq!(
        cancelled.original_request.as_ref().unwrap().status,
        RequestStatus::Cancelled
    );
    assert!(cancelled.original_grant.is_none());
    assert!(driver.management().change(cancel).await.unwrap().replayed);
    let late = command(
        driver,
        "human_target",
        "bot-a",
        "bot-b",
        "late-approve",
        AgentInvokeChange::Approve {
            request_id: pending.request_id,
            expected_version: pending.version,
        },
    );
    assert_eq!(
        driver.management().change(late).await,
        Err(AgentInvokeError::Conflict)
    );
    let pending = request(driver, "reject-request").await;
    let rejected = driver
        .management()
        .change(command(
            driver,
            "human_target",
            "bot-a",
            "bot-b",
            "reject-key",
            AgentInvokeChange::Reject {
                request_id: pending.request_id,
                expected_version: pending.version,
            },
        ))
        .await
        .unwrap();
    assert_eq!(
        rejected.original_request.unwrap().status,
        RequestStatus::Rejected
    );
    assert!(rejected.original_grant.is_none());
    assert_eq!(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-a", "bot-b"))
            .await,
        Err(AgentInvokeError::Forbidden)
    );
    assert_eq!(driver.audit_count().await, 4);
}

pub async fn agent_invoke_command_repo_port_contract_tests(driver: &dyn AgentInvokeContractDriver) {
    setup(driver).await;
    let pending = request(driver, "race-request").await;
    let approve = command(
        driver,
        "human_target",
        "bot-a",
        "bot-b",
        "race-approve",
        AgentInvokeChange::Approve {
            request_id: pending.request_id.clone(),
            expected_version: pending.version,
        },
    );
    let cancel = command(
        driver,
        "human_source",
        "bot-a",
        "bot-b",
        "race-cancel",
        AgentInvokeChange::Cancel {
            request_id: pending.request_id,
            expected_version: pending.version,
        },
    );
    let scope = driver.scope();
    // Both snapshots are captured BEFORE either writer is released. No sleeps
    // or scheduling assumption is used to infer a race.
    let approve_facts = driver
        .facts()
        .management_snapshot(&scope, &approve)
        .await
        .unwrap();
    let cancel_facts = driver
        .facts()
        .management_snapshot(&scope, &cancel)
        .await
        .unwrap();
    let (a, b) = tokio::join!(
        driver.commands().execute(&scope, approve, approve_facts),
        driver.commands().execute(&scope, cancel, cancel_facts),
    );
    assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
    assert!(a == Err(AgentInvokeError::Conflict) || b == Err(AgentInvokeError::Conflict));
    assert_eq!(
        driver.audit_count().await,
        2,
        "losing writer has no edge/request/audit/receipt effects"
    );
}

pub async fn agent_invoke_facts_port_contract_tests(driver: &dyn AgentInvokeContractDriver) {
    setup(driver).await;
    let pending = request(driver, "authority-request").await;
    driver.role("human_source", "bot-a", None).await;
    let approval = command(
        driver,
        "human_target",
        "bot-a",
        "bot-b",
        "stale-requester",
        AgentInvokeChange::Approve {
            request_id: pending.request_id,
            expected_version: pending.version,
        },
    );
    assert_eq!(
        driver.management().change(approval.clone()).await,
        Err(AgentInvokeError::Conflict)
    );
    driver
        .role("human_source", "bot-a", Some(AgentInvokeRole::Owner))
        .await;
    assert_eq!(driver.management().change(approval).await, Err(AgentInvokeError::Conflict),
        "restoring requester authority must not revive a request approved against an old authority epoch");
    let c = command(
        driver,
        "human_target",
        "bot-a",
        "bot-b",
        "stale-owner",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    );
    let scope = driver.scope();
    let facts = driver
        .facts()
        .management_snapshot(&scope, &c)
        .await
        .unwrap();
    driver.role("human_target", "bot-b", None).await;
    assert_eq!(
        driver.commands().execute(&scope, c, facts).await,
        Err(AgentInvokeError::Forbidden)
    );
    assert_eq!(driver.audit_count().await, 1);
}

/// Driver limits must all be 1 for this test; all other suites may use any
/// explicit positive limits sufficient for their one-pair operations.
pub async fn run_agent_invoke_limits_contract(driver: &dyn AgentInvokeContractDriver) {
    setup(driver).await;
    let first = grant(driver, "limited-grant", 0)
        .await
        .original_grant
        .unwrap();
    let out = command(
        driver,
        "human_target",
        "bot-a",
        "bot-c",
        "out-over-limit",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    );
    assert_eq!(
        driver.management().change(out.clone()).await,
        Err(AgentInvokeError::LimitExceeded)
    );
    let inbound = command(
        driver,
        "human_target",
        "bot-d",
        "bot-b",
        "in-over-limit",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    );
    assert_eq!(
        driver.management().change(inbound).await,
        Err(AgentInvokeError::LimitExceeded)
    );
    driver
        .management()
        .change(command(
            driver,
            "human_source",
            "bot-a",
            "bot-b",
            "limit-revoke",
            AgentInvokeChange::Revoke {
                edge_id: first.edge_id,
                expected_version: first.version,
            },
        ))
        .await
        .unwrap();
    assert!(
        driver.management().change(out).await.is_ok(),
        "a failed key did not reserve a receipt; revoke frees degree"
    );
    request(driver, "limited-pending").await;
    let pending_out = command(
        driver,
        "human_source",
        "bot-a",
        "bot-c",
        "pending-out-over",
        AgentInvokeChange::Request,
    );
    assert_eq!(
        driver.management().change(pending_out).await,
        Err(AgentInvokeError::LimitExceeded)
    );
    let pending_in = command(
        driver,
        "human_source",
        "bot-e",
        "bot-b",
        "pending-in-over",
        AgentInvokeChange::Request,
    );
    assert_eq!(
        driver.management().change(pending_in).await,
        Err(AgentInvokeError::LimitExceeded)
    );
    assert_eq!(driver.audit_count().await, 4);
}

pub async fn run_agent_invoke_approve_revoke_contract(
    driver: &dyn AgentInvokeContractDriver,
    approve_first: bool,
) {
    setup(driver).await;
    let first = grant(driver, "before-race", 0)
        .await
        .original_grant
        .unwrap();
    let pending = request(driver, "race-request").await;
    let approve = command(
        driver,
        "human_target",
        "bot-a",
        "bot-b",
        "racing-approve",
        AgentInvokeChange::Approve {
            request_id: pending.request_id,
            expected_version: pending.version,
        },
    );
    let revoke = command(
        driver,
        "human_source",
        "bot-a",
        "bot-b",
        "racing-revoke",
        AgentInvokeChange::Revoke {
            edge_id: first.edge_id,
            expected_version: first.version,
        },
    );
    let scope = driver.scope();
    let approval_facts = driver
        .facts()
        .management_snapshot(&scope, &approve)
        .await
        .unwrap();
    let revoke_facts = driver
        .facts()
        .management_snapshot(&scope, &revoke)
        .await
        .unwrap();
    let (a, b) = if approve_first {
        tokio::join!(
            driver.commands().execute(&scope, approve, approval_facts),
            driver.commands().execute(&scope, revoke, revoke_facts)
        )
    } else {
        let (b, a) = tokio::join!(
            driver.commands().execute(&scope, revoke, revoke_facts),
            driver.commands().execute(&scope, approve, approval_facts)
        );
        (a, b)
    };
    assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
    assert!(a == Err(AgentInvokeError::Conflict) || b == Err(AgentInvokeError::Conflict));
    assert_eq!(driver.audit_count().await, 3);
    assert_eq!(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-a", "bot-b"))
            .await
            .is_ok(),
        a.is_ok()
    );
}

pub async fn run_agent_invoke_atomic_limits_contract(driver: &dyn AgentInvokeContractDriver) {
    setup(driver).await;
    let a = command(
        driver,
        "human_target",
        "bot-a",
        "bot-b",
        "competing-a",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    );
    let b = command(
        driver,
        "human_target",
        "bot-a",
        "bot-c",
        "competing-b",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    );
    let scope = driver.scope();
    let af = driver
        .facts()
        .management_snapshot(&scope, &a)
        .await
        .unwrap();
    let bf = driver
        .facts()
        .management_snapshot(&scope, &b)
        .await
        .unwrap();
    let (ar, br) = tokio::join!(
        driver.commands().execute(&scope, a.clone(), af),
        driver.commands().execute(&scope, b.clone(), bf)
    );
    assert_eq!(usize::from(ar.is_ok()) + usize::from(br.is_ok()), 1);
    let losing = if ar.is_ok() { b } else { a };
    assert_eq!(
        driver.management().change(losing).await,
        Err(AgentInvokeError::LimitExceeded)
    );
    let allowed = usize::from(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-a", "bot-b"))
            .await
            .is_ok(),
    ) + usize::from(
        driver
            .invocation()
            .authorize_invocation(input(driver, "bot-a", "bot-c"))
            .await
            .is_ok(),
    );
    assert_eq!(allowed, 1);
    assert_eq!(driver.audit_count().await, 1);
}
