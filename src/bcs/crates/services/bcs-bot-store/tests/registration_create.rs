use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use bcs_bot_store::{MemoryBotRepo, PersistentBotRepo};
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{BotCapabilities, BotRepoPort, ServiceError};
use tokio::sync::{Barrier, Notify, Semaphore};

fn caps(name: &str) -> BotCapabilities {
    BotCapabilities {
        name: Some(name.into()),
        summary: Some(format!("summary-{name}")),
        domains: vec![format!("domain-{name}")],
        visibility: "private".into(),
        ..Default::default()
    }
}

async fn create(repo: &dyn BotRepoPort, name: &str, token: &str) -> Result<bool, ServiceError> {
    repo.create_registration_if_absent("bot-a".into(), caps(name), "owner-a", token)
        .await
}

async fn schema(db: &dyn DbPlugin) {
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
        bot_uuid TEXT NOT NULL, env TEXT NOT NULL, name TEXT NOT NULL,
        bot_info TEXT, session_token TEXT UNIQUE, created_by TEXT, visibility TEXT,
        status TEXT NOT NULL DEFAULT 'online', actor_kind TEXT NOT NULL DEFAULT 'bot',
        is_deleted INTEGER NOT NULL DEFAULT 0, agent_code TEXT,
        registered_at TEXT, updated_at TEXT, PRIMARY KEY (bot_uuid, env)
    )",
    ))
    .await
    .unwrap();
}

async fn sqlite() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    schema(db.as_ref()).await;
    db
}

fn persistent(db: Arc<dyn DbPlugin>) -> PersistentBotRepo {
    PersistentBotRepo::with_sql_flavor(db, DbSqlFlavor::Sqlite)
}

async fn assert_state(repo: &dyn BotRepoPort, name: &str, token: &str) {
    let bot = repo.try_get("bot-a").await.unwrap().unwrap();
    assert_eq!(bot.capabilities.name.as_deref(), Some(name));
    assert_eq!(bot.capabilities.summary, caps(name).summary);
    assert_eq!(bot.capabilities.domains, caps(name).domains);
    assert_eq!(bot.created_by.as_deref(), Some("owner-a"));
    assert!(repo.load_token("bot-a").await.as_deref() == Some(token));
    assert!(repo.try_load_token("bot-a").await.unwrap().as_deref() == Some(token));
    assert!(repo.find_bot_by_token(token).await.as_deref() == Some("bot-a"));
}

async fn contract(repo: &dyn BotRepoPort) {
    assert!(create(repo, "original", "runtime-original").await.unwrap());
    assert_state(repo, "original", "runtime-original").await;
    assert!(
        !repo
            .create_registration_if_absent(
                "bot-a".into(),
                caps("stale"),
                "other-owner",
                "runtime-stale",
            )
            .await
            .unwrap()
    );
    assert_state(repo, "original", "runtime-original").await;
    assert!(repo.find_bot_by_token("runtime-stale").await.is_none());

    repo.update_capabilities("bot-a", caps("renamed"))
        .await
        .unwrap();
    repo.save_token("bot-a", "runtime-rotated").await.unwrap();
    assert!(!create(repo, "original", "runtime-original").await.unwrap());
    assert_state(repo, "renamed", "runtime-rotated").await;
    assert!(repo.find_bot_by_token("runtime-original").await.is_none());

    assert!(repo.soft_delete("bot-a").await);
    assert!(
        !create(repo, "resurrection", "runtime-resurrection")
            .await
            .unwrap()
    );
    assert!(repo.try_get("bot-a").await.unwrap().is_none());
    assert!(repo.load_token("bot-a").await.is_none());
    assert!(repo.try_load_token("bot-a").await.unwrap().is_none());
    assert!(
        repo.find_bot_by_token("runtime-resurrection")
            .await
            .is_none()
    );
}

#[tokio::test]
async fn memory_atomic_creation_contract() {
    let temp = tempfile::tempdir().unwrap();
    contract(&MemoryBotRepo::with_base_dir(temp.path().into())).await;
}

