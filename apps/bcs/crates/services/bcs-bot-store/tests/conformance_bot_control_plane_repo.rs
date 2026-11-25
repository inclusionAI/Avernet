#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use bcs_test_support::contract::repo::bot_control_plane_repo_port_contract_tests;
#[path = "control_plane_parts/cases_1.rs"]
mod cases_1;
#[path = "support/candidate_friend_set_contract.rs"]
mod candidate_friend_set_contract;
#[path = "control_plane_parts/cases_2.rs"]
mod cases_2;
#[path = "control_plane_parts/cases_3.rs"]
mod cases_3;

use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot_store::{MemoryBotRepo, PersistentBotRepo};
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue as Value, db_get_column, db_get_column_opt,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCandidateReadQuery, BotCandidateVisibility, BotCapabilities, BotSearchCandidateQuery, BotSearchFriendshipFilter,
    BotControlPlaneDescriptorPatch, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotControlPlaneRecord, BotControlPlaneRepoPort, BotRepoPort, BotTaskModesQuery,
    FriendCheckInStrategy, TaskModeMatch, UserVisibility,
};
use bcs_service_api::types::ServiceError;
use tokio::sync::Barrier;


struct SynchronizedSnapshotDb {
    inner: Arc<dyn DbPlugin>,
    snapshot_read_barrier: Barrier,
}

#[async_trait]
impl DbPlugin for SynchronizedSnapshotDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let synchronize_snapshot = statement
            .sql()
            .starts_with("SELECT bot_info FROM bcs_bots");
        let rows = self.inner.query(statement).await?;
        if synchronize_snapshot {
            self.snapshot_read_barrier.wait().await;
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

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

struct UnchangedUpdateDb;

#[async_trait]
impl DbPlugin for UnchangedUpdateDb {
    async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
        Ok(vec![DbRow::new(BTreeMap::from([
            ("bot_uuid".to_string(), Value::from("unchanged")),
            ("name".to_string(), Value::from("Unchanged")),
            ("bot_info".to_string(), Value::from("{}")),
            ("visibility".to_string(), Value::from("protected")),
            ("status".to_string(), Value::from("online")),
            ("actor_kind".to_string(), Value::from("bot")),
            ("env".to_string(), Value::from("dev")),
            ("created_by".to_string(), Value::from("staff-1")),
            ("agent_code".to_string(), Value::Null),
            ("gmt_create_ms".to_string(), Value::from(1_000_i64)),
            ("gmt_modified_ms".to_string(), Value::from(1_000_i64)),
        ]))])
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        Ok(DbExecuteResult {
            affected_rows: 0,
            last_insert_id: None,
        })
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        Ok(Vec::new())
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

async fn fixture() -> (PersistentBotRepo, Arc<dyn DbPlugin>) {
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_sql_flavor(
        db.clone(),
        DbSqlFlavor::Sqlite,
    );
    (repo, db)
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    create_control_plane_schema(db.as_ref()).await;
    db
}

async fn create_control_plane_schema(db: &dyn DbPlugin) {
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            agent_card TEXT,
            connection_mode TEXT DEFAULT 'plugin',
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            bot_uuid TEXT NOT NULL,
            name TEXT NOT NULL,
            bot_info TEXT,
            session_token TEXT,
            registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            env TEXT,
            visibility TEXT NOT NULL DEFAULT 'public',
            created_by TEXT,
            actor_kind TEXT NOT NULL DEFAULT 'bot',
            status TEXT NOT NULL DEFAULT 'online',
            is_deleted INTEGER NOT NULL DEFAULT 0,
            agent_code TEXT,
            task_claim_mode INTEGER NOT NULL DEFAULT 0,
            task_dream_mode INTEGER NOT NULL DEFAULT 0,
            user_visibility TEXT NOT NULL DEFAULT 'protected',
            friend_ext JSON,
            friend_check_in_strategy TEXT NOT NULL DEFAULT 'APPROVAL',
            UNIQUE (bot_uuid, env)
        )",
    ))
    .await
    .expect("create bots table");
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_friendships (
            left_bot TEXT NOT NULL,
            right_bot TEXT NOT NULL,
            env TEXT NOT NULL,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (left_bot, right_bot, env)
        )",
    ))
    .await
    .expect("create friendships table");
}

