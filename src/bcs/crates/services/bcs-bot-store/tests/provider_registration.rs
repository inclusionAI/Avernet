use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_bot_store::{DbProviderRegistrationStore, MemoryProviderRegistrationStore};
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::provider_registration::ProviderRegistrationRepoPort;
use bcs_service_api::types::provider_registration::{
    ProviderRegistrationMode, ProviderRegistrationRecord,
};
use tokio::sync::Barrier;

fn candidate(index: usize) -> ProviderRegistrationRecord {
    ProviderRegistrationRecord {
        provider_id: "Provider".into(),
        provider_bot_ref: "Ref".into(),
        owner: format!("owner-{index}"),
        mode: ProviderRegistrationMode::Upstream,
        bot_name: format!("Bot {index}"),
        bot_uuid: format!("bot-{index}"),
        bot_token: format!("test-runtime-credential-{index}"),
        webhook_url: None,
        completed: false,
    }
}

fn migration(flavor: &str, filename: &str) -> String {
    std::fs::read_to_string(format!(
        "{}/../../../migrations/{flavor}/{filename}",
        env!("CARGO_MANIFEST_DIR")
    ))
    .expect("registration migration must exist")
}

async fn migrate(db: &dyn DbPlugin) {
    let sql = migration("sqlite", "030_provider_registrations.sql");
    for statement in sql.split(';').map(str::trim).filter(|sql| !sql.is_empty()) {
        db.execute(DbStatement::new(statement)).await.unwrap();
    }
}

async fn sqlite() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    migrate(db.as_ref()).await;
    db
}

fn store(db: Arc<dyn DbPlugin>, env: &str) -> DbProviderRegistrationStore {
    DbProviderRegistrationStore::new(db, DbSqlFlavor::Sqlite, env.into())
}

#[tokio::test]
async fn sqlite_reopens_pending_and_completed_credentials() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("registrations.sqlite");
    {
        let db = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
        migrate(db.as_ref()).await;
        store(db, "dev").reserve(candidate(0)).await.unwrap();
    }
    {
        let repo = store(
            Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap()),
            "dev",
        );
        assert!(repo.reserve(candidate(1)).await.unwrap() == candidate(0));
        repo.complete("Provider", "Ref").await.unwrap();
    }
    let repo = store(
        Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap()),
        "dev",
    );
    let mut expected = candidate(0);
    expected.completed = true;
    assert!(repo.get("Provider", "Ref").await.unwrap().unwrap() == expected);
}

#[tokio::test]
async fn sqlite_environment_scope_includes_completion_and_both_unique_keys() {
    let db = sqlite().await;
    let a = store(db.clone(), "dev");
    let b = store(db.clone(), "Dev");
    let c = store(db, "dev ");
    a.reserve(candidate(0)).await.unwrap();
    for repo in [&b, &c] {
        assert!(repo.get("Provider", "Ref").await.unwrap().is_none());
        assert!(repo.complete("Provider", "Ref").await.is_err());
        repo.reserve(candidate(0)).await.unwrap();
    }
    b.complete("Provider", "Ref").await.unwrap();
    assert!(!a.get("Provider", "Ref").await.unwrap().unwrap().completed);
    assert!(!c.get("Provider", "Ref").await.unwrap().unwrap().completed);
}

