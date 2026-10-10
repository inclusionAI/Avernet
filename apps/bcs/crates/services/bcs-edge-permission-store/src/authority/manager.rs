//! Manual manager mutations + manager listing over the authority schema
//! (plan Task 4, spec §5.4/§6).
//!
//! `mutate_manager` is a ONE-TRANSACTION contract built from the Task 2
//! primitives:
//! 1. a validated read (one read transaction over the target Bot's rows —
//!    aggregate existence/ownership/authority/subject checks + the subject's
//!    team sources) selects WHICH mutation applies and rejects invalid
//!    requests with their business branches (`BotNotFound`,
//!    `OwnershipNotInitialized`, `CorruptAuthority`, `Forbidden`,
//!    `Conflict`, `InvalidSubject`);
//! 2. ONE write transaction on the serialized Bot boundary performs
//!    lock → audited, re-checked, changing writes: every changing statement
//!    (and its batched `bot_manager_changes` INSERT SELECT) re-validates the
//!    actor's current owner/manager role INSIDE the transaction, so a
//!    concurrently revoked manager can never commit a mutation. An
//!    `ExecuteChecked` pin makes any drift between the validated read and
//!    the write transaction roll the WHOLE attempt back; the store then
//!    re-validates (bounded retries) and surfaces the branch the new state
//!    demands (typically `Forbidden` or an idempotent no-change).
//!
//! Revokes select the actually-changed edges FIRST (brief: 批量 INSERT
//! SELECT 审计选取实际变化边) and attach the bulk UPDATE with the brief's
//! revoke predicate (only `direct`/`ownership_transfer`; `team/*` rows never
//! match). Grants restore a previously revoked row under the same id —
//! never INSERT-IGNORE. Large source sets cost ONE audit INSERT SELECT and
//! ONE bulk UPDATE whose parameter counts do not grow with the source count.
//!
//! `list_managers` shares the Bot validation and returns the owner in its
//! own field plus one user-deduplicated `user_id ASC` page.

use bcs_db_api::{
    DbError, DbRow, DbSqlFlavor, DbStatement, DbTransactionStep, DbTransactionStepResult,
    DbValue,
};
use bcs_domain::ManagementSource;
use tracing::warn;

use bcs_service_api::port::repo::bot_authority::{human_actor_id, user_id_from_actor};
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::{
    AuditActor, BotManagerList, BotManagerSummary, ManagerMutation, ManagerMutationResult,
};
use bcs_service_api::{ServiceError, ServiceResult};

use super::audit::manager_change_audit_statement;
use super::codec::{ownership_state_from_owner_row, relation_from_row, ROLE_ROW_COLUMNS};
use crate::common::service_db_error;

/// Optimistic-write attempts before surfacing a retryable conflict: each
/// attempt re-validates, so genuine state settles into its business branch
/// long before this budget is spent.
const MAX_MUTATION_ATTEMPTS: usize = 3;
/// Page-users chunk of the manager-list source lookup (keeps bound
/// parameters per statement safely below the SQLite ceiling).
const MANAGER_LIST_PAGE_CHUNK: usize = 200;

impl super::reads::DbBotAuthorityStore {
    // ------------------------------------------------------------------
    // mutate_manager
    // ------------------------------------------------------------------

    pub(super) async fn mutate_manager_inner(
        &self,
        actor: AuditActor,
        bot_id: &str,
        mutation: ManagerMutation,
    ) -> ServiceResult<ManagerMutationResult> {
        let subject_user_id = match &mutation {
            ManagerMutation::GrantDirect { user_id } | ManagerMutation::RevokeNonTeam { user_id } => {
                user_id.clone()
            }
        };
        let mut last_conflict = None;
        for _ in 0..MAX_MUTATION_ATTEMPTS {
            match self
                .mutate_manager_once(actor.clone(), bot_id, &subject_user_id, &mutation)
                .await
            {
                Ok(result) => return Ok(result),
                Err(MutateAttempt::Service(err)) => return Err(err),
                Err(MutateAttempt::Drift(err)) => {
                    warn!(
                        bot_id,
                        "manager mutation lost its optimistic window, re-validating: {}",
                        err
                    );
                    last_conflict = Some(err);
                }
            }
        }
        Err(ServiceError::Authority(AuthorityError::Conflict(format!(
            "concurrent manager mutation on bot '{bot_id}'; retry: {}",
            last_conflict
                .as_ref()
                .map(|err| err.to_string())
                .unwrap_or_else(|| "optimistic window lost repeatedly".to_string())
        ))))
    }

