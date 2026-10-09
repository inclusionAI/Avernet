//! First-ownership initialization storage boundary (plan Task 5).
//!
//! The helpers here generate the authority steps consumed by this crate's
//! EXISTING creation transactions — nothing else. `create_registration_once`
//! and `create_provider_bot` still own their Bot/gateway INSERT SQL and call
//! [`ownership_initialization_steps`] to append, IN THE SAME ONE COMMIT:
//!
//! 1. the exact Task 2 CAS
//!    `UPDATE bcs_bots SET ownership_version = 1
//!     WHERE env = ? AND bot_uuid = ? AND ownership_version = 0
//!       AND is_deleted = 0` (ExecuteChecked, expected 1);
//! 2. the trusted owner Human materialization (`ensure_human`):
//!    INSERT-OR-IGNORE actor row, an existing row is never overwritten;
//! 3. the unique approved owner edge (guarded by the approved-slot
//!    emptiness plus the `uk_edge_active_owner_slot` unique index);
//! 4. the default permission-profile ensure (INSERT-OR-IGNORE, never
//!    recreating or bumping an existing default, D12 rule 2);
//! 5. the `bot_ownership_initializations` audit row (source
//!    `registration` from the create commits, `governed_repair` from
//!    [`initialize_existing_ownership`]).
//!
//! Any step failure rolls the whole creation back — a Bot registration
//! consumed by an initialization is all-or-nothing, and a failed attempt on
//! an existing runtime Bot never touches its pre-existing fields. Later
//! management/transfer SQL stays in the edge-permission store; this crate
//! never imports that store's concrete types — the shared `bcs_domain`
//! role/source constants keep the shapes aligned.
//!
//! [`initialize_existing_ownership`] is the governed entry for an
//! EXISTING live Bot still at version 0: it never auto-claims (an
//! already-initialized Bot is `Conflict`, a missing/soft-deleted Bot is
//! `BotNotFound`), and the memory twin performs the same work inside the
//! exact [`MemoryBotRepo`] critical section the registration and
//! authority reads share.

use std::collections::BTreeMap;

use bcs_db_api::{DbError, DbSqlFlavor, DbStatement, DbTransactionStep};
use bcs_service_api::types::{
    AuditActor, INITIALIZED_OWNERSHIP_VERSION, OWNER_SOURCE_ID, OWNER_SOURCE_KIND,
    ROLE_GRANT_REF_ID, UNINITIALIZED_OWNERSHIP_VERSION,
};
use bcs_service_api::port::repo::bot_authority::human_actor_id;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::{OwnershipInitialization, OwnershipState};
use bcs_service_api::{ActorKind, ActorStatus, BotCapabilities, ServiceError, ServiceResult};



use super::memory::memory_authority::{
    MemoryAuthorityState, MemoryOwnershipInitRecord, MemoryRoleEdgeRow,
};
use super::memory::RegisteredBotInner;
use super::{BotInfo, PersistentBotRepo, Value, resolve_env};

/// `bot_ownership_initializations.source` for a create-consumed
/// initialization (the frozen Task 2 CHECK vocabulary).
pub(super) const SOURCE_REGISTRATION: &str = "registration";
/// `bot_ownership_initializations.source` for the governed version-0 lane.
pub(super) const SOURCE_GOVERNED_REPAIR: &str = "governed_repair";

/// Wildcard-allow rules template of the default profile seeded at
/// registration (byte-identical to the edge-permission store's seed — the
/// digest below is its SHA-256; the owning test pins the stored row shape).
const WILDCARD_ALLOW_RULES: &str = r#"[{"tool":"*","specifier":"*","effect":"allow"}]"#;

/// Default summary of an ensured Human actor row (the same constant the
/// runtime `ensure_human_actor` path writes).
const HUMAN_DEFAULT_SUMMARY: &str = "写点什么介绍自己";

/// SHA-256 hex digest of the rules template (permission_profiles.digest).
fn sha256_hex(input: &str) -> String {
    use sha2::{Digest as _, Sha256};
    let digest = Sha256::digest(input.as_bytes());
    let mut hex = String::with_capacity(digest.len() * 2);
    for byte in digest {
        hex.push_str(&format!("{byte:02x}"));
    }
    hex
}

