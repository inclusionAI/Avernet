//! Trusted (ownership-initialized) SQL registration composites
//! for [`PersistentBotRepo`] — bot-authority plan Task 4,
//! spec §10.2.
//!
//! One `transaction_with_options` for the Bot row (with
//! `ownership_version = 1` and the owner as first
//! `created_by`), the Human ACTOR row (`bcs_bots`, ruled as
//! in-transaction 必要创建 — ruling T4-1; the actor row IS
//! the authority Human registry), the approved owner edge,
//! the default runtime profile and the `initialize` audit.
//! Failure is all-or-nothing: nothing is hydrated into
//! process memory, no token is mapped and no runtime
//! activation happens. An existing or tombstoned identity is
//! never overwritten, so a reused Bot ID can never reset a
//! committed owner.
//!
//! The governance `initialize_ownership` backfill runs the
//! same authority pieces on an existing live Bot without
//! rewriting `created_by` and without resetting an owner
//! whose registration already initialized. The Human
//! screening for the backfill lives in the governance FACTS
//! read (`read_ownership_governance` — the bcs-admin dry-run
//! classification, spec §10.4), never in the composite.

use bcs_db_api::{
    DbError, DbSqlFlavor, DbStatement, DbTransactionParam,
    DbTransactionStep, DbValue as Value, db_get_column, db_get_column_opt,
};
use bcs_edge_permission_store::lifecycle;
use bcs_service_api::types::bot_authority::{
    AuthorityMutation, BotOwnershipGovernance,
    BotOwnershipView, OwnershipInitialization, human_actor_id,
};
use bcs_service_api::{ActorKind, BotCapabilities, ServiceError, ServiceResult};

use super::PersistentBotRepo;
use super::registration_create::identity_duplicate;
use crate::BotInfo;
use crate::ownership;

/// Same deterministic-rollback retry budget as the
/// manager/transfer write paths.
const MAX_RETRIES: u32 = 3;

fn registration_failed() -> ServiceError {
    ServiceError::InternalError("trusted bot registration failed".into())
}

fn initialization_uncertain() -> ServiceError {
    ServiceError::InternalError(format!(
        "{}: the ownership transaction exceeded the deadline; per \
         contract it rolled back and the caller must not auto-retry",
        bcs_service_api::application::v1::bot_authority::
            ERROR_BOT_TX_OUTCOME_UNCERTAIN
    ))
}

fn map_registration_tx_error(err: DbError) -> ServiceError {
    match err {
        DbError::Timeout => initialization_uncertain(),
        _ => registration_failed(),
    }
}

/// Deterministic local rollbacks whose whole effect the
/// next iteration's fresh read re-classifies (the manager
/// paths' same retry rule).
fn deterministic_rollback(err: &DbError) -> bool {
    match err {
        DbError::ConditionFailed { .. }
        | DbError::Backend(_) => true,
        _ => false,
    }
}