    /// One validate → plan → atomically-mutate attempt.
    async fn mutate_manager_once(
        &self,
        actor: AuditActor,
        bot_id: &str,
        subject_user_id: &str,
        mutation: &ManagerMutation,
    ) -> Result<ManagerMutationResult, MutateAttempt> {
        // Structural fail-closed FIRST: manager mutations belong to the
        // current Human owner/managers (Gate 0). A Service/System identifier
        // must never be allowed to alias a `human_<uid>` role-edge
        // `from_id`, so non-Human actors never reach an XY-bound statement.
        let actor_from_id = match &actor {
            AuditActor::Human { user_id } => human_actor_id(user_id),
            AuditActor::Service { .. } | AuditActor::System { .. } => {
                return Err(MutateAttempt::Service(ServiceError::Authority(
                    AuthorityError::Forbidden(format!(
                        "manager mutations are performed by the current Human owner/managers; \
                         this actor kind must use its governed lane (actor kind '{}')",
                        actor.kind_str()
                    )),
                )))
            }
        };
        let subject_from_id = human_actor_id(subject_user_id);

        // -- Validated read transaction: aggregate + team sources ------------
        let validated = self
            .db
            .transaction(vec![
                DbTransactionStep::Query(self.validation_aggregate_statement(
                    bot_id,
                    &actor_from_id,
                    &subject_from_id,
                )),
                DbTransactionStep::Query(self.subject_team_sources_statement(
                    bot_id,
                    &subject_from_id,
                )),
            ])
            .await
            .map_err(|err| MutateAttempt::Service(self.db_error("authority_mutate_read", err)))?;
        let rows = match &validated[0] {
            DbTransactionStepResult::Rows(rows) => rows.clone(),
            _ => return Err(MutateAttempt::Service(ServiceError::InternalError(
                "authority_mutate_read: expected the aggregate query".to_string(),
            ))),
        };
        let team_rows = match &validated[1] {
            DbTransactionStepResult::Rows(rows) => rows.clone(),
            _ => return Err(MutateAttempt::Service(ServiceError::InternalError(
                "authority_mutate_read: expected the team-source query".to_string(),
            ))),
        };
        let Some(snapshot) = rows.into_iter().next() else {
            return Err(MutateAttempt::Service(ServiceError::BotNotFound(
                bot_id.to_string(),
            )));
        };
        let ownership_version = row_u64(&snapshot, "ownership_version")
            .map_err(MutateAttempt::Service)?;
        if ownership_version == bcs_domain::UNINITIALIZED_OWNERSHIP_VERSION {
            return Err(MutateAttempt::Service(ServiceError::Authority(
                AuthorityError::OwnershipNotInitialized {
                    bot_id: bot_id.to_string(),
                    env: self.env.clone(),
                },
            )));
        }
        let owner_edge_count = row_u64(&snapshot, "owner_edge_count")?;
        if owner_edge_count != 1 {
            return Err(MutateAttempt::Service(self.corrupt(
                bot_id,
                format!(
                    "initialized bot has {owner_edge_count} approved owner edges \
                     (manager mutation denied)"
                ),
            )));
        }
        let actor_role_count = row_u64(&snapshot, "actor_role_count")?;
        if actor_role_count == 0 {
            return Err(MutateAttempt::Service(ServiceError::Authority(
                AuthorityError::Forbidden(format!(
                    "actor '{}' holds no current owner/manager role on bot '{}'",
                    actor.actor_id(),
                    bot_id
                )),
            )));
        }
        let target_is_owner_count = row_u64(&snapshot, "target_is_owner_count")?;
        if target_is_owner_count > 0 {
            return Err(MutateAttempt::Service(ServiceError::Authority(
                AuthorityError::Conflict(format!(
                    "user '{subject_user_id}' is the owner of bot '{bot_id}'; \
                     the owner role changes only through the ownership-transfer flow"
                )),
            )));
        }
        let target_live_count = row_u64(&snapshot, "target_live_count")?;
        if target_live_count == 0 {
            return Err(MutateAttempt::Service(ServiceError::Authority(
                AuthorityError::InvalidSubject(format!(
                    "target user '{subject_user_id}' is not a live human actor \
                     in env '{}'",
                    self.env
                )),
            )));
        }

        // -- Branch decision at the validated snapshot -----------------------
        enum Plan {
            /// Idempotent repeat at the validated state. `remaining_team_sources`
            /// is populated ONLY for revokes (the field is defined empty
            /// unless a revoke left team sources in place).
            Noop {
                remaining_team_sources: Vec<String>,
            },
            GrantInsert,
            GrantRestore,
            Revoke { expected_edge_writes: u64 },
        }
        let plan = match mutation {
            ManagerMutation::GrantDirect { .. } => {
                let direct_approved = row_u64(&snapshot, "direct_approved_count")?;
                let direct_revoked = snapshot
                    .get_i64("direct_revoked_id")
                    .ok()
                    .flatten()
                    .is_some();
                if direct_approved > 0 {
                    Plan::Noop {
                        remaining_team_sources: Vec::new(),
                    }
                } else if direct_revoked {
                    Plan::GrantRestore
                } else {
                    Plan::GrantInsert
                }
            }
            ManagerMutation::RevokeNonTeam { .. } => {
                let expected = row_u64(&snapshot, "nonteam_approved_count")?;
                if expected == 0 {
                    // Team-only subject (or nothing left): report the team
                    // sources read under the same validated snapshot.
                    Plan::Noop {
                        remaining_team_sources: decode_team_rows(&team_rows, bot_id, &self.env)?,
                    }
                } else {
                    Plan::Revoke {
                        expected_edge_writes: expected,
                    }
                }
            }
        };

        let operation_id = uuid::Uuid::new_v4().to_string();
        match plan {
            Plan::Noop {
                remaining_team_sources,
            } => {
                // Idempotent repeat at the validated state: no writes, no
                // audit rows (审计只记变化), and the revoke-shaped result carries
                // the team sources read under the same validated snapshot.
                Ok(ManagerMutationResult {
                    changed: false,
                    remaining_team_sources,
                })
            }
            Plan::GrantInsert => {
                self.run_grant_transaction(
                    bot_id,
                    &actor,
                    &actor_from_id,
                    subject_user_id,
                    &subject_from_id,
                    &operation_id,
                    true,
                )
                .await?;
                Ok(ManagerMutationResult {
                    changed: true,
                    remaining_team_sources: Vec::new(),
                })
            }
            Plan::GrantRestore => {
                self.run_grant_transaction(
                    bot_id,
                    &actor,
                    &actor_from_id,
                    subject_user_id,
                    &subject_from_id,
                    &operation_id,
                    false,
                )
                .await?;
                Ok(ManagerMutationResult {
                    changed: true,
                    remaining_team_sources: Vec::new(),
                })
            }
            Plan::Revoke {
                expected_edge_writes,
            } => {
                let remaining = self
                    .run_revoke_transaction(
                        bot_id,
                        &actor,
                        &actor_from_id,
                        subject_user_id,
                        &subject_from_id,
                        &operation_id,
                        expected_edge_writes,
                    )
                    .await?;
                Ok(ManagerMutationResult {
                    changed: true,
                    remaining_team_sources: remaining,
                })
            }
        }
    }