/// Fail-closed validation BEFORE any write: the trusted owner User ID and
/// the operation id must be present, and only a `Human` (registration) or
/// `System` (governed repair) actor may initialize — `Service` identifiers
/// are rejected (they must never be recorded as Human user ids, and the
/// frozen `chk_init_actor_kind` CHECK admits only human/system anyway).
pub(super) fn validate_initialization(initialization: &OwnershipInitialization) -> ServiceResult<()> {
    if initialization.owner_user_id.trim().is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: "ownership initialization requires the trusted owner User ID".into(),
            request_id: None,
        });
    }
    if initialization.operation_id.trim().is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: "ownership initialization requires an operation id".into(),
            request_id: None,
        });
    }
    match &initialization.actor {
        AuditActor::Human { .. } | AuditActor::System { .. } => Ok(()),
        AuditActor::Service { .. } => Err(ServiceError::InvalidOperation {
            message: "ownership initialization is performed by a Human (registration) or \
                      System (governed repair) actor; service identities are rejected"
                .into(),
            request_id: None,
        }),
    }
}

/// The authority steps appended to a creation (or governed-init)
/// transaction. Order is binding: the CAS takes the Bot's row write lock
/// first — the same first lock every authority flow takes — and every
/// later statement runs under it. `batch_id` is the governed migration's
/// batch tag on the initialization ledger row (`None` for registration and
/// plain governed-repair lanes, plan Task 17).
pub(super) fn ownership_initialization_steps(
    flavor: &DbSqlFlavor,
    env: &str,
    bot_id: &str,
    initialization: &OwnershipInitialization,
    source: &'static str,
    batch_id: Option<&str>,
) -> ServiceResult<Vec<DbTransactionStep>> {
    validate_initialization(initialization)?;
    let human_id = human_actor_id(&initialization.owner_user_id);
    // Unique per (operation, bot): one audit row per committed
    // initialization, and a caller-reused operation id across different
    // Bots can never collide on the table's unique audit id.
    let audit_id = format!("{}-init-{}", initialization.operation_id, bot_id);
    let bot_info = serde_json::to_string(&BotInfo {
        summary: Some(HUMAN_DEFAULT_SUMMARY.to_string()),
        ..Default::default()
    })
    .map_err(|_| sanitized("Human actor serialization failed"))?;
    let from_dual = match flavor {
        DbSqlFlavor::Sqlite => "",
        DbSqlFlavor::Mysql => " FROM DUAL",
    };
    let steps = vec![
        // 1. The brief's exact CAS: only an uninitialized, live row moves to 1.
        DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "UPDATE bcs_bots SET ownership_version = 1 \
                 WHERE env = ? AND bot_uuid = ? AND ownership_version = 0 AND is_deleted = 0",
                vec![Value::from(env), Value::from(bot_id)],
            ),
            expected_affected_rows: 1,
        },
        // 2. ensure_human: INSERT (OR) IGNORE — an existing Human row is
        //    never overwritten, matching the runtime ensure contract.
        DbTransactionStep::Execute(DbStatement::with_params(
            format!(
                "{} INTO bcs_bots \
                 (bot_uuid, actor_kind, name, bot_info, session_token, created_by, \
                  visibility, status, env, registered_at, updated_at) \
                 VALUES (?, 'human', ?, ?, ?, ?, 'protected', 'online', ?, \
                         CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                flavor.insert_or_ignore()
            ),
            vec![
                Value::from(human_id.as_str()),
                Value::from(initialization.owner_user_id.as_str()),
                Value::from(bot_info),
                Value::from(uuid::Uuid::new_v4().to_string()),
                Value::from(initialization.owner_user_id.as_str()),
                Value::from(env),
            ],
        )),
        // 3. The unique approved owner edge, guarded by slot emptiness; the
        //    partial unique index (uk_edge_active_owner_slot) is the
        //    structural backstop — a corrupt pre-existing slot rolls the
        //    whole creation back, it never merges owners.
        DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                format!(
                    "INSERT INTO edge_grants \
                       (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
                        originator_policy_type, originator_policy_data, \
                        management_source_kind, management_source_id) \
                     SELECT ?, ?, ?, 'owner', {ROLE_GRANT_REF_ID}, NULL, 'approved', \
                            'same_as_from', NULL, '{OWNER_SOURCE_KIND}', '{OWNER_SOURCE_ID}'\
                     {from_dual} \
                     WHERE NOT EXISTS (SELECT 1 FROM edge_grants o \
                           WHERE o.env = ? AND o.to_id = ? AND o.grant_kind = 'owner' \
                             AND o.status = 'approved')"
                ),
                vec![
                    Value::from(env),
                    Value::from(human_id.as_str()),
                    Value::from(bot_id),
                    Value::from(env),
                    Value::from(bot_id),
                ],
            ),
            expected_affected_rows: 1,
        },
        // 4. Default profile ensure: INSERT (OR) IGNORE — a pre-existing
        //    default is left byte-for-byte as it is (same id, revision,
        //    digest; never rebuilt by a failed-then-retried initialization).
        DbTransactionStep::Execute(DbStatement::with_params(
            format!(
                "{} INTO permission_profiles \
                   (bot_id, env, name, description, rules_template, revision, digest, \
                    is_default, status, created_by, updated_by) \
                 VALUES (?, ?, 'default', NULL, ?, 1, ?, 1, 'active', 'system', NULL)",
                flavor.insert_or_ignore()
            ),
            vec![
                Value::from(bot_id),
                Value::from(env),
                Value::from(WILDCARD_ALLOW_RULES),
                Value::from(sha256_hex(WILDCARD_ALLOW_RULES)),
            ],
        )),
        // 5. Initialization audit (bot_ownership_initializations): one row
        //    per committed operation, unique audit id derived from the
        //    operation id so a retried identical operation stays one row.
        //    `batch_id` is NULL for registration/plain governed-repair
        //    lanes and carries the governed migration batch otherwise.
        DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "INSERT INTO bot_ownership_initializations \
                   (audit_id, env, bot_id, owner_user_id, initial_version, source, \
                    actor_kind, actor_id, operation_id, batch_id) \
                 VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
                vec![
                    Value::from(audit_id),
                    Value::from(env),
                    Value::from(bot_id),
                    Value::from(initialization.owner_user_id.as_str()),
                    Value::from(source),
                    Value::from(initialization.actor.kind_str()),
                    Value::from(initialization.actor.actor_id()),
                    Value::from(initialization.operation_id.as_str()),
                    batch_id.map(Value::from).unwrap_or(Value::Null),
                ],
            ),
            expected_affected_rows: 1,
        },
    ];
    Ok(steps)
}

