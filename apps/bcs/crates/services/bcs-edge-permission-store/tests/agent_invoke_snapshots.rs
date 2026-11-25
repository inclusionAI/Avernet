//! SQL facts are consumed through the real application/pure policy, not a
//! fixture that authorizes directly from a bare edge or cached boolean.
use async_trait::async_trait;
use bcs_db_api::*;
use bcs_domain::edge_permission::EdgeStatus;
use bcs_edge_permission::agent_invoke::{AgentInvokeApplicationService, AgentInvokePolicy};
use bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore;
use bcs_service_api::application::agent_invoke::{AgentInvocationService,AgentInvokeGrantManagementService};
use bcs_service_api::core::agent_invoke::AgentInvokePolicyCoreService;
use bcs_service_api::port::agent_invoke::AgentInvokeFactsPort;
use bcs_service_api::port::repo::agent_invoke::AgentInvokeFactRepoPort;
use bcs_service_api::types::agent_invoke::*;
use std::num::NonZeroU32;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

#[path = "support/agent_invoke_snapshots.rs"]
mod fixture;
use fixture::*;

#[tokio::test]
async fn application_uses_exact_durable_approval_and_current_generations() {
    let f = Fixture::new().await;
    assert_eq!(
        f.app.authorize_invocation(input()).await,
        Err(AgentInvokeError::Forbidden)
    );
    let edge_id = f.grant().await;
    let permit = f.app.authorize_invocation(input()).await.unwrap();
    assert_eq!(permit.ref_id, edge_id);
    assert_eq!(permit.revision, Some(1));
    f.sql("UPDATE edge_grants SET status = 'revoked' WHERE grant_kind = 'agent_invoke'")
        .await;
    assert_eq!(
        f.app.authorize_invocation(input()).await,
        Err(AgentInvokeError::Forbidden)
    );
    assert_eq!(
        f.store
            .runtime_snapshot(&scope(), &pair())
            .await
            .unwrap()
            .approval
            .unwrap()
            .edge
            .status,
        EdgeStatus::Revoked
    );
}

#[tokio::test]
async fn qualification_loss_restoration_and_binding_change_do_not_revive_approval() {
    let f = Fixture::new().await;
    f.grant().await;
    let mut p = AgentInvokeFactPublication {
        state: f
            .store
            .read_fact(&scope(), "bot-a")
            .await
            .unwrap()
            .unwrap()
            .state,
        expected_revision: 1,
        valid_until_ms: f.store.database_time_ms().await.unwrap() + 30_000,
    };
    p.state.eligible = false;
    p.state.eligibility_generation = nz(2);
    f.store.publish_fact(&scope(), p.clone()).await.unwrap();
    assert_eq!(
        f.app.authorize_invocation(input()).await,
        Err(AgentInvokeError::Forbidden)
    );
    p.expected_revision = 2;
    p.state.eligible = true;
    p.state.eligibility_generation = nz(3);
    f.store.publish_fact(&scope(), p.clone()).await.unwrap();
    assert_eq!(
        f.app.authorize_invocation(input()).await,
        Err(AgentInvokeError::Forbidden)
    );
    let policy=Arc::new(AgentInvokePolicy);
    let commands=Arc::new(bcs_edge_permission_store::agent_invoke::DbAgentInvokeCommandStore::new(f.store.clone(),policy.clone()));
    let management=AgentInvokeApplicationService::new(scope(),policy,f.store.clone(),commands).unwrap();
    let current=f.store.runtime_snapshot(&scope(),&pair()).await.unwrap().approval.unwrap();
    management.change(command("human_target",AgentInvokeChange::Grant {expected_version:current.version.get()})).await.unwrap();
    assert!(f.app.authorize_invocation(input()).await.is_ok());
    p.expected_revision = 3;
    p.state.binding_generation = nz(2);
    f.store.publish_fact(&scope(), p).await.unwrap();
    assert_eq!(
        f.app.authorize_invocation(input()).await,
        Err(AgentInvokeError::Forbidden)
    );
}