    /// The grant write transaction: lock the Bot row, change the single
    /// direct/manual slot (fresh INSERT — or RESTORE of the revoked row under
    /// the same id — never INSERT IGNORE), audit the one changed edge. The
    /// audit write failing rolls the business write back with it.
    #[allow(clippy::too_many_arguments)]
    async fn run_grant_transaction(
        &self,
        bot_id: &str,
        actor: &AuditActor,
        actor_from_id: &str,
        subject_user_id: &str,
        subject_from_id: &str,
        operation_id: &str,
        fresh_insert: bool,
    ) -> Result<(), MutateAttempt> {
        let changing = if fresh_insert {
            self.grant_insert_statement(bot_id, subject_from_id, actor_from_id)
        } else {
            self.grant_restore_statement(bot_id, subject_from_id, actor_from_id)
        };
        let audit = manager_change_audit_statement(
            &self.flavor,
            "grant",
            &format!(
                "env = ? AND {} = ? AND {} = ? AND status = 'approved' \
                 AND grant_kind = 'manager' \
                 AND management_source_kind = 'direct' AND management_source_id = 'manual'",
                super::transfer_query::binary_identity(&self.flavor, "to_id"),
                super::transfer_query::binary_identity(&self.flavor, "from_id"),
            ),
            &self.env,
            bot_id,
            subject_user_id,
            subject_from_id,
            actor,
            actor_from_id,
            operation_id,
        );
        let results = self
            .db
            .transaction(vec![
                DbTransactionStep::Query(self.bot_lock_statement(bot_id)),
                DbTransactionStep::ExecuteChecked {
                    statement: changing,
                    expected_affected_rows: 1,
                },
                DbTransactionStep::ExecuteChecked {
                    statement: audit,
                    expected_affected_rows: 1,
                },
            ])
            .await
            .map_err(|err| self.map_write_error(err))?;
        // Defensive: the audit step must have recorded exactly the one edge.
        if let Some(DbTransactionStepResult::Executed(audit_result)) = results.get(2) {
            if audit_result.affected_rows != 1 {
                return Err(MutateAttempt::Service(ServiceError::InternalError(format!(
                    "authority manager grant audit wrote {} rows (expected 1)",
                    audit_result.affected_rows
                ))));
            }
        }
        Ok(())
    }

