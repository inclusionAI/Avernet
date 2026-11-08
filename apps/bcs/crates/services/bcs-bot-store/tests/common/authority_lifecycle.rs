//! Shared SQL lifecycle composites (bot-authority
//! plan Task 4): trusted ownership registration,
//! governance initialize and Provider retirement, over
//! the REAL composite transactions with the real
//! schema.
//!
//! Every scenario function takes the [`Fixture`] — the
//! suite runs the same scenarios on the SQLite store
//! (local suites) and, via the plan §6 gate
//! (`authority_mysql.rs`), on the MySQL store over the
//! FULL MySQL migration chain. Scenario bodies stay
//! dialect-neutral; only the fixture constructors and
//! the two SQLite-ism seeds branch on the flavor.
//!
//! The REAL composite transaction is driven with one
//! deterministic infrastructure-failure injection at a
//! time (a message pattern match on the composed
//! transaction statements — the same `ControlledDb`
//! rollback-assertion precedent). Post-state touches
//! the same database: NO half registration, NO owner
//! edge, NO profile, NO audit, NO token mapping and
//! NO runtime activation after any failure.
#![allow(dead_code)]

use std::sync::{Arc, Mutex};

use bcs_bot_store::{DbProviderStore, PersistentBotRepo};
use bcs_db_api::{
    DbError, DbExecuteResult, DbPlugin, DbRow, DbResult, DbHealth,
    DbSqlFlavor, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::port::repo::{
    bot_provider::BotProviderRepoPort, BotAuthorityRepoPort, BotRepoPort,
    PermissionProfileRepoPort,
};
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityMutation, BotAccessRelation, ManagerEdit,
    ManagerEditAction, OwnershipInitialization,
};
use bcs_service_api::BotCapabilities;

pub fn resolve_env() -> String {
    bcs_config::resolve_env_str()
}

// ---------------------------------------------------------------------------
// Fixture schema (the same tables the migrations create).
// ---------------------------------------------------------------------------

pub const SCHEMA: &[&str] = &[
    "CREATE TABLE bcs_bots (\
        bot_uuid TEXT NOT NULL, \
        env TEXT NOT NULL, \
        name TEXT NOT NULL DEFAULT '', \
        bot_info TEXT, \
        session_token TEXT, \
        created_by TEXT, \
        visibility TEXT NOT NULL DEFAULT 'protected', \
        status TEXT NOT NULL DEFAULT 'online', \
        actor_kind TEXT NOT NULL DEFAULT 'bot', \
        is_deleted INTEGER NOT NULL DEFAULT 0, \
        ownership_version INTEGER NOT NULL DEFAULT 0, \
        agent_code TEXT, \
        connection_mode TEXT, \
        provider_id TEXT, \
        provider_bot_ref TEXT, \
        webhook_url TEXT, \
        registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        UNIQUE (bot_uuid, env))",
    "CREATE UNIQUE INDEX uk_bcs_bots_provider_ref_env \
        ON bcs_bots (env, provider_id, provider_bot_ref)",
    "CREATE TABLE edge_grants (\
        id INTEGER PRIMARY KEY AUTOINCREMENT, \
        env TEXT NOT NULL, \
        from_id TEXT NOT NULL, \
        to_id TEXT NOT NULL, \
        grant_kind TEXT NOT NULL, \
        grant_ref_id INTEGER NOT NULL, \
        rules TEXT, \
        status TEXT NOT NULL DEFAULT 'approved', \
        originator_policy_type TEXT NOT NULL DEFAULT 'any', \
        originator_policy_data TEXT, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        UNIQUE (from_id, to_id, env, grant_kind, grant_ref_id))",
    "CREATE UNIQUE INDEX uk_edge_approved_owner \
        ON edge_grants(env, to_id) \
        WHERE grant_kind = 'owner' AND status = 'approved'",
    "CREATE TABLE bot_authority_changes (\
        id INTEGER PRIMARY KEY AUTOINCREMENT, \
        env TEXT NOT NULL, \
        bot_id TEXT NOT NULL, \
        mutation_id TEXT NOT NULL, \
        sequence INTEGER NOT NULL, \
        action TEXT NOT NULL, \
        actor_kind TEXT NOT NULL, \
        actor_id TEXT NOT NULL, \
        subject_id TEXT NOT NULL, \
        before_state TEXT, \
        after_state TEXT, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        UNIQUE (env, mutation_id, sequence))",
    "CREATE TABLE permission_profiles (\
        id INTEGER PRIMARY KEY AUTOINCREMENT, \
        bot_id TEXT NOT NULL, \
        env TEXT NOT NULL, \
        name TEXT NOT NULL DEFAULT 'default', \
        description TEXT, \
        rules_template TEXT NOT NULL, \
        revision INTEGER NOT NULL DEFAULT 1, \
        digest TEXT NOT NULL, \
        is_default INTEGER NOT NULL DEFAULT 0, \
        status TEXT NOT NULL DEFAULT 'active', \
        created_by TEXT NOT NULL, \
        updated_by TEXT, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    "CREATE UNIQUE INDEX uk_profile_bot_env_default \
        ON permission_profiles(bot_id, env, is_default) \
        WHERE status = 'active'",
    "CREATE TABLE bcs_provider_bot_bindings (\
        bot_uuid TEXT NOT NULL, env TEXT NOT NULL, \
        provider_id TEXT NOT NULL, provider_bot_ref TEXT NOT NULL, \
        webhook_url TEXT, disabled INTEGER NOT NULL DEFAULT 0, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE bcs_user_identities (\
        id INTEGER PRIMARY KEY AUTOINCREMENT, \
        user_id TEXT NOT NULL, \
        auth_source TEXT NOT NULL, \
        external_user_id TEXT NOT NULL, \
        user_name TEXT, env TEXT NOT NULL UNIQUE, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        UNIQUE (user_id))",
    "CREATE TABLE bot_ownership_transfers (\
        id INTEGER PRIMARY KEY AUTOINCREMENT, \
        env TEXT NOT NULL, \
        bot_id TEXT NOT NULL, \
        target_owner_actor_id TEXT NOT NULL, \
        from_owner_actor_id TEXT NOT NULL, \
        expected_ownership_version INTEGER NOT NULL, \
        status TEXT NOT NULL DEFAULT 'pending', \
        requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        expires_at TEXT NOT NULL, \
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        decided_at TEXT, \
        decided_by_kind TEXT, \
        decided_by_id TEXT, \
        reason TEXT, \
        result_ownership_version INTEGER, \
        bot_name_snapshot TEXT, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        UNIQUE (env, bot_id, target_owner_actor_id))",
    "CREATE UNIQUE INDEX uk_transfer_pending_retire \
        ON bot_ownership_transfers (env, bot_id) WHERE status = 'pending'",
];

/// Deterministic infrastructure-failure injection: when
/// armed, a transaction whose ANY step contains the
/// pattern fails BEFORE executing anything (the same
/// observable result as a mid-transaction abort for an
/// all-or-nothing transaction).
struct FailInjectDb {
    inner: Arc<dyn DbPlugin>,
    armed: Mutex<Vec<&'static str>>,
    /// One-shot transaction-entrance hook: runs a
    /// scenario-supplied async step BETWEEN the
    /// composite's pre-reads and its transaction — the
    /// deterministic seam for "committed state drifted
    /// after the pre-read" races (the plain count guard
    /// cannot see identity-only drift).
    hook: Mutex<Option<TxHook>>,
}

type TxHook = Box<
    dyn FnOnce()
        -> std::pin::Pin<
            Box<dyn Future<Output = ()> + Send>,
        > + Send,
>;

impl FailInjectDb {
    fn new(inner: Arc<dyn DbPlugin>) -> Arc<Self> {
        Arc::new(Self {
            inner,
            armed: Mutex::new(Vec::new()),
            hook: Mutex::new(None),
        })
    }

    fn arm(&self, pattern: &'static str) {
        self.armed.lock().unwrap().push(pattern);
    }

    fn armed(&self, sql: &str) -> bool {
        self.armed
            .lock()
            .unwrap()
            .iter()
            .any(|pattern| sql.contains(pattern))
    }

    /// Arm the one-shot hook: it runs (once) at the
    /// NEXT transaction's entrance, before any step.
    fn arm_transaction_hook<F, Fut>(&self, hook: F)
    where
        F: FnOnce() -> Fut + Send + 'static,
        Fut: Future<Output = ()> + Send + 'static,
    {
        *self.hook.lock().unwrap() =
            Some(Box::new(move || Box::pin(hook())));
    }
}

#[async_trait::async_trait]
impl DbPlugin for FailInjectDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        let hook = self.hook.lock().unwrap().take();
        if let Some(hook) = hook {
            hook().await;
        }
        let hit = steps.iter().any(|step| {
            let sql = match step {
                DbTransactionStep::Query(statement) => statement.sql(),
                DbTransactionStep::Execute(statement) => statement.sql(),
                DbTransactionStep::ExecuteChecked { statement, .. } => statement.sql(),
            };
            self.armed(sql)
        });
        if hit {
            return Err(DbError::Backend(
                "injected registration transaction failure".to_string(),
            ));
        }
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

pub struct Fixture {
    db: Arc<FailInjectDb>,
    bot_repo: Arc<PersistentBotRepo>,
    provider_repo: Arc<DbProviderStore>,
    authority: Arc<bcs_edge_permission_store::DbBotAuthorityStore>,
    profile_port: Arc<bcs_edge_permission_store::DbPermissionProfileStore>,
    flavor: DbSqlFlavor,
}