/// Sanitized storage failure (driver text may carry credentials).
pub(super) fn sanitized(message: &str) -> ServiceError {
    ServiceError::InternalError(message.into())
}

/// Re-read and re-branch after a CAS/step failure: races must surface the
/// state's business branch, never an "expected 1" internal detail.
async fn refresh_initialization_branch(
    repo: &PersistentBotRepo,
    bot_id: &str,
) -> ServiceError {
    let env = resolve_env();
    let rows = repo
        .db_query(
            "SELECT ownership_version, COALESCE(is_deleted, 0) AS deleted \
             FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
            vec![Value::from(bot_id), Value::from(env.as_str())],
        )
        .await;
    let Some(row) = rows.ok().and_then(|rows| rows.into_iter().next()) else {
        return sanitized("ownership re-validation read failed");
    };
    let deleted = row.get_i64("deleted").ok().flatten().unwrap_or(0);
    if deleted == 1 {
        return ServiceError::BotNotFound(bot_id.to_string());
    }
    let version = row
        .get_i64("ownership_version")
        .ok()
        .flatten()
        .unwrap_or(0);
    if version > 0 {
        return ServiceError::Authority(AuthorityError::Conflict(format!(
            "bot '{bot_id}' already has an initialized owner (ownership_version = {version}); \
             first-ownership initialization only applies to uninitialized (version 0) bots"
        )));
    }
    sanitized("ownership initialization failed")
}

impl PersistentBotRepo {
    /// Governed, atomic initialization of an EXISTING live Bot whose
    /// ownership is still uninitialized (version 0), `batch_id = NULL`
    /// on the initialization ledger (the plain governed-repair lane).
    pub(super) async fn initialize_existing_ownership_impl(
        &self,
        bot_id: &str,
        initialization: &OwnershipInitialization,
    ) -> ServiceResult<OwnershipState> {
        self.initialize_existing_ownership_with_batch_impl(bot_id, initialization, None)
            .await
    }

    /// Governed, atomic initialization tagging the migration `batch_id` on
    /// the initialization ledger (plan Task 17) — the recovery/replay key
    /// an interrupted `initialize_batch` replays against.
    pub(super) async fn initialize_existing_ownership_in_batch_impl(
        &self,
        bot_id: &str,
        initialization: &OwnershipInitialization,
        batch_id: &str,
    ) -> ServiceResult<OwnershipState> {
        if batch_id.trim().is_empty() {
            return Err(sanitized("batched initialization requires a non-blank batch id"));
        }
        self.initialize_existing_ownership_with_batch_impl(bot_id, initialization, Some(batch_id.trim()))
            .await
    }