    /// The revoke write transaction: lock the Bot row, audit the
    /// actually-changed edges (selected BEFORE the update through the same
    /// predicate), bulk-revoke them with the brief's revoke predicate
    /// (team sources never match), and read the remaining team sources from
    /// the same transaction's state.
    #[allow(clippy::too_many_arguments)]
    async fn run_revoke_transaction(
        &self,
        bot_id: &str,
        actor: &AuditActor,
        actor_from_id: &str,
        subject_user_id: &str,
        subject_from_id: &str,
        operation_id: &str,
        expected_edge_writes: u64,
    ) -> Result<Vec<String>, MutateAttempt> {
        let row_conditions = format!(
            "env = ? AND {} = ? AND {} = ? AND status = 'approved' \
             AND grant_kind = 'manager' \
             AND management_source_kind IN ('direct', 'ownership_transfer')",
            super::transfer_query::binary_identity(&self.flavor, "to_id"),
            super::transfer_query::binary_identity(&self.flavor, "from_id"),
        );
        let audit = manager_change_audit_statement(
            &self.flavor,
            "revoke",
            &row_conditions,
            &self.env,
            bot_id,
            subject_user_id,
            subject_from_id,
            actor,
            actor_from_id,
            operation_id,
        );
        // The brief's revoke predicate (only direct/ownership_transfer match;
        // team/* rows stay) extended with the SAME in-transaction mutation
        // guards the audit used, so the changing write can never touch a
        // state the validated plan no longer describes.
        let mut revoke_params = vec![
            DbValue::from(self.env.as_str()),
            DbValue::from(bot_id),
            DbValue::from(subject_from_id),
        ];
        revoke_params.extend(super::audit::mutation_guard_params(
            &self.env,
            bot_id,
            actor_from_id,
            subject_from_id,
        ));
        let revoke_update = DbStatement::with_params(
            &format!(
                "UPDATE edge_grants SET status = 'revoked', gmt_modified = {} \
                 WHERE env = ? AND {} = ? AND {} = ? AND status = 'approved' \
                   AND grant_kind = 'manager' \
                   AND management_source_kind IN ('direct', 'ownership_transfer') \
                   AND {}",
                self.flavor.now(),
                super::transfer_query::binary_identity(&self.flavor, "to_id"),
                super::transfer_query::binary_identity(&self.flavor, "from_id"),
                super::audit::mutation_guards(&self.flavor),
            ),
            revoke_params,
        );
        let results = self
            .db
            .transaction(vec![
                DbTransactionStep::Query(self.bot_lock_statement(bot_id)),
                // Audit FIRST, selecting the rows about to change (brief:
                // 批量 INSERT SELECT 审计,为先选取后变更).
                DbTransactionStep::ExecuteChecked {
                    statement: audit,
                    expected_affected_rows: expected_edge_writes,
                },
                DbTransactionStep::ExecuteChecked {
                    statement: revoke_update,
                    expected_affected_rows: expected_edge_writes,
                },
                // Remaining team sources from the SAME transaction's state.
                DbTransactionStep::Query(self.subject_team_sources_statement(
                    bot_id,
                    subject_from_id,
                )),
            ])
            .await
            .map_err(|err| self.map_write_error(err))?;
        let team_rows = match results.get(3) {
            Some(DbTransactionStepResult::Rows(rows)) => rows.clone(),
            _ => {
                return Err(MutateAttempt::Service(ServiceError::InternalError(
                    "authority manager revoke: expected the team-source query".to_string(),
                )))
            }
        };
        decode_team_rows(&team_rows, bot_id, &self.env).map_err(MutateAttempt::Service)
    }

    fn map_write_error(&self, err: DbError) -> MutateAttempt {
        match err {
            // The validated snapshot drifted (concurrent mutation won the
            // lock first): roll back and re-validate the new state.
            DbError::ConditionFailed { expected, actual } => MutateAttempt::Drift(DbError::ConditionFailed { expected, actual }),
            other => MutateAttempt::Service(self.db_error("authority_mutate_write", other)),
        }
    }