pub async fn new_sqlite_fixture() -> Fixture {
    let inner = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().expect("sqlite"));
    for statement in SCHEMA {
        inner
            .execute(DbStatement::new(*statement))
            .await
            .expect("authority fixture schema");
    }
    build_fixture(FailInjectDb::new(inner), DbSqlFlavor::Sqlite)
}

/// MySQL fixture over the given (already
/// migration-chain-applied) MySQL plugin.
pub async fn new_mysql_fixture(inner: Arc<dyn DbPlugin>) -> Fixture {
    build_fixture(FailInjectDb::new(inner), DbSqlFlavor::Mysql)
}

fn build_fixture(db: Arc<FailInjectDb>, flavor: DbSqlFlavor) -> Fixture {
    let env = resolve_env();
    let bot_repo = Arc::new(PersistentBotRepo::with_sql_flavor(
        db.clone(),
        flavor,
    ));
    let provider_repo = match flavor {
        DbSqlFlavor::Mysql => Arc::new(DbProviderStore::mysql(db.clone())),
        DbSqlFlavor::Sqlite => Arc::new(DbProviderStore::sqlite(db.clone())),
    };
    let authority = match flavor {
        DbSqlFlavor::Mysql => Arc::new(
            bcs_edge_permission_store::DbBotAuthorityStore::mysql(db.clone(), env),
        ),
        DbSqlFlavor::Sqlite => Arc::new(
            bcs_edge_permission_store::DbBotAuthorityStore::sqlite(db.clone(), env),
        ),
    };
    let profile_port = match flavor {
        DbSqlFlavor::Mysql => Arc::new(
            bcs_edge_permission_store::DbPermissionProfileStore::mysql(db.clone()),
        ),
        DbSqlFlavor::Sqlite => Arc::new(
            bcs_edge_permission_store::DbPermissionProfileStore::sqlite(db.clone()),
        ),
    };
    Fixture {
        db,
        bot_repo,
        provider_repo,
        authority,
        profile_port,
        flavor,
    }
}

/// A FRESH mutation id per command — the audit table keeps
/// UNIQUE (env, mutation_id, sequence), so two registrations
/// of the same owner need two mutations (exactly like the
/// real application surface).
fn mutation(owner: &str) -> OwnershipInitialization {
    static COUNTER: std::sync::atomic::AtomicU64 =
        std::sync::atomic::AtomicU64::new(1);
    let sequence =
        COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    OwnershipInitialization {
        owner_user_id: owner.to_string(),
        mutation: AuthorityMutation {
            mutation_id: format!("mutation-{owner}-{sequence}"),
            actor: AuthorityActor::Human {
                user_id: owner.to_string(),
            },
        },
    }
}


async fn seed_runtime_bot(fixture: &Fixture, bot_id: &str) -> bool {
    // The version-0 runtime entry keeps its own creation
    // path (no-Human runtime Bot, spec §10.3).
    fixture
        .bot_repo
        .create_registration_if_absent(
            bot_id.to_string(),
            BotCapabilities {
                name: Some("runtime".to_string()),
                ..Default::default()
            },
            "11111111",
            "runtime-token",
        )
        .await
        .expect("runtime registration")
}

fn capabilities() -> BotCapabilities {
    BotCapabilities {
        name: Some("Trusted Bot".to_string()),
        visibility: "protected".to_string(),
        ..Default::default()
    }
}

async fn column(
    db: &FailInjectDb,
    sql: &str,
    params: Vec<DbValue>,
    name: &str,
) -> Option<DbValue> {
    db.query(DbStatement::with_params(sql, params))
        .await
        .expect("fixture query")
        .first()
        .map(|row| row.get(name).expect("column").clone())
}

/// Post-failure assertions: a registration that failed
/// mid-composite wrote NO rows anywhere — and, pinned
/// explicitly, never wrote an OAuth identity row (the
/// identity-table workaround is gone; the Human ACTOR row
/// is registration's only Human creation).
async fn assert_no_half_registration(
    fixture: &Fixture,
    bot_id: &str,
) {
    // No bot row, no token mapping, no runtime activation.
    assert!(fixture
        .bot_repo
        .load_token(bot_id)
        .await
        .is_none());
    assert!(!fixture.bot_repo.is_connected(bot_id).await);
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bcs_bots",
            vec![],
        ))
        .await
        .expect("bots count");
    let bots: i64 = rows[0]
        .get("c")
        .expect("count")
        .as_i64()
        .expect("int");
    let human_rows = if bots == 0 { 0 } else {
        bcs_db_api::db_get_column::<i64>(
            &fixture
                .db
                .query(DbStatement::with_params(
                    "SELECT COUNT(*) AS c FROM bcs_bots WHERE actor_kind = 'human'",
                    vec![],
                ))
                .await
                .expect("humans count")[0],
            "c",
        )
        .expect("int")
    };
    assert_eq!(human_rows, 0, "no half-created Human actor row");
    {
        let rows = fixture
            .db
            .query(DbStatement::with_params(
                "SELECT COUNT(*) AS c FROM bcs_user_identities",
                vec![],
            ))
            .await
            .expect("identities count");
        let count: i64 = bcs_db_api::db_get_column(&rows[0], "c").unwrap();
        assert_eq!(
            count, 0,
            "a failed registration must not write OAuth identity rows"
        );
    }
    for (table, olumn) in [
        ("edge_grants", "c"),
        ("permission_profiles", "c"),
        ("bot_authority_changes", "c"),
    ] {
        let rows = fixture
            .db
            .query(DbStatement::with_params(
                &format!("SELECT COUNT(*) AS c FROM {table}"),
                vec![],
            ))
            .await
            .expect(table);
        let count: i64 = bcs_db_api::db_get_column(&rows[0], olumn).expect("int");
        assert_eq!(count, 0, "no half-written rows in {table}");
    }
}

// ---------------------------------------------------------------------------
// v1 Human-token composite (the SQL Bot Repo).
// ---------------------------------------------------------------------------

pub async fn trusted_v1_registration_commits_the_whole_state(fixture: &Fixture) {
    let created = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_abc12345".to_string(),
            capabilities(),
            "11111111",
            "bot-token-1",
            mutation("11111111"),
        )
        .await
        .expect("committed");
    assert!(created);

    // Bot row: version 1, owner as first created_by, token persisted.
    let version = column(
        &fixture.db,
        "SELECT ownership_version AS v, created_by AS cb, session_token AS tok, \
         actor_kind AS kind FROM bcs_bots WHERE bot_uuid = ?",
        vec!["bot_abc12345".into()],
        "v",
    )
    .await
    .expect("version");
    assert_eq!(version.as_i64(), Some(1));
    let (created_by, _): (String, String) = {
        let rows = fixture
            .db
            .query(DbStatement::with_params(
                "SELECT created_by AS cb, session_token AS tok, actor_kind AS kind \
                 FROM bcs_bots WHERE bot_uuid = ?",
                vec!["bot_abc12345".into()],
            ))
            .await
            .expect("row");
        (
            bcs_db_api::db_get_column(&rows[0], "cb").unwrap(),
            bcs_db_api::db_get_column(&rows[0], "tok").unwrap(),
        )
    };
    assert_eq!(created_by, "11111111");
    assert_eq!(
        fixture.bot_repo.load_token("bot_abc12345").await.as_deref(),
        Some("bot-token-1")
    );
    // Runtime was NOT activated by registration.
    assert!(!fixture.bot_repo.is_connected("bot_abc12345").await);

    // Human ACTOR row exists next to the bot row.
    let human = column(
        &fixture.db,
        "SELECT actor_kind FROM bcs_bots WHERE bot_uuid = ?",
        vec!["human_11111111".into()],
        "actor_kind",
    )
    .await;
    let _ = human;
    let actor: Option<String> = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT actor_kind FROM bcs_bots WHERE bot_uuid = ?",
            vec!["human_11111111".into()],
        ))
        .await
        .expect("human row")
        .first()
        .map(|row| bcs_db_api::db_get_column(row, "actor_kind").expect("str"));
    assert_eq!(actor.as_deref(), Some("human"));

    // Owner edge: approved, unified grant_ref_id = 0.
    let edge = fixture
        .authority
        .role("human_11111111", "bot_abc12345")
        .await
        .expect("role");
    assert_eq!(edge, Some(BotAccessRelation::Owner));

    // Default profile: wildcard allow, revision 1, active.
    let profile = fixture
        .profile_port
        .get_active_default("bot_abc12345", &resolve_env())
        .await
        .expect("default profile");
    assert_eq!(profile.revision, 1);
    assert_eq!(profile.is_default, true);
    assert_eq!(
        profile.rules_template,
        serde_json::from_str::<serde_json::Value>(
            bcs_edge_permission_store::lifecycle::TRUSTED_DEFAULT_RULES
        )
        .unwrap()
    );

    // Audit: one initialize row with the real initiator and no token.
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT action, actor_kind, actor_id, subject_id, after_state, \
             bot_id FROM bot_authority_changes",
            vec![],
        ))
        .await
        .expect("audit");
    assert_eq!(rows.len(), 1, "exactly one initialize audit row");
    let action: String = bcs_db_api::db_get_column(&rows[0], "action").unwrap();
    assert_eq!(action, "initialize");
    let actor_id: String = bcs_db_api::db_get_column(&rows[0], "actor_id").unwrap();
    assert_eq!(actor_id, "human_11111111");
    let subject: String = bcs_db_api::db_get_column(&rows[0], "subject_id").unwrap();
    assert_eq!(subject, "human_11111111");
    let after: String = bcs_db_api::db_get_column(&rows[0], "after_state").unwrap();
    assert_eq!(
        after,
        "{\"owner\":\"human_11111111\",\"version\":1}"
    );
    let audit_bot: String = bcs_db_api::db_get_column(&rows[0], "bot_id").unwrap();
    assert_eq!(audit_bot, "bot_abc12345");
    assert!(!after.contains("bot-token"));

    // Human idempotence: registering a second bot for the same
    // owner keeps one Human row.
    let second = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_abc99999".to_string(),
            capabilities(),
            "11111111",
            "bot-token-2",
            mutation("11111111"),
        )
        .await
        .expect("second bot");
    assert!(second);
    let humans = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bcs_bots WHERE actor_kind = 'human'",
            vec![],
        ))
        .await
        .expect("humans");
    assert_eq!(humans.len(), 1);
}