async fn race(stores: Vec<Arc<dyn ProviderRegistrationRepoPort>>) {
    let barrier = Arc::new(Barrier::new(stores.len()));
    let mut tasks = Vec::new();
    for (index, repo) in stores.into_iter().enumerate() {
        let barrier = barrier.clone();
        tasks.push(tokio::spawn(async move {
            barrier.wait().await;
            repo.reserve(candidate(index)).await.unwrap()
        }));
    }
    let winner = tasks.remove(0).await.unwrap();
    for task in tasks {
        assert!(task.await.unwrap() == winner);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn parallel_reservations_return_one_immutable_winner() {
    let memory: Arc<dyn ProviderRegistrationRepoPort> =
        Arc::new(MemoryProviderRegistrationStore::new());
    race(vec![memory; 16]).await;
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("concurrent.sqlite");
    let db = LocalSqliteDbPlugin::new_file(&path).unwrap();
    migrate(&db).await;
    let stores = (0..16)
        .map(|_| {
            Arc::new(store(
                Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap()),
                "dev",
            )) as Arc<dyn ProviderRegistrationRepoPort>
        })
        .collect();
    race(stores).await;
}

#[tokio::test]
async fn sqlite_reservations_are_atomic_between_processes() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("processes.sqlite");
    let db = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
    migrate(db.as_ref()).await;
    let mut workers = Vec::new();
    for index in 0..6 {
        workers.push(
            std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--ignored", "--exact", "sqlite_reservation_worker"])
                .env("BCS_REGISTRATION_TEST_DB", &path)
                .env("BCS_REGISTRATION_TEST_INDEX", index.to_string())
                .stdout(std::process::Stdio::null())
                .spawn()
                .unwrap(),
        );
    }
    for mut worker in workers {
        assert!(worker.wait().unwrap().success());
    }
    let winner = store(db, "dev")
        .get("Provider", "Ref")
        .await
        .unwrap()
        .unwrap();
    assert!((0..6).any(|index| winner == candidate(index)));
}

#[tokio::test]
#[ignore = "subprocess helper, run by sqlite_reservations_are_atomic_between_processes"]
async fn sqlite_reservation_worker() {
    let path = std::env::var("BCS_REGISTRATION_TEST_DB").unwrap();
    let index = std::env::var("BCS_REGISTRATION_TEST_INDEX")
        .unwrap()
        .parse()
        .unwrap();
    let repo = store(
        Arc::new(LocalSqliteDbPlugin::new_file(path).unwrap()),
        "dev",
    );
    let winner = repo.reserve(candidate(index)).await.unwrap();
    assert!(repo.get("Provider", "Ref").await.unwrap().unwrap() == winner);
}

// Inject infrastructure failures below the real store. Only SQL templates are
// recorded: parameter values include internal credentials and must stay private.
struct ControlledDb {
    db: Arc<dyn DbPlugin>,
    execute_results: Mutex<VecDeque<DbResult<DbExecuteResult>>>,
    query_error: Mutex<Option<DbError>>,
    calls: Mutex<Vec<(bool, String)>>,
}

impl ControlledDb {
    fn new(db: Arc<dyn DbPlugin>) -> Self {
        Self {
            db,
            execute_results: Mutex::new(VecDeque::new()),
            query_error: Mutex::new(None),
            calls: Mutex::new(Vec::new()),
        }
    }

    fn fail_execute(&self, error: DbError) {
        self.execute_results.lock().unwrap().push_back(Err(error));
    }
}

#[async_trait]
impl DbPlugin for ControlledDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.calls
            .lock()
            .unwrap()
            .push((false, statement.sql().to_string()));
        let failure = self.query_error.lock().unwrap().take();
        if let Some(error) = failure {
            return Err(error);
        }
        self.db.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.calls
            .lock()
            .unwrap()
            .push((true, statement.sql().to_string()));
        let result = self.execute_results.lock().unwrap().pop_front();
        if let Some(result) = result {
            return result;
        }
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

#[tokio::test]
async fn arbitrary_insert_failures_cannot_be_hidden_by_an_existing_winner() {
    let db = Arc::new(ControlledDb::new(sqlite().await));
    let repo = store(db.clone(), "dev");
    repo.reserve(candidate(0)).await.unwrap();
    for error in [
        DbError::Backend("disk is full".into()),
        DbError::Backend("connection 1062 timed out".into()),
        DbError::Backend(
            "NOT NULL constraint failed: bcs_provider_registrations.record_json".into(),
        ),
        DbError::InvalidInput("UNIQUE constraint failed".into()),
    ] {
        db.calls.lock().unwrap().clear();
        db.fail_execute(error);
        assert!(repo.reserve(candidate(1)).await.is_err());
        assert!(db.calls.lock().unwrap().iter().all(|(write, _)| *write));
    }
    assert!(repo.get("Provider", "Ref").await.unwrap().unwrap() == candidate(0));
}

