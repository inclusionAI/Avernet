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
    "CREATE TABLE bcs_bots (agent_card TEXT,\
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
        initial_manager_user_ids: Vec::new(),
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

#[path="authority_lifecycle_registration.rs"]
mod registration;
#[path="authority_lifecycle_retirement.rs"]
mod retirement;
#[path="authority_lifecycle_retirement_races.rs"]
mod retirement_races;
pub use registration::*;
pub use retirement::*;
pub use retirement_races::*;
use registration::provider_record_ref;
use retirement::{seed_pending_transfer,audit_actions};
use bcs_service_api::core::provider::{ProviderRetire,ProviderRetireBranch};
use bcs_service_api::types::bot_authority::{TransferAction,TransferCommand,TransferKey};

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