pub async fn trusted_v1_reused_bot_id_never_resets_owner(fixture: &Fixture) {
    // A version-0 runtime Bot pre-exists.
    assert!(seed_runtime_bot(&fixture, "bot_runtime1").await);
    // Trusted registration on the SAME id is refused (create-only).
    let reused = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_runtime1".to_string(),
            capabilities(),
            "11111111",
            "attacker-token",
            mutation("11111111"),
        )
        .await
        .expect("absent result");
    assert!(!reused);
    // Nothing on the preexisting state moved.
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT session_token, created_by, ownership_version FROM bcs_bots \
             WHERE bot_uuid = ?",
            vec!["bot_runtime1".into()],
        ))
        .await
        .expect("row");
    let token: String = bcs_db_api::db_get_column(&rows[0], "session_token").unwrap();
    assert_eq!(token, "runtime-token");
    let version: i64 = bcs_db_api::db_get_column(&rows[0], "ownership_version").unwrap();
    assert_eq!(version, 0, "runtime version-0 entry is preserved");
    let edges = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM edge_grants",
            vec![],
        ))
        .await
        .expect("edges");
    assert_eq!(edges.len(), 1);
    let count: i64 = bcs_db_api::db_get_column(&edges[0], "c").unwrap();
    assert_eq!(count, 0, "no authority rows added");
}

pub async fn trusted_v1_tombstoned_bot_id_never_resurrects(fixture: &Fixture) {
    assert!(seed_runtime_bot(&fixture, "bot_dead1").await);
    fixture
        .db
        .execute(DbStatement::with_params(
            "UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = ?",
            vec!["bot_dead1".into()],
        ))
        .await
        .expect("tombstone");
    let reused = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_dead1".to_string(),
            capabilities(),
            "11111111",
            "some-token",
            mutation("11111111"),
        )
        .await
        .expect("create-only refusals are not errors");
    assert!(!reused);
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = ?",
            vec!["bot_dead1".into()],
        ))
        .await
        .expect("row");
    let deleted: i64 = bcs_db_api::db_get_column(&rows[0], "is_deleted").unwrap();
    assert_eq!(deleted, 1, "still deleted");
}

/// The INSERT-ignore owner-actor write cannot prove an
/// EXISTING owner row is a live Human: a tombstoned owner
/// must refuse the whole transaction (Bot row, owner edge,
/// profile and audit all ride the same rollback).
pub async fn trusted_v1_registration_refuses_a_tombstoned_owner(fixture: &Fixture) {
    // Plant the owner Human actor directly, then tombstone it.
    fixture
        .db
        .execute(DbStatement::with_params(
            "INSERT INTO bcs_bots \
             (bot_uuid, actor_kind, name, env, created_by, visibility, status) \
             VALUES ('human_77777777', 'human', 'Dead Owner', ?, '77777777', \
                     'protected', 'online')",
            vec![resolve_env().into()],
        ))
        .await
        .expect("plant human actor");
    fixture
        .db
        .execute(DbStatement::new(
            "UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = 'human_77777777'",
        ))
        .await
        .expect("tombstone owner");

    let result = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_dead-owner".to_string(),
            capabilities(),
            "77777777",
            "owner-token",
            mutation("77777777"),
        )
        .await;
    assert!(
        result.is_err(),
        "a tombstoned owner must refuse the registration: {result:?}"
    );

    // Nothing leaked: no Bot row, no owner edge, no profile,
    // no audit row for this mutation, and the tombstone stands.
    for (table, where_clause) in [
        ("bcs_bots", "bot_uuid = 'bot_dead-owner'"),
        ("edge_grants", "to_id = 'bot_dead-owner'"),
        ("permission_profiles", "bot_id = 'bot_dead-owner'"),
        ("bot_authority_changes", "bot_id = 'bot_dead-owner'"),
    ] {
        let rows = fixture
            .db
            .query(DbStatement::new(&format!(
                "SELECT COUNT(*) AS c FROM {table} WHERE {where_clause}"
            )))
            .await
            .expect("rollback probe");
        let count: i64 = bcs_db_api::db_get_column(&rows[0], "c").unwrap();
        assert_eq!(count, 0, "no half-written rows in {table}");
    }
    let rows = fixture
        .db
        .query(DbStatement::new(
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'human_77777777'",
        ))
        .await
        .expect("human row");
    let deleted: i64 = bcs_db_api::db_get_column(&rows[0], "is_deleted").unwrap();
    assert_eq!(deleted, 1, "tombstone untouched by the refused registration");
}

// Injected failure points (4.1): owner write, profile write and
// audit write abort the WHOLE transaction.
pub async fn owner_edge_failure_rolls_back_everything(fixture: &Fixture) {
    fixture.db.arm("INSERT");
    // The bot-row INSERT is the first statement; arming every
    // insert aborts at the same transaction boundary — scope the
    // injection to the owner edge instead.
    fixture.db.armed.lock().unwrap().clear();
    fixture.db.arm("edge_grants");
    let error = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_fail1".to_string(),
            capabilities(),
            "11111111",
            "bot-token-x",
            mutation("11111111"),
        )
        .await
        .expect_err("owner write failure surfaces");
    assert!(
        error.to_string().contains("trusted bot registration failed"),
        "no SQL text leaks: {error}"
    );
    assert_no_half_registration(&fixture, "bot_fail1").await;
}

pub async fn profile_failure_rolls_back_everything(fixture: &Fixture) {
    fixture.db.arm("permission_profiles");
    let error = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_fail2".to_string(),
            capabilities(),
            "11111111",
            "bot-token-x",
            mutation("11111111"),
        )
        .await
        .expect_err("profile write failure surfaces");
    assert!(error.to_string().contains("trusted bot registration failed"));
    assert_no_half_registration(&fixture, "bot_fail2").await;
}

pub async fn audit_failure_rolls_back_everything(fixture: &Fixture) {
    fixture.db.arm("bot_authority_changes");
    let error = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_fail3".to_string(),
            capabilities(),
            "11111111",
            "bot-token-x",
            mutation("11111111"),
        )
        .await
        .expect_err("audit failure surfaces");
    assert!(error.to_string().contains("trusted bot registration failed"));
    assert_no_half_registration(&fixture, "bot_fail3").await;
}

pub async fn human_actor_write_failure_rolls_back_everything(fixture: &Fixture) {
    // The Human ACTOR insert is identifiable by its column list.
    fixture.db.arm("actor_kind, name, bot_info, session_token");
    let error = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_fail4".to_string(),
            capabilities(),
            "11111111",
            "bot-token-x",
            mutation("11111111"),
        )
        .await
        .expect_err("human create failure surfaces");
    assert!(error.to_string().contains("trusted bot registration failed"));
    assert_no_half_registration(&fixture, "bot_fail4").await;
}