#[tokio::test]
async fn sqlite_atomic_creation_contract() {
    contract(&persistent(sqlite().await)).await;
}

#[tokio::test]
async fn memory_preserves_existing_file_and_does_not_publish_a_losing_candidate() {
    let temp = tempfile::tempdir().unwrap();
    let first = MemoryBotRepo::with_base_dir(temp.path().into());
    assert!(
        create(&first, "original", "runtime-original")
            .await
            .unwrap()
    );
    let path = temp.path().join("bot-a/bot.json");
    let before = tokio::fs::read(&path).await.unwrap();
    let other = MemoryBotRepo::with_base_dir(temp.path().into());
    assert!(!create(&other, "stale", "runtime-stale").await.unwrap());
    assert!(tokio::fs::read(&path).await.unwrap() == before);
    assert!(other.get("bot-a").await.is_none());
    assert!(other.load_token("bot-a").await.as_deref() == Some("runtime-original"));
}

#[tokio::test]
async fn memory_checks_in_memory_only_and_deleted_identities() {
    let temp = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(temp.path().into());
    repo.register("bot-a".into(), caps("existing"))
        .await
        .unwrap();
    assert!(!create(&repo, "stale", "runtime-stale").await.unwrap());
    assert_eq!(
        repo.get("bot-a")
            .await
            .unwrap()
            .capabilities
            .name
            .as_deref(),
        Some("existing")
    );
    assert!(!temp.path().join("bot-a/bot.json").exists());
    assert!(repo.soft_delete("bot-a").await);
    assert!(!create(&repo, "stale", "runtime-stale").await.unwrap());
    assert!(repo.get("bot-a").await.is_none());
}

#[tokio::test]
async fn memory_token_index_hint_does_not_hide_an_existing_file() {
    let temp = tempfile::tempdir().unwrap();
    let original = MemoryBotRepo::with_base_dir(temp.path().into());
    assert!(
        create(&original, "original", "runtime-original")
            .await
            .unwrap()
    );
    let repo = MemoryBotRepo::with_base_dir(temp.path().into());
    repo.store_token_mapping("runtime-original".into(), "bot-a".into())
        .await;
    assert!(!create(&repo, "stale", "runtime-original").await.unwrap());
    let content: serde_json::Value = serde_json::from_slice(
        &tokio::fs::read(temp.path().join("bot-a/bot.json"))
            .await
            .unwrap(),
    )
    .unwrap();
    assert_eq!(content["name"], "original");
}

#[tokio::test]
async fn memory_read_and_write_failures_never_publish_success() {
    for obstruction in ["parent-file", "record-directory", "malformed-record"] {
        let temp = tempfile::tempdir().unwrap();
        let base = temp.path().join("bots");
        match obstruction {
            "parent-file" => tokio::fs::write(&base, b"not a directory").await.unwrap(),
            "record-directory" => tokio::fs::create_dir_all(base.join("bot-a/bot.json"))
                .await
                .unwrap(),
            _ => {
                tokio::fs::create_dir_all(base.join("bot-a")).await.unwrap();
                tokio::fs::write(base.join("bot-a/bot.json"), b"{invalid")
                    .await
                    .unwrap();
            }
        }
        let repo = MemoryBotRepo::with_base_dir(base);
        assert!(create(&repo, "new", "runtime-new").await.is_err());
        assert!(repo.get("bot-a").await.is_none());
    }
}