#[tokio::test]
async fn registered_hidden_retired_missing_or_wrong_actor_cannot_be_overridden_by_fact() {
    for sql in [
        "UPDATE bcs_bots SET visibility='hidden' WHERE bot_uuid='bot-b'",
        "UPDATE bcs_bots SET is_deleted=1 WHERE bot_uuid='bot-b'",
        "DELETE FROM bcs_bots WHERE bot_uuid='bot-b'",
        "UPDATE bcs_bots SET actor_kind='human' WHERE bot_uuid='bot-b'",
    ] {
        let f = Fixture::new().await;
        f.grant().await;
        f.sql(sql).await;
        assert!(f.app.authorize_invocation(input()).await.is_err(), "{sql}");
    }
}

#[tokio::test]
async fn closed_missing_or_expired_feed_is_unavailable_not_empty_success() {
    for sql in [
        "UPDATE agent_invoke_scopes SET runtime_enabled=0",
        "DELETE FROM agent_invoke_facts WHERE bot_id='bot-a'",
        "UPDATE agent_invoke_facts SET valid_until_ms=1 WHERE bot_id='bot-a'",
    ] {
        let f = Fixture::new().await;
        f.grant().await;
        f.sql(sql).await;
        assert_eq!(
            f.app.authorize_invocation(input()).await,
            Err(AgentInvokeError::Unavailable),
            "{sql}"
        );
    }
}

#[tokio::test]
async fn malformed_or_cross_domain_approval_is_invalid_as_a_whole() {
    for sql in [
        "DELETE FROM agent_invoke_grants",
        "UPDATE agent_invoke_facts SET valid_until_ms=9223372036854775807",
        "UPDATE agent_invoke_grants SET scope_id=100",
        "UPDATE edge_grants SET rules='null' WHERE grant_kind='agent_invoke'",
        "UPDATE edge_grants SET originator_policy_data='[]' WHERE grant_kind='agent_invoke'",
        "UPDATE edge_grants SET originator_policy_type='any' WHERE grant_kind='agent_invoke'",
        "UPDATE edge_grants SET grant_ref_id=1 WHERE grant_kind='agent_invoke'",
        "UPDATE edge_grants SET status='unexpected' WHERE grant_kind='agent_invoke'",
    ] {
        let f = Fixture::new().await;
        f.grant().await;
        f.sql(sql).await;
        assert_eq!(
            f.app.authorize_invocation(input()).await,
            Err(AgentInvokeError::InvalidState),
            "{sql}"
        );
    }
    let f = Fixture::new().await;
    f.grant().await;
    f.sql("INSERT INTO edge_grants (env,from_id,to_id,grant_kind,grant_ref_id,status,originator_policy_type) VALUES ('dev','bot-a','bot-b','agent_invoke',9,'approved','same_as_from')").await;
    assert_eq!(
        f.app.authorize_invocation(input()).await,
        Err(AgentInvokeError::InvalidState)
    );
}

#[tokio::test]
async fn scope_and_case_exact_pair_do_not_fall_back_to_neighbor_approval() {
    let f = Fixture::new().await;
    f.grant().await;
    let mut other = scope();
    other.namespace.push_str("-other");
    assert_eq!(
        f.store.runtime_snapshot(&other, &pair()).await,
        Err(AgentInvokeError::NotFound)
    );
    for bad in [
        AgentInvokePair {
            from_bot_id: "BOT-A".into(),
            to_bot_id: "bot-b".into(),
        },
        AgentInvokePair {
            from_bot_id: "bot-a ".into(),
            to_bot_id: "bot-b".into(),
        },
    ] {
        assert!(f.store.runtime_snapshot(&scope(), &bad).await.is_err());
    }
    assert_eq!(
        f.store
            .runtime_snapshot(
                &scope(),
                &AgentInvokePair {
                    from_bot_id: "bot-a".into(),
                    to_bot_id: "bot-a".into()
                }
            )
            .await,
        Err(AgentInvokeError::InvalidInput)
    );
}