/// Ruling T4-1 acceptance (spec §10.2 literal): a
/// fresh-Human registration creates the owner's Human
/// ACTOR row in the SAME transaction, and that owner can
/// afterwards pass the Task-2 human-existence guards (a
/// manager grant by that owner succeeds). The OAuth
/// identity table is never touched: a pre-existing row
/// (e.g. a real OAuth sync) keeps its bytes, and a Human
/// without one needs no workaround.
pub async fn registration_creates_the_owner_actor_row_and_the_owner_can_manage(fixture: &Fixture) {
    // A PRE-EXISTING OAuth identity row keeps its bytes
    // through the registration: the actor row — not this
    // table — carries authority existence now.
    fixture
        .db
        .execute(DbStatement::with_params(
            "INSERT INTO bcs_user_identities \
             (user_id, auth_source, external_user_id, env) \
             VALUES ('vip-owner', 'oauth-provider', 'ext-vip', 'fixture')",
            vec![],
        ))
        .await
        .expect("pre-existing identity");

    let registered = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_ownermanage".to_string(),
            capabilities(),
            "vip-owner",
            "bot-token-own",
            mutation("vip-owner"),
        )
        .await
        .expect("vip-owner registration commits");
    assert!(registered);
    // The registration ensured the owner's Human ACTOR row
    // (authorities read THIS registry, spec §3.1)...
    let row = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT actor_kind FROM bcs_bots WHERE bot_uuid = ?",
            vec!["human_vip-owner".into()],
        ))
        .await
        .expect("actor row");
    assert_eq!(row.len(), 1, "exactly one Human actor row");
    let actor_kind: String =
        bcs_db_api::db_get_column(&row[0], "actor_kind").unwrap();
    assert_eq!(actor_kind, "human");
    // ...and never wrote or replaced an OAuth identity row.
    let row = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT auth_source, external_user_id FROM bcs_user_identities \
             WHERE user_id = ?",
            vec!["vip-owner".into()],
        ))
        .await
        .expect("identity row");
    assert_eq!(row.len(), 1, "no duplicate identity row");
    let auth_source: String =
        bcs_db_api::db_get_column(&row[0], "auth_source").unwrap();
    assert_eq!(auth_source, "oauth-provider");

    // The second Human of the pairing: their own fresh
    // registration creates their actor row too (and needs
    // no OAuth identity row at all).
    let second = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_second1".to_string(),
            capabilities(),
            "second-owner",
            "bot-token-2nd",
            mutation("second-owner"),
        )
        .await
        .expect("second-owner registration commits");
    assert!(second);

    // The FIRST owner grants the second owner as a
    // manager — this used to fail with
    // bot_authority_human_unknown before the identity row
    // was ensured in-transaction (ruling T4-1).
    let change = fixture
        .authority
        .edit_manager(ManagerEdit {
            bot_id: "bot_ownermanage".to_string(),
            // The repo port contracts are keyed by the
            // persisted Human ACTOR id.
            user_id: "human_second-owner".to_string(),
            action: ManagerEditAction::Grant,
            mutation: mutation("vip-owner").mutation,
        })
        .await
        .expect("manager grant by the registered owner");
    assert_eq!(change.added, 1);
    assert_eq!(change.total, 1);
}

pub async fn mutation_must_match_the_verified_initiator(fixture: &Fixture) {
    let forged = OwnershipInitialization {
        owner_user_id: "11111111".to_string(),
        mutation: AuthorityMutation {
            mutation_id: "m-1".to_string(),
            actor: AuthorityActor::Human {
                user_id: "someone-else".to_string(),
            },
        },
    };
    let error = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_fail6".to_string(),
            capabilities(),
            "11111111",
            "bot-token-x",
            forged,
        )
        .await
        .expect_err("diverged initiator is refused");
    assert!(matches!(error, bcs_service_api::ServiceError::InvalidOperation { .. }));
    assert_no_half_registration(&fixture, "bot_fail6").await;
}

// ---------------------------------------------------------------------------
// Governance initialize_ownership.
// ---------------------------------------------------------------------------

pub async fn governance_initialize_backfills_once_and_is_idempotent(fixture: &Fixture) {
    assert!(seed_runtime_bot(&fixture, "bot_gov1").await);
    // Production shape: the governance backfill only applies
    // while the recorded Human actor is live (bcs-admin's
    // classify gate); the stored creator record (seeded as
    // '11111111' by the runtime entry) names the owner the
    // composite may initialize — byte-exact.
    fixture
        .bot_repo
        .ensure_human_actor("11111111", "11111111")
        .await
        .expect("ensure backfill owner actor");
    let view = fixture
        .bot_repo
        .initialize_ownership("bot_gov1", "11111111", mutation("11111111").mutation)
        .await
        .expect("initialize");
    assert_eq!(view.owner_user_id, "11111111");
    assert_eq!(view.owner_actor_id, "human_11111111");
    let role = fixture
        .authority
        .role("human_11111111", "bot_gov1")
        .await
        .expect("role");
    assert_eq!(role, Some(BotAccessRelation::Owner));
    // Repeat: the CURRENT owner is returned untouched.
    let view2 = fixture
        .bot_repo
        .initialize_ownership("bot_gov1", "11111111", mutation("11111111").mutation)
        .await
        .expect("idempotent");
    assert_eq!(view2.owner_user_id, "11111111");
    let audits = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bot_authority_changes",
            vec![],
        ))
        .await
        .expect("audits");
    let count: i64 = bcs_db_api::db_get_column(&audits[0], "c").unwrap();
    assert_eq!(count, 1, "no duplicate initialize audit");

    // A later DIFFERENT initialize never resets the owner.
    let error = fixture
        .bot_repo
        .initialize_ownership("bot_gov1", "99999999", mutation("99999999").mutation)
        .await
        .expect_err("cannot reset a committed owner");
    assert!(matches!(
        error,
        bcs_service_api::ServiceError::Conflict(_)
    ));
    let role = fixture
        .authority
        .role("human_11111111", "bot_gov1")
        .await
        .expect("role");
    assert_eq!(role, Some(BotAccessRelation::Owner));
}

pub async fn governance_initialize_refuses_creator_mismatch(fixture: &Fixture) {
    // The stored creator record is authority for the governance
    // backfill: its owner must name the recorded creator
    // byte-for-byte (a case-insensitive candidate list query is
    // never permission to mint).
    assert!(seed_runtime_bot(&fixture, "bot_mismatch").await);
    fixture
        .bot_repo
        .ensure_human_actor("22222222", "22222222")
        .await
        .expect("ensure echo candidate");
    let error = fixture
        .bot_repo
        .initialize_ownership("bot_mismatch", "22222222", mutation("22222222").mutation)
        .await
        .expect_err("mismatching operator must be refused");
    assert!(
        matches!(error, bcs_service_api::ServiceError::Conflict(_)),
        "Conflict expected, got: {error:?}"
    );
    // Nothing half-applied: version stays 0, no owner edge, no audit.
    let bots = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT ownership_version FROM bcs_bots WHERE bot_uuid = ?",
            vec!["bot_mismatch".into()],
        ))
        .await
        .expect("bot row");
    let version: i64 = bcs_db_api::db_get_column(&bots[0], "ownership_version").unwrap();
    assert_eq!(version, 0, "mismatch must not move the version");
    let roles = fixture
        .authority
        .role("human_22222222", "bot_mismatch")
        .await
        .expect("role");
    assert_eq!(roles, None, "no owner edge minted by the refusal");
    let audits = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bot_authority_changes",
            vec![],
        ))
        .await
        .expect("audit count");
    let count: i64 = bcs_db_api::db_get_column(&audits[0], "c").unwrap();
    assert_eq!(count, 0, "no audit written by the refusal");
}

pub async fn governance_initialize_never_targets_a_human_actor_row(fixture: &Fixture) {
    // A Human ACTOR row is the authority registry's record of a
    // person, never a physical Bot; management edges point only to
    // physical Bots. ensure_human_actor seeds such a row whose
    // created record matches the caller so only the kind guard
    // discriminates this scenario.
    fixture
        .bot_repo
        .ensure_human_actor("22222222", "22222222")
        .await
        .expect("ensure human actor row");
    let error = fixture
        .bot_repo
        .initialize_ownership("human_22222222", "22222222", mutation("22222222").mutation)
        .await
        .expect_err("a Human actor row must never be initialized");
    assert!(
        matches!(error, bcs_service_api::ServiceError::Conflict(_)),
        "Conflict expected, got: {error:?}"
    );
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT ownership_version FROM bcs_bots WHERE bot_uuid = ?",
            vec!["human_22222222".into()],
        ))
        .await
        .expect("human row");
    let version: i64 = bcs_db_api::db_get_column(&rows[0], "ownership_version").unwrap();
    assert_eq!(version, 0, "a Human actor row never gains an ownership version");
    let role = fixture
        .authority
        .role("human_22222222", "human_22222222")
        .await
        .expect("role");
    assert_eq!(role, None, "no human→human owner edge");
}

pub async fn governance_initialize_refuses_missing_and_deleted_bots(fixture: &Fixture) {
    let error = fixture
        .bot_repo
        .initialize_ownership("bot_missing", "22222222", mutation("22222222").mutation)
        .await
        .expect_err("missing bot");
    assert!(matches!(error, bcs_service_api::ServiceError::BotNotFound(_)));
    assert!(seed_runtime_bot(&fixture, "bot_gone").await);
    fixture
        .db
        .execute(DbStatement::with_params(
            "UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = ?",
            vec!["bot_gone".into()],
        ))
        .await
        .expect("tombstone");
    let error = fixture
        .bot_repo
        .initialize_ownership("bot_gone", "22222222", mutation("22222222").mutation)
        .await
        .expect_err("deleted bot");
    assert!(matches!(error, bcs_service_api::ServiceError::Conflict(_)));
}

// ---------------------------------------------------------------------------
// v2 Provider composite (the SQL Bot Provider store).
// ---------------------------------------------------------------------------

pub fn provider_record(
    bot_id: &str,
    mode: BotConnectionMode,
) -> BotProviderRecord {
    provider_record_ref(bot_id, mode, "ref-1")
}

fn provider_record_ref(
    bot_id: &str,
    mode: BotConnectionMode,
    provider_bot_ref: &str,
) -> BotProviderRecord {
    BotProviderRecord {
        bot_uuid: bot_id.to_string(),
        provider_id: "provider-a".to_string(),
        provider_bot_ref: provider_bot_ref.to_string(),
        connection_mode: mode,
        webhook_url: if mode == BotConnectionMode::Gateway {
            Some("https://gw.example.test".to_string())
        } else {
            None
        },
        is_deleted: false,
    }
}

