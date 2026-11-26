//! Unit tests for real SQL assembly. Not a Singlebox or external-host proof.
use super::*;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{application::agent_invoke::*, types::agent_invoke::*};
use bcs_test_support::contract::agent_invoke::AgentInvokeContractDriver;
#[path = "../../../../services/bcs-edge-permission-store/tests/support/agent_invoke_sql_driver.rs"]
mod fixture;

fn registration(env: &str) -> AgentInvokeRegistration {
    let n = std::num::NonZeroU32::MIN;
    AgentInvokeRegistration { scope: AgentInvokeScope { namespace: format!("isolated-{env}"), env: env.into() },
        tenant: None, governance_apps: BTreeMap::new(), limits: AgentInvokeLimits {
            max_out_degree: n, max_in_degree: n, max_pending_out: n, max_pending_in: n },
        max_fact_ttl_ms: NonZeroU64::new(60_000).unwrap(), direct_limits: AgentInvokeDirectLimits {
            default_timeout_ms: NonZeroU64::new(1_000).unwrap(), max_timeout_ms: NonZeroU64::new(60_000).unwrap() } }
}
async fn database() -> Arc<LocalSqliteDbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db
}
async fn count(db: &dyn DbPlugin, table: &str) -> u64 {
    let rows = db.query(bcs_db_api::DbStatement::new(format!("SELECT COUNT(*) AS n FROM {table}"))).await.unwrap();
    bcs_db_api::db_get_column(&rows[0], "n").unwrap()
}
#[tokio::test]
async fn absent_registration_reads_no_schema_and_adds_no_hook_even_with_shared_chain() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap()); // NO migration/schema.
    let mut chain = Arc::new(InterceptorChain::new());
    let shared = chain.clone();
    assert!(AgentInvokeWiring::select(None, db.clone(), DbSqlFlavor::Sqlite, "local", &mut chain).await.unwrap().is_none());
    assert!(Arc::ptr_eq(&chain, &shared));
    assert!(chain.is_empty());
    assert_eq!(count(db.as_ref(), "sqlite_master").await, 0);
}
#[tokio::test]
async fn invalid_registrations_fail_before_any_database_effect_or_hook() {
    let valid = registration("local");
    let mut values = Vec::new();
    let mut r = valid.clone(); r.scope.env = "foreign".into(); values.push(r);
    let mut r = valid.clone(); r.tenant = Some("*".into()); values.push(r);
    let mut r = valid.clone(); r.tenant = Some(" ".into()); values.push(r);
    let mut r = valid.clone(); r.governance_apps.insert(0, "governor".into()); values.push(r);
    let mut r = valid.clone(); r.governance_apps.insert(7, "*".into()); values.push(r);
    let mut r = valid.clone(); r.governance_apps.insert(7, " ".into()); values.push(r);
    let mut r = valid.clone(); r.governance_apps.insert(7, "a".repeat(257)); values.push(r);
    let mut r = valid.clone(); r.direct_limits.default_timeout_ms = NonZeroU64::new(60_001).unwrap(); values.push(r);
    let mut r = valid.clone(); r.direct_limits.max_timeout_ms = NonZeroU64::new(86_400_001).unwrap(); values.push(r);
    let mut r = valid.clone(); r.scope.namespace = " whitespace ".into(); values.push(r);
    let mut r = valid.clone(); r.max_fact_ttl_ms = NonZeroU64::new(u64::MAX).unwrap(); values.push(r);
    for r in values {
        let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
        let mut chain = Arc::new(InterceptorChain::new());
        assert!(AgentInvokeWiring::select(Some(r), db.clone(), DbSqlFlavor::Sqlite, "local", &mut chain).await.is_err());
        assert!(chain.is_empty());
        assert_eq!(count(db.as_ref(), "sqlite_master").await, 0);
    }
}
#[tokio::test]
async fn already_shared_chain_is_rejected_before_scope_creation() {
    let db = database().await;
    let mut chain = Arc::new(InterceptorChain::new());
    let _retained = chain.clone();
    assert!(AgentInvokeWiring::select(Some(registration("local")), db.clone(), DbSqlFlavor::Sqlite, "local", &mut chain).await.is_err());
    assert_eq!(count(db.as_ref(), "agent_invoke_scopes").await, 0);
}
#[tokio::test]
async fn explicit_registration_creates_only_closed_scope_and_never_invents_facts() {
    let db = database().await;
    let mut chain = Arc::new(InterceptorChain::new());
    Arc::get_mut(&mut chain).unwrap().push(bcs_routing::security::SecurityInterceptor::new(
        Arc::new(bcs_security_gateway_local::NoopSecurityGateway), false));
    let old_hooks = chain.len();
    let wiring = AgentInvokeWiring::select(Some(registration("local")), db.clone(), DbSqlFlavor::Sqlite, "local", &mut chain)
        .await.unwrap().unwrap();
    assert_eq!(chain.len(), old_hooks + 1, "retain existing security/instrumentation chain");
    let closed = wiring.store.scope_state().await.unwrap();
    assert!(!closed.writer_enabled && !closed.runtime_enabled);
    assert_eq!(closed.revision, NonZeroU64::MIN);
    for table in ["agent_invoke_facts", "agent_invoke_governance", "agent_invoke_grants", "agent_invoke_requests", "agent_invoke_audit", "agent_invoke_receipts"] {
        assert_eq!(count(db.as_ref(), table).await, 0, "{table}");
    }
}
#[tokio::test]
async fn reopen_preserves_explicit_gates_and_rejects_other_limits_or_namespace() {
    let db = database().await;
    let r = registration("local");
    let mut chain = Arc::new(InterceptorChain::new());
    let wiring = AgentInvokeWiring::select(Some(r.clone()), db.clone(), DbSqlFlavor::Sqlite, "local", &mut chain).await.unwrap().unwrap();
    let active = wiring.store.set_enabled(NonZeroU64::MIN, true, true).await.unwrap(); // trusted test host ONLY.
    let mut new_chain = Arc::new(InterceptorChain::new());
    let reopened = AgentInvokeWiring::select(Some(r.clone()), db.clone(), DbSqlFlavor::Sqlite, "local", &mut new_chain).await.unwrap().unwrap();
    assert_eq!(reopened.store.scope_state().await.unwrap(), active, "reopen neither resets nor enables gates");
    for namespace in [false, true] {
        let mut changed = r.clone();
        if namespace { changed.scope.namespace.push_str("-other"); }
        else { changed.limits.max_out_degree = std::num::NonZeroU32::new(2).unwrap(); }
        let mut chain = Arc::new(InterceptorChain::new());
        assert!(AgentInvokeWiring::select(Some(changed), db.clone(), DbSqlFlavor::Sqlite, "local", &mut chain).await.is_err());
        assert!(chain.is_empty());
        assert_eq!(reopened.store.scope_state().await.unwrap(), active);
    }
    assert_eq!(count(db.as_ref(), "agent_invoke_scopes").await, 1);
}
#[tokio::test]
async fn actual_sql_approval_and_revoke_are_seen_by_the_composed_operation_hook() {
    let db = database().await;
    let d = fixture::Driver::new_on(db.clone(), DbSqlFlavor::Sqlite, "local").await;
    for bot in ["source", "target"] { d.register(bot).await; }
    d.role("human_source", "source", Some(AgentInvokeRole::Owner)).await;
    d.role("human_target", "target", Some(AgentInvokeRole::Owner)).await;
    let mut chain = Arc::new(InterceptorChain::new());
    let wiring = AgentInvokeWiring::select(Some(registration("local")), db, DbSqlFlavor::Sqlite, "local", &mut chain).await.unwrap().unwrap();
    let input = AgentInvocationInput { authentication: AgentInvocationAuthentication::TrustedInProcess,
        principal: AgentInvokePrincipal::Agent("source".into()), authenticated_scope: d.scope.clone(),
        pair: AgentInvokePair { from_bot_id: "source".into(), to_bot_id: "target".into() },
        originator: "source".into(), operation: AgentInvokeOperation::DirectInvocation };
    assert_eq!(chain.authorize_agent_invocation(&input).await, Err(AgentInvokeError::Forbidden));
    let grant = wiring.management.change(AgentInvokeCommand { principal: AgentInvokePrincipal::Human("human_target".into()),
        authenticated_scope: d.scope.clone(), pair: input.pair.clone(), change: AgentInvokeChange::Grant { expected_version: 0 },
        idempotency_key: "composed-grant".into(), reason: "explicit synthetic host test".into() }).await.unwrap();
    let approved = grant.original_grant.unwrap();
    let permit = chain.authorize_agent_invocation(&input).await.unwrap();
    assert_eq!(permit.ref_id, approved.edge_id.get());
    assert_eq!(permit.revision, Some(approved.version.get()));
    wiring.management.change(AgentInvokeCommand { principal: AgentInvokePrincipal::Human("human_target".into()),
        authenticated_scope: d.scope, pair: input.pair.clone(), change: AgentInvokeChange::Revoke { edge_id: approved.edge_id, expected_version: approved.version },
        idempotency_key: "composed-revoke".into(), reason: "current shared SQL revoke".into() }).await.unwrap();
    assert_eq!(chain.authorize_agent_invocation(&input).await, Err(AgentInvokeError::Forbidden));
}