#[allow(clippy::too_many_arguments)]
async fn seed_bot(
    db: &dyn DbPlugin,
    bot_id: &str,
    name: &str,
    kind: &str,
    visibility: &str,
    status: &str,
    created_by: Option<&str>,
    timestamp: &str,
) {
    let bot_info = serde_json::json!({
        "summary": format!("summary-{bot_id}"),
        "domains": ["planning"],
        "skills": [{"name": "plan", "description": "Make a plan"}],
        "scopes": ["workspace"]
    })
    .to_string();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots
         (gmt_create, gmt_modified, bot_uuid, name, bot_info, session_token,
          registered_at, updated_at, env, visibility, created_by, actor_kind,
          status, is_deleted, agent_code)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        vec![
            Value::from(timestamp),
            Value::from(timestamp),
            Value::from(bot_id),
            Value::from(name),
            Value::from(bot_info),
            Value::from(format!("token-{bot_id}")),
            Value::from(timestamp),
            Value::from(timestamp),
            Value::from("dev"),
            Value::from(visibility),
            created_by.map(Value::from).unwrap_or(Value::Null),
            Value::from(kind),
            Value::from(status),
            Value::from(format!("agent-{bot_id}")),
        ],
    ))
    .await
    .expect("seed bot row");
}


/// Sort a Vec<String> in place so roster filter assertions stay order-independent
/// (the repo orders by gmt_create DESC, bot_uuid ASC, which the caller should not re-derive).
fn sorted_ids(mut ids: Vec<String>) -> Vec<String> {
    ids.sort();
    ids
}

/// Extract + sort the bot_ids from a roster query result for order-independent comparison.
fn roster_ids(records: Vec<BotControlPlaneRecord>) -> Vec<String> {
    sorted_ids(records.into_iter().map(|record| record.bot_id).collect())
}


/// Build a `BotTaskModesQuery` with task toggles unset so the visibility /
/// status / user_visibility filters are the only narrowing dimensions. Used by
/// the filter-branch coverage tests below.
fn task_modes_query(
    env: &str,
    visibility: Option<String>,
    status: Option<ActorStatus>,
    user_visibility: Option<UserVisibility>,
) -> BotTaskModesQuery {
    BotTaskModesQuery {
        env: env.to_string(),
        task_claim_mode: None,
        task_dream_mode: None,
        match_mode: TaskModeMatch::Any,
        visibility,
        status,
        user_visibility,
    }
}

#[tokio::test]
async fn memory_candidate_friend_set_contract() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp.path().to_path_buf());
    candidate_friend_set_contract::verify(
        &repo,
        &repo,
        &bcs_config::resolve_env_str(),
        "memory-candidate-set",
        // The Memory store uses the Bot id as a directory name, so the
        // filesystem's 255-byte component limit is its maximum identity.
        255,
    )
    .await;
}

#[tokio::test]
async fn sqlite_candidate_friend_set_contract() {
    let (repo, _db) = fixture().await;
    candidate_friend_set_contract::verify(
        &repo,
        &repo,
        &bcs_config::resolve_env_str(),
        "sqlite-candidate-set",
        // `bcs_bots.bot_uuid` is VARCHAR(256).
        256,
    )
    .await;
}