pub async fn trusted_v2_registration_commits_binding_owner_and_authority(fixture: &Fixture) {
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record("bot_prov1", BotConnectionMode::Gateway),
            capabilities(),
            "11111111",
            "bot-token-p1",
            mutation("11111111"),
        )
        .await
        .expect("committed");
    let binding = fixture
        .provider_repo
        .get_connection_mode("bot_prov1")
        .await
        .expect("mode")
        .expect("bot exists");
    assert_eq!(binding, BotConnectionMode::Gateway);
    let role = fixture
        .authority
        .role("human_11111111", "bot_prov1")
        .await
        .expect("role");
    assert_eq!(role, Some(BotAccessRelation::Owner));
    let profile = fixture
        .profile_port
        .get_active_default("bot_prov1", &resolve_env())
        .await;
    assert!(profile.is_some(), "default profile committed");
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT action FROM bot_authority_changes WHERE bot_id = ?",
            vec!["bot_prov1".into()],
        ))
        .await
        .expect("audit");
    let action: String = bcs_db_api::db_get_column(&rows[0], "action").unwrap();
    assert_eq!(action, "initialize");
    // Version-1 Human composite parity: plugin mode too.
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref("bot_prov2", BotConnectionMode::Plugin, "ref-2"),
            capabilities(),
            "11111111",
            "bot-token-p2",
            mutation("11111111"),
        )
        .await
        .expect("committed plugin bot");
    // A reused Provider/ref (even tombstoned) conflicts.
    let error = fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref("bot_prov3", BotConnectionMode::Plugin, "ref-1"),
            capabilities(),
            "11111111",
            "bot-token-p3",
            mutation("11111111"),
        )
        .await
        .expect_err("duplicate ref");
    assert!(matches!(error, bcs_service_api::ServiceError::Conflict(_)));
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bcs_bots WHERE bot_uuid = ?",
            vec!["bot_prov3".into()],
        ))
        .await
        .expect("bots");
    let count: i64 = bcs_db_api::db_get_column(&rows[0], "c").unwrap();
    assert_eq!(count, 0, "no Bot written for the conflicting ref");
}

pub async fn trusted_v2_binding_failure_rolls_back_everything(fixture: &Fixture) {
    fixture.db.arm("bcs_provider_bot_bindings");
    let error = fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record("bot_prov_fail", BotConnectionMode::Gateway),
            capabilities(),
            "11111111",
            "bot-token-pf",
            mutation("11111111"),
        )
        .await
        .expect_err("binding failure surfaces");
    assert!(
        matches!(error, bcs_service_api::ServiceError::InternalError(_)),
        "persistence failures stay internal: {error}"
    );
    assert_no_half_registration(&fixture, "bot_prov_fail").await;
    // The Provider/ref stays free for the honest retry.
    fixture.db.armed.lock().unwrap().clear();
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record("bot_prov_fail", BotConnectionMode::Gateway),
            capabilities(),
            "11111111",
            "bot-token-pf",
            mutation("11111111"),
        )
        .await
        .expect("retry succeeds");
}

pub async fn trusted_v2_owner_write_failure_rolls_back_everything(fixture: &Fixture) {
    fixture.db.arm("edge_grants");
    let error = fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record("bot_prov_fail2", BotConnectionMode::Plugin),
            capabilities(),
            "11111111",
            "bot-token-pf2",
            mutation("11111111"),
        )
        .await
        .expect_err("owner write failure surfaces");
    assert!(
        matches!(error, bcs_service_api::ServiceError::InternalError(_)),
        "persistence failures stay internal: {error}"
    );
    assert_no_half_registration(&fixture, "bot_prov_fail2").await;
}

// ---------------------------------------------------------------------------
// Runtime propagation: no runtime activation from store writes.
// ---------------------------------------------------------------------------

pub async fn committed_registration_does_not_activate_runtime(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_rt1".to_string(),
            capabilities(),
            "11111111",
            "bot-token-rt",
            mutation("11111111"),
        )
        .await
        .expect("committed");
    assert!(!fixture.bot_repo.is_connected("bot_rt1").await);
    // The persisted token remains the only credential
    // source; the registration published no process-local
    // session state.
    assert_eq!(
        fixture.bot_repo.load_token("bot_rt1").await.as_deref(),
        Some("bot-token-rt")
    );
}

use bcs_service_api::core::provider::{
    ProviderRetire, ProviderRetireBranch,
};
use bcs_service_api::types::bot_authority::{
    TransferAction, TransferCommand, TransferKey,
};

pub fn retire_mutation_provider(provider_id: &str) -> AuthorityMutation {
    AuthorityMutation {
        mutation_id: format!("retire-{provider_id}-1"),
        actor: AuthorityActor::Provider {
            provider_id: provider_id.to_string(),
        },
    }
}

pub fn retire_mutation_human(owner: &str) -> AuthorityMutation {
    AuthorityMutation {
        mutation_id: format!("retire-human-{owner}"),
        actor: AuthorityActor::Human {
            user_id: owner.to_string(),
        },
    }
}

async fn seed_pending_transfer(fixture: &Fixture, bot_id: &str, target: &str) {
    // +2 days in the DB clock domain of each flavor.
    let expires_at = match fixture.flavor {
        DbSqlFlavor::Mysql => "DATE_ADD(CURRENT_TIMESTAMP, INTERVAL 2 DAY)",
        DbSqlFlavor::Sqlite => "datetime('now', '+2 days')",
    };
    fixture
        .db
        .execute(DbStatement::with_params(
            &format!(
                "INSERT INTO bot_ownership_transfers \
             (env, bot_id, target_owner_actor_id, from_owner_actor_id, \
              expected_ownership_version, status, requested_at, \
              expires_at, bot_name_snapshot) \
             VALUES (?, ?, ?, '11111111', 1, 'pending', \
                     CURRENT_TIMESTAMP, {expires_at}, \
                     'snapshot')"
            ),
            vec![
                resolve_env().as_str().into(),
                bot_id.into(),
                target.into(),
            ],
        ))
        .await
        .expect("seed pending transfer");
}

async fn edge_status(
    fixture: &Fixture,
    bot_id: &str,
    owner: &str,
) -> String {
    column(
        &fixture.db,
        "SELECT status FROM edge_grants WHERE env = ? AND to_id = ? \
         AND from_id = ? AND grant_kind = 'owner' AND grant_ref_id = 0",
        vec![resolve_env().as_str().into(), bot_id.into(), owner.into()],
        "status",
    )
    .await
    .expect("edge present")
    .as_str()
    .expect("text")
    .to_string()
}

async fn audit_rows_count(fixture: &Fixture, bot_id: &str) -> usize {
    fixture
        .db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bot_authority_changes WHERE bot_id = ?",
            vec![bot_id.into()],
        ))
        .await
        .expect("audit count")[0]
        .get("c")
        .expect("count")
        .as_i64()
        .expect("int") as usize
}

async fn audit_actions(fixture: &Fixture, bot_id: &str) -> Vec<String> {
    fixture
        .db
        .query(DbStatement::with_params(
            "SELECT action FROM bot_authority_changes \
             WHERE bot_id = ? ORDER BY sequence",
            vec![bot_id.into()],
        ))
        .await
        .expect("audit actions")
        .iter()
        .map(|row| bcs_db_api::db_get_column::<String>(row, "action").unwrap())
        .collect()
}

/// (action, actor_kind, actor_id) pairs ordered by sequence —
/// the retire audit carries the REAL initiator; the closure
/// audits carry the fixed system marker.
async fn audit_actor_pairs(
    fixture: &Fixture,
    bot_id: &str,
) -> Vec<(String, String, String)> {
    fixture
        .db
        .query(DbStatement::with_params(
            "SELECT action, actor_kind, actor_id FROM bot_authority_changes \
             WHERE bot_id = ? ORDER BY sequence",
            vec![bot_id.into()],
        ))
        .await
        .expect("audit rows")
        .iter()
        .map(|row| {
            (
                bcs_db_api::db_get_column::<String>(row, "action").unwrap(),
                bcs_db_api::db_get_column::<String>(row, "actor_kind").unwrap(),
                bcs_db_api::db_get_column::<String>(row, "actor_id").unwrap(),
            )
        })
        .collect()
}