impl PersistentBotRepo {
    /// Bot row of the trusted registration: identical shape
    /// to the create-only INSERT plus `ownership_version = 1`
    /// — the runtime register paths keep version 0 (no-Human
    /// runtime entry, spec §10.3).
    pub(super) fn owned_bot_insert_statement(
        &self,
        bot_id: &str,
        capabilities: &BotCapabilities,
        created_by: &str,
        token: &str,
        env: &str,
    ) -> ServiceResult<DbTransactionStep> {
        let info = BotInfo {
            summary: capabilities.summary.clone(),
            domains: capabilities.domains.clone(),
            skills: capabilities.skills.clone(),
            scopes: capabilities.scopes.clone(),
            binding_channels: capabilities.binding_channels.clone(),
            hidden: false,
            agent_code: capabilities.agent_code.clone(),
            agent_token: capabilities.agent_token.clone(),
        };
        let info = serde_json::to_string(&info)
            .map_err(|_| registration_failed())?;
        let visibility = if capabilities.visibility.is_empty() {
            "private"
        } else {
            capabilities.visibility.as_str()
        };
        Ok(DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, name, bot_info, session_token, \
                 created_by, visibility, status, actor_kind, agent_code, \
                 is_deleted, env, registered_at, updated_at, connection_mode, \
                 ownership_version) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, \
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, 1)",
                vec![
                    Value::from(bot_id),
                    Value::from(capabilities.name.as_deref().unwrap_or(bot_id)),
                    Value::from(info.as_str()),
                    Value::from(token),
                    Value::from(created_by),
                    Value::from(visibility),
                    Value::from("online"),
                    Value::from("bot"),
                    Value::from(capabilities.agent_code.clone()),
                    Value::from(0_i64),
                    Value::from(env),
                    Value::from(bcs_service_api::bot_provider::BotConnectionMode::Plugin.as_str()),
                ],
            ),
            expected_affected_rows: 1,
        })
    }

    async fn bot_exists(&self, bot_id: &str, env: &str) -> ServiceResult<bool> {
        let rows = self
            .db_query(
                "SELECT bot_uuid FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
                vec![Value::from(bot_id), Value::from(env)],
            )
            .await
            .map_err(|_| registration_failed())?;
        Ok(!rows.is_empty())
    }

    pub(super) async fn create_registration_owned(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
        initialization: &OwnershipInitialization,
    ) -> ServiceResult<bool> {
        let env = bcs_config::resolve_env_str();
        // The first created_by IS the trusted owner; diverged
        // callers are refused instead of silently recorded.
        if created_by.trim() != initialization.owner_user_id {
            return Err(ownership::initialization_invalid_public(
                "registration created_by must be the verified owner",
            ));
        }
        if bot_id.trim().is_empty() || token.trim().is_empty() {
            return Err(ownership::initialization_invalid_public(
                "Bot ID and runtime credential are required",
            ));
        }
        // Create-only: an existing identity (active or
        // tombstoned) is never rewritten.
        if self.bot_exists(&bot_id, &env).await? {
            return Ok(false);
        }
        let human_session_token = uuid::Uuid::new_v4().to_string();
        let mut steps = vec![self.owned_bot_insert_statement(
            &bot_id, &capabilities, created_by, token, &env,
        )?];
        let ownership_steps = ownership::ownership_authorization_steps(
            self.flavor,
            &env,
            &bot_id,
            initialization,
            &human_session_token,
            steps.len(),
        )?;
        steps.extend(ownership_steps.steps);
        steps.push(ownership_steps.final_guard);

        match self
            .db
            .transaction_with_options(steps, ownership::transaction_options())
            .await
        {
            Ok(_) => Ok(true),
            // A racing creator between the pre-read and the
            // INSERT resolves to plain "already present"; the
            // winner's committed registration stays untouched.
            Err(error) if identity_duplicate(&error, self.flavor) => {
                if self.bot_exists(&bot_id, &env).await? {
                    Ok(false)
                } else {
                    Err(registration_failed())
                }
            }
            Err(error) => Err(map_registration_tx_error(error)),
        }
    }

    /// Governance backfill of ownership on an existing live
    /// Bot (Task 4, spec §10.5): owner edge + version 1 +
    /// `initialize` audit in ONE transaction, idempotent and
    /// owner-preserving. Tombstoned or missing IDs never
    /// resurrect.
    pub(super) async fn initialize_owned_bot(
        &self,
        bot_id: &str,
        owner_user_id: &str,
        mutation: &AuthorityMutation,
    ) -> ServiceResult<BotOwnershipView> {
        let env = bcs_config::resolve_env_str();
        if owner_user_id.trim().is_empty()
            || owner_user_id != owner_user_id.trim()
            || owner_user_id.starts_with("human_")
        {
            return Err(ownership::initialization_invalid_public(
                "owner_user_id must be the raw verified User ID",
            ));
        }
        ownership::validate_mutation(mutation)?;
        let owner_actor = ownership::owner_actor_id(owner_user_id);
        let (audit_kind, audit_actor): (&str, String) = (
            mutation.actor.kind().as_str(),
            mutation.actor.actor_id(),
        );
        let lock_suffix = match self.flavor {
            DbSqlFlavor::Mysql => " FOR UPDATE",
            DbSqlFlavor::Sqlite => "",
        };

        for _ in 0..MAX_RETRIES {
            // Pre-read is parameter preparation only: absent or
            // tombstoned IDs never resurrect; an
            // already-initialized Bot returns its CURRENT owner
            // untouched (no created_by rewrite, no owner reset).
            let rows = self
                .db_query(
                    "SELECT ownership_version, is_deleted, actor_kind, created_by \
                     FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
                    vec![Value::from(bot_id), Value::from(env.as_str())],
                )
                .await
                .map_err(|_| registration_failed())?;
            match rows.as_slice() {
                [row] => {
                    let is_deleted: i64 = db_get_column(
                        row,
                        "is_deleted",
                    )
                    .map_err(|_| registration_failed())?;
                    if is_deleted != 0 {
                        return Err(ServiceError::Conflict(
                            "bot was removed; ownership cannot be \
                             initialized on a tombstoned ID"
                                .to_string(),
                        ));
                    }
                    // Ownership targets are physical Bots only: a
                    // Human ACTOR row must never gain an owner edge
                    // (bcs-admin classify() rejects it too).
                    let actor_kind: String = db_get_column_opt(row, "actor_kind")
                        .map_err(|_| registration_failed())?
                        .unwrap_or_else(|| "bot".to_string());
                    if actor_kind != "bot" {
                        return Err(ServiceError::Conflict(
                            "ownership targets only physical Bots; a Human actor row is never initialized".to_string(),
                        ));
                    }

                    let version: i64 = db_get_column(
                        row,
                        "ownership_version",
                    )
                    .map_err(|_| registration_failed())?;
                    if version > 0 {
                        let view = self.current_owner_view(bot_id).await?;
                        // Never reset a committed owner: a repeat
                        // with the SAME owner is idempotent; a
                        // different owner is refused.
                        if view.owner_user_id != owner_user_id {
                            return Err(ServiceError::Conflict(
                                "bot ownership is already initialized                                  with a different owner; the owner is                                  never reset"
                                    .to_string(),
                            ));
                        }
                        return Ok(view);
                    }
                }
                [] => return Err(ServiceError::BotNotFound(bot_id.to_string())),
                _ => return Err(registration_failed()),
            }

            // The initialized owner must be the stored creator fact,
            // byte-for-byte: a candidate list query (case-insensitive
            // collation) is never authority for minting ownership.
            // Only the version-0 INIT path is guarded — a committed
            // owner's idempotent echo (post-transfer, where the
            // current owner legitimately differs from the historical
            // creator record) must keep working. A NULL creator
            // carries no fact to contradict and stays initializable.
            {
                let created_by: Option<String> = db_get_column_opt(&rows[0], "created_by")
                    .map_err(|_| registration_failed())?;
                if let Some(creator) = created_by.as_deref() {
                    if creator != owner_user_id {
                        return Err(ServiceError::Conflict(format!(
                            "stored creator '{creator}' does not match the requested owner '{owner_user_id}'; ownership initialization requires the stored creator record to name the owner"
                        )));
                    }
                }
            }

            // In-transaction; statement definitions only — no
            // execution branches. All guard rows are consumed
            // by the final binding statement.
            let mut steps: Vec<DbTransactionStep> = Vec::new();

            // 0: Bot row lock + liveness guard.
            steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    format!(
                        "SELECT bot_uuid AS ok FROM bcs_bots \
                         WHERE bot_uuid = ? AND env = ? \
                         AND is_deleted = 0{lock_suffix}"
                    )
                    .as_str(),
                    vec![
                        DbTransactionParam::value(Value::from(bot_id)),
                        DbTransactionParam::value(Value::from(env.as_str())),
                    ],
                ),
            ));
            let bot_guard = steps.len() - 1;

            // 1: version must still be 0 under the row lock.
            steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    format!(
                        "SELECT t.c AS ok FROM \
                         (SELECT COUNT(*) AS c FROM bcs_bots \
                          WHERE bot_uuid = ? AND env = ? AND \
                          ownership_version = 0 AND is_deleted = 0) t \
                         WHERE t.c = 1{lock_suffix}"
                    )
                    .as_str(),
                    vec![
                        DbTransactionParam::value(Value::from(bot_id)),
                        DbTransactionParam::value(Value::from(env.as_str())),
                    ],
                ),
            ));
            let version_guard = steps.len() - 1;

            // 2: owner edge (insert-or-ignore) and its exactly-one
            // guard. (The Human screening of the backfill lives
            // in the governance facts read; this composite only
            // re-verifies the Bot row and the version CAS — the
            // identity-row guard was the removed T4-1
            // workaround's persistent-layer echo.)
            steps.push(DbTransactionStep::Execute(
                lifecycle::owner_edge_statement(
                    self.flavor,
                    &env,
                    &owner_actor,
                    bot_id,
                ),
            ));
            steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    format!(
                        "SELECT t.c AS ok FROM \
                         (SELECT COUNT(*) AS c FROM edge_grants \
                          WHERE env = ? AND {} AND grant_kind = 'owner' \
                          AND grant_ref_id = 0 AND rules IS NULL \
                          AND status = 'approved') t \
                         WHERE t.c = 1{lock_suffix}",
                        match self.flavor {
                            DbSqlFlavor::Mysql => "CAST(to_id AS BINARY) = ?",
                            DbSqlFlavor::Sqlite => "to_id = ?",
                        }
                    )
                    .as_str(),
                    vec![
                        DbTransactionParam::value(Value::from(env.as_str())),
                        DbTransactionParam::value(Value::from(bot_id)),
                    ],
                ),
            ));
            let owner_guard = steps.len() - 1;

            // 3: version CAS — exactly one row moves to
    // version 1. The guard re-verifies the physical-Bot
    // kind and (when a creator fact exists) the requested
    // owner identity byte-for-byte, so a candidate list
    // read elsewhere is never the granting authority.
    // A NULL creator stays initializable (governance).
    // The byte-exact identity check is dialect-agnostic:
    // a plain `=` prefilter plus HEX equality on every
    // engine (vendor CAST broke on the SQLite-backed
    // MysqlStore fixtures).
    steps.push(DbTransactionStep::ExecuteChecked {
        statement: DbStatement::with_params(
            "UPDATE bcs_bots SET ownership_version = 1 \
             WHERE bot_uuid = ? AND env = ? AND \
             ownership_version = 0 AND is_deleted = 0 \
             AND actor_kind = 'bot' \
             AND (created_by IS NULL OR (created_by = ? AND \
             HEX(created_by) = HEX(?)))",
            vec![
                Value::from(bot_id),
                Value::from(env.as_str()),
                Value::from(owner_user_id),
                Value::from(owner_user_id),
            ],
        ),
        expected_affected_rows: 1,
    });

            // 4: audit row + its guard. The audit identity is
            // the real initiating actor of the mutation; tokens
            // never enter audit rows.
            steps.push(DbTransactionStep::ExecuteChecked {
                statement: lifecycle::initialize_audit_statement(
                    &env,
                    bot_id,
                    &mutation.mutation_id,
                    audit_kind,
                    &audit_actor,
                    &owner_actor,
                    1,
                ),
                expected_affected_rows: 1,
            });
            steps.push(DbTransactionStep::Query(
                lifecycle::initialize_audit_guard_statement(
                    &env,
                    bot_id,
                    &mutation.mutation_id,
                ),
            ));
            let audit_guard = steps.len() - 1;

            // 5: final guard consumption — a failed guard has
            // no row to bind and the transaction rolls back.
            steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    "SELECT ? AS t0, ? AS t1, ? AS t2, ? AS t3",
                    vec![
                        DbTransactionParam::query_result(
                            bot_guard, 0, "ok",
                        ),
                        DbTransactionParam::query_result(
                            version_guard, 0, "ok",
                        ),
                        DbTransactionParam::query_result(
                            owner_guard, 0, "ok",
                        ),
                        DbTransactionParam::query_result(
                            audit_guard, 0, "sequence",
                        ),
                    ],
                ),
            ));

            match self
                .db
                .transaction_with_options(steps, ownership::transaction_options())
                .await
            {
                Ok(_) => {
                    return Ok(BotOwnershipView {
                        bot_id: bot_id.to_string(),
                        owner_user_id: owner_user_id.to_string(),
                        owner_actor_id: owner_actor.clone(),
                    });
                }
                Err(error) if deterministic_rollback(&error) => {
                    // A racing initialize committed first: the
                    // next iteration's pre-read returns the
                    // winner's state, nothing is reset.
                    continue;
                }
                Err(DbError::Timeout) => {
                    return Err(initialization_uncertain());
                }
                Err(_) => return Err(registration_failed()),
            }
        }
        Err(ServiceError::InternalError(
            "ownership initialization kept racing; retry budget \
             exhausted"
                .to_string(),
        ))
    }

    /// Current owner of an initialized Bot (governance read
    /// for the idempotent backfill result).
    async fn current_owner_view(
        &self,
        bot_id: &str,
    ) -> ServiceResult<BotOwnershipView> {
        let env = bcs_config::resolve_env_str();
        let predicate = match self.flavor {
            DbSqlFlavor::Mysql => "CAST(to_id AS BINARY) = ?",
            DbSqlFlavor::Sqlite => "to_id = ?",
        };
        let rows = self
            .db_query(
                &format!(
                    "SELECT from_id FROM edge_grants \
                     WHERE env = ? AND {predicate} \
                     AND grant_kind = 'owner' \
                     AND grant_ref_id = 0 AND rules IS NULL \
                     AND status = 'approved'"
                ),
                vec![Value::from(env.as_str()), Value::from(bot_id)],
            )
            .await
            .map_err(|_| registration_failed())?;
        if rows.len() != 1 {
            // Owner completeness is part of the initialization
            // contract (the strict reads refuse such states).
            return Err(ServiceError::InternalError(format!(
                "{}: bot {bot_id} has {} approved owner edges",
                bcs_service_api::application::v1::bot_authority::
                    ERROR_BOT_OWNER_INTEGRITY,
                rows.len()
            )));
        }
        let actor: String =
            db_get_column(&rows[0], "from_id")
                .map_err(|_| registration_failed())?;
        let user = actor
            .strip_prefix("human_")
            .ok_or_else(registration_failed)?
            .to_string();
        Ok(BotOwnershipView {
            bot_id: bot_id.to_string(),
            owner_user_id: user,
            owner_actor_id: actor,
        })
    }
}

