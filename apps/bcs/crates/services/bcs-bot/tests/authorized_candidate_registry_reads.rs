//! Strict registry backfill of the authorized candidate search.
//!
//! The authorized entry validates one bounded recall batch against the
//! authoritative control plane and then hydrates `RegisteredBot` rows through
//! `try_get_many`. The tests below run the real SQLite registry (cold process
//! cache) behind a counting `DbPlugin` so the backfill stage's SQL budget is
//! observable: 1 and 20 authorized ids must each be ONE batched statement,
//! never a per-id `SELECT`. Control-plane reads run through a stub whose own
//! call count is recorded separately, so the registry delta is not confused
//! with control-plane/projection cost. A registry database failure must
//! propagate as `Err` instead of producing a successful empty page or a name
//! fallback, while genuinely missing ids are skipped.

use bcs_db_migrations as bootstrap_migrations;

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_bot::{BotCandidateSearchCore, BotCore};
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::core::WorkerProfileCoreService;
use bcs_service_api::{
    ActorKind, ActorStatus, AuthorizedCandidateSearchQuery, BotCandidateReadQuery,
    BotCandidateSearchCoreService, BotCandidateSearchMode, BotCandidateVisibility, BotCapabilities,
    BotControlPlaneCandidate, BotControlPlaneCoreService, BotControlPlaneDescriptor,
    BotControlPlaneOwnedQuery, BotControlPlanePatch, BotControlPlaneRecord, BotControlPlaneView,
    BotRepoPort, BotSearchCandidateQuery, FriendCheckInStrategy,
    FriendCoreService, ServiceError, ServiceResult, UserVisibility, WorkerProfile,
    WorkerRecommendCommand, WorkerRecommendResult, WorkerRecommendation,
};

/// Counting `DbPlugin` wrapper over the real SQLite plugin. Writes are
/// forwarded so seeding through the repository works; read queries, their rows
/// and their SQL text are recorded so the backfill batch is observable. The
/// failure toggle makes every later read fail like an unavailable database.
struct CountReads {
    db: Arc<LocalSqliteDbPlugin>,
    queries: AtomicUsize,
    rows: AtomicUsize,
    statements: Mutex<Vec<String>>,
    fail_queries: AtomicBool,
}

#[async_trait]
impl DbPlugin for CountReads {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        if self.fail_queries.load(Ordering::SeqCst) {
            return Err(DbError::Backend("injected registry database failure".into()));
        }
        self.queries.fetch_add(1, Ordering::SeqCst);
        self.statements
            .lock()
            .unwrap()
            .push(statement.sql().to_string());
        let rows = self.db.query(statement).await?;
        self.rows.fetch_add(rows.len(), Ordering::SeqCst);
        Ok(rows)
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.db.execute(statement).await
    }
    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.db.transaction(steps).await
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        self.db.health_check().await
    }
}

/// Complete committed SQLite migration chain, then the persisted registry over
/// the counting wrapper. `BotCore` is not connected to any runtime, so every
/// registry read is a cold-cache database read.
async fn fixture() -> (
    Arc<LocalSqliteDbPlugin>,
    Arc<CountReads>,
    Arc<PersistentBotRepo>,
    BotCore,
) {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    let counted = Arc::new(CountReads {
        db: db.clone(),
        queries: AtomicUsize::new(0),
        rows: AtomicUsize::new(0),
        statements: Mutex::new(Vec::new()),
        fail_queries: AtomicBool::new(false),
    });
    let repo = Arc::new(PersistentBotRepo::with_sql_flavor(
        counted.clone(),
        DbSqlFlavor::Sqlite,
    ));
    let core = BotCore::with_repo(repo.clone());
    (db, counted, repo, core)
}