pub async fn provider_retire_metadata_branch_commits_the_whole_state(fixture: &Fixture) {
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref("bot_pr1", BotConnectionMode::Plugin, "ret-ref"),
            capabilities(),
            "11111111",
            "plugin-token",
            mutation("11111111"),
        )
        .await
        .expect("registered");
    seed_pending_transfer(&fixture, "bot_pr1", "human_22222222").await;

    let retirement = fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "bot_pr1".into(),
            provider_bot_ref: "ret-ref".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::Metadata,
        })
        .await
        .expect("retired");
    assert!(retirement.changed);
    assert_eq!(retirement.branch, ProviderRetireBranch::Metadata);

    // Tombstone (the Provider/ref tombstone IS the Bot row
    // tombstone in the metadata branch).
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_pr1'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        1
    );
    // Owner edge revoked in the SAME transaction.
    assert_eq!(
        edge_status(&fixture, "bot_pr1", "human_11111111").await,
        "revoked"
    );
    // Pending closed with the fixed system marker and reason.
    let rows = fixture
        .db
        .query(DbStatement::with_params(
            "SELECT status, decided_by_kind, decided_by_id, reason \
             FROM bot_ownership_transfers WHERE bot_id = 'bot_pr1'",
            vec![]))
        .await
        .expect("transfer");
    let row = &rows[0];
    let status: String =
        bcs_db_api::db_get_column(row, "status").unwrap();
    let decided_kind: String =
        bcs_db_api::db_get_column(row, "decided_by_kind").unwrap();
    let decided_id: String =
        bcs_db_api::db_get_column(row, "decided_by_id").unwrap();
    let reason: String =
        bcs_db_api::db_get_column(row, "reason").unwrap();
    assert_eq!(status, "invalidated");
    assert_eq!(decided_kind, "system");
    assert_eq!(decided_id, "system_transfer_close");
    assert_eq!(reason, "bot_retired");
    // The audits: retire with the REAL Provider actor plus the
    // system closure; the provider token never appears.
    // initialize (registration) + retire + one closure audit.
    let audit_count = audit_rows_count(&fixture, "bot_pr1").await;
    assert_eq!(audit_count, 3);
    let actions = audit_actions(&fixture, "bot_pr1").await;
    // The registration's `initialize` row stays; the retire
    // + system closure follow.
    assert_eq!(
        actions,
        vec![
            "initialize".to_string(),
            "retire".to_string(),
            "transfer_invalidate".to_string(),
        ]
    );
    let pairs: Vec<_> = audit_actor_pairs(&fixture, "bot_pr1")
        .await
        .into_iter()
        .filter(|(action, _, _)| action != "initialize")
        .collect();
    assert_eq!(
        pairs,
        vec![
            ("retire".to_string(), "provider".to_string(),
             "provider_provider-a".to_string()),
            ("transfer_invalidate".to_string(), "system".to_string(),
             "system_transfer_close".to_string()),
        ]
    );
}

pub async fn provider_retire_failure_is_atomic(fixture: &Fixture) {
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref("bot_pr2", BotConnectionMode::Plugin, "ret-fail"),
            capabilities(),
            "11111111",
            "plugin-token2",
            mutation("11111111"),
        )
        .await
        .expect("registered");
    // Inject the retire audit failure: the WHOLE transaction
    // (tombstone, binding disable, edge revocation, pending
    // closure) rolls back — Bot, projection, edges and
    // pending are untouched.
    fixture
        .db
        .arm("INSERT INTO bot_authority_changes \
              (env, bot_id, mutation_id, sequence, action, actor_kind, \
               actor_id, subject_id, before_state, after_state) \
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?");
    let outcome = fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "bot_pr2".into(),
            provider_bot_ref: "ret-fail".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::Metadata,
        })
        .await;
    assert!(outcome.is_err(), "the injected audit failure is an error");
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_pr2'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        0
    );
    assert_eq!(
        edge_status(&fixture, "bot_pr2", "human_11111111").await,
        "approved"
    );
    assert_eq!(
        audit_rows_count(&fixture, "bot_pr2").await,
        1, // only the registration's `initialize` audit
    );
}

pub async fn provider_retire_retry_is_idempotent_without_duplicate_audit(fixture: &Fixture) {
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref("bot_pr3", BotConnectionMode::Plugin, "ret-re"),
            capabilities(),
            "11111111",
            "plugin-token3",
            mutation("11111111"),
        )
        .await
        .expect("registered");
    let params = |mutation: AuthorityMutation| ProviderRetire {
        provider_id: "provider-a".into(),
        bot_id: "bot_pr3".into(),
        provider_bot_ref: "ret-re".into(),
        mutation,
        expected_branch: ProviderRetireBranch::Metadata,
    };
    let first = fixture
        .provider_repo
        .retire_with_authority(params(retire_mutation_provider("provider-a")))
        .await
        .expect("retired");
    assert!(first.changed);
    let second = fixture
        .provider_repo
        .retire_with_authority(params(retire_mutation_provider("provider-a")))
        .await
        .expect("retry");
    // The already-tombstoned retire returns the SAME
    // identity/location data with changed=false and NO
    // duplicate retire audit.
    assert!(!second.changed);
    assert_eq!(first.bot_id, second.bot_id);
    assert_eq!(first.branch, second.branch);
    assert_eq!(
        audit_rows_count(&fixture, "bot_pr3").await,
        2,
        "no duplicate retire audit on retry"
    );
    let retire_rows = audit_actions(&fixture, "bot_pr3")
        .await
        .into_iter()
        .filter(|action| action == "retire")
        .count();
    assert_eq!(retire_rows, 1, "the retire audit is written once");
}

pub async fn provider_retire_binding_fallback_branch_disables_binding(fixture: &Fixture) {
    // A legacy gateway Bot BEFORE metadata backfill: no
    // metadata record, only the compatibility binding.
    fixture
        .bot_repo
        .create_registration_if_absent(
            "bot_fb1".to_string(),
            capabilities(),
            "11111111",
            "fb-token",
        )
        .await.expect("runtime bot");
    // Seed the legacy binding + an owner edge (pre-authority lore).
    fixture
        .db
        .execute(DbStatement::with_params(
            "INSERT INTO bcs_provider_bot_bindings \
             (bot_uuid, env, provider_id, provider_bot_ref, disabled) \
             VALUES ('bot_fb1', ?, 'provider-a', 'fb-ref', 0)",
            vec![resolve_env().as_str().into()],
        ))
        .await
        .expect("binding");
    fixture
        .db
        .execute(DbStatement::with_params(
            &format!(
                "{} INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
              originator_policy_type) \
             VALUES (?, 'human_11111111', 'bot_fb1', 'owner', 0, \
                     NULL, 'approved', 'same_as_from')",
                match fixture.flavor {
                    DbSqlFlavor::Mysql => "INSERT IGNORE",
                    DbSqlFlavor::Sqlite => "INSERT OR IGNORE",
                }
            ),
            vec![resolve_env().as_str().into()],
        ))
        .await
        .expect("edge");
    let retirement = fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "bot_fb1".into(),
            provider_bot_ref: "fb-ref".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::BindingFallback,
        })
        .await
        .expect("retired via binding");
    assert!(retirement.changed);
    assert_eq!(retirement.branch, ProviderRetireBranch::BindingFallback);
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_fb1'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        1
    );
    let disabled = column(&fixture.db,
        "SELECT disabled FROM bcs_provider_bot_bindings WHERE bot_uuid = 'bot_fb1'",
        vec![], "disabled").await.unwrap().as_i64().unwrap();
    assert_eq!(disabled, 1, "the binding carries the retry identity");
    // The tombstone retains the branch identity: the retry
    // re-derives the SAME branch with changed = false.
    let retry = fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "bot_fb1".into(),
            provider_bot_ref: "fb-ref".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::BindingFallback,
        })
        .await
        .expect("retry");
    assert!(!retry.changed);
    assert_eq!(retry.branch, ProviderRetireBranch::BindingFallback);
}

pub async fn provider_retire_legacy_unbound_and_branch_mismatch(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent(
            "legacy-suffix:11111111".to_string(),
            capabilities(),
            "11111111",
            "legacy-token",
        )
        .await.expect("legacy bot");
    // No metadata, no binding; the owner-suffix shape holds.
    fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "legacy-suffix:11111111".into(),
            provider_bot_ref: "legacy-suffix:11111111".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::LegacyUnbound,
        })
        .await
        .expect("retired via legacy unbound");
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'legacy-suffix:11111111'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        1
    );

    // A non-owner-suffixed Bot can NEVER take the LegacyUnbound
    // branch: the branch expectation mismatches the durable
    // state instead of silently reinterpreting it.
    fixture
        .bot_repo
        .create_registration_if_absent(
            "plain_bot".to_string(),
            capabilities(),
            "11111111",
            "plain-token",
        )
        .await.expect("plain bot");
    let mismatch = fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "plain_bot".into(),
            provider_bot_ref: "plain_bot".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::LegacyUnbound,
        })
        .await;
    assert!(mismatch.is_err(), "branch mismatch is an error");
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'plain_bot'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        0
    );
}

pub async fn normal_retire_revokes_authority_and_never_chains_provider(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_norm".to_string(),
            capabilities(),
            "11111111",
            "norm-token",
            mutation("11111111"),
        )
        .await.expect("registered bot");
    seed_pending_transfer(&fixture, "bot_norm", "human_33333333").await;

    let changed = fixture
        .bot_repo
        .retire_with_authority("bot_norm", retire_mutation_human("11111111"))
        .await
        .expect("retired");
    assert!(changed);
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_norm'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        1
    );
    assert_eq!(
        edge_status(&fixture, "bot_norm", "human_11111111").await,
        "revoked"
    );
    // The registration's initialize row stays; the retirement
    // adds the retire (REAL Human initiator) and the system
    // closure audit.
    let rows = audit_actor_pairs(&fixture, "bot_norm").await;
    assert_eq!(
        rows,
        vec![
            ("initialize".to_string(), "human".to_string(),
             "human_11111111".to_string()),
            ("retire".to_string(), "human".to_string(), "human_11111111".to_string()),
            ("transfer_invalidate".to_string(), "system".to_string(),
             "system_transfer_close".to_string()),
        ]
    );

    // The ordinary retire is a DISTINCT command: a
    // provider-managed Bot is refused, never re-deleted.
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref("bot_pm", BotConnectionMode::Gateway, "pm-ref"),
            capabilities(),
            "11111111",
            "pm-token",
            mutation("11111111"),
        )
        .await
        .expect("provider bot");
    let refused = fixture
        .bot_repo
        .retire_with_authority("bot_pm", retire_mutation_human("11111111"))
        .await;
    assert!(refused.is_err(), "provider-managed bots retire through Provider commands");
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_pm'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        0
    );
}