async fn race(repos: Vec<Arc<dyn BotRepoPort>>) {
    let barrier = Arc::new(Barrier::new(repos.len()));
    let mut tasks = Vec::new();
    for (index, repo) in repos.into_iter().enumerate() {
        let barrier = barrier.clone();
        tasks.push(tokio::spawn(async move {
            barrier.wait().await;
            let name = format!("candidate-{index}");
            let token = format!("runtime-{index}");
            (
                create(repo.as_ref(), &name, &token).await.unwrap(),
                name,
                token,
            )
        }));
    }
    let mut winners = 0;
    for task in tasks {
        winners += usize::from(task.await.unwrap().0);
    }
    assert_eq!(winners, 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn concurrent_memory_and_independent_sqlite_connections_have_one_winner() {
    let temp = tempfile::tempdir().unwrap();
    let memory: Arc<dyn BotRepoPort> =
        Arc::new(MemoryBotRepo::with_base_dir(temp.path().join("memory")));
    race(vec![memory.clone(); 12]).await;
    let bot = memory.get("bot-a").await.unwrap();
    let index = bot
        .capabilities
        .name
        .as_deref()
        .unwrap()
        .strip_prefix("candidate-")
        .unwrap();
    assert_state(
        memory.as_ref(),
        &format!("candidate-{index}"),
        &format!("runtime-{index}"),
    )
    .await;

    let path = temp.path().join("bots.sqlite");
    let db = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
    schema(db.as_ref()).await;
    let repos = (0..12)
        .map(|_| {
            Arc::new(persistent(Arc::new(
                LocalSqliteDbPlugin::new_file(&path).unwrap(),
            ))) as Arc<dyn BotRepoPort>
        })
        .collect();
    race(repos).await;
    let repo = persistent(db);
    let bot = repo.get("bot-a").await.unwrap();
    let index = bot
        .capabilities
        .name
        .as_deref()
        .unwrap()
        .strip_prefix("candidate-")
        .unwrap();
    assert_state(
        &repo,
        &format!("candidate-{index}"),
        &format!("runtime-{index}"),
    )
    .await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn independent_memory_instances_cannot_replace_a_persisted_winner() {
    let temp = tempfile::tempdir().unwrap();
    let repos = (0..12)
        .map(|_| Arc::new(MemoryBotRepo::with_base_dir(temp.path().into())) as Arc<dyn BotRepoPort>)
        .collect();
    race(repos).await;
    let persisted: serde_json::Value = serde_json::from_slice(
        &tokio::fs::read(temp.path().join("bot-a/bot.json"))
            .await
            .unwrap(),
    )
    .unwrap();
    let name = persisted["name"].as_str().unwrap();
    let index = name.strip_prefix("candidate-").unwrap();
    assert!(persisted["token"].as_str() == Some(format!("runtime-{index}").as_str()));
}

struct Pause {
    entered: Notify,
    release: Semaphore,
    before_insert: bool,
    used: AtomicBool,
}

impl Pause {
    fn new(before_insert: bool) -> Self {
        Self {
            entered: Notify::new(),
            release: Semaphore::new(0),
            before_insert,
            used: AtomicBool::new(false),
        }
    }
    async fn wait(&self) {
        self.entered.notify_one();
        self.release.acquire().await.unwrap().forget();
    }
}

struct ControlledDb {
    db: Arc<dyn DbPlugin>,
    insert_error: Mutex<Option<DbError>>,
    query_error: AtomicBool,
    token_result: Mutex<Option<DbResult<Vec<DbRow>>>>,
    zero_insert: AtomicBool,
    calls: Mutex<Vec<(bool, String)>>,
    pause: Option<Arc<Pause>>,
}

impl ControlledDb {
    fn new(db: Arc<dyn DbPlugin>) -> Self {
        Self {
            db,
            insert_error: Mutex::new(None),
            query_error: AtomicBool::new(false),
            token_result: Mutex::new(None),
            zero_insert: AtomicBool::new(false),
            calls: Mutex::new(Vec::new()),
            pause: None,
        }
    }
}

#[async_trait]
impl DbPlugin for ControlledDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.calls
            .lock()
            .unwrap()
            .push((false, statement.sql().into()));
        if statement
            .sql()
            .starts_with("SELECT session_token FROM bcs_bots")
        {
            if let Some(result) = self.token_result.lock().unwrap().take() {
                return result;
            }
        }
        if self.query_error.load(Ordering::SeqCst) {
            return Err(DbError::Backend(
                "injected authoritative read failure".into(),
            ));
        }
        self.db.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.calls
            .lock()
            .unwrap()
            .push((true, statement.sql().into()));
        if let Some(error) = self.insert_error.lock().unwrap().take() {
            return Err(error);
        }
        if self.zero_insert.load(Ordering::SeqCst) {
            return Ok(DbExecuteResult {
                affected_rows: 0,
                last_insert_id: None,
            });
        }
        let pause = self
            .pause
            .as_ref()
            .filter(|_| statement.sql().starts_with("INSERT INTO bcs_bots"))
            .filter(|pause| !pause.used.swap(true, Ordering::SeqCst));
        if let Some(pause) = pause.filter(|pause| pause.before_insert) {
            pause.wait().await;
        }
        let result = self.db.execute(statement).await;
        if let Some(pause) = pause.filter(|pause| !pause.before_insert) {
            pause.wait().await;
        }
        result
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

#[tokio::test]
async fn sqlite_failed_insert_and_failed_deleted_identity_read_are_errors() {
    let db = Arc::new(ControlledDb::new(sqlite().await));
    let repo = persistent(db.clone());
    assert!(create(&repo, "original", "runtime-original").await.unwrap());
    assert!(repo.soft_delete("bot-a").await);
    db.query_error.store(true, Ordering::SeqCst);
    assert!(create(&repo, "stale", "runtime-stale").await.is_err());
    db.query_error.store(false, Ordering::SeqCst);
    assert!(!create(&repo, "stale", "runtime-stale").await.unwrap());
    for error in [
        DbError::Backend("disk full".into()),
        DbError::Backend("connection 1062 failed".into()),
        DbError::Backend(
            "execute sqlite statement: NOT NULL constraint failed: bcs_bots.name".into(),
        ),
    ] {
        db.calls.lock().unwrap().clear();
        *db.insert_error.lock().unwrap() = Some(error);
        assert!(create(&repo, "stale", "runtime-stale").await.is_err());
        assert!(db.calls.lock().unwrap().iter().all(|(write, _)| *write));
    }
    db.zero_insert.store(true, Ordering::SeqCst);
    assert!(create(&repo, "stale", "runtime-stale").await.is_err());
}

#[tokio::test]
async fn different_unique_constraint_is_not_an_existing_bot_result() {
    let db = sqlite().await;
    let repo = persistent(db.clone());
    assert!(create(&repo, "original", "runtime-original").await.unwrap());
    assert!(
        repo.create_registration_if_absent(
            "bot-b".into(),
            caps("other"),
            "owner-b",
            "runtime-original",
        )
        .await
        .is_err()
    );
    assert!(repo.get("bot-b").await.is_none());
    assert_state(&repo, "original", "runtime-original").await;
}

#[tokio::test]
async fn mysql_uses_strict_insert_and_only_confirms_the_bot_identity_constraint() {
    let db = Arc::new(ControlledDb::new(sqlite().await));
    let repo = PersistentBotRepo::new(db.clone());
    assert!(create(&repo, "original", "runtime-original").await.unwrap());
    {
        let calls = db.calls.lock().unwrap();
        assert_eq!(calls.len(), 1, "creation must not pre-read or upsert");
        assert!(calls[0].0 && calls[0].1.starts_with("INSERT INTO bcs_bots"));
        assert!(!calls[0].1.contains("IGNORE") && !calls[0].1.contains("UPDATE"));
    }
    for prefix in ["mysql execute failed", "mysql prepared execute failed"] {
        db.calls.lock().unwrap().clear();
        *db.insert_error.lock().unwrap() = Some(DbError::Backend(format!(
            "{prefix}: Server error: `ERROR 23000 (1062): Duplicate entry 'bot-env' for key 'bcs_bots.uk_bot_env''"
        )));
        assert!(!create(&repo, "stale", "runtime-stale").await.unwrap());
        assert!(
            db.calls
                .lock()
                .unwrap()
                .iter()
                .any(|(write, sql)| !write && !sql.contains("is_deleted"))
        );
        *db.insert_error.lock().unwrap() = Some(DbError::Backend(format!(
            "{prefix}: Server error: `ERROR 23000 (1062): Duplicate entry 'credential' for key 'uk_session_token''"
        )));
        assert!(create(&repo, "stale", "runtime-stale").await.is_err());
    }
    *db.insert_error.lock().unwrap() = Some(DbError::Backend(
        "mysql execute failed: Server error: `ERROR 23000 (1062): Duplicate entry 'identity' for key 'uk_bot_env''".into(),
    ));
    assert!(
        repo.create_registration_if_absent(
            "missing-bot".into(),
            caps("missing"),
            "owner-a",
            "runtime-missing",
        )
        .await
        .is_err()
    );
    assert_state(&repo, "original", "runtime-original").await;
}

#[tokio::test]
async fn delayed_db_writer_never_overwrites_newer_credentials_or_resurrects_deleted_bot() {
    for (before_insert, deleted) in [(true, false), (true, true), (false, false), (false, true)] {
        let db = sqlite().await;
        let pause = Arc::new(Pause::new(before_insert));
        let mut controlled = ControlledDb::new(db.clone());
        controlled.pause = Some(pause.clone());
        let slow = Arc::new(persistent(Arc::new(controlled)));
        let slow_clone = slow.clone();
        let mut task =
            tokio::spawn(
                async move { create(slow_clone.as_ref(), "stale", "runtime-stale").await },
            );
        tokio::select! {
            _ = pause.entered.notified() => {},
            result = &mut task => panic!("creation returned before reaching INSERT: {:?}", result.unwrap()),
            _ = tokio::time::sleep(Duration::from_secs(5)) => panic!("INSERT was never reached"),
        }
        let writer = persistent(db);
        if before_insert {
            assert!(
                create(&writer, "original", "runtime-original")
                    .await
                    .unwrap()
            );
        }
        writer
            .update_capabilities("bot-a", caps("renamed"))
            .await
            .unwrap();
        writer.save_token("bot-a", "runtime-rotated").await.unwrap();
        if deleted {
            assert!(writer.soft_delete("bot-a").await);
        }
        pause.release.add_permits(1);
        assert_eq!(task.await.unwrap().unwrap(), !before_insert);
        if deleted {
            assert!(slow.try_get("bot-a").await.unwrap().is_none());
            assert!(slow.load_token("bot-a").await.is_none());
        } else {
            assert_state(slow.as_ref(), "renamed", "runtime-rotated").await;
        }
    }
}

#[tokio::test]
async fn token_only_db_failure_is_sanitized_and_never_reported_as_absence() {
    let db = Arc::new(ControlledDb::new(sqlite().await));
    let repo = persistent(db.clone());
    assert!(create(&repo, "original", "runtime-original").await.unwrap());
    *db.token_result.lock().unwrap() = Some(Err(DbError::Backend(
        "token query failed with sensitive-credential-value".into(),
    )));
    assert!(repo.try_get("bot-a").await.unwrap().is_some());
    let (result, events) = bcs_test_support::capture_request_logs("fallible-token-read", async {
        repo.try_load_token("bot-a").await
    })
    .await;
    let error = result.expect_err("credential read failure is not absence");
    assert!(matches!(error, ServiceError::InternalError(_)));
    assert!(!error.to_string().contains("sensitive-credential-value"));
    assert!(
        !serde_json::to_string(&events)
            .unwrap()
            .contains("sensitive-credential-value")
    );
    assert!(
        db.token_result.lock().unwrap().is_none(),
        "must reach the credential query"
    );
    assert!(repo.try_load_token("bot-a").await.unwrap().as_deref() == Some("runtime-original"));
}

#[tokio::test]
async fn authoritative_token_read_ignores_stale_cache_and_filters_deleted_or_missing() {
    let db = sqlite().await;
    let repo = persistent(db.clone());
    repo.register_with_owner_and_token(
        "bot-a".into(),
        caps("original"),
        "owner-a",
        "runtime-original",
    )
    .await
    .unwrap();
    assert!(repo.try_load_token("missing").await.unwrap().is_none());
    let writer = persistent(db);
    writer.save_token("bot-a", "runtime-rotated").await.unwrap();
    assert!(repo.try_load_token("bot-a").await.unwrap().as_deref() == Some("runtime-rotated"));
    assert!(writer.soft_delete("bot-a").await);
    assert!(repo.try_load_token("bot-a").await.unwrap().is_none());
}

#[tokio::test]
async fn token_projection_rejects_invalid_rows_but_accepts_null() {
    use bcs_db_api::DbValue;
    let db = Arc::new(ControlledDb::new(sqlite().await));
    let repo = persistent(db.clone());
    let row = |value| DbRow::new([("session_token".into(), value)].into_iter().collect());
    for rows in [
        vec![DbRow::empty()],
        vec![row(DbValue::I64(1234567))],
        vec![row(DbValue::Bytes(vec![255]))],
        vec![row(DbValue::Null), row(DbValue::Null)],
    ] {
        *db.token_result.lock().unwrap() = Some(Ok(rows));
        let error = repo
            .try_load_token("bot-a")
            .await
            .expect_err("invalid projection must fail");
        assert!(!error.to_string().contains("1234567"));
        assert!(
            db.token_result.lock().unwrap().is_none(),
            "must validate the returned row"
        );
    }
    *db.token_result.lock().unwrap() = Some(Ok(vec![row(DbValue::Null)]));
    assert!(repo.try_load_token("bot-a").await.unwrap().is_none());
    *db.token_result.lock().unwrap() =
        Some(Ok(vec![row(DbValue::Bytes(b"runtime-current".to_vec()))]));
    assert!(repo.try_load_token("bot-a").await.unwrap().as_deref() == Some("runtime-current"));
}

#[tokio::test]
async fn memory_fallible_token_read_checks_tombstone_memory_and_strict_file_in_order() {
    let temp = tempfile::tempdir().unwrap();
    let repo = MemoryBotRepo::with_base_dir(temp.path().into());
    assert!(repo.try_load_token("missing").await.unwrap().is_none());
    assert!(create(&repo, "original", "runtime-original").await.unwrap());
    let fresh = MemoryBotRepo::with_base_dir(temp.path().into());
    assert!(fresh.try_load_token("bot-a").await.unwrap().as_deref() == Some("runtime-original"));
    repo.save_token("bot-a", "runtime-rotated").await.unwrap();
    assert!(fresh.try_load_token("bot-a").await.unwrap().as_deref() == Some("runtime-rotated"));
    let path = temp.path().join("bot-a/bot.json");
    tokio::fs::write(&path, b"{sensitive-credential-value")
        .await
        .unwrap();
    let error = fresh
        .try_load_token("bot-a")
        .await
        .expect_err("malformed file must fail");
    assert!(!error.to_string().contains("sensitive-credential-value"));
    assert!(repo.try_load_token("bot-a").await.unwrap().as_deref() == Some("runtime-rotated"));
    assert!(repo.soft_delete("bot-a").await);
    assert!(repo.try_load_token("bot-a").await.unwrap().is_none());
    tokio::fs::write(
        &path,
        br#"{"bot_id":"wrong-bot","registered_at":1,"token":"sensitive-credential-value"}"#,
    )
    .await
    .unwrap();
    assert!(fresh.try_load_token("bot-a").await.is_err());
    tokio::fs::create_dir_all(temp.path().join("directory-bot/bot.json"))
        .await
        .unwrap();
    assert!(fresh.try_load_token("directory-bot").await.is_err());
}