#[tokio::test]
async fn strict_source_roles_target_owner_and_no_agent_self_approval() {
    let f = Fixture::new().await;
    for (actor, change, allowed) in [
        ("human_source", AgentInvokeChange::Request, true),
        ("human_manager", AgentInvokeChange::Request, true),
        (
            "human_target",
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
            true,
        ),
        (
            "human_target-manager",
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
            false,
        ),
    ] {
        let command = command(actor, change);
        let snapshot = f
            .store
            .management_snapshot(&scope(), &command)
            .await
            .unwrap();
        assert_eq!(
            AgentInvokePolicy
                .authorize_change(&scope(), &command, &snapshot)
                .is_ok(),
            allowed
        );
    }
    let mut c = command(
        "human_target",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    );
    c.principal = AgentInvokePrincipal::Agent("bot-a".into());
    let snap = f.store.management_snapshot(&scope(), &c).await.unwrap();
    assert_eq!(snap.source_role, None);
    assert_eq!(snap.target_role, None);
    assert_eq!(
        AgentInvokePolicy.authorize_change(&scope(), &c, &snap),
        Err(AgentInvokeError::Forbidden)
    );
}

#[tokio::test]
async fn authority_loss_restoration_changes_fence_and_no_legacy_creator_claim() {
    let f = Fixture::new().await;
    let c = command("human_manager", AgentInvokeChange::Request);
    let before = f.store.management_snapshot(&scope(), &c).await.unwrap();
    f.sql("UPDATE edge_grants SET status='revoked' WHERE from_id='human_manager'")
        .await;
    f.sql("INSERT INTO bot_authority_changes (env,bot_id,mutation_id,sequence,action,actor_kind,actor_id,subject_id) VALUES ('dev','bot-a','snapshot-revoke',1,'manager_revoke','human','human_source','human_manager')").await;
    let lost = f.store.management_snapshot(&scope(), &c).await.unwrap();
    assert_eq!(lost.source_role, None);
    assert_ne!(before.fence, lost.fence);
    f.sql("UPDATE edge_grants SET status='approved' WHERE from_id='human_manager'")
        .await;
    f.sql("INSERT INTO bot_authority_changes (env,bot_id,mutation_id,sequence,action,actor_kind,actor_id,subject_id) VALUES ('dev','bot-a','snapshot-restore',1,'manager_restore','human','human_source','human_manager')").await;
    let restored = f.store.management_snapshot(&scope(), &c).await.unwrap();
    assert_eq!(restored.source_role, Some(AgentInvokeRole::Manager));
    assert_ne!(before.fence, restored.fence);
    f.sql("UPDATE bcs_bots SET ownership_version=0, created_by='manager' WHERE bot_uuid='bot-a'")
        .await;
    assert_eq!(
        f.store
            .management_snapshot(&scope(), &c)
            .await
            .unwrap()
            .source_role,
        None
    );
}

#[tokio::test]
async fn malformed_owner_or_dead_human_is_not_synthesized_into_authority() {
    let f = Fixture::new().await;
    let c = command("human_source", AgentInvokeChange::Request);
    f.sql("UPDATE edge_grants SET rules='[]' WHERE from_id='human_source'")
        .await;
    assert_eq!(
        f.store.management_snapshot(&scope(), &c).await,
        Err(AgentInvokeError::InvalidState)
    );
    let f = Fixture::new().await;
    let c = command("human_manager", AgentInvokeChange::Request);
    f.sql("UPDATE bcs_bots SET is_deleted=1 WHERE bot_uuid='human_manager'")
        .await;
    assert_eq!(
        f.store
            .management_snapshot(&scope(), &c)
            .await
            .unwrap()
            .source_role,
        None
    );
}

#[tokio::test]
async fn expired_qualification_does_not_remove_target_owner_stop_loss_authority() {
    let f = Fixture::new().await;
    let edge = f.grant().await;
    f.sql("UPDATE agent_invoke_facts SET valid_until_ms=1")
        .await;
    let c = command(
        "human_target",
        AgentInvokeChange::Revoke {
            edge_id: nz(edge),
            expected_version: nz(1),
        },
    );
    let snapshot = f.store.management_snapshot(&scope(), &c).await.unwrap();
    assert!(!snapshot.source.active && !snapshot.target.active);
    assert_eq!(
        AgentInvokePolicy.authorize_change(&scope(), &c, &snapshot),
        Ok(())
    );
}

