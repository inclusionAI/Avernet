//! Shared trusted-registration chain fixture (bot-authority
//! plan Task 4): the REAL application → Core → SQL-store
//! chain over one SQLite database, with deterministic
//! infrastructure-failure injection on the composed
//! transaction text. Used by the app-register entry tests
//! and by both HTTP surface suites (legacy + OpenAPI).
//!
//! Failure injection deformes NOTHING structural —
//! application, cores and stores are the production
//! types; only the DB plugin wrapper is a test double
//! (the `ControlledDb` precedent from bot-store's
//! registration tests).

use std::sync::{Arc, Mutex};

use bcs_app_register::RegisterServiceImpl;
use bcs_bot::BotCore;
use bcs_bot::core::provider_registration::ProviderRegistrationCore;
use bcs_bot_store::{DbProviderStore, PersistentBotRepo};
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbRow, DbResult,
    DbSqlFlavor, DbStatement, DbTransactionStep,
    DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::provider_registration_token::{
    self, ProviderRegisterTokenPayload, ProviderRegistrationMode,
};
use bcs_domain::{register_token_encode, RegisterTokenPayload};
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::core::BotRegistryCoreService;
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort, PermissionProfileRepoPort};
use bcs_service_api::types::bot_authority::BotAccessRelation;

/// Register-token secret shared by issuance and tests.
pub const REGISTER_SECRET: &[u8] = b"registration-chain-test-secret!!";

/// The owner user id every default fixture identity uses.
pub const OWNER: &str = "11111111";

const SCHEMA: &[&str] = &[
    // bcs_bots baseline subset (the columns the composite
    // writes/reads), matching the migration's meanings.
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
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        UNIQUE (env, bot_uuid), \
        UNIQUE (env, provider_id, provider_bot_ref))",
    "CREATE TABLE bcs_providers (\
        provider_id TEXT NOT NULL, \
        env TEXT NOT NULL, \
        name TEXT NOT NULL DEFAULT '', \
        config TEXT, \
        disabled INTEGER NOT NULL DEFAULT 0, \
        created_by TEXT, \
        owners TEXT, \
        slug TEXT, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        PRIMARY KEY (provider_id, env))",
    "CREATE TABLE bcs_provider_credentials (\
        provider_id TEXT NOT NULL, env TEXT NOT NULL, \
        credential_kind TEXT NOT NULL, secret_value TEXT, \
        disabled INTEGER NOT NULL DEFAULT 0, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        UNIQUE (provider_id, env, credential_kind))",
    "CREATE TABLE bcs_user_identities (\
        id INTEGER PRIMARY KEY AUTOINCREMENT, \
        user_id TEXT NOT NULL, \
        auth_source TEXT NOT NULL, \
        external_user_id TEXT NOT NULL, \
        user_name TEXT, env TEXT NOT NULL UNIQUE, \
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
        UNIQUE (user_id))",
];

/// Deterministic transaction failure injection: when a
/// pattern is armed, any transaction containing a statement
/// whose SQL matches fails BEFORE executing anything — the
/// observable result of a mid-transaction abort, because
/// these composites are all-or-nothing.
pub struct ChainFailDb {
    inner: Arc<dyn DbPlugin>,
    armed: Mutex<Vec<&'static str>>,
}

impl ChainFailDb {
    fn new(inner: Arc<dyn DbPlugin>) -> Arc<Self> {
        Arc::new(Self {
            inner,
            armed: Mutex::new(Vec::new()),
        })
    }

    pub fn arm(&self, pattern: &'static str) {
        self.armed.lock().unwrap().push(pattern);
    }

    pub fn disarm_all(&self) {
        self.armed.lock().unwrap().clear();
    }

    fn armed(&self, sql: &str) -> bool {
        self.armed
            .lock()
            .unwrap()
            .iter()
            .any(|pattern| sql.contains(pattern))
    }
}

#[async_trait::async_trait]
impl DbPlugin for ChainFailDb {
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

/// The full REAL chain. `register_service` carries BOTH
/// trusted cores (registry + provider registration) wired
/// to this fixture's stores.
pub struct RegistrationChain {
    pub db: Arc<ChainFailDb>,
    pub bot_repo: Arc<PersistentBotRepo>,
    pub provider_repo: Arc<DbProviderStore>,
    pub profile_store: Arc<bcs_edge_permission_store::DbPermissionProfileStore>,
    pub authority_store:
        Arc<bcs_edge_permission_store::DbBotAuthorityStore>,
    pub registry_core: Arc<BotCore>,
    pub register_service: Arc<dyn bcs_service_api::application::v1::RegisterService>,
    /// The human-token (v1) verify secret — the same
    /// secret the chains' facade verifies with.
    pub secret: Vec<u8>,
}

pub async fn registration_chain() -> RegistrationChain {
    let inner = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    for statement in SCHEMA {
        inner
            .execute(DbStatement::new(*statement))
            .await
            .expect("registration chain schema");
    }
    let db = ChainFailDb::new(inner);
    let env = bcs_config::resolve_env_str();
    let bot_repo = Arc::new(PersistentBotRepo::with_sql_flavor(
        db.clone(),
        DbSqlFlavor::Sqlite,
    ));
    let provider_store = Arc::new(DbProviderStore::sqlite(db.clone()));
    let authority_store =
        Arc::new(bcs_edge_permission_store::DbBotAuthorityStore::sqlite(
            db.clone(),
            env.clone(),
        ));
    let profile_store =
        Arc::new(bcs_edge_permission_store::DbPermissionProfileStore::sqlite(
            db.clone(),
        ));

    // Seed the Provider record (a real Authorization row for
    // the v2 admission): the owner is the provider manager.
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_providers (provider_id, env, name, config, \
         disabled, created_by, owners, slug) \
         VALUES ('provider-a', ?, 'Provider A', ?, 0, ?, ?, NULL)",
        vec![
            env.as_str().into(),
            r#"{"downlink":{"enabled":false,"auth_mode":"static_bearer"}}"#.into(),
            OWNER.into(),
            format!("[\"{OWNER}\"]").into(),
        ],
    ))
    .await
    .expect("seed provider record");

    let registry_core = Arc::new(BotCore::with_repo(bot_repo.clone())
        .with_bot_provider_repo(provider_store.clone()));
    let relation = Arc::new(bcs_relation::RelationCore::memory());
    let provider_core = Arc::new(ProviderRegistrationCore::new(
        provider_store.clone(),
        provider_store.clone(),
        provider_store.clone(),
        provider_store.clone(),
        registry_core.clone(),
        relation,
        env,
        Vec::new(),
        OutboundUrlGuard::allowing_private_networks_for_tests(),
    ));
    let register_service: Arc<
        dyn bcs_service_api::application::v1::RegisterService,
    > = Arc::new(
        RegisterServiceImpl::new(REGISTER_SECRET.to_vec())
            .with_registry(registry_core.clone())
            .with_provider_registration(provider_core),
    );

    RegistrationChain {
        db,
        bot_repo,
        provider_repo: provider_store,
        profile_store,
        authority_store,
        registry_core,
        register_service,
        secret: REGISTER_SECRET.to_vec(),
    }
}

