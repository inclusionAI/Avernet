use bcs_db_migrations as bootstrap_migrations;
#[path = "support/open_check_in_contract.rs"]
mod contract;
#[path = "support/bot_profile_fixture.rs"]
mod fixture;

#[tokio::test]
async fn memory_open_check_in_visibility_contract() {
    let dir = tempfile::tempdir().unwrap();
    let repo = bcs_bot_store::MemoryBotRepo::with_base_dir(dir.path().into());
    contract::verify(&repo).await;
}

#[tokio::test]
async fn sqlite_open_check_in_visibility_contract() {
    let (_db, repo) = fixture::sqlite_repo().await;
    contract::verify(&repo).await;
}

use async_trait::async_trait;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue,
};
use bcs_service_api::{
    BotCapabilities, BotControlPlanePatch, BotControlPlaneRecord, BotControlPlaneRepoPort, BotRepoPort,
    FriendCheckInStrategy,
};
use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};

// Interpose only the scheduling boundary. Both writers execute real SQLite SQL.
struct BeforeUpdateDb {
    inner: Arc<dyn DbPlugin>,
    armed: AtomicBool,
    writes: AtomicUsize,
    control_reads: AtomicUsize,
    concurrent_update: &'static str,
}
#[async_trait]
impl DbPlugin for BeforeUpdateDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        if statement
            .sql()
            .starts_with("SELECT bot_uuid, name, bot_info")
        {
            self.control_reads.fetch_add(1, Ordering::SeqCst);
        }
        self.inner.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        if statement.sql().starts_with("UPDATE bcs_bots SET") {
            self.writes.fetch_add(1, Ordering::SeqCst);
            if self.armed.swap(false, Ordering::SeqCst) {
                self.inner
                    .execute(DbStatement::new(self.concurrent_update))
                    .await?;
            }
        }
        self.inner.execute(statement).await
    }
    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

async fn update_after_concurrent_write(
    sql: &'static str,
    expected: &str,
    initial_scope: serde_json::Value,
) -> BotControlPlaneRecord {
    let (db, _) = fixture::sqlite_repo().await;
    let wrapped = Arc::new(BeforeUpdateDb {
        inner: db.clone(),
        armed: AtomicBool::new(false),
        writes: AtomicUsize::new(0),
        control_reads: AtomicUsize::new(0),
        concurrent_update: sql,
    });
    let repo = PersistentBotRepo::with_sql_flavor(wrapped.clone(), DbSqlFlavor::Sqlite);
    let env = bcs_config::resolve_env_str();
    repo.register_with_owner_and_token(
        "race".into(),
        BotCapabilities {
            name: Some("Race".into()),
            ..Default::default()
        },
        "owner",
        "test-token",
    )
    .await
    .unwrap();
    repo.patch_control_plane(
        "race",
        &env,
        BotControlPlanePatch {
            visibility: Some("protected".into()),
            friend_ext: Some(serde_json::from_value(initial_scope).unwrap()),
            ..Default::default()
        },
    )
    .await
    .unwrap()
    .unwrap();
    wrapped.writes.store(0, Ordering::SeqCst);
    wrapped.control_reads.store(0, Ordering::SeqCst);
    wrapped.armed.store(true, Ordering::SeqCst);
    let updated = repo
        .patch_control_plane(
            "race",
            &env,
            BotControlPlanePatch {
                friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
                ..Default::default()
            },
        )
        .await
        .unwrap()
        .unwrap();
    assert_eq!(updated.visibility, expected);
    assert_eq!(
        updated.friend_check_in_strategy,
        FriendCheckInStrategy::Open
    );
    assert_eq!(
        wrapped.writes.load(Ordering::SeqCst),
        1,
        "no follow-up visibility write"
    );
    assert_eq!(
        wrapped.control_reads.load(Ordering::SeqCst),
        2,
        "one precheck and one read-back"
    );
    assert_eq!(
        repo.try_get("race")
            .await
            .unwrap()
            .unwrap()
            .capabilities
            .visibility,
        expected
    );
    let rows = db
        .query(DbStatement::new(
            "SELECT visibility, friend_check_in_strategy FROM bcs_bots WHERE bot_uuid = 'race'",
        ))
        .await
        .unwrap();
    assert_eq!(rows[0].get("visibility"), Some(&DbValue::from(expected)));
    assert_eq!(
        rows[0].get("friend_check_in_strategy"),
        Some(&DbValue::from("OPEN"))
    );
    updated
}

#[tokio::test]
async fn sqlite_open_patch_preserves_private_committed_after_precheck() {
    update_after_concurrent_write(
        "UPDATE bcs_bots SET visibility = 'private' WHERE bot_uuid = 'race'",
        "private",
        serde_json::json!({}),
    )
    .await;
}

#[tokio::test]
async fn sqlite_open_patch_promotes_despite_scope_committed_after_precheck() {
    let updated = update_after_concurrent_write(
        "UPDATE bcs_bots SET friend_ext = '{\"view_scope_agent_friend_deps\":[\"D1\"]}' WHERE bot_uuid = 'race'",
        "public", serde_json::json!({})
    ).await;
    assert_eq!(updated.friend_ext["view_scope_agent_friend_deps"], serde_json::json!(["D1"]));
}

#[tokio::test]
async fn sqlite_open_patch_promotes_in_the_same_write_after_scope_is_cleared() {
    update_after_concurrent_write(
        "UPDATE bcs_bots SET friend_ext = '{}' WHERE bot_uuid = 'race'",
        "public",
        serde_json::json!({"view_scope_agent_friend_deps": ["D1"]}),
    )
    .await;
}

#[tokio::test]
async fn sqlite_failed_open_patch_publishes_neither_visibility_nor_strategy() {
    let (db, repo) = fixture::sqlite_repo().await;
    let env = bcs_config::resolve_env_str();
    repo.register_with_owner_and_token(
        "fail-open".into(),
        BotCapabilities {
            name: Some("Fail Open".into()),
            ..Default::default()
        },
        "owner",
        "test-token",
    )
    .await
    .unwrap();
    repo.patch_control_plane(
        "fail-open",
        &env,
        BotControlPlanePatch {
            visibility: Some("protected".into()),
            ..Default::default()
        },
    )
    .await
    .unwrap()
    .unwrap();
    db.execute(DbStatement::new(
        "CREATE TRIGGER reject_open BEFORE UPDATE ON bcs_bots \
         WHEN NEW.friend_check_in_strategy = 'OPEN' \
         BEGIN SELECT RAISE(ABORT, 'injected failure'); END",
    ))
    .await
    .unwrap();
    assert!(
        repo.patch_control_plane(
            "fail-open",
            &env,
            BotControlPlanePatch {
                friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
                ..Default::default()
            }
        )
        .await
        .is_err()
    );
    let record = repo
        .get_control_plane("fail-open", &env)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(record.visibility, "protected");
    assert_eq!(
        record.friend_check_in_strategy,
        FriendCheckInStrategy::Approval
    );
    assert_eq!(
        repo.try_get("fail-open")
            .await
            .unwrap()
            .unwrap()
            .capabilities
            .visibility,
        "protected"
    );
}
