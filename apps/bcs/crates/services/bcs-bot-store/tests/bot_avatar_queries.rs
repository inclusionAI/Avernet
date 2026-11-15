//! Store-level query budget of the single-batch avatar read (bot-avatar
//! plan Task 4).
//!
//! The batched `read_avatar_batch` runs over a counting `DbPlugin` wrapper
//! so its DB access cost is observable: a deduplicated page of at most 256
//! distinct ids is EXACTLY one SELECT that projects only
//! `bot_uuid, avatar_url`, an over-limit input is rejected before any
//! query, and an empty page queries nothing. The core-level chunking on
//! top of this contract (whole participant pages, dedupe, one query per
//! store batch) is covered by `services/bcs-bot/tests/bot_profile.rs`.

use bcs_db_migrations as bootstrap_migrations;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_bot_store::PersistentBotRepo;
use bcs_service_api::types::bot_profile::{AvatarChange, ProfileWrite};
use bcs_service_api::{BotCapabilities, BotRepoPort, ServiceError};

/// Counting `DbPlugin` wrapper over the real SQLite plugin: standalone read
/// queries are counted (and their statements kept for projection checks)
/// while writes are forwarded, so seeding registrations and avatars through
/// the repository is never blocked.
struct CountReads {
    db: Arc<LocalSqliteDbPlugin>,
    queries: AtomicUsize,
    rows: AtomicUsize,
    statements: Mutex<Vec<String>>,
}

#[async_trait]
impl DbPlugin for CountReads {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
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

/// The complete committed migration chain (including `040_bot_avatar`), then
/// the persisted repo over the counting wrapper.
async fn fixture() -> (Arc<LocalSqliteDbPlugin>, Arc<CountReads>, PersistentBotRepo) {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    let counted = Arc::new(CountReads {
        db: db.clone(),
        queries: AtomicUsize::new(0),
        rows: AtomicUsize::new(0),
        statements: Mutex::new(Vec::new()),
    });
    let repo = PersistentBotRepo::with_sql_flavor(counted.clone(), DbSqlFlavor::Sqlite);
    (db, counted, repo)
}

/// Register one bot and store its avatar through the Task 3 store
/// contract. Both are write paths (forwarded, not counted), so the read
/// counters stay at zero until an avatar page is actually queried.
async fn seed_avatar(repo: &PersistentBotRepo, id: &str, url: &str) {
    assert!(repo
        .create_registration_if_absent(
            id.to_string(),
            BotCapabilities::default(),
            "owner",
            &format!("token-{id}"),
        )
        .await
        .unwrap());
    assert_eq!(
        repo.update_avatar_existing(id, AvatarChange::Set(url.to_string()))
            .await
            .unwrap(),
        ProfileWrite::Updated
    );
}

#[tokio::test]
async fn one_avatar_batch_reads_256_distinct_ids_in_a_single_projected_query() {
    let (_db, count, repo) = fixture().await;
    // 257 registered bots: a submitted page of 256 must read exactly its
    // own 256 rows — never the whole table.
    let ids: Vec<String> = (0..257).map(|index| format!("avatar-{index}")).collect();
    for id in &ids {
        seed_avatar(&repo, id, &format!("https://example.com/{id}.png")).await;
    }

    let page: Vec<String> = ids.iter().take(256).cloned().collect();
    let before_queries = count.queries.load(Ordering::SeqCst);
    let before_rows = count.rows.load(Ordering::SeqCst);

    // Duplicate ids collapse before the bound: one deduplicated batch.
    let mut duplicated = page.clone();
    duplicated.push(page[0].clone());
    let avatars = repo.read_avatar_batch(&duplicated).await.unwrap();
    assert_eq!(avatars.len(), 256);
    assert_eq!(count.queries.load(Ordering::SeqCst) - before_queries, 1);
    assert_eq!(count.rows.load(Ordering::SeqCst) - before_rows, 256);
    assert_eq!(
        avatars["avatar-0"].as_deref(),
        Some("https://example.com/avatar-0.png")
    );
    assert_eq!(
        avatars["avatar-255"].as_deref(),
        Some("https://example.com/avatar-255.png")
    );

    // The one query projects only the display columns of the submitted page:
    // 256 id parameters plus the env parameter, with the human exclusion in
    // place and no sensitive column in the projection.
    let statements = count.statements.lock().unwrap().clone();
    assert_eq!(statements.len(), 1);
    let sql = &statements[0];
    assert!(sql.contains("SELECT bot_uuid, avatar_url FROM bcs_bots"));
    assert!(sql.contains("env = ?"));
    assert!(sql.contains("actor_kind"));
    assert_eq!(sql.matches('?').count(), 257);
    for secret in ["session_token", "agent_token", "agent_code", "bot_info"] {
        assert!(!sql.contains(secret), "avatar read must not project {secret}");
    }
}

#[tokio::test]
async fn one_avatar_id_is_one_bounded_query() {
    let (_db, count, repo) = fixture().await;
    seed_avatar(&repo, "avatar-one", "https://example.com/one.png").await;
    let before = count.queries.load(Ordering::SeqCst);
    let avatars = repo
        .read_avatar_batch(&["avatar-one".to_string()])
        .await
        .unwrap();
    assert_eq!(avatars.len(), 1);
    assert_eq!(avatars["avatar-one"].as_deref(), Some("https://example.com/one.png"));
    assert_eq!(count.queries.load(Ordering::SeqCst) - before, 1);
}

#[tokio::test]
async fn over_limit_avatar_batch_is_rejected_before_any_query() {
    let (_db, count, repo) = fixture().await;
    let too_many: Vec<String> = (0..257).map(|index| format!("avatar-{index}")).collect();
    assert!(matches!(
        repo.read_avatar_batch(&too_many).await,
        Err(ServiceError::InvalidOperation { .. })
    ));
    assert_eq!(count.queries.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn empty_avatar_batch_issues_no_query() {
    let (_db, count, repo) = fixture().await;
    let avatars = repo.read_avatar_batch(&[]).await.unwrap();
    assert!(avatars.is_empty());
    assert_eq!(count.queries.load(Ordering::SeqCst), 0);
}