/// Mint a v1 Human registration token for a staff id.
pub fn mint_v1_token(staff_no: &str, secret: &[u8]) -> String {
    let exp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
        + 600;
    register_token_encode(
        &RegisterTokenPayload {
            v: 1,
            id: format!("human_{staff_no}"),
            exp,
        },
        secret,
    )
}

/// Mint an expired v1 token (expiry already passed).
pub fn mint_expired_v1_token(staff_no: &str, secret: &[u8]) -> String {
    register_token_encode(
        &RegisterTokenPayload {
            v: 1,
            id: format!("human_{staff_no}"),
            exp: 1,
        },
        secret,
    )
}

/// Mint a v2 Provider registration token.
pub fn mint_v2_token(
    staff_no: &str,
    provider_id: &str,
    secret: &[u8],
) -> String {
    let exp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
        + 600;
    provider_registration_token::encode(
        &ProviderRegisterTokenPayload {
            v: 2,
            purpose: "provider_bot_registration".to_string(),
            id: format!("human_{staff_no}"),
            provider_id: provider_id.to_string(),
            allowed_modes: vec![
                ProviderRegistrationMode::Plugin,
                ProviderRegistrationMode::Gateway,
            ],
            exp,
            // The conformance helpers mint the HISTORICAL v2 shape:
            // no auth mode claim (historical tokens keep their
            // existing registration semantics).
            auth_mode: None,
        },
        secret,
    )
}

/// Post-state assertion: after a failed registration NO
/// half state exists — no composed bot/human/identity/edge/
/// profile/audit/binding rows, no runtime activation.
pub async fn assert_no_half_registration(chain: &RegistrationChain, bot_id: &str) {
    assert!(chain.bot_repo.load_token(bot_id).await.is_none());
    assert!(!chain.bot_repo.is_connected(bot_id).await);
    let count = async |chain: &RegistrationChain, sql: &str| -> i64 {
        let rows = chain
            .db
            .query(DbStatement::with_params(&format!(
                "SELECT COUNT(*) AS c FROM {sql}"
            ), vec![]))
            .await
            .expect("count");
        bcs_db_api::db_get_column::<i64>(&rows[0], "c")
            .expect("int")
    };
    let bots = count(chain, "bcs_bots WHERE actor_kind = 'bot'").await;
    assert_eq!(bots, 0, "no half-created Bot row");
    let humans =
        count(chain, "bcs_bots WHERE actor_kind = 'human'").await;
    assert_eq!(humans, 0, "no half-created Human actor row");
    assert_eq!(count(chain, "edge_grants").await, 0, "no owner edge");
    assert_eq!(
        count(chain, "permission_profiles").await,
        0,
        "no default profile"
    );
    assert_eq!(
        count(chain, "bot_authority_changes").await,
        0,
        "no initialize audit"
    );
    assert_eq!(
        count(chain, "bcs_provider_bot_bindings").await,
        0,
        "no binding projection"
    );
    assert_eq!(
        count(chain, "bcs_user_identities").await,
        0,
        "no half-created Human identity row"
    );
    // Runtime never activated for the failed identity.
    assert!(chain
        .registry_core
        .get(bot_id)
        .await
        .is_none());
}

/// Owner relation of a user (by raw staff id) on a bot in
/// this chain's bound env.
pub async fn owner_relation(
    chain: &RegistrationChain,
    bot_id: &str,
) -> Option<BotAccessRelation> {
    chain
        .authority_store
        .role(&format!("human_{OWNER}"), bot_id)
        .await
        .expect("strict role read")
}

/// The committed default profile of a bot through the REAL
/// SQL profile store.
pub async fn committed_profile(
    chain: &RegistrationChain,
    bot_id: &str,
) -> Option<bcs_domain::edge_permission::PermissionProfile> {
    let env = bcs_config::resolve_env_str();
    chain
        .profile_store
        .get_active_default(bot_id, &env)
        .await
}
