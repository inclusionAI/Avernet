//! Shared lifecycle fixture: real application, SQL facts and SQL command store.
//! Role/inventory seeding is trusted test setup, not a public management API.
#![allow(dead_code)]
use async_trait::async_trait;
use bcs_db_api::*;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission::agent_invoke::{AgentInvokeApplicationService, AgentInvokePolicy};
use bcs_edge_permission_store::agent_invoke::{DbAgentInvokeCommandStore, DbAgentInvokeStore};
use bcs_service_api::application::agent_invoke::{
    AgentInvocationService, AgentInvokeGrantManagementService,
};
use bcs_service_api::port::agent_invoke::AgentInvokeFactsPort;
use bcs_service_api::port::repo::agent_invoke::{
    AgentInvokeCommandRepoPort, AgentInvokeFactRepoPort, AgentInvokeGovernanceRepoPort,
};
use bcs_service_api::types::agent_invoke::*;
use bcs_test_support::contract::agent_invoke::AgentInvokeContractDriver;
use std::num::{NonZeroU32, NonZeroU64};
use std::sync::Arc;

pub struct Driver {
    pub db: Arc<dyn DbPlugin>,
    pub store: Arc<DbAgentInvokeStore>,
    pub commands: Arc<DbAgentInvokeCommandStore>,
    pub service: AgentInvokeApplicationService,
    pub scope: AgentInvokeScope,
    pub flavor: DbSqlFlavor,
}
impl Driver {
    pub async fn new() -> Self {
        let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
        bcs_db_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        Self::new_on(db, DbSqlFlavor::Sqlite, "sql-lifecycle").await
    }
    pub async fn new_on(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor, env: &str) -> Self {
        let scope = AgentInvokeScope {
            namespace: format!("isolated-{env}"),
            env: env.into(),
        };
        let one = NonZeroU32::MIN;
        let store = Arc::new(
            DbAgentInvokeStore::new(
                db.clone(),
                flavor,
                scope.clone(),
                AgentInvokeLimits {
                    max_out_degree: one,
                    max_in_degree: one,
                    max_pending_out: one,
                    max_pending_in: one,
                },
                NonZeroU64::new(60_000).unwrap(),
            )
            .unwrap(),
        );
        store.initialize_scope().await.unwrap();
        store
            .set_enabled(NonZeroU64::MIN, true, true)
            .await
            .unwrap();
        let policy = Arc::new(AgentInvokePolicy);
        let commands = Arc::new(DbAgentInvokeCommandStore::new(
            store.clone(),
            policy.clone(),
        ));
        let service = AgentInvokeApplicationService::new(
            scope.clone(),
            policy,
            store.clone(),
            commands.clone(),
        )
        .unwrap();
        Self {
            db,
            store,
            commands,
            service,
            scope,
            flavor,
        }
    }
    pub async fn delegate(
        &self,
        principal: &AgentInvokePrincipal,
        pair: &AgentInvokePair,
        expected_revision: u64,
        actions: Vec<AgentInvokeAction>,
        reason: &str,
    ) -> AgentInvokeResult<NonZeroU64> {
        self.store
            .publish_delegation(
                &self.scope,
                AgentInvokeDelegationPublication {
                    principal: principal.clone(),
                    pair: pair.clone(),
                    expected_revision,
                    actions,
                    reason: reason.into(),
                },
            )
            .await
    }
    pub async fn sql(&self, sql: &str, params: Vec<DbValue>) {
        self.db
            .execute(DbStatement::with_params(sql, params))
            .await
            .unwrap();
    }
    pub async fn human(&self, id: &str) {
        let insert = if self.flavor == DbSqlFlavor::Mysql {
            "INSERT IGNORE"
        } else {
            "INSERT OR IGNORE"
        };
        self.sql(&format!("{insert} INTO bcs_bots (bot_uuid,name,env,actor_kind,is_deleted) VALUES (?,?,?,'human',0)"),vec![id.into(),id.into(),self.scope.env.as_str().into()]).await;
    }
    pub async fn seed_role(&self, human: &str, bot: &str, role: Option<AgentInvokeRole>) {
        self.human(human).await;
        self.human("human_fixture_successor").await;
        let env = self.scope.env.as_str();
        let existing=self.db.query(DbStatement::with_params("SELECT grant_kind FROM edge_grants WHERE env=? AND from_id=? AND to_id=? AND grant_kind IN ('owner','manager') AND status='approved'",vec![env.into(),human.into(),bot.into()])).await.unwrap();
        let was_owner = existing
            .iter()
            .any(|row| row.get_string("grant_kind").unwrap().as_deref() == Some("owner"));
        self.sql("UPDATE edge_grants SET status='revoked' WHERE env=? AND from_id=? AND to_id=? AND grant_kind IN ('owner','manager')",vec![env.into(),human.into(),bot.into()]).await;
        if role == Some(AgentInvokeRole::Owner) {
            self.sql("UPDATE edge_grants SET status='revoked' WHERE env=? AND to_id=? AND grant_kind='owner'",vec![env.into(),bot.into()]).await;
        }
        let mut grants = Vec::new();
        if was_owner && role != Some(AgentInvokeRole::Owner) {
            grants.push(("human_fixture_successor", "owner"));
        }
        if let Some(role) = role {
            grants.push((
                human,
                if role == AgentInvokeRole::Owner {
                    "owner"
                } else {
                    "manager"
                },
            ));
        }
        for (actor, kind) in grants {
            let insert = if self.flavor == DbSqlFlavor::Mysql {
                "INSERT IGNORE"
            } else {
                "INSERT OR IGNORE"
            };
            self.sql(&format!("{insert} INTO edge_grants (env,from_id,to_id,grant_kind,grant_ref_id,rules,status,originator_policy_type) VALUES (?,?,?,?,0,NULL,'revoked','same_as_from')"),vec![env.into(),actor.into(),bot.into(),kind.into()]).await;
            self.sql("UPDATE edge_grants SET status='approved' WHERE env=? AND from_id=? AND to_id=? AND grant_kind=? AND grant_ref_id=0",vec![env.into(),actor.into(),bot.into(),kind.into()]).await;
        }
        self.sql("INSERT INTO bot_authority_changes (env,bot_id,mutation_id,sequence,action,actor_kind,actor_id,subject_id) VALUES (?,?,?,1,'fixture_role_change','human','human_fixture_successor',?)",vec![env.into(),bot.into(),uuid::Uuid::new_v4().to_string().into(),human.into()]).await;
    }
    pub async fn qualify(&self, bot: &str, eligible: bool, rebind: bool) {
        let old = self.store.read_fact(&self.scope, bot).await.unwrap();
        let mut state = old
            .as_ref()
            .map(|f| f.state.clone())
            .unwrap_or(AgentInvokeBotState {
                bot_id: bot.into(),
                binding_generation: NonZeroU64::MIN,
                eligibility_generation: NonZeroU64::MIN,
                eligible: true,
                active: true,
                hidden: false,
            });
        if let Some(old) = &old {
            if state.eligible != eligible {
                state.eligibility_generation =
                    NonZeroU64::new(old.state.eligibility_generation.get() + 1).unwrap();
            }
            if rebind {
                state.binding_generation =
                    NonZeroU64::new(old.state.binding_generation.get() + 1).unwrap();
            }
        }
        state.eligible = eligible;
        self.store
            .publish_fact(
                &self.scope,
                AgentInvokeFactPublication {
                    state,
                    expected_revision: old.map_or(0, |f| f.revision.get()),
                    valid_until_ms: self.store.database_time_ms().await.unwrap() + 30_000,
                },
            )
            .await
            .unwrap();
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
        self.commands.as_ref()
    }
    async fn register(&self, bot: &str) {
        self.sql("INSERT INTO bcs_bots (bot_uuid,name,env,actor_kind,ownership_version,visibility,is_deleted) VALUES (?,?,?,'bot',1,'protected',0)",vec![bot.into(),bot.into(),self.scope.env.as_str().into()]).await;
        self.qualify(bot, true, false).await;
    }
    async fn role(&self, human: &str, bot: &str, role: Option<AgentInvokeRole>) {
        self.seed_role(human, bot, role).await;
    }
    async fn publish(&self, bot: &str, eligible: bool, rebind: bool) {
        self.qualify(bot, eligible, rebind).await;
    }
    async fn audit_count(&self) -> usize {
        let rows=self.db.query(DbStatement::with_params("SELECT COUNT(*) AS n FROM agent_invoke_audit WHERE scope_id=(SELECT id FROM agent_invoke_scopes WHERE namespace=? AND env=?) AND action<>'qualification_publish'",vec![self.scope.namespace.as_str().into(),self.scope.env.as_str().into()])).await.unwrap();
        db_get_column::<u64>(&rows[0], "n").unwrap() as usize
    }
}