struct CountDb {
    inner: Arc<dyn DbPlugin>,
    queries: AtomicUsize,
}
#[async_trait]
impl DbPlugin for CountDb {
    async fn query(&self, s: DbStatement) -> DbResult<Vec<DbRow>> {
        self.queries.fetch_add(1, Ordering::SeqCst);
        self.inner.query(s).await
    }
    async fn execute(&self, s: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(s).await
    }
    async fn transaction(
        &self,
        s: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(s).await
    }
    async fn transaction_with_options(
        &self,
        s: Vec<DbTransactionStep>,
        o: DbTransactionOptions,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction_with_options(s, o).await
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}
#[tokio::test]
async fn runtime_is_one_real_sql_statement_and_forged_ingress_reads_nothing() {
    let f = Fixture::new().await;
    f.grant().await;
    let db = Arc::new(CountDb {
        inner: f.db,
        queries: AtomicUsize::new(0),
    });
    let n = NonZeroU32::new(2).unwrap();
    let store = Arc::new(
        DbAgentInvokeStore::new(
            db.clone(),
            DbSqlFlavor::Sqlite,
            scope(),
            AgentInvokeLimits {
                max_out_degree: n,
                max_in_degree: n,
                max_pending_out: n,
                max_pending_in: n,
            },
            nz(60_000),
        )
        .unwrap(),
    );
    let app = AgentInvokeApplicationService::new(
        scope(),
        Arc::new(AgentInvokePolicy),
        store,
        Arc::new(NoCommands),
    )
    .unwrap();
    app.authorize_invocation(input()).await.unwrap();
    assert_eq!(db.queries.load(Ordering::SeqCst), 1);
    db.queries.store(0, Ordering::SeqCst);
    let mut forged = input();
    forged.originator = "bot-c".into();
    assert_eq!(
        app.authorize_invocation(forged).await,
        Err(AgentInvokeError::Forbidden)
    );
    assert_eq!(db.queries.load(Ordering::SeqCst), 0);
}

const REQUEST_ID: &str = "00000000-0000-4000-8000-000000000001";
async fn seed_request(f: &Fixture, creator: &str, kind: &str) {
    f.db.execute(DbStatement::with_params("INSERT INTO permission_requests (request_id,env,from_id,to_id,request_kind,requested_ref_id,status,created_by) VALUES (?, 'dev','bot-a','bot-b','agent_invoke',0,'pending',?)",vec![REQUEST_ID.into(),creator.into()])).await.unwrap();
    f.db.execute(DbStatement::with_params("INSERT INTO agent_invoke_requests (request_id,scope_id,version,creator_kind,source_ownership_version,source_authority_sequence,edge_version,from_binding_generation,to_binding_generation,from_eligibility_generation,to_eligibility_generation) SELECT ?,id,1,?,1,0,0,1,1,1,1 FROM agent_invoke_scopes",vec![REQUEST_ID.into(),kind.into()])).await.unwrap();
}
fn decision(actor: &str) -> AgentInvokeCommand {
    command(
        actor,
        AgentInvokeChange::Approve {
            request_id: REQUEST_ID.into(),
            expected_version: nz(1),
        },
    )
}
fn digest(principal: &AgentInvokePrincipal) -> String {
    use sha2::{Digest, Sha256};
    let kind = match principal {
        AgentInvokePrincipal::Governance(_) => "governance",
        AgentInvokePrincipal::Human(_) => "human",
        AgentInvokePrincipal::Agent(_) => "agent",
    };
    Sha256::digest(
        serde_json::json!([kind, principal.id()])
            .to_string()
            .as_bytes(),
    )
    .iter()
    .map(|b| format!("{b:02x}"))
    .collect()
}
async fn delegate(f: &Fixture, principal: &AgentInvokePrincipal, actions: &str) {
    f.db.execute(DbStatement::with_params("INSERT INTO agent_invoke_governance (scope_id,principal_hash,principal_id,from_id,to_id,revision,actions_json,updated_at_ms) SELECT id,?,?,'bot-a','bot-b',1,?,1 FROM agent_invoke_scopes",vec![digest(principal).into(),principal.id().into(),actions.into()])).await.unwrap();
}

#[tokio::test]
async fn original_requester_authority_is_not_borrowed_from_deciding_owner() {
    let f = Fixture::new().await;
    seed_request(&f, "human_manager", "human").await;
    let c = decision("human_target");
    let before = f.store.management_snapshot(&scope(), &c).await.unwrap();
    assert_eq!(
        before.request_creator,
        Some(AgentInvokePrincipal::Human("human_manager".into()))
    );
    assert!(before.requester_source_authorized);
    f.sql("UPDATE edge_grants SET status='revoked' WHERE from_id='human_manager'")
        .await;
    let lost = f.store.management_snapshot(&scope(), &c).await.unwrap();
    assert!(!lost.requester_source_authorized);
    assert_eq!(lost.target_role, Some(AgentInvokeRole::Owner));
    assert_ne!(before.fence, lost.fence);
    f.sql("UPDATE edge_grants SET status='approved' WHERE from_id='human_manager'")
        .await;
    f.sql("UPDATE bcs_bots SET is_deleted=1 WHERE bot_uuid='human_manager'")
        .await;
    assert!(
        !f.store
            .management_snapshot(&scope(), &c)
            .await
            .unwrap()
            .requester_source_authorized
    );
}

#[tokio::test]
async fn request_kind_scope_pair_and_metadata_fail_closed() {
    for sql in [
        "UPDATE permission_requests SET request_kind='connect'",
        "UPDATE permission_requests SET to_id='bot-other'",
        "UPDATE permission_requests SET env='other'",
        "DELETE FROM permission_requests",
    ] {
        let f = Fixture::new().await;
        seed_request(&f, "human_manager", "human").await;
        f.sql(sql).await;
        assert_eq!(
            f.store
                .management_snapshot(&scope(), &decision("human_target"))
                .await,
            Err(AgentInvokeError::NotFound),
            "{sql}"
        );
    }
    for sql in [
        "DELETE FROM agent_invoke_requests",
        "UPDATE agent_invoke_requests SET scope_id=99",
        "UPDATE agent_invoke_requests SET creator_kind='agent'",
        "UPDATE permission_requests SET requested_rules='[]'",
        "UPDATE permission_requests SET requested_ref_id=99",
        "UPDATE permission_requests SET status='approved'",
        "UPDATE permission_requests SET created_by=''",
    ] {
        let f = Fixture::new().await;
        seed_request(&f, "human_manager", "human").await;
        f.sql(sql).await;
        assert_eq!(
            f.store
                .management_snapshot(&scope(), &decision("human_target"))
                .await,
            Err(AgentInvokeError::InvalidState),
            "{sql}"
        );
    }
}

#[tokio::test]
async fn governance_delegation_is_exact_kind_principal_pair_and_action() {
    let f = Fixture::new().await;
    let principal = AgentInvokePrincipal::Governance("governor".into());
    delegate(&f, &principal, "[\"grant\"]").await;
    let mut c = command(
        "human_target",
        AgentInvokeChange::Grant {
            expected_version: 0,
        },
    );
    c.principal = principal;
    let snapshot = f.store.management_snapshot(&scope(), &c).await.unwrap();
    assert_eq!(snapshot.delegated_actions, vec![AgentInvokeAction::Grant]);
    assert_eq!(
        AgentInvokePolicy.authorize_change(&scope(), &c, &snapshot),
        Ok(())
    );
    c.change = AgentInvokeChange::Request;
    let snapshot = f.store.management_snapshot(&scope(), &c).await.unwrap();
    assert_eq!(
        AgentInvokePolicy.authorize_change(&scope(), &c, &snapshot),
        Err(AgentInvokeError::Forbidden)
    );
    c.principal = AgentInvokePrincipal::Governance("other-governor".into());
    assert!(
        f.store
            .management_snapshot(&scope(), &c)
            .await
            .unwrap()
            .delegated_actions
            .is_empty()
    );
    c.principal = AgentInvokePrincipal::Agent("governor".into());
    assert!(
        f.store
            .management_snapshot(&scope(), &c)
            .await
            .unwrap()
            .delegated_actions
            .is_empty()
    );
    f.sql("UPDATE agent_invoke_governance SET to_id='bot-other'")
        .await;
    c.principal = AgentInvokePrincipal::Governance("governor".into());
    assert!(
        f.store
            .management_snapshot(&scope(), &c)
            .await
            .unwrap()
            .delegated_actions
            .is_empty()
    );
}

#[tokio::test]
async fn malformed_governance_or_principal_hash_collision_is_invalid_not_permission() {
    for sql in [
        "UPDATE agent_invoke_governance SET principal_id='other'",
        "UPDATE agent_invoke_governance SET actions_json='[\"grant\",\"grant\"]'",
        "UPDATE agent_invoke_governance SET actions_json='[\"future\"]'",
        "UPDATE agent_invoke_governance SET actions_json='invalid'",
    ] {
        let f = Fixture::new().await;
        let principal = AgentInvokePrincipal::Governance("governor".into());
        delegate(&f, &principal, "[\"grant\"]").await;
        f.sql(sql).await;
        let mut c = command(
            "human_target",
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
        );
        c.principal = principal;
        assert_eq!(
            f.store.management_snapshot(&scope(), &c).await,
            Err(AgentInvokeError::InvalidState),
            "{sql}"
        );
    }
}

#[tokio::test]
async fn governance_requester_needs_current_request_delegation_not_approver_authority() {
    let f = Fixture::new().await;
    let principal = AgentInvokePrincipal::Governance("request-governor".into());
    delegate(&f, &principal, "[\"request\"]").await;
    seed_request(&f, "request-governor", "governance").await;
    let c = decision("human_target");
    let before = f.store.management_snapshot(&scope(), &c).await.unwrap();
    assert!(before.requester_source_authorized);
    assert_eq!(before.request_creator, Some(principal));
    f.sql("UPDATE agent_invoke_governance SET actions_json='[]', revision=2")
        .await;
    let lost = f.store.management_snapshot(&scope(), &c).await.unwrap();
    assert!(!lost.requester_source_authorized);
    assert_ne!(before.fence, lost.fence);
}

#[tokio::test]
async fn human_registration_replacement_changes_the_management_fence() {
    let f = Fixture::new().await;
    let c = command("human_manager", AgentInvokeChange::Request);
    let before = f.store.management_snapshot(&scope(), &c).await.unwrap();
    f.sql("DELETE FROM bcs_bots WHERE bot_uuid='human_manager'")
        .await;
    f.sql("INSERT INTO bcs_bots (bot_uuid,name,env,actor_kind,is_deleted) VALUES ('human_manager','replacement','dev','human',0)").await;
    let after = f.store.management_snapshot(&scope(), &c).await.unwrap();
    assert_eq!(before.source_role, after.source_role);
    assert_ne!(
        before.fence, after.fence,
        "physical Human identity is not just a reused actor string"
    );
}

#[tokio::test]
async fn legacy_profile_rules_and_public_visibility_do_not_replace_this_approval() {
    let f = Fixture::new().await;
    f.sql("UPDATE bcs_bots SET visibility='public' WHERE bot_uuid='bot-b'")
        .await;
    f.sql("INSERT INTO edge_grants (env,from_id,to_id,grant_kind,grant_ref_id,rules,status,originator_policy_type) VALUES ('dev','bot-a','bot-b','rules',7,'[]','approved','any')").await;
    assert_eq!(
        f.app.authorize_invocation(input()).await,
        Err(AgentInvokeError::Forbidden)
    );
    f.sql("INSERT INTO edge_grants (env,from_id,to_id,grant_kind,grant_ref_id,status,originator_policy_type) VALUES ('dev','bot-a','bot-b','permission_profile',9,'approved','any')").await;
    assert_eq!(
        f.app.authorize_invocation(input()).await,
        Err(AgentInvokeError::Forbidden)
    );
}
