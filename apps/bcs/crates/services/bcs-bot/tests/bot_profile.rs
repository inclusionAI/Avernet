//! Core-level Bot profile/avatar aggregation (bot-avatar plan Task 4).
//!
//! `BotRegistryCoreService` adds the existing-only profile writes (same
//! signatures as `BotRepoPort`) and the participant-page batch read
//! `avatar_urls`. Unlike the repository's `read_avatar_batch`, the core
//! accepts a WHOLE participant page: it deduplicates and chunks into
//! store-sized batches and merges their maps, keeping the query budget at
//! one DB query per store batch. The seeding runs against the complete
//! committed migration chain (Task 3's real schema fixture) through a
//! counting DbPlugin wrapper whose write paths are forwarded, so seeding is
//! never blocked and the core's page-to-query budget stays observable.

use bcs_db_migrations as bootstrap_migrations;
#[path = "../../bcs-bot-store/tests/support/bot_profile_fixture.rs"]
#[allow(dead_code)]
mod fixture;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot::BotCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::types::bot_profile::{AvatarChange, ExistingProfileUpdate, ProfileWrite};
use bcs_service_api::{BotCapabilities, BotRepoPort, BotRegistryCoreService, ServiceError};

/// Counting `DbPlugin` wrapper over the real SQLite plugin (adapted from the
/// store's `conformance_agent_registration.rs` shape): standalone read
/// queries and their rows are counted, while `execute` and `transaction` are
/// forwarded so the repo's registration and avatar writes can seed real
/// rows.
struct CountReads {
    db: Arc<LocalSqliteDbPlugin>,
    queries: AtomicUsize,
    rows: AtomicUsize,
}