async fn seed_bot(repo: &PersistentBotRepo, id: &str) {
    assert!(repo
        .create_registration_if_absent(
            id.to_string(),
            BotCapabilities {
                name: Some(id.to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
            "owner",
            &format!("token-{id}"),
        )
        .await
        .unwrap());
}

/// Authoritative control-plane stub: returns the configured ids as
/// authorized candidates (no database access), recording each query.
struct StubControlPlane {
    ids: Vec<String>,
    calls: AtomicUsize,
    queries: Mutex<Vec<BotSearchCandidateQuery>>,
}

impl StubControlPlane {
    fn returning(ids: Vec<String>) -> Self {
        Self {
            ids,
            calls: AtomicUsize::new(0),
            queries: Mutex::new(Vec::new()),
        }
    }

    fn calls(&self) -> usize {
        self.calls.load(Ordering::SeqCst)
    }

    fn queries(&self) -> Vec<BotSearchCandidateQuery> {
        self.queries.lock().unwrap().clone()
    }
}

fn authorized_record(id: &str, created_at: u64) -> BotControlPlaneRecord {
    BotControlPlaneRecord {
        bot_id: id.to_string(),
        kind: ActorKind::Bot,
        name: id.to_string(),
        visibility: "public".to_string(),
        status: ActorStatus::Online,
        env: bcs_config::resolve_env_str(),
        created_by: Some("owner".to_string()),
        descriptor: BotControlPlaneDescriptor {
            summary: String::new(),
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
        },
        agent_code: None,
        task_claim_mode: false,
        task_dream_mode: false,
        created_at,
        updated_at: created_at,
        user_visibility: UserVisibility::Public,
        friend_ext: Default::default(),
        friend_check_in_strategy: FriendCheckInStrategy::Open,
    }
}

#[async_trait]
impl BotControlPlaneCoreService for StubControlPlane {
    async fn get_record(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        Err(ServiceError::InternalError(
            "stub control plane record read is not configured".into(),
        ))
    }

    async fn get(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        Err(ServiceError::InternalError(
            "stub control plane read is not configured".into(),
        ))
    }

    async fn get_by_ids(
        &self,
        _bot_ids: &[String],
        _env: &str,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        Err(ServiceError::InternalError(
            "stub control plane batch read is not configured".into(),
        ))
    }

    async fn list_candidates(
        &self,
        _query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        Err(ServiceError::InternalError(
            "stub control plane list is not configured".into(),
        ))
    }

    async fn search_candidates(
        &self,
        query: BotSearchCandidateQuery,
    ) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        self.queries.lock().unwrap().push(query.clone());
        let records = self
            .ids
            .iter()
            .enumerate()
            .filter(|(_, id)| {
                query
                    .bot_uuids
                    .as_ref()
                    .is_none_or(|ids| ids.contains(id))
            })
            .map(|(index, id)| {
                let record = authorized_record(id, index as u64 + 1);
                let is_friend = query.friend_ids.contains(id);
                BotControlPlaneCandidate {
                    bot: BotControlPlaneView {
                        record,
                        provider: None,
                    },
                    is_friend,
                }
            })
            .collect::<Vec<_>>();
        let total = records.len() as u64;
        Ok((records, total))
    }

    async fn list_by_creator(
        &self,
        _query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        Err(ServiceError::InternalError(
            "stub control plane creator list is not configured".into(),
        ))
    }

    async fn patch(
        &self,
        _bot_id: &str,
        _env: &str,
        _patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        Err(ServiceError::InternalError(
            "stub control plane patch is not configured".into(),
        ))
    }
}

/// Provider stub returning the recall batch in the configured order.
struct StaticRecommend {
    ids: Vec<String>,
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
    ) -> ServiceResult<Vec<WorkerProfile>> {
        Ok(Vec::new())
    }
}

struct NoFriends;

#[async_trait]
impl FriendCoreService for NoFriends {
    async fn list_friends(&self, _bot_id: &str) -> Vec<String> {
        Vec::new()
    }

    async fn are_friends(&self, _bot_a: &str, _bot_b: &str) -> bool {
        false
    }

    async fn are_all_friends(&self, _bot_id: &str, others: &[String]) -> ServiceResult<()> {
        if others.is_empty() {
            Ok(())
        } else {
            Err(ServiceError::NotFriends(others.to_vec()))
        }
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        Ok(0)
    }
}

fn authorized_query(limit: usize) -> AuthorizedCandidateSearchQuery {
    AuthorizedCandidateSearchQuery {
        query: "batch query".to_string(),
        acting_actor_id: "acting".to_string(),
        env: bcs_config::resolve_env_str(),
        viewer_kind: ActorKind::Human,
        visibility: BotCandidateVisibility::Discovery,
        friend_ids: Default::default(),
        limit,
    }
}

fn service_with(
    registry: BotCore,
    control_plane: Arc<StubControlPlane>,
    provider_ids: &[String],
) -> BotCandidateSearchCore {
    BotCandidateSearchCore::new(
        Arc::new(registry),
        Arc::new(NoFriends),
        Arc::new(StaticRecommend {
            ids: provider_ids.to_vec(),
        }),
        0.0,
    )
    .with_control_plane(control_plane)
}

async fn assert_single_batch_backfill(
    count: &CountReads,
    control_plane: &StubControlPlane,
    service: &BotCandidateSearchCore,
    ids: &[String],
) {
    let before_queries = count.queries.load(Ordering::SeqCst);
    let before_statements = count.statements.lock().unwrap().len();

    let result = service
        .search_authorized_candidates(authorized_query(ids.len()))
        .await
        .expect("authorized semantic search");

    assert_eq!(result.mode, BotCandidateSearchMode::Semantic);
    assert_eq!(
        result
            .hits
            .iter()
            .map(|hit| hit.bot.bot_uuid.as_str())
            .collect::<Vec<_>>(),
        ids.iter().map(String::as_str).collect::<Vec<_>>()
    );

    // The registry backfill is ONE batched SELECT for the whole recall batch.
    assert_eq!(count.queries.load(Ordering::SeqCst) - before_queries, 1);
    let statements = count.statements.lock().unwrap();
    let new_statements = &statements[before_statements..];
    assert_eq!(new_statements.len(), 1);
    assert!(
        new_statements[0].contains("bot_uuid IN ("),
        "registry backfill must be one batched IN query, got: {}",
        new_statements[0]
    );

    // Control-plane reads are counted separately from the registry stage.
    assert_eq!(control_plane.calls(), 1);
    let queries = control_plane.queries();
    assert_eq!(queries[0].bot_uuids.as_deref(), Some(ids));
    assert_eq!(queries[0].limit, ids.len() as u64);
}

