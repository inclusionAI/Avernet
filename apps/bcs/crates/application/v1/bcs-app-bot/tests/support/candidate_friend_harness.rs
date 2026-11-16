//! Shared SQLite edge/profile harness for the v1 candidate friend-read tests.
//!
//! The application entry points run over an actual `DbConnectService` on
//! SQLite. A `DbPlugin` wrapper counts friend-page statements, profile reads
//! and can inject per-page failures/row corruption so a failed read can never
//! become a successful empty or partial friend set. Control-plane, registry
//! and projection stages are counted separately so the search budget is not
//! mistaken for the friend-page cost.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::HashSet;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_app_bot::{BotServiceConfig, BotServiceImpl};
use bcs_bot::{BotCandidateSearchCore, EmptyWorkerProfileCoreService};
use bcs_bot_store::MemoryBotRepo;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionOptions,
    DbTransactionStep, DbTransactionStepResult, DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission::DbConnectService;
use bcs_edge_permission_store::{
    DbBotActorConfigStore, DbEdgeGrantStore, DbPermissionProfileStore, DbPermissionRequestStore,
};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity, BotCandidate,
    BotCandidatePurpose, BotCandidateSearchResult, BotService, ListBotCandidates, Page,
    SearchBotCandidates,
};
use bcs_service_api::port::repo::PermissionProfileRepoPort;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCandidateReadQuery, BotCapabilities, BotControlPlaneCandidate,
    BotControlPlaneCoreService, BotControlPlaneDescriptor, BotControlPlaneOwnedQuery,
    BotControlPlanePatch, BotControlPlaneRecord, BotControlPlaneView, BotRegistryCoreService,
    BotSearchCandidateQuery, EnsureHumanResult, NoopFriendAuthSyncPort,
    NoopFriendConnectNotificationPort, RegisteredBot, ServiceResult, UserVisibility,
    WorkerRecommendCommand, WorkerRecommendResult, WorkerRecommendation,
};
use bcs_service_api::core::WorkerProfileCoreService;
use bcs_test_support::{authority_hook_for, grant_owner_edge, NoopFriendCoreService};

pub const ACTING_BOT: &str = "acting";
pub const ACTOR_HUMAN: &str = "staff-1";
pub const NO_PAGE: usize = 0;
pub const NO_CORRUPTION: usize = 0;
pub const DRIFT_TOTAL: usize = 1;
pub const MISSING_PAGE: usize = 2;
pub const DUPLICATE_ID: usize = 3;
pub const MALFORMED_ROW: usize = 4;
pub const FRIEND_PAGE_SQL_PREFIX: &str = "WITH friend_ids AS";

#[derive(Default, Clone, Copy, Debug)]
pub struct Reads {
    pub friend_pages: usize,
    pub friend_rows: usize,
    pub profile_queries: usize,
}

pub struct CountingDb {
    inner: Arc<dyn DbPlugin>,
    reads: Mutex<Reads>,
    fail_page: AtomicUsize,
    corrupt_page: AtomicUsize,
    corruption: AtomicUsize,
    duplicate_id: Mutex<Option<String>>,
}

impl CountingDb {
    fn new(inner: Arc<dyn DbPlugin>) -> Self {
        Self {
            inner,
            reads: Mutex::new(Reads::default()),
            fail_page: AtomicUsize::new(NO_PAGE),
            corrupt_page: AtomicUsize::new(NO_PAGE),
            corruption: AtomicUsize::new(NO_CORRUPTION),
            duplicate_id: Mutex::new(None),
        }
    }

    fn reset_reads(&self) {
        *self.reads.lock().expect("reads lock") = Reads::default();
    }

    pub fn fail_page(&self, page: usize) {
        self.fail_page.store(page, Ordering::SeqCst);
    }

    pub fn corrupt_page(&self, page: usize, corruption: usize, duplicate_id: Option<&str>) {
        self.corrupt_page.store(page, Ordering::SeqCst);
        self.corruption.store(corruption, Ordering::SeqCst);
        *self.duplicate_id.lock().expect("duplicate id lock") =
            duplicate_id.map(str::to_string);
    }