/// A retired Bot NEVER resurrects through the lifecycle
/// write paths: the governance backfill refuses tombstoned
/// IDs (already for pre-authority tombstones); the retire
/// tombstone gets the same refusal, and creation stays
/// create-only. The Human-deletion full-flow competition
/// (deleting a Human that still owns live Bots) is Task 6;
/// this pins the same "no approved owner edge to a dying
/// identity" store invariant on the Bot side.
pub async fn retired_bot_id_never_resurrects_through_lifecycle_writes(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_gone".to_string(),
            capabilities(),
            "11111111",
            "gone-token",
            mutation("11111111"),
        )
        .await
        .expect("registered");
    fixture
        .bot_repo
        .retire_with_authority("bot_gone", retire_mutation_human("11111111"))
        .await
        .expect("retired");

    // Governance initialization refuses the tombstoned ID.
    let backfill = fixture
        .bot_repo
        .initialize_ownership("bot_gone", "11111111", retire_mutation_human("11111111"))
        .await;
    assert!(
        backfill.is_err(),
        "initialize never resurrects a retired Bot"
    );
    assert_eq!(
        column(&fixture.db,
            "SELECT COUNT(*) AS c FROM edge_grants WHERE to_id = 'bot_gone' \
             AND status = 'approved'",
            vec![], "c").await.unwrap().as_i64().unwrap(),
        0,
        "no approved owner edge exists to the retiring identity"
    );
    // Atomic re-creation stays create-only: a reused retired
    // ID never resets anything and never comes back alive.
    assert!(
        !fixture
            .bot_repo
            .create_registration_if_absent_with_ownership(
                "bot_gone".to_string(),
                capabilities(),
                "11111111",
                "another-token",
                mutation("11111111"),
            )
            .await
            .expect("create-only"),
    );
    assert_eq!(
        column(&fixture.db,
            "SELECT is_deleted FROM bcs_bots WHERE bot_uuid = 'bot_gone'",
            vec![], "is_deleted").await.unwrap().as_i64().unwrap(),
        1
    );
}

/// The retire ↔ accept barrier (spec §6.3: accept and Bot
/// retirement serialize on the same Bot row): the retirement
/// closes the pending first; the later accept decision sees
/// terminal state and refuses — one side wins, the other
/// observes, no half-way resurrection of the pending.
pub async fn retire_serializes_against_the_transfer_accept(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_race".to_string(),
            capabilities(),
            "11111111",
            "race-token",
            mutation("11111111"),
        )
        .await
        .expect("registered");
    seed_pending_transfer(&fixture, "bot_race", "human_22222222").await;

    fixture
        .bot_repo
        .retire_with_authority("bot_race", retire_mutation_human("11111111"))
        .await
        .expect("retired first (serialized)");

    let accept = fixture
        .authority
        .change_transfer(TransferCommand {
            key: TransferKey {
                bot_id: "bot_race".to_string(),
                target_owner_actor_id: "human_22222222".to_string(),
            },
            action: TransferAction::Accept,
            mutation: AuthorityMutation {
                mutation_id: "accept-after-retire".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "22222222".to_string(),
                },
            },
        })
        .await;
    assert!(
        accept.is_err(),
        "the accept cannot act on the retired Bot's closed pending"
    );
    assert_eq!(
        column(&fixture.db,
            "SELECT status FROM bot_ownership_transfers \
             WHERE bot_id = 'bot_race'",
            vec![], "status").await.unwrap().as_str().expect("text"),
        "invalidated",
        "the pending stays closed"
    );
}

// ---------------------------------------------------------------------------
// Review Finding #8: the pending closure identity is
// read INSIDE the transaction. The pre-read is
// parameter preparation only; the deterministic
// one-shot transaction hook below mutates the
// pendings between the pre-read and the lock so the
// count-stable / identity-drift interleave ("cancel
// Bob → create Carol") runs deterministically. The
// first attempt must roll back on the per-row
// identity guards and the retry must close the row
// the transaction ACTUALLY saw — the closure audit
// never names a row the transaction did not see.
// ---------------------------------------------------------------------------

/// Arm the one-shot hook: at the next transaction's
/// entrance, cancel `cancelled`'s pending and create
/// `created`'s pending (the same command interleave
/// a racing owner/target pair produces).
fn arm_cancel_and_new_pending_hook(
    fixture: &Fixture,
    bot_id: &str,
    cancelled: &str,
    created: &str,
) {
    // +2 days in the DB clock domain of each flavor.
    let expires_at = match fixture.flavor {
        DbSqlFlavor::Mysql => {
            "DATE_ADD(CURRENT_TIMESTAMP, INTERVAL 2 DAY)"
        }
        DbSqlFlavor::Sqlite => "datetime('now', '+2 days')",
    };
    let db = fixture.db.clone();
    let bot_id = bot_id.to_string();
    let cancelled = cancelled.to_string();
    let created = created.to_string();
    fixture.db.arm_transaction_hook(move || {
        let db = db.clone();
        let bot_id = bot_id.clone();
        let cancelled = cancelled.clone();
        let created = created.clone();
        async move {
            db.inner
                .execute(DbStatement::with_params(
                    format!(
                        "UPDATE bot_ownership_transfers \
                         SET status = 'cancelled', \
                             decided_at = CURRENT_TIMESTAMP, \
                             gmt_modified = CURRENT_TIMESTAMP \
                         WHERE env = ? AND bot_id = ? \
                           AND target_owner_actor_id = ? \
                           AND status = 'pending'"
                    )
                    .as_str(),
                    vec![
                        DbValue::from(resolve_env().as_str()),
                        DbValue::from(bot_id.as_str()),
                        DbValue::from(cancelled.as_str()),
                    ],
                ))
                .await
                .expect("hook: cancel the pre-read pending");
            db.inner
                .execute(DbStatement::with_params(
                    format!(
                        "INSERT INTO bot_ownership_transfers \
                         (env, bot_id, target_owner_actor_id, \
                          from_owner_actor_id, \
                          expected_ownership_version, status, \
                          requested_at, expires_at, \
                          bot_name_snapshot) \
                         VALUES (?, ?, ?, '11111111', 1, 'pending', \
                                 CURRENT_TIMESTAMP, {expires_at}, \
                                 'snapshot')"
                    )
                    .as_str(),
                    vec![
                        DbValue::from(resolve_env().as_str()),
                        DbValue::from(bot_id.as_str()),
                        DbValue::from(created.as_str()),
                    ],
                ))
                .await
                .expect("hook: create the other pending");
        }
    });
}

async fn transfer_pending_row(
    fixture: &Fixture,
    bot_id: &str,
    target: &str,
) -> Vec<(String, Option<String>, Option<String>, Option<String>)> {
    fixture
        .db
        .query(DbStatement::with_params(
            format!(
                "SELECT status, decided_by_kind, decided_by_id, reason \
                 FROM bot_ownership_transfers \
                 WHERE bot_id = ? AND target_owner_actor_id = ?"
            )
            .as_str(),
            vec![
                DbValue::from(bot_id),
                DbValue::from(target),
            ],
        ))
        .await
        .expect("transfer rows")
        .into_iter()
        .map(|row| {
            (
                bcs_db_api::db_get_column::<String>(&row, "status")
                    .unwrap(),
                row.get_string("decided_by_kind")
                    .expect("decided_by_kind"),
                row.get_string("decided_by_id")
                    .expect("decided_by_id"),
                row.get_string("reason").expect("reason"),
            )
        })
        .collect()
}

async fn closure_audit_subjects(
    fixture: &Fixture,
    bot_id: &str,
) -> Vec<String> {
    fixture
        .db
        .query(DbStatement::with_params(
            "SELECT subject_id FROM bot_authority_changes \
             WHERE bot_id = ? AND action = 'transfer_invalidate' \
             ORDER BY sequence",
            vec![DbValue::from(bot_id)],
        ))
        .await
        .expect("closure audits")
        .iter()
        .map(|row| {
            bcs_db_api::db_get_column::<String>(
                row,
                "subject_id",
            )
            .unwrap()
        })
        .collect()
}