#[async_trait]
impl DbPlugin for CountReads {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.queries.fetch_add(1, Ordering::SeqCst);
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

/// Task 3's real migration-chain fixture builds the schema; the counting
/// wrapper (writes forwarded) then carries every query of the core's
/// repository, so the core's page-to-query budget is observable.
async fn counted_core() -> (
    Arc<LocalSqliteDbPlugin>,
    Arc<CountReads>,
    Arc<PersistentBotRepo>,
    BotCore,
) {
    let (db, _) = fixture::sqlite_repo().await;
    let counted = Arc::new(CountReads {
        db: db.clone(),
        queries: AtomicUsize::new(0),
        rows: AtomicUsize::new(0),
    });
    let repo = Arc::new(PersistentBotRepo::with_sql_flavor(
        counted.clone(),
        DbSqlFlavor::Sqlite,
    ));
    let core = BotCore::with_repo(repo.clone());
    (db, counted, repo, core)
}

/// Register one bot and store its avatar through the Task 3 store contract.
/// Both are write paths (forwarded, not counted), so the read counters stay
/// at zero until an avatar page is actually queried.
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

/// A complete profile update for the core's existing-only write.
fn profile_update(name: &str, avatar: AvatarChange) -> ExistingProfileUpdate {
    ExistingProfileUpdate {
        capabilities: BotCapabilities {
            name: Some(name.to_string()),
            summary: Some("core profile write".to_string()),
            domains: vec!["avatar-domain".to_string()],
            ..Default::default()
        },
        avatar,
    }
}

#[tokio::test]
async fn avatar_urls_dedupes_a_duplicated_participant_page_into_two_queries() {
    let (_db, count, repo, core) = counted_core().await;
    let ids = (0..257).map(|i| format!("avatar-{i}")).collect::<Vec<_>>();
    for (index, id) in ids.iter().enumerate() {
        seed_avatar(&repo, id, &format!("https://example.com/avatar/{index}.png")).await;
    }
    let mut duplicated = ids.clone();
    duplicated.push(ids[0].clone());
    let before = count.queries.load(Ordering::SeqCst);
    let before_rows = count.rows.load(Ordering::SeqCst);
    let values = core.avatar_urls(&duplicated).await.unwrap();
    assert_eq!(values.len(), 257);
    assert_eq!(count.queries.load(Ordering::SeqCst) - before, 2);
    // The two batches read exactly the page's own rows (256 + 1): no
    // full-table fetch, and the duplicated id costs no third query.
    assert_eq!(count.rows.load(Ordering::SeqCst) - before_rows, 257);
    assert_eq!(
        values["avatar-0"].as_deref(),
        Some("https://example.com/avatar/0.png")
    );
    assert_eq!(
        values["avatar-256"].as_deref(),
        Some("https://example.com/avatar/256.png")
    );
}

#[tokio::test]
async fn avatar_urls_empty_page_issues_no_query() {
    let (_db, count, _repo, core) = counted_core().await;
    let before = count.queries.load(Ordering::SeqCst);
    let values = core.avatar_urls(&[]).await.unwrap();
    assert!(values.is_empty());
    assert_eq!(count.queries.load(Ordering::SeqCst), before);
    assert_eq!(count.queries.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn avatar_urls_pages_of_one_and_256_ids_are_single_queries() {
    let (_db, count, repo, core) = counted_core().await;
    seed_avatar(&repo, "avatar-single", "https://example.com/single.png").await;
    let before = count.queries.load(Ordering::SeqCst);
    let values = core
        .avatar_urls(&["avatar-single".to_string()])
        .await
        .unwrap();
    assert_eq!(values.len(), 1);
    assert_eq!(
        values["avatar-single"].as_deref(),
        Some("https://example.com/single.png")
    );
    assert_eq!(count.queries.load(Ordering::SeqCst) - before, 1);

    let ids: Vec<String> = (0..256).map(|i| format!("avatar-page-{i}")).collect();
    for id in &ids {
        seed_avatar(&repo, id, "https://example.com/page.png").await;
    }
    let before = count.queries.load(Ordering::SeqCst);
    let values = core.avatar_urls(&ids).await.unwrap();
    assert_eq!(values.len(), 256);
    assert_eq!(count.queries.load(Ordering::SeqCst) - before, 1);
}

#[tokio::test]
async fn avatar_urls_a_300_id_page_is_exactly_two_256_batches() {
    // Pins the batch-size arithmetic at a non-trivial page: ceil(300/256) =
    // 2 repository batch calls, reading exactly the page's own 300 rows —
    // no fallback per-id loop after the first full batch.
    let (_db, count, repo, core) = counted_core().await;
    let ids: Vec<String> = (0..300).map(|i| format!("avatar-300-{i}")).collect();
    for (index, id) in ids.iter().enumerate() {
        seed_avatar(&repo, id, &format!("https://example.com/300/{index}.png")).await;
    }
    let before_queries = count.queries.load(Ordering::SeqCst);
    let before_rows = count.rows.load(Ordering::SeqCst);
    let values = core.avatar_urls(&ids).await.unwrap();
    assert_eq!(values.len(), 300);
    assert_eq!(
        values["avatar-300-0"].as_deref(),
        Some("https://example.com/300/0.png")
    );
    assert_eq!(
        values["avatar-300-299"].as_deref(),
        Some("https://example.com/300/299.png")
    );
    // ceil(300/256) = 2 store-sized batch reads, and no third query.
    assert_eq!(count.queries.load(Ordering::SeqCst) - before_queries, 2);
    assert_eq!(count.rows.load(Ordering::SeqCst) - before_rows, 300);
}

#[tokio::test]
async fn avatar_urls_leaves_foreign_environment_ids_absent() {
    let (db, count, repo, core) = counted_core().await;
    seed_avatar(&repo, "avatar-home", "https://example.com/home.png").await;
    db.execute(DbStatement::new(
        "INSERT INTO bcs_bots (bot_uuid, env, name, avatar_url) \
         VALUES ('avatar-foreign', 'avatar-foreign-env', 'Foreign', 'https://example.com/foreign.png')",
    ))
    .await
    .unwrap();
    let before = count.queries.load(Ordering::SeqCst);
    let values = core
        .avatar_urls(&["avatar-home".to_string(), "avatar-foreign".to_string()])
        .await
        .unwrap();
    assert_eq!(
        values["avatar-home"].as_deref(),
        Some("https://example.com/home.png")
    );
    assert!(!values.contains_key("avatar-foreign"));
    assert_eq!(count.queries.load(Ordering::SeqCst) - before, 1);
}

#[tokio::test]
async fn avatar_urls_excludes_humans_and_keeps_tombstone_faces() {
    let (_db, count, repo, core) = counted_core().await;
    // Human actors never join the Bot display read (pass-through of the
    // store's human exclusion).
    core.ensure_human_actor("avatar-staff", "Human Avatar").await.unwrap();
    // A tombstoned participant keeps its stored face for display reads.
    seed_avatar(&repo, "avatar-tomb", "https://example.com/tomb.png").await;
    assert!(repo.soft_delete("avatar-tomb").await);

    let before = count.queries.load(Ordering::SeqCst);
    let values = core
        .avatar_urls(&["human_avatar-staff".to_string(), "avatar-tomb".to_string()])
        .await
        .unwrap();
    assert!(!values.contains_key("human_avatar-staff"));
    assert_eq!(
        values["avatar-tomb"].as_deref(),
        Some("https://example.com/tomb.png")
    );
    assert_eq!(count.queries.load(Ordering::SeqCst) - before, 1);
}

#[tokio::test]
async fn avatar_urls_propagates_store_errors_instead_of_empty_maps() {
    let (db, _count, repo, core) = counted_core().await;
    seed_avatar(&repo, "avatar-broken", "https://example.com/broken.png").await;
    db.execute(DbStatement::new("DROP TABLE bcs_bots"))
        .await
        .unwrap();
    assert!(matches!(
        core.avatar_urls(&["avatar-broken".to_string()]).await,
        Err(ServiceError::InternalError(_))
    ));
}

#[tokio::test]
async fn core_profile_and_avatar_writes_delegate_existing_only_store_semantics() {
    let (_db, _count, repo, core) = counted_core().await;

    // Missing identities stay missing: no INSERT path behind the core.
    assert_eq!(
        core.update_avatar_existing(
            "missing",
            AvatarChange::Set("https://example.com/never.png".into()),
        )
        .await
        .unwrap(),
        ProfileWrite::Missing,
    );
    assert_eq!(
        core.update_profile_existing(
            "missing",
            profile_update("Lost", AvatarChange::Set("https://example.com/never.png".into())),
        )
        .await
        .unwrap(),
        ProfileWrite::Missing,
    );

    // The avatar write delegates to the store's existing-only contract and
    // becomes visible to the batched read.
    assert!(repo
        .create_registration_if_absent(
            "avatar-core".into(),
            BotCapabilities::default(),
            "owner",
            "stored-token",
        )
        .await
        .unwrap());
    assert_eq!(
        core.update_avatar_existing(
            "avatar-core",
            AvatarChange::Set("https://example.com/core.png".into()),
        )
        .await
        .unwrap(),
        ProfileWrite::Updated,
    );
    let values = core
        .avatar_urls(&["avatar-core".to_string()])
        .await
        .unwrap();
    assert_eq!(
        values["avatar-core"].as_deref(),
        Some("https://example.com/core.png")
    );

    // The composite profile write assigns the capabilities wholesale, a
    // Keep transition never clears the stored avatar, and the existing
    // owner/token fields survive the write.
    assert_eq!(
        core.update_profile_existing("avatar-core", profile_update("Core Name", AvatarChange::Keep))
            .await
            .unwrap(),
        ProfileWrite::Updated,
    );
    let bot = core.try_get("avatar-core").await.unwrap().unwrap();
    assert_eq!(bot.capabilities.name.as_deref(), Some("Core Name"));
    assert_eq!(
        core.try_load_token("avatar-core").await.unwrap().as_deref(),
        Some("stored-token"),
    );
    let values = core
        .avatar_urls(&["avatar-core".to_string()])
        .await
        .unwrap();
    assert_eq!(
        values["avatar-core"].as_deref(),
        Some("https://example.com/core.png")
    );

    // Tombstoned identities refuse every write through the core too.
    assert!(repo.soft_delete("avatar-core").await);
    assert_eq!(
        core.update_avatar_existing(
            "avatar-core",
            AvatarChange::Set("https://example.com/ghost.png".into()),
        )
        .await
        .unwrap(),
        ProfileWrite::Missing,
    );
    assert_eq!(
        core.update_profile_existing("avatar-core", profile_update("Gone", AvatarChange::Keep))
            .await
            .unwrap(),
        ProfileWrite::Missing,
    );
}

#[tokio::test]
async fn unconfigured_registries_fail_closed_for_the_profile_and_avatar_contracts() {
    let noop = bcs_test_support::NoopBotRegistryCoreService;
    assert!(matches!(
        noop.update_profile_existing("bot", profile_update("Lost", AvatarChange::Keep))
            .await,
        Err(ServiceError::InternalError(_)),
    ));
    assert!(matches!(
        noop.update_avatar_existing("bot", AvatarChange::Keep).await,
        Err(ServiceError::InternalError(_)),
    ));
    assert!(matches!(
        noop.avatar_urls(&["bot".to_string()]).await,
        Err(ServiceError::InternalError(_)),
    ));
}