/// The full MySQL migration chain runs multiple contracts in the same env.
/// Reproduce that precondition on SQLite too: a public unrelated Bot must not
/// change this contract's capacity totals or quoted-ID assertions.
#[tokio::test]
async fn candidate_friend_set_contract_ignores_other_contract_rows() {
    let (repo, _db) = fixture().await;
    repo.create_registration_if_absent(
        "unrelated-public-bot".into(),
        BotCapabilities {
            name: Some("Other contract's public Bot".into()),
            visibility: "public".into(),
            ..Default::default()
        },
        "other-owner",
        "unrelated-public-bot-token",
    ).await.expect("seed unrelated public bot");
    candidate_friend_set_contract::verify(
        &repo, &repo, &bcs_config::resolve_env_str(), "isolated-candidates", 256,
    ).await;
}

/// The candidate stores must bind the whole friend set as ONE JSON array that
/// the dialect expands with `json_each`, never as a per-friend `UNION ALL`
/// chain. Page and COUNT share the same conditions and bindings; only the
/// page statement adds LIMIT/OFFSET. This is the regression evidence for the
/// old behavior that failed at 501 friends on SQLite.
#[tokio::test]
async fn sqlite_candidate_friend_set_uses_one_json_binding_for_page_and_count() {
    let inner = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    create_control_plane_schema(inner.as_ref()).await;
    let db = Arc::new(RecordingDb::new(inner));
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), DbSqlFlavor::Sqlite);
    let env = bcs_config::resolve_env_str();
    let acting = "candidate-shape-acting".to_string();
    assert!(
        repo.create_registration_if_absent(
            acting.clone(),
            BotCapabilities {
                name: Some("Candidate Shape Acting".into()),
                visibility: "private".into(),
                ..Default::default()
            },
            "staff-1",
            "candidate-shape-token",
        )
        .await
        .expect("register acting bot")
    );

    let all_friends = (0..1_000)
        .map(|index| format!("candidate-shape-friend-{index:04}"))
        .collect::<HashSet<_>>();
    let mut observed_param_counts = Vec::new();
    for count in [0_usize, 100, 500, 501, 1_000] {
        let friends = all_friends
            .iter()
            .take(count)
            .cloned()
            .collect::<HashSet<_>>();
        for (viewer, purpose) in [
            (ActorKind::Bot, BotCandidateVisibility::Discovery),
            (ActorKind::Bot, BotCandidateVisibility::Collaboration),
            (ActorKind::Human, BotCandidateVisibility::Discovery),
            (ActorKind::Human, BotCandidateVisibility::Collaboration),
        ] {
            db.clear();
            let (page, total) = repo
                .list_control_plane_candidates(BotCandidateReadQuery {
                    acting_bot_id: acting.clone(),
                    env: env.clone(),
                    visibility: purpose,
                    viewer_kind: viewer,
                    friend_ids: friends.clone(),
                    name: None,
                    offset: 0,
                    limit: 1,
                })
                .await
                .expect("list candidate SQL shape");
            let statements = db.take();
            assert_eq!(
                statements.len(),
                2,
                "list must stay page + COUNT ({viewer:?}/{purpose:?}/{count})",
            );
            assert!(page.is_empty());
            assert_eq!(total, 0);
            let (page_statement, count_statement) = (&statements[0], &statements[1]);
            for (label, (sql, params)) in [
                ("page", page_statement),
                ("count", count_statement),
            ] {
                assert!(
                    sql.contains("json_each(?)"),
                    "{label} must expand one bound JSON array: {sql}",
                );
                assert!(
                    !sql.contains("UNION ALL"),
                    "{label} must not build a per-friend UNION chain: {sql}",
                );
                let friend_json = params[0].as_str().expect("friend JSON parameter");
                let decoded: Vec<String> =
                    serde_json::from_str(friend_json).expect("valid friend JSON");
                assert_eq!(decoded.len(), count);
            }
            assert_eq!(
                page_statement.1[..count_statement.1.len()],
                count_statement.1[..],
                "page and COUNT must share every condition binding",
            );
            if count == 1_000 {
                let json = page_statement.1[0].as_str().expect("friend JSON");
                println!(
                    "candidate friend set evidence: friends=1000 json_bytes={} page_sql_bytes={} count_sql_bytes={} params={}",
                    json.len(),
                    page_statement.0.len(),
                    count_statement.0.len(),
                    page_statement.1.len(),
                );
            }
            observed_param_counts.push((viewer, purpose, count, page_statement.1.len()));
        }

        db.clear();
        let (page, total) = repo
            .search_control_plane_candidates(BotSearchCandidateQuery {
                acting_bot_id: acting.clone(),
                env: env.clone(),
                visibility: BotCandidateVisibility::Collaboration,
                viewer_kind: Some(ActorKind::Bot),
                friend_ids: friends,
                bot_uuids: None,
                name: None,
                q: Some("candidate shape".into()),
                visibility_filter: None,
                user_visibility: None,
                status: None,
                friendship: Some(BotSearchFriendshipFilter::All),
                tc_bot: None,
                name_only: true,
                offset: 0,
                limit: 100,
            })
            .await
            .expect("search candidate SQL shape");
        let statements = db.take();
        assert_eq!(statements.len(), 2, "search must stay page + COUNT");
        assert!(page.is_empty());
        assert_eq!(total, 0);
        for (label, (sql, params)) in [
            ("page", &statements[0]),
            ("count", &statements[1]),
        ] {
            assert!(sql.contains("json_each(?)"), "{label}: {sql}");
            assert!(!sql.contains("UNION ALL"), "{label}: {sql}");
            assert_eq!(
                serde_json::from_str::<Vec<String>>(params[0].as_str().unwrap())
                    .unwrap()
                    .len(),
                count,
            );
        }
        assert_eq!(
            statements[0].1[..statements[1].1.len()],
            statements[1].1[..],
            "search page and COUNT share every condition binding",
        );
    }
    let param_counts = observed_param_counts
        .iter()
        .map(|(_, _, _, params)| *params)
        .collect::<HashSet<_>>();
    assert_eq!(
        param_counts.len(),
        1,
        "binding count must not grow with the friend set: {observed_param_counts:?}",
    );
}