    fn db_error(&self, operation: &'static str, err: DbError) -> ServiceError {
        service_db_error(operation, err)
    }

    // ------------------------------------------------------------------
    // Statements
    // ------------------------------------------------------------------

    /// Lock read of the precise Bot row: the write transaction's first step.
    /// SQLite reserves the write lock (IMMEDIATE) for any transaction that
    /// contains writes starting at this step; MySQL takes the row lock with
    /// `FOR UPDATE`.
    pub(super) fn bot_lock_statement(&self, bot_id: &str) -> DbStatement {
        let suffix = match self.flavor {
            DbSqlFlavor::Sqlite => "",
            DbSqlFlavor::Mysql => " FOR UPDATE",
        };
        DbStatement::with_params(
            &format!(
                "SELECT bot_uuid, env, ownership_version FROM bcs_bots \
                 WHERE bot_uuid = ? AND env = ?{suffix}"
            ),
            vec![
                DbValue::from(bot_id),
                DbValue::from(self.env.as_str()),
            ],
        )
    }

    /// One-row validated aggregate of the mutate decision inputs. Zero rows
    /// means the bot does not exist in this env (or is soft-deleted).
    fn validation_aggregate_statement(
        &self,
        bot_id: &str,
        actor_from_id: &str,
        subject_from_id: &str,
    ) -> DbStatement {
        let env = self.env.as_str();
        let to_b = super::transfer_query::binary_identity(&self.flavor, "e.to_id");
        let from_b = super::transfer_query::binary_identity(&self.flavor, "e.from_id");
        DbStatement::with_params(
            &format!(
                "SELECT \
                   b.ownership_version AS ownership_version, \
                   (SELECT COUNT(*) FROM edge_grants e \
                      WHERE e.env = ? AND {to_b} = ? AND e.grant_kind = 'owner' \
                        AND e.status = 'approved') AS owner_edge_count, \
                   (SELECT COUNT(*) FROM edge_grants e \
                      WHERE e.env = ? AND {to_b} = ? AND {from_b} = ? \
                        AND e.grant_kind IN ('owner', 'manager') \
                        AND e.status = 'approved') AS actor_role_count, \
                   (SELECT COUNT(*) FROM edge_grants e \
                      WHERE e.env = ? AND {to_b} = ? AND {from_b} = ? \
                        AND e.grant_kind = 'owner' AND e.status = 'approved') \
                     AS target_is_owner_count, \
                   (SELECT COUNT(*) FROM bcs_bots h \
                      WHERE h.bot_uuid = ? AND h.env = ? AND h.actor_kind = 'human' \
                        AND COALESCE(h.is_deleted, 0) = 0) AS target_live_count, \
                   (SELECT COUNT(*) FROM edge_grants e \
                      WHERE e.env = ? AND {to_b} = ? AND {from_b} = ? \
                        AND e.grant_kind = 'manager' \
                        AND e.management_source_kind = 'direct' \
                        AND e.management_source_id = 'manual' \
                        AND e.status = 'approved') AS direct_approved_count, \
                   (SELECT e.id FROM edge_grants e \
                      WHERE e.env = ? AND {to_b} = ? AND {from_b} = ? \
                        AND e.grant_kind = 'manager' \
                        AND e.management_source_kind = 'direct' \
                        AND e.management_source_id = 'manual' \
                        AND e.status = 'revoked') AS direct_revoked_id, \
                   (SELECT COUNT(*) FROM edge_grants e \
                      WHERE e.env = ? AND {to_b} = ? AND {from_b} = ? \
                        AND e.grant_kind = 'manager' AND e.status = 'approved' \
                        AND e.management_source_kind IN ('direct', 'ownership_transfer')) \
                     AS nonteam_approved_count \
                 FROM bcs_bots b \
                 WHERE b.bot_uuid = ? AND b.env = ? AND COALESCE(b.is_deleted, 0) = 0"
            ),
            vec![
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(actor_from_id),
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(subject_from_id),
                DbValue::from(subject_from_id),
                DbValue::from(env),
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(subject_from_id),
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(subject_from_id),
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(subject_from_id),
                DbValue::from(bot_id),
                DbValue::from(env),
            ],
        )
    }

