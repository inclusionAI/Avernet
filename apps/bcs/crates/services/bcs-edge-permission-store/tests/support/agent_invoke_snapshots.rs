//! Shared snapshot-only fixture. Seeding SQL is not a production command path.
#![allow(dead_code)]
use async_trait::async_trait;
use bcs_db_api::*;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission::agent_invoke::{AgentInvokeApplicationService, AgentInvokePolicy};
use bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore;
use bcs_service_api::application::agent_invoke::AgentInvocationService;
use bcs_service_api::port::agent_invoke::AgentInvokeFactsPort;
use bcs_service_api::port::repo::agent_invoke::{
    AgentInvokeCommandRepoPort, AgentInvokeFactRepoPort,
};
use bcs_service_api::types::agent_invoke::*;
use std::num::{NonZeroU32, NonZeroU64};
use std::sync::Arc;

pub fn nz(v: u64) -> NonZeroU64 {
    NonZeroU64::new(v).unwrap()
}
pub fn scope() -> AgentInvokeScope {
    AgentInvokeScope {
        namespace: "sql-snapshot".into(),
        env: "dev".into(),
    }
}
pub fn pair() -> AgentInvokePair {
    AgentInvokePair {
        from_bot_id: "bot-a".into(),
        to_bot_id: "bot-b".into(),
    }
}
pub fn input() -> AgentInvocationInput {
    AgentInvocationInput {
        authentication: AgentInvocationAuthentication::TrustedInProcess,
            principal: AgentInvokePrincipal::Agent("bot-a".into()),
        authenticated_scope: scope(),
        pair: pair(),
        originator: "bot-a".into(),
        operation: AgentInvokeOperation::DirectInvocation,
    }
}
pub fn command(principal: &str, change: AgentInvokeChange) -> AgentInvokeCommand {
    AgentInvokeCommand {
        principal: AgentInvokePrincipal::Human(principal.into()),
        authenticated_scope: scope(),
        pair: pair(),
        change,
        idempotency_key: "snapshot-key".into(),
        reason: "snapshot fixture".into(),
    }
}
pub struct NoCommands;
#[async_trait]
impl AgentInvokeCommandRepoPort for NoCommands {
    async fn execute(
        &self,
        _: &AgentInvokeScope,
        _: AgentInvokeCommand,
        _: AgentInvokeManagementSnapshot,
    ) -> AgentInvokeResult<AgentInvokeReceipt> {
        panic!("runtime authorization must never execute a management command")
    }
}
pub struct Fixture {
    pub db: Arc<dyn DbPlugin>,
    pub store: Arc<DbAgentInvokeStore>,
    pub app: AgentInvokeApplicationService,
    pub scope: AgentInvokeScope,
}
impl Fixture {
    pub async fn new() -> Self {
        let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
        bcs_db_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        Self::new_on(db, DbSqlFlavor::Sqlite, scope()).await
    }
    pub async fn new_on(
        db: Arc<dyn DbPlugin>,
        flavor: DbSqlFlavor,
        scope: AgentInvokeScope,
    ) -> Self {
        let n = NonZeroU32::new(2).unwrap();
        let store = Arc::new(
            DbAgentInvokeStore::new(
                db.clone(),
                flavor,
                scope.clone(),
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
        store.initialize_scope().await.unwrap();
        for bot in ["bot-a", "bot-b"] {
            db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, ownership_version, visibility, is_deleted) VALUES (?, ?, ?, 'bot', 1, 'protected', 0)", vec![bot.into(), bot.into(), scope.env.as_str().into()])).await.unwrap();
            store
                .publish_fact(
                    &scope,
                    AgentInvokeFactPublication {
                        state: AgentInvokeBotState {
                            bot_id: bot.into(),
                            binding_generation: nz(1),
                            eligibility_generation: nz(1),
                            eligible: true,
                            active: true,
                            hidden: false,
                        },
                        expected_revision: 0,
                        valid_until_ms: store.database_time_ms().await.unwrap() + 30_000,
                    },
                )
                .await
                .unwrap();
        }
        for human in [
            "human_source",
            "human_target",
            "human_manager",
            "human_target-manager",
        ] {
            db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, is_deleted) VALUES (?, ?, ?, 'human', 0)", vec![human.into(), human.into(), scope.env.as_str().into()])).await.unwrap();
        }
        for (actor, bot, kind) in [
            ("human_source", "bot-a", "owner"),
            ("human_target", "bot-b", "owner"),
            ("human_manager", "bot-a", "manager"),
            ("human_target-manager", "bot-b", "manager"),
        ] {
            db.execute(DbStatement::with_params("INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, originator_policy_type, originator_policy_data) VALUES (?, ?, ?, ?, 0, NULL, 'approved', 'same_as_from', NULL)", vec![scope.env.as_str().into(), actor.into(), bot.into(), kind.into()])).await.unwrap();
        }
        store
            .set_enabled(store.scope_state().await.unwrap().revision, true, true)
            .await
            .unwrap();
        let app = AgentInvokeApplicationService::new(
            scope.clone(),
            Arc::new(AgentInvokePolicy),
            store.clone(),
            Arc::new(NoCommands),
        )
        .unwrap();
        Self {
            db,
            store,
            app,
            scope,
        }
    }
    pub async fn grant(&self) -> u64 {
        self.db.execute(DbStatement::with_params("INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, originator_policy_type, originator_policy_data) VALUES (?, 'bot-a', 'bot-b', 'agent_invoke', 0, NULL, 'approved', 'same_as_from', NULL)", vec![self.scope.env.as_str().into()])).await.unwrap();
        let rows = self.db.query(DbStatement::with_params("SELECT id FROM edge_grants WHERE env=? AND from_id='bot-a' AND to_id='bot-b' AND grant_kind='agent_invoke' AND grant_ref_id=0", vec![self.scope.env.as_str().into()])).await.unwrap();
        let id: u64 = db_get_column(&rows[0], "id").unwrap();
        self.db.execute(DbStatement::with_params("INSERT INTO agent_invoke_grants (edge_id, scope_id, version, from_binding_generation, to_binding_generation, from_eligibility_generation, to_eligibility_generation, created_at_ms, updated_at_ms) SELECT ?, id, 1, 1, 1, 1, 1, 1, 1 FROM agent_invoke_scopes WHERE namespace = ? AND env = ?", vec![id.into(), self.scope.namespace.as_str().into(), self.scope.env.as_str().into()])).await.unwrap();
        self.db.execute(DbStatement::with_params("UPDATE agent_invoke_grants SET from_registration_id=(SELECT id FROM bcs_bots WHERE bot_uuid='bot-a' AND env=?),to_registration_id=(SELECT id FROM bcs_bots WHERE bot_uuid='bot-b' AND env=?),from_registration_revision=1,to_registration_revision=1 WHERE edge_id=?",vec![self.scope.env.as_str().into(),self.scope.env.as_str().into(),id.into()])).await.unwrap();
        id
    }
    pub async fn sql(&self, sql: &str) {
        self.db.execute(DbStatement::new(sql)).await.unwrap();
    }
}

#[async_trait]
impl bcs_test_support::contract::agent_invoke_snapshots::AgentInvokeSnapshotFixture for Fixture {
    fn scope(&self) -> AgentInvokeScope {
        self.scope.clone()
    }
    fn facts(&self) -> &dyn AgentInvokeFactsPort {
        self.store.as_ref()
    }
    fn invocation(&self) -> &dyn AgentInvocationService {
        &self.app
    }
    async fn prepare(&self) {
        self.grant().await;
    }
    async fn lose_source_qualification(&self) {
        let old = self
            .store
            .read_fact(&self.scope, "bot-a")
            .await
            .unwrap()
            .unwrap();
        let mut state = old.state;
        state.eligible = false;
        state.eligibility_generation =
            NonZeroU64::new(state.eligibility_generation.get() + 1).unwrap();
        self.store
            .publish_fact(
                &self.scope,
                AgentInvokeFactPublication {
                    state,
                    expected_revision: old.revision.get(),
                    valid_until_ms: self.store.database_time_ms().await.unwrap() + 30_000,
                },
            )
            .await
            .unwrap();
    }
}