/// ONE governance-facts read for the bcs-admin
/// ownership-backfill dry-run (spec §10.4): the SAME facts the
/// apply classification consumes — live state, `created_by`,
/// whether the owner's Human ACTOR row is live in the instance
/// env, the current ownership version and the approved owner
/// edges — so the dry-run classification and the apply write
/// can never disagree about the world. A pure read; `None` for
/// a Bot unknown in the bound env.
impl PersistentBotRepo {
    pub(super) async fn read_ownership_governance(
        &self,
        bot_id: &str,
    ) -> ServiceResult<Option<BotOwnershipGovernance>> {
        let env = bcs_config::resolve_env_str();
        let rows = self
            .db_query(
                "SELECT actor_kind, created_by, \
                        ownership_version, is_deleted \
                 FROM bcs_bots \
                 WHERE bot_uuid = ? AND env = ? LIMIT 1",
                vec![Value::from(bot_id), Value::from(env.as_str())],
            )
            .await
            .map_err(|_| registration_failed())?;
        let Some(row) = rows.first() else {
            return Ok(None);
        };
        let actor_kind: String = db_get_column(row, "actor_kind")
            .unwrap_or_else(|_| "bot".to_string());
        let actor_kind = if actor_kind == "human" {
            ActorKind::Human
        } else {
            ActorKind::Bot
        };
        let is_deleted: i64 = db_get_column(row, "is_deleted")
            .unwrap_or_else(|_| 0);
        let created_by: Option<String> =
            db_get_column_opt(row, "created_by")
                .map_err(|_| registration_failed())?;
        let ownership_version: i64 = db_get_column(row, "ownership_version")
            .unwrap_or_else(|_| 0);
        let owned = ownership_version.max(0) as u64;

        // The owner's Human ACTOR row must be live in the instance
        // env (`actor_kind = 'human'`, `human_<user_id>`, not
        // tombstoned) — the same registry the authority guards
        // read, so the dry-run classification can never
        // disagree with the guards.
        let bot_predicate = match self.flavor {
            DbSqlFlavor::Mysql => "CAST(bot_uuid AS BINARY) = ?",
            DbSqlFlavor::Sqlite => "bot_uuid = ?",
        };
        let owner_actor_present = match created_by.as_deref() {
            Some(user) => {
                let rows = self
                    .db_query(
                        &format!(
                            "SELECT COUNT(*) AS c FROM bcs_bots \
                             WHERE {bot_predicate} \
                               AND actor_kind = 'human' \
                               AND env = ? \
                               AND COALESCE(is_deleted, 0) = 0"
                        ),
                        vec![
                            Value::from(
                                human_actor_id(user).as_str(),
                            ),
                            Value::from(env.as_str()),
                        ],
                    )
                    .await
                    .map_err(|_| registration_failed())?;
                rows.first()
                    .map(|row| {
                        db_get_column::<i64>(row, "c")
                            .unwrap_or(0)
                    })
                    .unwrap_or(0)
                    > 0
            }
            None => false,
        };

        let owner_predicate = match self.flavor {
            DbSqlFlavor::Mysql => "CAST(to_id AS BINARY) = ?",
            DbSqlFlavor::Sqlite => "to_id = ?",
        };
        let edge_rows = self
            .db_query(
                &format!(
                    "SELECT from_id FROM edge_grants \
                     WHERE env = ? AND {owner_predicate} \
                     AND grant_kind = 'owner' \
                     AND grant_ref_id = 0 AND rules IS NULL \
                     AND status = 'approved' \
                     ORDER BY from_id ASC"
                ),
                vec![Value::from(env.as_str()), Value::from(bot_id)],
            )
            .await
            .map_err(|_| registration_failed())?;
        let owner_edges = edge_rows
            .iter()
            .map(|row| {
                db_get_column::<String>(row, "from_id")
            })
            .collect::<Result<Vec<String>, _>>()
            .map_err(|_| registration_failed())?;

        Ok(Some(BotOwnershipGovernance {
            bot_id: bot_id.to_string(),
            actor_kind,
            live: is_deleted == 0,
            created_by,
            owner_actor_present,
            ownership_version: owned,
            owner_edges,
        }))
    }
}