    /// The one-transaction Task 5 lane both governed entries share.
    async fn initialize_existing_ownership_with_batch_impl(
        &self,
        bot_id: &str,
        initialization: &OwnershipInitialization,
        batch_id: Option<&str>,
    ) -> ServiceResult<OwnershipState> {
        validate_initialization(initialization)?;
        let env = resolve_env();
        // Fast-path read (never an authority source): branch missing,
        // soft-deleted and already-initialized Bots before locking.
        let rows = self
            .db_query(
                "SELECT ownership_version, COALESCE(is_deleted, 0) AS deleted \
                 FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
                vec![Value::from(bot_id), Value::from(env.as_str())],
            )
            .await
            .map_err(|_| sanitized("ownership initialization read failed"))?;
        let Some(row) = rows.into_iter().next() else {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        };
        let deleted = row.get_i64("deleted").ok().flatten().unwrap_or(0);
        let version = row
            .get_i64("ownership_version")
            .ok()
            .flatten()
            .unwrap_or(0);
        if deleted == 1 {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        }
        if version > UNINITIALIZED_OWNERSHIP_VERSION as i64 {
            return Err(ServiceError::Authority(AuthorityError::Conflict(format!(
                "bot '{bot_id}' already has an initialized owner (ownership_version = {version}); \
                 first-ownership initialization only applies to uninitialized (version 0) bots"
            ))));
        }
        // The one-transaction boundary. The first statement locks the Bot's
        // row (FOR UPDATE on MySQL; the write lock is reserved by SQLite's
        // IMMEDIATE transaction), matching every other authority flow.
        let lock_suffix = match self.flavor {
            DbSqlFlavor::Mysql => " FOR UPDATE",
            DbSqlFlavor::Sqlite => "",
        };
        let mut steps = vec![DbTransactionStep::Query(DbStatement::with_params(
            format!(
                "SELECT bot_uuid FROM bcs_bots \
                 WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0{lock_suffix}"
            ),
            vec![Value::from(bot_id), Value::from(env.as_str())],
        ))];
        steps.extend(ownership_initialization_steps(
            &self.flavor,
            &env,
            bot_id,
            initialization,
            SOURCE_GOVERNED_REPAIR,
            batch_id,
        )?);
        match self.db.transaction(steps).await {
            Ok(_) => Ok(OwnershipState {
                owner_user_id: initialization.owner_user_id.clone(),
                ownership_version: INITIALIZED_OWNERSHIP_VERSION,
            }),
            Err(DbError::ConditionFailed { expected, actual }) => {
                let _ = (expected, actual);
                Err(refresh_initialization_branch(self, bot_id).await)
            }
            Err(_) => Err(sanitized("ownership initialization commit failed")),
        }
    }
}

/// Memory twin: apply one initialization inside the caller-held critical
/// section (`bots` + `authority` write locks, the same state boundary as
/// registration and Task 3/4 authority). No intermediate failure path can
/// leave partial state: the armed test failure is consumed before any
/// mutation, and the human/profile ensures are idempotent under one
/// critical section.
pub(crate) fn memory_apply_initialization(
    bots: &mut BTreeMap<String, RegisteredBotInner>,
    authority: &mut MemoryAuthorityState,
    env: &str,
    bot_id: &str,
    initialization: &OwnershipInitialization,
    source: &str,
    batch_id: Option<&str>,
) -> ServiceResult<()> {
    validate_initialization(initialization)?;
    let human_id = human_actor_id(&initialization.owner_user_id);
    // ensure_human: preserve an existing row (name and fields), materialize
    // the default Human actor otherwise.
    if !bots.contains_key(&human_id) {
        bots.insert(
            human_id.clone(),
            RegisteredBotInner {
                bot_id: human_id.clone(),
                last_heartbeat: std::time::Instant::now(),
                capabilities: BotCapabilities {
                    name: Some(initialization.owner_user_id.clone()),
                    summary: Some(HUMAN_DEFAULT_SUMMARY.to_string()),
                    visibility: "protected".to_string(),
                    ..Default::default()
                },
                ws_connection: None,
                session_token: Some(uuid::Uuid::new_v4().to_string()),
                env: Some(env.to_string()),
                status: ActorStatus::Online,
                actor_kind: ActorKind::Human,
                created_by: Some(initialization.owner_user_id.clone()),
                protocol_version: 1,
                user_visibility: Default::default(),
                friend_ext: serde_json::Map::new(),
                friend_check_in_strategy: Default::default(),
            },
        );
    }
    // CAS equivalent: this helper runs only after the caller proved the
    // version is 0. Write the version, then the edge must not duplicate an
    // approved slot (the memory twin of the unique partial index).
    let already_owner = authority.role_rows.iter().any(|row| {
        row.env == env
            && row.to_id == bot_id
            && row.grant_kind == "owner"
            && row.status == "approved"
    });
    if already_owner {
        return Err(sanitized(
            "initialized bot already has an approved owner edge",
        ));
    }
    authority
        .ownership_versions
        .insert(bot_id.to_string(), INITIALIZED_OWNERSHIP_VERSION);
    let edge_id = authority.allocate_edge_id();
    authority.role_rows.push(MemoryRoleEdgeRow {
        id: edge_id,
        env: env.to_string(),
        from_id: human_id,
        to_id: bot_id.to_string(),
        grant_kind: "owner".to_string(),
        grant_ref_id: ROLE_GRANT_REF_ID as i64,
        rules: None,
        status: "approved".to_string(),
        management_source_kind: OWNER_SOURCE_KIND.to_string(),
        management_source_id: OWNER_SOURCE_ID.to_string(),
    });
    // Default profile ensure: an existing id is never reallocated or
    // bumped (D12 rule 2, the memory twin of INSERT OR IGNORE).
    let profile_key = (env.to_string(), bot_id.to_string());
    if !authority.default_profiles.contains_key(&profile_key) {
        let id = authority.next_default_profile_id;
        authority.next_default_profile_id = id.saturating_add(1);
        authority.default_profiles.insert(profile_key, id);
    }
    // The initialization audit row: one per committed operation. The
    // batch tag mirrors the SQL ledger's `batch_id` column (NULL for the
    // registration/plain governed-repair lanes).
    authority
        .initialization_records
        .push(MemoryOwnershipInitRecord {
            audit_id: format!("{}-init-{}", initialization.operation_id, bot_id),
            env: env.to_string(),
            bot_id: bot_id.to_string(),
            owner_user_id: initialization.owner_user_id.to_string(),
            source: source.to_string(),
            actor_kind: initialization.actor.kind_str().to_string(),
            actor_id: initialization.actor.actor_id().to_string(),
            operation_id: initialization.operation_id.to_string(),
            batch_id: batch_id.map(str::to_string),
        });
    Ok(())
}