    pub fn reads(&self) -> Reads {
        *self.reads.lock().expect("reads lock")
    }
}

#[async_trait]
impl DbPlugin for CountingDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let sql = statement.sql().to_string();
        let is_friend_page = sql.starts_with(FRIEND_PAGE_SQL_PREFIX);
        let is_profile_read = !is_friend_page && sql.contains("permission_profiles");
        let page = if is_friend_page {
            self.reads.lock().expect("reads lock").friend_pages + 1
        } else {
            NO_PAGE
        };
        if is_friend_page && self.fail_page.load(Ordering::SeqCst) == page {
            // Reuse a real plugin failure without fabricating a successful read.
            return self
                .inner
                .query(DbStatement::new("SELECT * FROM missing_friend_page"))
                .await;
        }
        let mut rows = self.inner.query(statement).await?;
        {
            let mut reads = self.reads.lock().expect("reads lock");
            if is_friend_page {
                reads.friend_pages += 1;
                reads.friend_rows += rows.len();
            }
            if is_profile_read {
                reads.profile_queries += 1;
            }
        }
        if is_friend_page && self.corrupt_page.load(Ordering::SeqCst) == page {
            let corruption = self.corruption.load(Ordering::SeqCst);
            match corruption {
                DRIFT_TOTAL => {
                    for row in rows.iter_mut() {
                        rewrite(row, "total", DbValue::from(102_u64));
                    }
                }
                MISSING_PAGE => {
                    for row in rows.iter_mut() {
                        rewrite(row, "peer_id", DbValue::Null);
                    }
                }
                DUPLICATE_ID => {
                    let duplicate = self
                        .duplicate_id
                        .lock()
                        .expect("duplicate id lock")
                        .clone()
                        .expect("duplicate id");
                    for row in rows.iter_mut() {
                        rewrite(row, "peer_id", DbValue::from(duplicate.clone()));
                    }
                }
                MALFORMED_ROW => {
                    for row in rows.iter_mut() {
                        rewrite(row, "total", DbValue::from("not-a-number"));
                    }
                }
                _ => {}
            }
        }
        Ok(rows)
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }

    async fn transaction_with_options(
        &self,
        steps: Vec<DbTransactionStep>,
        options: DbTransactionOptions,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction_with_options(steps, options).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

fn rewrite(row: &mut DbRow, column: &str, value: DbValue) {
    let mut columns = row.columns().clone();
    columns.insert(column.to_string(), value);
    *row = DbRow::new(columns);
}

/// Registry double that keeps the projection stages and the authorized
/// search's strict backfill separately observable.
pub struct CountingRegistry {
    pub avatar_batches: Arc<AtomicUsize>,
    pub runtime_batches: Arc<AtomicUsize>,
    pub registry_batches: Arc<AtomicUsize>,
    bots: Vec<RegisteredBot>,
}

#[async_trait]
impl BotRegistryCoreService for CountingRegistry {
    async fn register(
        &self,
        _bot_id: String,
        _capabilities: BotCapabilities,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str) -> bool {
        false
    }

    async fn get(&self, _bot_id: &str) -> Option<RegisteredBot> {
        None
    }

    async fn get_agent_credentials(
        &self,
        _bot_id: &str,
    ) -> Option<bcs_service_api::AgentCredentials> {
        None
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn list_uninitialized_bots_by_creator(
        &self,
        _created_by: &str,
        _limit: usize,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        Ok(Vec::new())
    }

    async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn discover(&self, _query: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_skills(&self, _skills: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_domains(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_scopes(&self, _scopes: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn unregister(&self, _bot_id: &str) -> bool {
        false
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(
        &self,
        _bot_id: &str,
        _caps: &BotCapabilities,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(&self, _bot_id: &str, _visibility: &str) -> ServiceResult<()> {
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_actor_status(
        &self,
        _bot_id: &str,
        _status: ActorStatus,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn ensure_human_actor(
        &self,
        _staff_no: &str,
        _nick_name: &str,
    ) -> ServiceResult<EnsureHumanResult> {
        Ok(EnsureHumanResult { created: false })
    }

    async fn has_been_onboarded(&self, _bot_id: &str) -> bool {
        false
    }

    async fn save_created_by(
        &self,
        _bot_id: &str,
        _created_by: &str,
        _overwrite: bool,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn save_token(&self, _bot_id: &str, _token: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn load_token(&self, _bot_id: &str) -> Option<String> {
        None
    }

    async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
        None
    }

    async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
        Err(())
    }

    async fn reconnect_streaming(&self, _existing_token: String) -> Result<(String, String), ()> {
        Err(())
    }

    async fn disconnect_streaming(&self, _bot_id: &str) {}

    async fn is_connected(&self, _bot_id: &str) -> bool {
        false
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Err(())
    }

    async fn list_connected(&self) -> Vec<String> {
        Vec::new()
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
        token
    }

    async fn try_get_many(&self, bot_ids: &[String]) -> ServiceResult<Vec<RegisteredBot>> {
        self.registry_batches.fetch_add(1, Ordering::SeqCst);
        let mut seen = HashSet::new();
        // Preserve REQUEST order (deduplicated, missing ids skipped), matching
        // the production strict-read contract the semantic branch relies on.
        Ok(bot_ids
            .iter()
            .filter(|bot_id| seen.insert(bot_id.as_str()))
            .filter_map(|bot_id| {
                self.bots
                    .iter()
                    .find(|bot| &bot.bot_uuid == bot_id)
                    .cloned()
            })
            .collect())
    }

    async fn avatar_urls(
        &self,
        _bot_ids: &[String],
    ) -> ServiceResult<bcs_service_api::types::bot_profile::BotAvatarMap> {
        self.avatar_batches.fetch_add(1, Ordering::SeqCst);
        Ok(Default::default())
    }

    async fn list_runtime_active_bot_ids(&self, _bot_ids: &[String]) -> Vec<String> {
        self.runtime_batches.fetch_add(1, Ordering::SeqCst);
        Vec::new()
    }
}

/// Authoritative control-plane double: records the list, search and projection
/// batch reads so every candidate-search stage stays separately countable.
pub struct RecordingCandidateCore {
    acting: BotControlPlaneRecord,
    candidates: Vec<BotControlPlaneRecord>,
    pub queries: Mutex<Vec<BotCandidateReadQuery>>,
    pub search_queries: Mutex<Vec<BotSearchCandidateQuery>>,
    pub batch_queries: Mutex<Vec<Vec<String>>>,
}

#[async_trait]
impl BotControlPlaneCoreService for RecordingCandidateCore {
    async fn get_record(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        Ok(Some(self.acting.clone()))
    }

    async fn get(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        unreachable!("candidate reads use the candidate query")
    }

    async fn get_by_ids(
        &self,
        bot_ids: &[String],
        _env: &str,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        self.batch_queries
            .lock()
            .expect("batch queries lock")
            .push(bot_ids.to_vec());
        Ok(bot_ids
            .iter()
            .filter_map(|bot_id| {
                self.candidates
                    .iter()
                    .find(|record| &record.bot_id == bot_id)
                    .map(|record| BotControlPlaneView {
                        record: record.clone(),
                        provider: None,
                    })
            })
            .collect())
    }

    async fn list_candidates(
        &self,
        query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        self.queries
            .lock()
            .expect("candidate queries lock")
            .push(query.clone());
        let records = self
            .candidates
            .iter()
            .filter(|record| query.friend_ids.contains(&record.bot_id))
            .map(|record| BotControlPlaneCandidate {
                bot: BotControlPlaneView {
                    record: record.clone(),
                    provider: None,
                },
                is_friend: true,
            })
            .collect::<Vec<_>>();
        let total = records.len() as u64;
        let page = records
            .into_iter()
            .skip(query.offset as usize)
            .take(query.limit as usize)
            .collect();
        Ok((page, total))
    }

    async fn search_candidates(
        &self,
        query: BotSearchCandidateQuery,
    ) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        self.search_queries
            .lock()
            .expect("search queries lock")
            .push(query.clone());
        let records = self
            .candidates
            .iter()
            .filter(|record| {
                query
                    .bot_uuids
                    .as_ref()
                    .is_none_or(|ids| ids.contains(&record.bot_id))
            })
            .filter(|record| {
                query
                    .name
                    .as_ref()
                    .is_none_or(|name| record.name.contains(name.as_str()))
            })
            .take(query.limit as usize)
            .map(|record| BotControlPlaneCandidate {
                is_friend: query.friend_ids.contains(&record.bot_id),
                bot: BotControlPlaneView {
                    record: record.clone(),
                    provider: None,
                },
            })
            .collect::<Vec<_>>();
        let total = records.len() as u64;
        Ok((records, total))
    }

    async fn list_by_creator(
        &self,
        _query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        unreachable!("candidate reads use the candidate query")
    }

    async fn patch(
        &self,
        _bot_id: &str,
        _env: &str,
        _patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        unreachable!("candidate reads use the candidate query")
    }
}

/// Worker-profile provider returning a fixed recall batch.
pub struct StaticRecommend {
    pub ids: Vec<String>,
}

#[async_trait]
impl WorkerProfileCoreService for StaticRecommend {
    async fn recommend_workers(
        &self,
        _command: WorkerRecommendCommand,
    ) -> ServiceResult<WorkerRecommendResult> {
        Ok(WorkerRecommendResult {
            recommendations: self
                .ids
                .iter()
                .enumerate()
                .map(|(index, id)| WorkerRecommendation {
                    worker_id: id.clone(),
                    score: 1.0 - index as f64 / 100.0,
                    short_profile: None,
                })
                .collect(),
            raw_response: serde_json::Value::Null,
        })
    }

    async fn batch_query_worker_profiles(
        &self,
        _worker_ids: &[String],
    ) -> ServiceResult<Vec<bcs_service_api::WorkerProfile>> {
        Ok(Vec::new())
    }
}

pub struct SeedFriend {
    pub id: String,
    pub kind: ActorKind,
    pub edge_status: &'static str,
    pub profile_active: bool,
    pub env: String,
}

pub fn approved_friend(id: &str, env: &str) -> SeedFriend {
    SeedFriend {
        id: id.to_string(),
        kind: ActorKind::Bot,
        edge_status: "approved",
        profile_active: true,
        env: env.to_string(),
    }
}

pub struct Harness {
    pub service: BotServiceImpl,
    pub db: Arc<CountingDb>,
    pub candidate: Arc<RecordingCandidateCore>,
    pub registry: Arc<CountingRegistry>,
    pub env: String,
}

impl Harness {
    pub fn reads(&self) -> Reads {
        self.db.reads()
    }

    /// Candidate-list control-plane reads.
    pub fn candidate_queries(&self) -> usize {
        self.candidate.queries.lock().expect("candidate queries lock").len()
    }

    /// Authorized-search control-plane reads (validation and name fallback).
    pub fn control_plane_searches(&self) -> usize {
        self.candidate
            .search_queries
            .lock()
            .expect("search queries lock")
            .len()
    }

    /// Projection-stage `get_by_ids` batch reads.
    pub fn projection_batches(&self) -> usize {
        self.candidate
            .batch_queries
            .lock()
            .expect("batch queries lock")
            .len()
    }

    /// Strict registry backfill batches issued by the authorized search Core.
    pub fn registry_batches(&self) -> usize {
        self.registry.registry_batches.load(Ordering::SeqCst)
    }

    pub fn avatar_batches(&self) -> usize {
        self.registry.avatar_batches.load(Ordering::SeqCst)
    }

    pub fn runtime_batches(&self) -> usize {
        self.registry.runtime_batches.load(Ordering::SeqCst)
    }

    pub async fn list(&self, offset: u64) -> Result<Page<BotCandidate>, ApplicationError> {
        self.service
            .list_candidates(ListBotCandidates {
                caller: human_caller(ACTOR_HUMAN),
                bot_id: ACTING_BOT.to_string(),
                purpose: BotCandidatePurpose::Collaboration,
                name: None,
                offset,
                limit: 20,
            })
            .await
    }

    pub async fn search(
        &self,
        query: Option<&str>,
    ) -> Result<BotCandidateSearchResult, ApplicationError> {
        self.service
            .search_candidates(SearchBotCandidates {
                caller: human_caller(ACTOR_HUMAN),
                bot_id: ACTING_BOT.to_string(),
                purpose: BotCandidatePurpose::Collaboration,
                query: query.map(str::to_string),
            })
            .await
    }
}

pub fn human_caller(staff_no: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-1".into()),
        user: Some(AuthenticatedUserIdentity {
            id: staff_no.to_string(),
            username: staff_no.to_string(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

pub fn record(bot_id: &str, kind: ActorKind, env: &str) -> BotControlPlaneRecord {
    BotControlPlaneRecord {
        bot_id: bot_id.to_string(),
        kind,
        name: bot_id.to_string(),
        visibility: "private".to_string(),
        status: ActorStatus::Online,
        env: env.to_string(),
        created_by: Some(ACTOR_HUMAN.to_string()),
        descriptor: BotControlPlaneDescriptor {
            summary: String::new(),
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
        },
        agent_code: None,
        task_claim_mode: false,
        task_dream_mode: false,
        created_at: 1,
        updated_at: 1,
        user_visibility: UserVisibility::Protected,
        friend_ext: Default::default(),
        friend_check_in_strategy: Default::default(),
    }
}

async fn create_schema(db: &Arc<dyn DbPlugin>) {
    db.execute(DbStatement::new(
        "CREATE TABLE edge_grants (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            env VARCHAR(32) NOT NULL, \
            from_id VARCHAR(128) NOT NULL, \
            to_id VARCHAR(128) NOT NULL, \
            grant_kind VARCHAR(32) NOT NULL, \
            grant_ref_id BIGINT NOT NULL, \
            rules TEXT, \
            status VARCHAR(16) NOT NULL DEFAULT 'approved', \
            originator_policy_type VARCHAR(32) NOT NULL DEFAULT 'any', \
            originator_policy_data TEXT, \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            UNIQUE (from_id, to_id, env, grant_kind, grant_ref_id))",
    ))
    .await
    .expect("create edge_grants");
    db.execute(DbStatement::new(
        "CREATE TABLE permission_profiles (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            bot_id VARCHAR(128) NOT NULL, \
            env VARCHAR(32) NOT NULL, \
            name VARCHAR(128) NOT NULL DEFAULT 'default', \
            description VARCHAR(512), \
            rules_template TEXT NOT NULL, \
            revision INTEGER NOT NULL DEFAULT 1, \
            digest VARCHAR(128) NOT NULL, \
            is_default INTEGER NOT NULL DEFAULT 0, \
            status VARCHAR(16) NOT NULL DEFAULT 'active', \
            created_by VARCHAR(128) NOT NULL, \
            updated_by VARCHAR(128), \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            UNIQUE (bot_id, env, is_default, status))",
    ))
    .await
    .expect("create permission_profiles");
}

async fn insert_edge(
    db: &Arc<dyn DbPlugin>,
    from: &str,
    to: &str,
    env: &str,
    ref_id: u64,
    status: &str,
) {
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants \
         (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
          originator_policy_type, originator_policy_data) \
         VALUES (?, ?, ?, 'permission_profile', ?, NULL, ?, 'any', NULL)",
        vec![
            DbValue::from(env),
            DbValue::from(from),
            DbValue::from(to),
            DbValue::from(ref_id),
            DbValue::from(status),
        ],
    ))
    .await
    .expect("insert edge");
}

pub async fn harness_with(
    seeds: Vec<SeedFriend>,
    worker_profiles: Arc<dyn WorkerProfileCoreService>,
) -> Harness {
    let env = bcs_config::resolve_env_str();
    let sqlite: Arc<dyn DbPlugin> =
        Arc::new(LocalSqliteDbPlugin::new().expect("local sqlite"));
    create_schema(&sqlite).await;
    let counting = Arc::new(CountingDb::new(sqlite.clone()));
    let db: Arc<dyn DbPlugin> = counting.clone();
    let profile_store = Arc::new(DbPermissionProfileStore::sqlite(db.clone()));
    let edge_store = Arc::new(DbEdgeGrantStore::sqlite(db.clone()));
    let request_store = Arc::new(DbPermissionRequestStore::sqlite(db.clone()));
    let config_store = Arc::new(DbBotActorConfigStore::sqlite(db.clone()));

    let mut candidates = Vec::new();
    for seed in seeds {
        let profile_id = profile_store
            .ensure_default_profile(&seed.id, &seed.env)
            .await
            .expect("seed default profile");
        if !seed.profile_active {
            db.execute(DbStatement::with_params(
                "UPDATE permission_profiles SET status = 'deleted' \
                 WHERE id = ?",
                vec![DbValue::from(profile_id)],
            ))
            .await
            .expect("deactivate default profile");
        }
        insert_edge(
            &db,
            ACTING_BOT,
            &seed.id,
            &seed.env,
            profile_id,
            seed.edge_status,
        )
        .await;
        if seed.kind == ActorKind::Bot {
            candidates.push(record(&seed.id, ActorKind::Bot, &env));
        }
    }

    let connect = Arc::new(DbConnectService::new(
        edge_store,
        profile_store,
        request_store,
        config_store,
        None,
        Arc::new(NoopFriendConnectNotificationPort),
        Arc::new(NoopFriendAuthSyncPort),
        env.clone(),
    ));
    let candidate = Arc::new(RecordingCandidateCore {
        acting: record(ACTING_BOT, ActorKind::Bot, &env),
        candidates: candidates.clone(),
        queries: Mutex::new(Vec::new()),
        search_queries: Mutex::new(Vec::new()),
        batch_queries: Mutex::new(Vec::new()),
    });
    let registry = Arc::new(CountingRegistry {
        avatar_batches: Arc::new(AtomicUsize::new(0)),
        runtime_batches: Arc::new(AtomicUsize::new(0)),
        registry_batches: Arc::new(AtomicUsize::new(0)),
        bots: candidates
            .iter()
            .map(|record| RegisteredBot {
                bot_uuid: record.bot_id.clone(),
                capabilities: BotCapabilities {
                    name: Some(record.name.clone()),
                    visibility: record.visibility.clone(),
                    ..Default::default()
                },
                env: Some(record.env.clone()),
                created_by: record.created_by.clone(),
                actor_kind: record.kind,
                status: record.status,
            })
            .collect(),
    });
    let search: Arc<dyn bcs_service_api::BotCandidateSearchCoreService> =
        Arc::new(
            BotCandidateSearchCore::new(
                registry.clone(),
                Arc::new(NoopFriendCoreService),
                worker_profiles,
                0.0,
            )
            .with_control_plane(candidate.clone()),
        );

    let authority_repo = Arc::new(MemoryBotRepo::new());
    grant_owner_edge(&authority_repo, ACTING_BOT, ACTOR_HUMAN).await;
    let service = BotServiceImpl::new(
        candidate.clone(),
        registry.clone(),
        Arc::new(NoopFriendCoreService),
        connect,
        search,
        BotServiceConfig { env: env.clone() },
    )
    .with_bot_authority_hook(authority_hook_for(authority_repo));
    counting.reset_reads();
    Harness {
        service,
        db: counting,
        candidate,
        registry,
        env,
    }
}

pub async fn harness(seeds: Vec<SeedFriend>) -> Harness {
    harness_with(seeds, Arc::new(EmptyWorkerProfileCoreService)).await
}

pub async fn friends_with_profile(
    count: usize,
    worker_profiles: Arc<dyn WorkerProfileCoreService>,
) -> Harness {
    let env = bcs_config::resolve_env_str();
    let seeds = (0..count)
        .map(|index| approved_friend(&format!("friend-{index:04}"), &env))
        .collect();
    harness_with(seeds, worker_profiles).await
}

pub async fn friends_harness(count: usize) -> Harness {
    friends_with_profile(count, Arc::new(EmptyWorkerProfileCoreService)).await
}