    /// The subject's live team sources (approved `team/<id>` edges), ordered
    /// `user_id`-stably by source id.
    fn subject_team_sources_statement(&self, bot_id: &str, subject_from_id: &str) -> DbStatement {
        DbStatement::with_params(
            &format!(
                "SELECT e.management_source_id AS team_id FROM edge_grants e \
                 WHERE e.env = ? AND {} = ? AND {} = ? AND e.status = 'approved' \
                   AND e.grant_kind = 'manager' AND e.management_source_kind = 'team' \
                 ORDER BY e.management_source_id",
                super::transfer_query::binary_identity(&self.flavor, "e.to_id"),
                super::transfer_query::binary_identity(&self.flavor, "e.from_id"),
            ),
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
                DbValue::from(subject_from_id),
            ],
        )
    }

    /// Fresh single-slot insert of the direct/manual manager edge, guarded
    /// by the whole mutation contract inside the write transaction: the
    /// slot must be truly empty (no approved and no revoked row — the
    /// unique key allows exactly one row in ANY status) AND the shared
    /// in-transaction mutation guards must hold (actor still authorized,
    /// subject is a live human, subject is not the owner; see
    /// `audit::mutation_guards`).
    fn grant_insert_statement(
        &self,
        bot_id: &str,
        subject_from_id: &str,
        actor_from_id: &str,
    ) -> DbStatement {
        let env = self.env.as_str();
        let from_dual = match self.flavor {
            DbSqlFlavor::Sqlite => "",
            DbSqlFlavor::Mysql => " FROM DUAL",
        };
        let mut params = vec![
            DbValue::from(env),
            DbValue::from(subject_from_id),
            DbValue::from(bot_id),
            DbValue::from(env),
            DbValue::from(bot_id),
            DbValue::from(subject_from_id),
            DbValue::from(env),
            DbValue::from(bot_id),
            DbValue::from(subject_from_id),
        ];
        params.extend(super::audit::mutation_guard_params(
            env,
            bot_id,
            actor_from_id,
            subject_from_id,
        ));
        DbStatement::with_params(
            &format!(
                "INSERT INTO edge_grants \
                   (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
                    originator_policy_type, originator_policy_data, \
                    management_source_kind, management_source_id) \
                 SELECT ?, ?, ?, 'manager', 0, NULL, 'approved', 'same_as_from', NULL, \
                    'direct', 'manual'{from_dual} \
                 WHERE NOT EXISTS (SELECT 1 FROM edge_grants t \
                       WHERE t.env = ? AND {} = ? AND {} = ? \
                         AND t.grant_kind = 'manager' \
                         AND t.management_source_kind = 'direct' \
                         AND t.management_source_id = 'manual' AND t.status = 'approved') \
                   AND NOT EXISTS (SELECT 1 FROM edge_grants t \
                       WHERE t.env = ? AND {} = ? AND {} = ? \
                         AND t.grant_kind = 'manager' \
                         AND t.management_source_kind = 'direct' \
                         AND t.management_source_id = 'manual' AND t.status = 'revoked') \
                   AND {}",
                super::transfer_query::binary_identity(&self.flavor, "t.to_id"),
                super::transfer_query::binary_identity(&self.flavor, "t.from_id"),
                super::transfer_query::binary_identity(&self.flavor, "t.to_id"),
                super::transfer_query::binary_identity(&self.flavor, "t.from_id"),
                super::audit::mutation_guards(&self.flavor),
            ),
            params,
        )
    }

    /// RESTORE of the previously revoked direct/manual row: exactly the one
    /// revoked source row returns to `approved` under the SAME id — never a
    /// second slot, never INSERT IGNORE. The shared in-transaction mutation
    /// guards hold alongside the row conditions (see
    /// `audit::mutation_guards`).
    fn grant_restore_statement(
        &self,
        bot_id: &str,
        subject_from_id: &str,
        actor_from_id: &str,
    ) -> DbStatement {
        let mut params = vec![
            DbValue::from(self.env.as_str()),
            DbValue::from(bot_id),
            DbValue::from(subject_from_id),
        ];
        params.extend(super::audit::mutation_guard_params(
            &self.env,
            bot_id,
            actor_from_id,
            subject_from_id,
        ));
        DbStatement::with_params(
            &format!(
                "UPDATE edge_grants SET status = 'approved', gmt_modified = {} \
                 WHERE env = ? AND {} = ? AND {} = ? AND grant_kind = 'manager' \
                   AND management_source_kind = 'direct' AND management_source_id = 'manual' \
                   AND status = 'revoked' \
                   AND {}",
                self.flavor.now(),
                super::transfer_query::binary_identity(&self.flavor, "to_id"),
                super::transfer_query::binary_identity(&self.flavor, "from_id"),
                super::audit::mutation_guards(&self.flavor),
            ),
            params,
        )
    }

    // ------------------------------------------------------------------
    // list_managers
    // ------------------------------------------------------------------

    pub(super) async fn list_managers_inner(
        &self,
        bot_id: &str,
        offset: u64,
        limit: u64,
    ) -> ServiceResult<BotManagerList> {
        // Bot validation first, reusing the ownership codec: live row +
        // version, then the strict unique approved owner slot.
        let bot_rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT b.ownership_version AS ownership_version FROM bcs_bots b \
                 WHERE b.bot_uuid = ? AND b.env = ? AND COALESCE(b.is_deleted, 0) = 0",
                vec![
                    DbValue::from(bot_id),
                    DbValue::from(self.env.as_str()),
                ],
            ))
            .await
            .map_err(|err| self.db_error("authority_list_managers", err))?;
        let Some(bot_row) = bot_rows.into_iter().next() else {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        };
        let ownership_version = row_u64(&bot_row, "ownership_version")
            .map_err(|err| err)?;
        if ownership_version == bcs_domain::UNINITIALIZED_OWNERSHIP_VERSION {
            return Err(ServiceError::Authority(AuthorityError::OwnershipNotInitialized {
                bot_id: bot_id.to_string(),
                env: self.env.clone(),
            }));
        }
        let owner_rows = self
            .db
            .query(DbStatement::with_params(
                &format!(
                    "SELECT {ROLE_ROW_COLUMNS} FROM edge_grants \
                     WHERE env = ? AND {} = ? AND grant_kind = 'owner' \
                       AND status = 'approved' ORDER BY id",
                    super::transfer_query::binary_identity(&self.flavor, "to_id"),
                ),
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(bot_id),
                ],
            ))
            .await
            .map_err(|err| self.db_error("authority_list_managers", err))?;
        match owner_rows.len() {
            1 => {}
            count => {
                return Err(self.corrupt(
                    bot_id,
                    format!("initialized bot has {count} approved owner edges"),
                ))
            }
        }
        let ownership = ownership_state_from_owner_row(
            &owner_rows[0],
            bot_id,
            &self.env,
            ownership_version,
        )?;

        // Manager page: ONE user-deduplicated GROUP BY page, user_id ASC
        // (from_id carries the shared `human_` prefix, so from_id ordering IS
        // user_id ordering), owner excluded from the explicit-manager page
        // even if a stray manager edge exists.
        let page_users = self
            .db
            .query(DbStatement::with_params(
                &format!(
                    "SELECT e.from_id AS from_id FROM edge_grants e \
                     WHERE e.env = ? AND {} = ? AND e.grant_kind = 'manager' \
                       AND e.status = 'approved' AND {} <> ? \
                     GROUP BY e.from_id ORDER BY e.from_id LIMIT ? OFFSET ?",
                    super::transfer_query::binary_identity(&self.flavor, "e.to_id"),
                    super::transfer_query::binary_identity(&self.flavor, "e.from_id"),
                ),
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(bot_id),
                    DbValue::from(human_actor_id(&ownership.owner_user_id)),
                    DbValue::from(limit.min(i64::MAX as u64) as i64),
                    DbValue::from(offset.min(i64::MAX as u64) as i64),
                ],
            ))
            .await
            .map_err(|err| self.db_error("authority_list_managers", err))?;
        let mut managers = Vec::<BotManagerSummary>::with_capacity(page_users.len());
        for user_chunk in page_users.chunks(MANAGER_LIST_PAGE_CHUNK) {
            let mut conjunction = String::new();
            let mut params = vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
            ];
            for (index, row) in user_chunk.iter().enumerate() {
                if index > 0 {
                    conjunction.push_str(" OR ");
                }
                conjunction.push_str(&format!(
                    "{} = ?",
                    super::transfer_query::binary_identity(&self.flavor, "from_id")
                ));
                params.push(DbValue::from(
                    row.get_string("from_id").ok().flatten().unwrap_or_default(),
                ));
            }
            let source_rows = self
                .db
                .query(DbStatement::with_params(
                    &format!(
                        "SELECT {ROLE_ROW_COLUMNS} FROM edge_grants \
                         WHERE env = ? AND {} = ? AND grant_kind = 'manager' \
                           AND status = 'approved' AND ({conjunction}) \
                         ORDER BY from_id, id",
                        super::transfer_query::binary_identity(&self.flavor, "to_id"),
                    ),
                    params,
                ))
                .await
                .map_err(|err| self.db_error("authority_list_managers", err))?;
            let mut decoded: Vec<(String, ManagementSource)> = Vec::with_capacity(source_rows.len());
            for row in &source_rows {
                let (user_id, source) =
                    self.manager_source_from_row(row, bot_id)?;
                decoded.push((user_id, source));
            }
            // Group the chunk's users positionally (page order preserved).
            for user_row in user_chunk {
                let from_id = user_row
                    .get_string("from_id")
                    .ok()
                    .flatten()
                    .unwrap_or_default();
                let user_id = user_id_from_actor(&from_id)
                    .ok_or_else(|| {
                        self.corrupt(
                            bot_id,
                            format!("manager edge from non-human actor id '{from_id}'"),
                        )
                    })?
                    .to_string();
                let mut sources: Vec<ManagementSource> = decoded
                    .iter()
                    .filter(|(candidate, _)| candidate == &user_id)
                    .map(|(_, source)| source.clone())
                    .collect();
                sources.sort_by(canonical_source_order);
                managers.push(BotManagerSummary { user_id, sources });
            }
        }
        Ok(BotManagerList {
            owner_user_id: ownership.owner_user_id,
            managers,
        })
    }

    /// Strict decode of one manager row into `(user_id, source)`; owner
    /// rows never reach this path (they are a separate question).
    fn manager_source_from_row(
        &self,
        row: &DbRow,
        bot_id: &str,
    ) -> ServiceResult<(String, ManagementSource)> {
        relation_from_row(row, bot_id, &self.env)?;
        let from_id = row
            .get_string("from_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        let user_id = user_id_from_actor(&from_id)
            .ok_or_else(|| {
                self.corrupt(
                    bot_id,
                    format!("manager edge from non-human actor id '{from_id}'"),
                )
            })?
            .to_string();
        let source_kind = row
            .get_string("management_source_kind")
            .ok()
            .flatten()
            .unwrap_or_default();
        let source_id = row
            .get_string("management_source_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        match bcs_domain::decode_role_source(&source_kind, &source_id) {
            Ok(bcs_domain::DecodedRoleSource::Manager(source)) => Ok((user_id, source)),
            _ => Err(self.corrupt(
                bot_id,
                format!(
                    "manager row source does not decode as a manager source \
                     ('{source_kind}', '{source_id}')"
                ),
            )),
        }
    }
}