#[tokio::test]
async fn read_and_completion_failures_propagate_without_changing_the_record() {
    let db = Arc::new(ControlledDb::new(sqlite().await));
    let repo = store(db.clone(), "dev");
    repo.reserve(candidate(0)).await.unwrap();
    *db.query_error.lock().unwrap() = Some(DbError::Backend("read failed".into()));
    assert!(repo.get("Provider", "Ref").await.is_err());
    *db.query_error.lock().unwrap() = Some(DbError::Backend("winner read failed".into()));
    assert!(repo.reserve(candidate(1)).await.is_err());
    db.fail_execute(DbError::Backend("write failed".into()));
    assert!(repo.complete("Provider", "Ref").await.is_err());
    assert!(repo.get("Provider", "Ref").await.unwrap().unwrap() == candidate(0));
}

#[tokio::test]
async fn malformed_json_is_an_error_without_exposing_credentials() {
    let db = sqlite().await;
    let repo = store(db.clone(), "dev");
    repo.reserve(candidate(0)).await.unwrap();
    let secret = candidate(0).bot_token;
    let malformed = serde_json::json!({"mode": secret}).to_string();
    db.execute(DbStatement::with_params(
        "UPDATE bcs_provider_registrations SET record_json = ?",
        vec![DbValue::from(malformed)],
    ))
    .await
    .unwrap();
    let error = repo
        .get("Provider", "Ref")
        .await
        .err()
        .expect("corrupt JSON must fail");
    assert!(!format!("{error:?}").contains(&secret));
}

#[tokio::test]
async fn mysql_uses_strict_insert_and_only_reads_a_verified_duplicate() {
    let db = Arc::new(ControlledDb::new(sqlite().await));
    store(db.clone(), "dev")
        .reserve(candidate(0))
        .await
        .unwrap();
    db.calls.lock().unwrap().clear();
    let repo = DbProviderRegistrationStore::new(db.clone(), DbSqlFlavor::Mysql, "dev".into());
    db.fail_execute(DbError::Backend(
        "mysql execute failed: Server error: `ERROR 23000 (1062): Duplicate entry 'identity' for key 'uk_registration_ref_env''".into()
    ));
    assert!(repo.reserve(candidate(1)).await.unwrap() == candidate(0));
    let calls = db.calls.lock().unwrap();
    assert!(
        calls[0].0
            && calls[0]
                .1
                .starts_with("INSERT INTO bcs_provider_registrations")
    );
    assert!(!calls[0].1.contains("IGNORE"));
    assert!(!calls[0].1.contains("UPDATE"));
    assert_eq!(calls.iter().filter(|(write, _)| !write).count(), 1);
    drop(calls);
    db.calls.lock().unwrap().clear();
    db.fail_execute(DbError::Backend("mysql connection 1062 failed".into()));
    assert!(repo.reserve(candidate(2)).await.is_err());
    assert!(db.calls.lock().unwrap().iter().all(|(write, _)| *write));
}

#[tokio::test]
async fn mysql_prepared_duplicate_recovers_but_unrelated_failures_do_not() {
    let db = Arc::new(ControlledDb::new(sqlite().await));
    store(db.clone(), "dev")
        .reserve(candidate(0))
        .await
        .unwrap();
    let repo = DbProviderRegistrationStore::new(db.clone(), DbSqlFlavor::Mysql, "dev".into());
    let duplicate = "mysql prepared execute failed: Server error: `ERROR 23000 (1062): Duplicate entry 'identity' for key 'bcs_provider_registrations.uk_registration_ref_env''";
    db.fail_execute(DbError::Backend(duplicate.into()));
    assert!(repo.reserve(candidate(1)).await.unwrap() == candidate(0));
    for failure in [
        format!("{duplicate}; close failed: network disconnected"),
        duplicate.replace("uk_registration_ref_env", "unrelated_trigger_constraint"),
        format!(
            "mysql execute failed: value rejected: {}",
            candidate(1).bot_token
        ),
    ] {
        db.calls.lock().unwrap().clear();
        db.fail_execute(DbError::Backend(failure));
        let error = repo
            .reserve(candidate(1))
            .await
            .err()
            .expect("write failure must propagate");
        assert!(!format!("{error:?}").contains(&candidate(1).bot_token));
        assert!(db.calls.lock().unwrap().iter().all(|(write, _)| *write));
    }
}