struct RecordingDb {
    inner: Arc<dyn DbPlugin>,
    statements: std::sync::Mutex<Vec<(String, Vec<Value>)>>,
}

impl RecordingDb {
    fn new(inner: Arc<dyn DbPlugin>) -> Self {
        Self {
            inner,
            statements: std::sync::Mutex::new(Vec::new()),
        }
    }

    fn clear(&self) {
        self.statements.lock().expect("recording lock").clear();
    }

    fn take(&self) -> Vec<(String, Vec<Value>)> {
        std::mem::take(&mut *self.statements.lock().expect("recording lock"))
    }
}

#[async_trait]
impl DbPlugin for RecordingDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.statements.lock().expect("recording lock").push((
            statement.sql().to_string(),
            statement.params().to_vec(),
        ));
        self.inner.query(statement).await
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

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

#[tokio::test]
async fn card_name_rejects_legacy_rename_but_allows_other_control_plane_updates() {
    let (repo, db) = fixture().await;
    seed_bot(db.as_ref(), "card-owned", "Card Agent", "bot", "public", "online", None, "2026-11-03 00:00:00").await;
    db.execute(DbStatement::new("UPDATE bcs_bots SET agent_card='{\"name\":\"Card Agent\"}' WHERE bot_uuid='card-owned' AND env='dev'")).await.unwrap();
    let error = repo.patch_control_plane("card-owned", "dev", BotControlPlanePatch {
        name: Some("Legacy Rename".into()), ..Default::default()
    }).await.unwrap_err();
    assert!(matches!(error, ServiceError::Conflict(_)));
    let updated = repo.patch_control_plane("card-owned", "dev", BotControlPlanePatch {
        name: Some("Card Agent".into()), visibility: Some("private".into()), ..Default::default()
    }).await.unwrap().unwrap();
    assert_eq!(updated.name, "Card Agent");
    assert_eq!(updated.visibility, "private");
}