/// Canonical source order for one user's manager summary: by the stored
/// (kind, id) pair — `direct` < `ownership_transfer` < `team` — stable for
/// every store.
fn canonical_source_order(left: &ManagementSource, right: &ManagementSource) -> std::cmp::Ordering {
    let (lk, li) = left.storage_parts();
    let (rk, ri) = right.storage_parts();
    (lk, li).cmp(&(rk, ri))
}

/// Counting column read for the mutation aggregates (every column here is
/// NULL-safe: counts are `COUNT(*)` and ids come back NULL when absent).
fn row_u64(row: &DbRow, column: &'static str) -> ServiceResult<u64> {
    row.get_i64(column)
        .ok()
        .flatten()
        .map(|value| value.max(0) as u64)
        .ok_or_else(|| {
            ServiceError::InternalError(format!(
                "authority mutation aggregate column '{column}' missing or NULL"
            ))
        })
}

/// Strictly decode the subject's team-source ids; an undecodable team row is
/// corruption, never a silently dropped source.
fn decode_team_rows(rows: &[DbRow], bot_id: &str, env: &str) -> ServiceResult<Vec<String>> {
    let mut team_sources = Vec::with_capacity(rows.len());
    for row in rows {
        let team_id = row
            .get_string("team_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        match bcs_domain::decode_role_source("team", &team_id) {
            Ok(bcs_domain::DecodedRoleSource::Manager(ManagementSource::Team(id))) => {
                team_sources.push(id)
            }
            _ => {
                return Err(ServiceError::Authority(AuthorityError::CorruptAuthority {
                    bot_id: bot_id.to_string(),
                    env: env.to_string(),
                    detail: format!("undecodable team manager source id '{team_id}'"),
                }))
            }
        }
    }
    team_sources.sort();
    Ok(team_sources)
}

enum MutateAttempt {
    /// The optimistic window drifted; the caller re-validates.
    Drift(DbError),
    /// A business or infrastructure branch — surface it unchanged.
    Service(ServiceError),
}

impl From<ServiceError> for MutateAttempt {
    fn from(err: ServiceError) -> Self {
        MutateAttempt::Service(err)
    }
}