impl super::MemoryBotRepo {
    /// Governed memory initialization of an existing live version-0 Bot,
    /// sharing the exact critical section of the Task 3/4 authority state
    /// (bots + authority under the deletion lane's deleted→bots order);
    /// `batch_id = NULL` on the ledger record (plain governed repair).
    pub(crate) async fn initialize_existing_ownership_impl(
        &self,
        bot_id: &str,
        initialization: &OwnershipInitialization,
    ) -> ServiceResult<OwnershipState> {
        self.initialize_existing_ownership_with_batch_impl(bot_id, initialization, None)
            .await
    }

    /// Governed memory initialization tagging the migration `batch_id`
    /// (plan Task 17) on the ledger record.
    pub(crate) async fn initialize_existing_ownership_in_batch_impl(
        &self,
        bot_id: &str,
        initialization: &OwnershipInitialization,
        batch_id: &str,
    ) -> ServiceResult<OwnershipState> {
        if batch_id.trim().is_empty() {
            return Err(sanitized("batched initialization requires a non-blank batch id"));
        }
        self.initialize_existing_ownership_with_batch_impl(
            bot_id,
            initialization,
            Some(batch_id.trim()),
        )
        .await
    }

    /// The memory twin of the one-transaction Task 5 lane both governed
    /// entries share.
    async fn initialize_existing_ownership_with_batch_impl(
        &self,
        bot_id: &str,
        initialization: &OwnershipInitialization,
        batch_id: Option<&str>,
    ) -> ServiceResult<OwnershipState> {
        validate_initialization(initialization)?;
        if self.take_authority_write_failure().await {
            return Err(sanitized("test-injected authority write failure"));
        }
        let env = resolve_env();
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        }
        let mut bots = self.bots.write().await;
        if !bots.contains_key(bot_id) {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        }
        let mut authority = self.authority.write().await;
        let version = authority
            .ownership_versions
            .get(bot_id)
            .copied()
            .unwrap_or(UNINITIALIZED_OWNERSHIP_VERSION);
        if version != UNINITIALIZED_OWNERSHIP_VERSION {
            return Err(ServiceError::Authority(AuthorityError::Conflict(format!(
                "bot '{bot_id}' already has an initialized owner (ownership_version = {version}); \
                 first-ownership initialization only applies to uninitialized (version 0) bots"
            ))));
        }
        memory_apply_initialization(
            &mut bots,
            &mut authority,
            &env,
            bot_id,
            initialization,
            SOURCE_GOVERNED_REPAIR,
            batch_id,
        )?;
        Ok(OwnershipState {
            owner_user_id: initialization.owner_user_id.clone(),
            ownership_version: INITIALIZED_OWNERSHIP_VERSION,
        })
    }
}