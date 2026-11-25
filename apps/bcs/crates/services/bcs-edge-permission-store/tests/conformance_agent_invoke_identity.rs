//! Real SQLite credential-to-registration/epoch handoff and pure policy fences.
use bcs_edge_permission::agent_invoke::AgentInvokePolicy;
use bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore;
use bcs_service_api::application::agent_invoke::AgentInvokeGrantManagementService;
use bcs_db_api::DbPlugin;
use bcs_service_api::core::agent_invoke::AgentInvokePolicyCoreService;
use bcs_service_api::port::agent_invoke::{
    AgentInvokeFactsPort, AgentInvokeIdentityResolutionPort,
};
use bcs_service_api::types::agent_invoke::*;
use bcs_test_support::contract::agent_invoke::AgentInvokeContractDriver;
#[path = "support/agent_invoke_sql_driver.rs"]
mod driver;

async fn setup() -> driver::Driver {
    let d = driver::Driver::new().await;
    for bot in ["identity-a", "identity-b", "identity-c"] {
        d.register(bot).await;
    }
    d.role("human_source", "identity-a", Some(AgentInvokeRole::Owner))
        .await;
    d.role("human_target", "identity-b", Some(AgentInvokeRole::Owner))
        .await;
    // Trusted initial fixture, not a production registry mutation proof.
    d.sql(
        "UPDATE bcs_bots SET agent_code='Code' WHERE bot_uuid IN ('identity-a','identity-c')",
        vec![],
    )
    .await;
    d
}
fn claim() -> AgentInvokeVerifiedBot {
    AgentInvokeVerifiedBot {
        bot_id: "identity-a".into(),
        agent_code: "Code".into(),
    }
}
fn input(d: &driver::Driver, source: AgentInvokeAuthenticatedSource) -> AgentInvocationInput {
    AgentInvocationInput {
        authentication: AgentInvocationAuthentication::VerifiedSource(source),
        principal: AgentInvokePrincipal::Agent("identity-a".into()),
        authenticated_scope: d.scope.clone(),
        pair: AgentInvokePair {
            from_bot_id: "identity-a".into(),
            to_bot_id: "identity-b".into(),
        },
        originator: "identity-a".into(),
        operation: AgentInvokeOperation::DirectInvocation,
    }
}
#[tokio::test]
async fn conformance_agent_invoke_identity_resolution() {
    let d = setup().await;
    let store: &DbAgentInvokeStore = d.store.as_ref();
    bcs_test_support::contract::agent_invoke::agent_invoke_identity_resolution_port_contract_tests(
        store,
        d.scope.clone(),
        claim(),
    )
    .await;
}
#[tokio::test]
async fn physical_and_qualification_binding_cannot_change_between_resolution_and_authorization() {
    let d = setup().await;
    let resolved = d
        .store
        .resolve_invocation_source(&d.scope, &claim())
        .await
        .unwrap();
    let input = input(&d, resolved);
    d.service
        .change(AgentInvokeCommand {
            principal: AgentInvokePrincipal::Human("human_target".into()),
            authenticated_scope: d.scope.clone(),
            pair: input.pair.clone(),
            change: AgentInvokeChange::Grant {
                expected_version: 0,
            },
            idempotency_key: "grant".into(),
            reason: "test".into(),
        })
        .await
        .unwrap();
    let snapshot = d
        .store
        .runtime_snapshot(&d.scope, &input.pair)
        .await
        .unwrap();
    assert!(
        AgentInvokePolicy
            .authorize_invocation(&d.scope, &input, &snapshot)
            .is_ok()
    );
    for scenario in [
        "registration-id",
        "identity-revision",
        "binding",
        "eligibility",
    ] {
        let mut changed = snapshot.clone();
        let next = |n: std::num::NonZeroU64| std::num::NonZeroU64::new(n.get() + 1).unwrap();
        match scenario {
            "registration-id" => {
                changed.source_registration.registration_id =
                    next(changed.source_registration.registration_id)
            }
            "identity-revision" => {
                changed.source_registration.identity_revision =
                    next(changed.source_registration.identity_revision)
            }
            "binding" => {
                changed.source.binding_generation = next(changed.source.binding_generation)
            }
            "eligibility" => {
                changed.source.eligibility_generation = next(changed.source.eligibility_generation)
            }
            _ => unreachable!(),
        }
        assert_eq!(
            AgentInvokePolicy.authorize_invocation(&d.scope, &input, &changed),
            Err(AgentInvokeError::Forbidden),
            "{scenario}"
        );
    }
}
#[tokio::test]
async fn resolver_is_exact_scoped_fallible_and_never_infers_eligibility_from_code() {
    for scenario in [
        "wrong-code",
        "null-code",
        "unknown-status",
        "future-lease",
        "wrong-id-case",
        "human",
        "deleted",
        "hidden",
        "ineligible",
        "expired",
        "rebound",
        "db-error",
        "disabled",
        "missing-facts",
    ] {
        let d = setup().await;
        let mut identity = claim();
        match scenario {
            "wrong-code" => identity.agent_code = "code".into(),
            "null-code" => d.sql("UPDATE bcs_bots SET agent_code=NULL WHERE bot_uuid='identity-a'",vec![]).await,
            "unknown-status" => d.sql("UPDATE bcs_bots SET status='corrupt' WHERE bot_uuid='identity-a'",vec![]).await,
            "future-lease" => d.sql("UPDATE agent_invoke_facts SET valid_until_ms=valid_until_ms+120000 WHERE bot_id='identity-a'",vec![]).await,
            "wrong-id-case" => identity.bot_id = "Identity-a".into(),
            "human" => d.sql("UPDATE bcs_bots SET actor_kind='human' WHERE bot_uuid='identity-a'", vec![]).await,
            "deleted" => d.sql("UPDATE bcs_bots SET is_deleted=1 WHERE bot_uuid='identity-a'", vec![]).await,
            "hidden" => d.sql("UPDATE bcs_bots SET status='hidden' WHERE bot_uuid='identity-a'", vec![]).await,
            "ineligible" => d.publish("identity-a", false, false).await,
            "expired" => d.sql("UPDATE agent_invoke_facts SET valid_until_ms=1 WHERE bot_id='identity-a'", vec![]).await,
            "rebound" => d.sql("UPDATE bcs_bots SET agent_invoke_identity_revision=agent_invoke_identity_revision+1 WHERE bot_uuid='identity-a'", vec![]).await,
            "db-error" => d.sql("DROP TABLE agent_invoke_facts", vec![]).await,
            "disabled" => {let state=d.store.scope_state().await.unwrap(); d.store.set_enabled(state.revision,false,false).await.unwrap();}
            "missing-facts" => d.sql("DELETE FROM agent_invoke_facts WHERE bot_id='identity-a'", vec![]).await,
            _ => unreachable!(),
        }
        assert!(
            d.store
                .resolve_invocation_source(&d.scope, &identity)
                .await
                .is_err(),
            "{scenario}"
        );
    }
}