#[tokio::test]
async fn mysql_async_display_recovers_both_constraints_with_both_driver_wrappers() {
    let db = Arc::new(ControlledDb::new(sqlite().await));
    store(db.clone(), "dev")
        .reserve(candidate(0))
        .await
        .unwrap();
    let repo = DbProviderRegistrationStore::new(db.clone(), DbSqlFlavor::Mysql, "dev".into());
    // Verified against the locked mysql_async 0.34.2 Error::Server Display:
    // #[error("Server error: `{}'", _0)]. The wrapper CLOSES with an apostrophe,
    // not a backtick. The key's closing apostrophe therefore produces "key''".
    for wrapper in ["mysql execute failed", "mysql prepared execute failed"] {
        for key in [
            "uk_registration_ref_env",
            "bcs_provider_registrations.uk_registration_ref_env",
            "uk_registration_bot_env",
            "bcs_provider_registrations.uk_registration_bot_env",
        ] {
            let error = format!(
                "{wrapper}: Server error: `ERROR 23000 (1062): Duplicate entry 'identity' for key '{key}''"
            );
            db.calls.lock().unwrap().clear();
            db.fail_execute(DbError::Backend(error.clone()));
            assert!(repo.reserve(candidate(1)).await.unwrap() == candidate(0));
            let reads = db
                .calls
                .lock()
                .unwrap()
                .iter()
                .filter(|(write, _)| !write)
                .count();
            assert_eq!(reads, 1);

            // A bot UUID collision without the requested identity remains an
            // error: the repository must never return another scope's token.
            let mut other_identity = candidate(0);
            other_identity.provider_bot_ref = "Other".into();
            db.fail_execute(DbError::Backend(error));
            assert!(repo.reserve(other_identity).await.is_err());
        }
        for server_error in [
            "ERROR 23000 (1048): Column 'record_json' cannot be null",
            "ERROR 23000 (1062): Duplicate entry 'identity' for key 'unrelated_constraint'",
        ] {
            db.calls.lock().unwrap().clear();
            db.fail_execute(DbError::Backend(format!(
                "{wrapper}: Server error: `{server_error}'"
            )));
            assert!(repo.reserve(candidate(1)).await.is_err());
            assert!(db.calls.lock().unwrap().iter().all(|(write, _)| *write));
        }
    }
}

#[tokio::test]
async fn mysql_zero_changed_rows_distinguishes_completed_from_missing() {
    let db = Arc::new(ControlledDb::new(sqlite().await));
    let sqlite_repo = store(db.clone(), "dev");
    sqlite_repo.reserve(candidate(0)).await.unwrap();
    sqlite_repo.complete("Provider", "Ref").await.unwrap();
    let repo = DbProviderRegistrationStore::new(db.clone(), DbSqlFlavor::Mysql, "dev".into());
    for reference in ["Ref", "missing"] {
        db.execute_results
            .lock()
            .unwrap()
            .push_back(Ok(DbExecuteResult {
                affected_rows: 0,
                last_insert_id: None,
            }));
        assert_eq!(
            repo.complete("Provider", reference).await.is_ok(),
            reference == "Ref"
        );
    }
    let calls = db.calls.lock().unwrap();
    assert!(
        calls
            .iter()
            .any(|(write, sql)| *write && sql.contains("gmt_modified = NOW()"))
    );
}

#[test]
fn mysql_migration_has_binary_identity_keys_and_audit_columns() {
    let sql = migration("mysql", "029_provider_registrations.sql").to_ascii_lowercase();
    for column in ["env", "provider_id", "provider_bot_ref", "bot_uuid"] {
        assert!(
            sql.lines()
                .any(|line| line.trim_start().starts_with(column) && line.contains("varbinary(")),
            "{column} must use exact binary identity"
        );
    }
    assert!(
        sql.contains("unique key uk_registration_ref_env (env, provider_id, provider_bot_ref)")
    );
    assert!(sql.contains("unique key uk_registration_bot_env (env, bot_uuid)"));
    assert!(sql.contains("record_json longtext not null"));
    assert!(sql.contains("completed tinyint(1) not null default 0"));
    assert!(sql.contains("gmt_create timestamp not null default current_timestamp"));
    assert!(sql.contains("gmt_modified timestamp not null default current_timestamp"));
}