#[tokio::test]
async fn cold_cache_backfill_reads_one_bot_in_one_batch_query() {
    let (_db, count, repo, registry) = fixture().await;
    let ids = vec!["batch-one".to_string()];
    for id in &ids {
        seed_bot(&repo, id).await;
    }
    let control_plane = Arc::new(StubControlPlane::returning(ids.clone()));
    let service = service_with(registry, control_plane.clone(), &ids);

    assert_single_batch_backfill(&count, &control_plane, &service, &ids).await;
}

#[tokio::test]
async fn cold_cache_backfill_reads_twenty_bots_in_one_batch_query() {
    let (_db, count, repo, registry) = fixture().await;
    let ids: Vec<String> = (0..20).map(|index| format!("batch-{index}")).collect();
    for id in &ids {
        seed_bot(&repo, id).await;
    }
    let control_plane = Arc::new(StubControlPlane::returning(ids.clone()));
    let service = service_with(registry, control_plane.clone(), &ids);

    assert_single_batch_backfill(&count, &control_plane, &service, &ids).await;
}

#[tokio::test]
async fn genuinely_missing_registry_ids_are_skipped() {
    let (_db, _count, repo, registry) = fixture().await;
    seed_bot(&repo, "present").await;
    let authorized = vec!["present".to_string(), "missing".to_string()];
    let control_plane = Arc::new(StubControlPlane::returning(authorized.clone()));
    let service = service_with(registry, control_plane, &authorized);

    let result = service
        .search_authorized_candidates(authorized_query(20))
        .await
        .expect("missing registry id is skipped, not an error");

    assert_eq!(result.mode, BotCandidateSearchMode::Semantic);
    assert_eq!(
        result
            .hits
            .iter()
            .map(|hit| hit.bot.bot_uuid.as_str())
            .collect::<Vec<_>>(),
        vec!["present"]
    );
}

#[tokio::test]
async fn registry_database_failure_propagates_without_name_fallback() {
    let (_db, count, repo, registry) = fixture().await;
    let ids = vec!["failing-a".to_string(), "failing-b".to_string()];
    for id in &ids {
        seed_bot(&repo, id).await;
    }
    let control_plane = Arc::new(StubControlPlane::returning(ids.clone()));
    let service = service_with(registry, control_plane.clone(), &ids);
    count.fail_queries.store(true, Ordering::SeqCst);

    let error = service
        .search_authorized_candidates(authorized_query(20))
        .await
        .expect_err("registry read failure must propagate");

    assert!(matches!(error, ServiceError::InternalError(_)));
    // Only the semantic authorization query ran; a registry failure must not
    // be swallowed into a successful empty result or a name fallback.
    assert_eq!(control_plane.calls(), 1);
    let queries = control_plane.queries();
    assert!(queries[0].bot_uuids.is_some());
    assert_eq!(queries[0].name, None);
}

#[tokio::test]
async fn authorized_registry_rows_keep_provider_order_and_friend_markers() {
    let (_db, _count, repo, registry) = fixture().await;
    for id in ["order-a", "order-b", "order-c"] {
        seed_bot(&repo, id).await;
    }
    let provider_ids = vec![
        "order-a".to_string(),
        "order-b".to_string(),
        "order-c".to_string(),
    ];
    let control_plane = Arc::new(StubControlPlane::returning(provider_ids.clone()));
    let mut query = authorized_query(20);
    query.friend_ids = ["order-b".to_string()].into_iter().collect();
    let service = service_with(registry, control_plane, &provider_ids);

    let result = service
        .search_authorized_candidates(query)
        .await
        .expect("authorized semantic search");

    assert_eq!(
        result
            .hits
            .iter()
            .map(|hit| (hit.bot.bot_uuid.as_str(), hit.is_friend, hit.score.is_some()))
            .collect::<Vec<_>>(),
        vec![
            ("order-a", false, true),
            ("order-b", true, true),
            ("order-c", false, true)
        ]
    );
}

#[tokio::test]
async fn empty_recall_batch_never_queries_registry() {
    let (_db, count, repo, registry) = fixture().await;
    seed_bot(&repo, "unused").await;
    let control_plane = Arc::new(StubControlPlane::returning(Vec::new()));
    // The provider returns nothing, so the entry falls back to the name query;
    // the control plane stub answers with its empty id set and no registry
    // batch is attempted.
    let service = service_with(registry, control_plane.clone(), &[]);
    let before = count.queries.load(Ordering::SeqCst);

    let result = service
        .search_authorized_candidates(authorized_query(20))
        .await
        .expect("empty recall falls back to name search");

    assert_eq!(result.mode, BotCandidateSearchMode::NameFallback);
    assert!(result.hits.is_empty());
    assert_eq!(count.queries.load(Ordering::SeqCst), before);
    assert_eq!(control_plane.calls(), 1);
    assert!(control_plane.queries()[0].name_only);
}