struct ObservedDb {
    inner: bcs_db_local::LocalSqliteDbPlugin,
    reads: std::sync::Mutex<Vec<(bcs_db_api::DbStatement, usize)>>,
    writes: std::sync::atomic::AtomicUsize,
}
#[async_trait::async_trait]
impl DbPlugin for ObservedDb {
    async fn query(&self, statement:bcs_db_api::DbStatement)->bcs_db_api::DbResult<Vec<bcs_db_api::DbRow>> {
        let rows=self.inner.query(statement.clone()).await?;
        self.reads.lock().unwrap().push((statement,rows.len()));Ok(rows)
    }
    async fn execute(&self,s:bcs_db_api::DbStatement)->bcs_db_api::DbResult<bcs_db_api::DbExecuteResult>{self.writes.fetch_add(1,std::sync::atomic::Ordering::SeqCst);self.inner.execute(s).await}
    async fn transaction(&self,steps:Vec<bcs_db_api::DbTransactionStep>)->bcs_db_api::DbResult<Vec<bcs_db_api::DbTransactionStepResult>>{self.writes.fetch_add(1,std::sync::atomic::Ordering::SeqCst);self.inner.transaction(steps).await}
    async fn health_check(&self)->bcs_db_api::DbResult<bcs_db_api::DbHealth>{self.inner.health_check().await}
}
#[tokio::test]
async fn identity_resolution_cost_is_one_bounded_current_query_with_no_writes() {
    use bcs_db_api::DbSqlFlavor;
    let db=std::sync::Arc::new(ObservedDb{inner:bcs_db_local::LocalSqliteDbPlugin::new().unwrap(),reads:Default::default(),writes:Default::default()});
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let d=driver::Driver::new_on(db.clone(),DbSqlFlavor::Sqlite,"identity-cost").await;
    d.register("identity-a").await;
    d.sql("UPDATE bcs_bots SET agent_code='Code' WHERE bot_uuid='identity-a'",vec![]).await;
    db.reads.lock().unwrap().clear();
    let writes=db.writes.load(std::sync::atomic::Ordering::SeqCst);
    d.store.resolve_invocation_source(&d.scope,&claim()).await.unwrap();
    assert_eq!(db.writes.load(std::sync::atomic::Ordering::SeqCst),writes);
    let reads=db.reads.lock().unwrap().clone();
    assert_eq!(reads.len(),1);assert_eq!(reads[0].1,1);assert_eq!(reads[0].0.params().len(),6);
    assert!(reads[0].0.sql().ends_with("LIMIT 2"));assert!(!reads[0].0.sql().contains("OFFSET"));
    let plan=db.query(bcs_db_api::DbStatement::with_params(format!("EXPLAIN QUERY PLAN {}",reads[0].0.sql()),reads[0].0.params().to_vec())).await.unwrap();
    let details=format!("{plan:?}");
    assert!(!details.contains("SCAN b "),"no Bot table scan: {details}");
    assert!(!details.contains("SCAN f "),"no fact table scan: {details}");
}