/// The reviewer's exact interleave (ordinary
/// retirement): "cancel Bob → create Carol" between
/// the pre-read and the transaction keeps the
/// pending COUNT at 1 while the identity changes.
/// The count-only guard of the old shape passed —
/// and the retirement audited Bob for Carol's
/// closure. Now the per-row in-transaction identity
/// read rolls attempt 1 back; the retry closes
/// CAROL's row and the closure audit names CAROL.
pub async fn retire_closes_the_pending_identity_seen_in_tx(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_pendrace".to_string(),
            capabilities(),
            "11111111",
            "race-token",
            mutation("11111111"),
        )
        .await
        .expect("registered bot");
    seed_pending_transfer(&fixture, "bot_pendrace", "human_bob")
        .await;
    arm_cancel_and_new_pending_hook(
        &fixture,
        "bot_pendrace",
        "human_bob",
        "human_carol",
    );

    let changed = fixture
        .bot_repo
        .retire_with_authority(
            "bot_pendrace",
            retire_mutation_human("11111111"),
        )
        .await
        .expect("retired after the identity drift");
    assert!(changed);

    // Bob's row: untouched by the retirement (the
    // racing command already closed it — NO
    // double-close).
    let bob = transfer_pending_row(&fixture, "bot_pendrace", "human_bob").await;
    assert_eq!(bob.len(), 1, "no double-close inserted rows");
    assert_eq!(bob[0].0, "cancelled", "Bob keeps his own terminal state");
    // Carol's row: the one the transaction closed, with
    // the fixed system marker and reason.
    let carol =
        transfer_pending_row(&fixture, "bot_pendrace", "human_carol").await;
    assert_eq!(carol.len(), 1);
    assert_eq!(carol[0].0, "invalidated");
    assert_eq!(carol[0].1.as_deref(), Some("system"));
    assert_eq!(
        carol[0].2.as_deref(),
        Some("system_transfer_close")
    );
    assert_eq!(carol[0].3.as_deref(), Some("bot_retired"));
    // The closure audit names CAROL — the identity the
    // transaction actually saw — exactly once, never Bob.
    assert_eq!(
        closure_audit_subjects(&fixture, "bot_pendrace").await,
        vec!["human_carol".to_string()],
        "the closure audit must name the in-tx identity"
    );
}

/// The same interleave through the Provider
/// retirement composite (the shared closure helper).
pub async fn provider_retire_closes_the_pending_identity_seen_in_tx(fixture: &Fixture) {
    fixture
        .provider_repo
        .create_provider_bot_with_ownership(
            provider_record_ref(
                "bot_pendprov",
                BotConnectionMode::Plugin,
                "prov-ref",
            ),
            capabilities(),
            "11111111",
            "plugin-token",
            mutation("11111111"),
        )
        .await
        .expect("registered provider bot");
    seed_pending_transfer(&fixture, "bot_pendprov", "human_bob")
        .await;
    arm_cancel_and_new_pending_hook(
        &fixture,
        "bot_pendprov",
        "human_bob",
        "human_carol",
    );

    let retirement = fixture
        .provider_repo
        .retire_with_authority(ProviderRetire {
            provider_id: "provider-a".into(),
            bot_id: "bot_pendprov".into(),
            provider_bot_ref: "prov-ref".into(),
            mutation: retire_mutation_provider("provider-a"),
            expected_branch: ProviderRetireBranch::Metadata,
        })
        .await
        .expect("retired after the identity drift");
    assert!(retirement.changed);

    let bob =
        transfer_pending_row(&fixture, "bot_pendprov", "human_bob").await;
    assert_eq!(bob[0].0, "cancelled", "no double-close");
    let carol =
        transfer_pending_row(&fixture, "bot_pendprov", "human_carol").await;
    assert_eq!(carol[0].0, "invalidated");
    assert_eq!(
        carol[0].2.as_deref(),
        Some("system_transfer_close")
    );
    assert_eq!(
        closure_audit_subjects(&fixture, "bot_pendprov").await,
        vec!["human_carol".to_string()],
        "the closure audit must name the in-tx identity"
    );
}

/// Count drift (the only pending vanishes between
/// the pre-read and the transaction): the count
/// guard rolls attempt 1 back, the retry sees an
/// empty pending set and commits WITHOUT writing
/// any closure or closure audit.
pub async fn retire_retries_when_the_pending_vanishes_between_preread_and_tx(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_pendvanish".to_string(),
            capabilities(),
            "11111111",
            "vanish-token",
            mutation("11111111"),
        )
        .await
        .expect("registered bot");
    seed_pending_transfer(
        &fixture,
        "bot_pendvanish",
        "human_bob",
    )
    .await;
    let db = fixture.db.clone();
    fixture
        .db
        .arm_transaction_hook(move || {
            let db = db.clone();
            async move {
                db.inner
                    .execute(DbStatement::with_params(
                        "UPDATE bot_ownership_transfers \
                         SET status = 'cancelled', \
                             decided_at = CURRENT_TIMESTAMP \
                         WHERE bot_id = 'bot_pendvanish' \
                           AND target_owner_actor_id = 'human_bob' \
                           AND status = 'pending'",
                        vec![],
                    ))
                    .await
                    .expect("hook: cancel the pending");
            }
        });

    let changed = fixture
        .bot_repo
        .retire_with_authority(
            "bot_pendvanish",
            retire_mutation_human("11111111"),
        )
        .await
        .expect("retired after the count drift");
    assert!(changed);
    assert_eq!(
        closure_audit_subjects(&fixture, "bot_pendvanish").await,
        Vec::<String>::new(),
        "a vanished pending must not produce a closure audit"
    );
    assert_eq!(
        audit_actions(&fixture, "bot_pendvanish").await,
        vec![
            "initialize".to_string(),
            "retire".to_string(),
        ],
        "the register + retire audits, no closure"
    );
}

/// Zero pending: the retirement writes NO closure
/// audit ("no audit before actual change") and no
/// transfer rows.
pub async fn retire_with_no_pendings_writes_no_closure_audit(fixture: &Fixture) {
    fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "bot_pendzero".to_string(),
            capabilities(),
            "11111111",
            "zero-token",
            mutation("11111111"),
        )
        .await
        .expect("registered bot");

    let changed = fixture
        .bot_repo
        .retire_with_authority(
            "bot_pendzero",
            retire_mutation_human("11111111"),
        )
        .await
        .expect("retired");
    assert!(changed);
    assert_eq!(
        closure_audit_subjects(&fixture, "bot_pendzero").await,
        Vec::<String>::new(),
        "no closure audit without pendings"
    );
    assert_eq!(
        audit_actions(&fixture, "bot_pendzero").await,
        vec![
            "initialize".to_string(),
            "retire".to_string(),
        ],
    );
}

/// The mine sweep's candidate query must prefilter in the
/// STORE, not in the application loop: initialized rows,
/// Human actor rows, other creators, tombstones and
/// conflict-edged rows never leave SQL. Steady state for a
/// fully-migrated owner is an EMPTY bounded read.
pub async fn uninitialized_owned_bots_prefilter_in_the_store(fixture: &Fixture) {
    let env = resolve_env();
    // (1) migratable: live physical Bot, byte-exact creator, version 0.
    assert!(seed_runtime_bot(fixture, "cand-ok").await);
    fixture
        .db
        .execute(DbStatement::new(
            "UPDATE bcs_bots SET created_by = '99999999' WHERE bot_uuid = 'cand-ok'",
        ))
        .await
        .expect("re-own candidate");
    // (2) initialized: full composite → version 1 → excluded.
    let committed = fixture
        .bot_repo
        .create_registration_if_absent_with_ownership(
            "cand-initialized".to_string(),
            capabilities(),
            "99999999",
            "initialized-token",
            mutation("99999999"),
        )
        .await
        .expect("initialized composite");
    assert!(committed);
    // (3) Human actor row (the composite created human_99999999) is
    // never a candidate even though created_by matches.
    // (4) another creator's Bot never surfaces.
    assert!(seed_runtime_bot(fixture, "cand-other-owner").await);
    // (5) tombstoned candidate never surfaces.
    assert!(seed_runtime_bot(fixture, "cand-tombstoned").await);
    fixture
        .db
        .execute(DbStatement::new(
            "UPDATE bcs_bots SET created_by = '99999999', is_deleted = 1 \
             WHERE bot_uuid = 'cand-tombstoned'",
        ))
        .await
        .expect("tombstone candidate");
    // (6) a DISAGREEING approved owner edge blocks the bounded
    // window — excluded store-side, never starved in-page.
    fixture
        .db
        .execute(DbStatement::with_params(
            "INSERT INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, status) \
             VALUES (?, 'human_someone-else', 'cand-ok', 'owner', 0, 'approved')",
            vec![env.clone().into()],
        ))
        .await
        .expect("plant disagreeing owner edge");

    let candidates = fixture
        .bot_repo
        .list_uninitialized_bots_by_creator("99999999", 100)
        .await
        .expect("candidate query");
    let ids: Vec<String> = candidates.iter().map(|bot| bot.bot_uuid.clone()).collect();
    assert_eq!(
        ids,
        Vec::<String>::new(),
        "the lone migratable Bot also carries a disagreeing edge; \
         everything else is excluded in the store: {ids:?}"
    );

    // Remove the disagreeing edge → the migratable row appears.
    fixture
        .db
        .execute(DbStatement::new(
            "DELETE FROM edge_grants WHERE to_id = 'cand-ok'",
        ))
        .await
        .expect("clear disagreeing edge");
    let candidates = fixture
        .bot_repo
        .list_uninitialized_bots_by_creator("99999999", 100)
        .await
        .expect("candidate query after edge cleanup");
    let ids: Vec<String> = candidates.iter().map(|bot| bot.bot_uuid.clone()).collect();
    assert_eq!(ids, vec!["cand-ok".to_string()]);

    // Initialize it → steady state is an EMPTY bounded read.
    fixture
        .bot_repo
        .initialize_ownership(
            "cand-ok",
            "99999999",
            AuthorityMutation {
                mutation_id: "cand-init".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "99999999".to_string(),
                },
            },
        )
        .await
        .expect("initialize candidate");
    let candidates = fixture
        .bot_repo
        .list_uninitialized_bots_by_creator("99999999", 100)
        .await
        .expect("steady-state candidate query");
    assert!(candidates.is_empty(), "steady state reads empty");
}
