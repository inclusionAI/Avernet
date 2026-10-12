//! Statement builders of the team-sync lane (plan Task 7): ONE
//! authoritative SQL grammar shared by SQLite and MySQL through
//! `DbSqlFlavor`. Kept beside the Task 4 audit primitives; the protocol
//! engine lives in `team_sync.rs`.
//!
//! Parameter budgets: IN-list statements bind at most `100 + 8`
//! parameters (the team audit statement's leading literals are the
//! widest); fresh-INSERT statements bind at most `60 * 13 = 780`
//! guarded parameters — both far below the SQLite bind ceiling. Every
//! changing statement shares the lane's in-lock subject guards so the
//! `ExecuteChecked` pins own the drift revalidation.

use bcs_db_api::{DbSqlFlavor, DbStatement, DbValue};

use bcs_service_api::port::repo::bot_authority::human_actor_id;
use bcs_service_api::types::team_manager_sync::TeamManagerOperation;
use bcs_service_api::types::AuditActor;

use super::audit::{audit_id_expr, team_manager_change_columns};
use super::team_sync::{PreparedSync, TEAM_SYNC_INSERT_CHUNK};
use super::transfer_query::binary_identity;

impl super::reads::DbBotAuthorityStore {

    /// One-row validated aggregate of the sync decision inputs: Bot
    /// liveness + `ownership_version` + the approved owner-slot count.
    pub(super) fn sync_read_aggregate_statement(&self, bot_id: &str) -> DbStatement {
        let env = self.env.as_str();
        DbStatement::with_params(
            &format!(
                "SELECT b.ownership_version AS ownership_version, \
                   (SELECT COUNT(*) FROM edge_grants e \
                      WHERE e.env = ? AND {} = ? AND e.grant_kind = 'owner' \
                        AND e.status = 'approved') AS owner_edge_count \
                 FROM bcs_bots b \
                 WHERE b.bot_uuid = ? AND b.env = ? AND COALESCE(b.is_deleted, 0) = 0",
                binary_identity(&self.flavor, "e.to_id"),
            ),
            vec![
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(bot_id),
                DbValue::from(env),
            ],
        )
    }

    /// The same-key durable idempotency slot
    /// (`uk_manager_sync_scope`).
    pub(super) fn sync_receipt_statement(&self, prepared: &PreparedSync) -> DbStatement {
        DbStatement::with_params(
            "SELECT payload, result FROM bot_manager_sync_operations \
             WHERE env = ? AND service_id = ? AND bot_id = ? AND team_id = ? \
               AND idempotency_key = ?",
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(prepared.service.service_id.clone()),
                DbValue::from(prepared.bot_id.clone()),
                DbValue::from(prepared.team_id.clone()),
                DbValue::from(prepared.idempotency_key.clone()),
            ],
        )
    }

    /// The Bot's exclusive approved owner edge.
    pub(super) fn sync_owner_slot_statement(&self, bot_id: &str) -> DbStatement {
        DbStatement::with_params(
            &format!(
                "SELECT from_id FROM edge_grants \
                 WHERE env = ? AND {} = ? AND grant_kind = 'owner' AND status = 'approved'",
                binary_identity(&self.flavor, "to_id"),
            ),
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
            ],
        )
    }

    /// The current approved members of ONE team's manager source.
    pub(super) fn sync_current_members_statement(&self, bot_id: &str, team_id: &str) -> DbStatement {
        DbStatement::with_params(
            &format!(
                "SELECT from_id FROM edge_grants \
                 WHERE env = ? AND {} = ? AND grant_kind = 'manager' \
                   AND management_source_kind = 'team' AND management_source_id = ? \
                   AND status = 'approved' \
                 ORDER BY from_id",
                binary_identity(&self.flavor, "to_id"),
            ),
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
                DbValue::from(team_id),
            ],
        )
    }

    /// Liveness validation of one snapshot chunk: the returned `bot_uuid`
    /// rows must cover the chunk exactly (live, same-env human actors).
    pub(super) fn sync_live_humans_chunk_statement(&self, chunk: &[String]) -> DbStatement {
        let (conjunction, params) = in_list_params(
            vec![DbValue::from(self.env.as_str())],
            chunk,
        );
        DbStatement::with_params(
            &format!(
                "SELECT bot_uuid FROM bcs_bots \
                 WHERE env = ? AND actor_kind = 'human' AND COALESCE(is_deleted, 0) = 0 \
                   AND bot_uuid IN ({conjunction})"
            ),
            params,
        )
    }

    /// Which snapshot-chunk users already hold a REVOKED row in the
    /// reconciled team's slot (classification: restore vs fresh insert).
    pub(super) fn sync_revoked_members_statement(
        &self,
        bot_id: &str,
        team_id: &str,
        chunk: &[String],
    ) -> DbStatement {
        let (conjunction, params) = in_list_params(
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
                DbValue::from(team_id),
            ],
            chunk,
        );
        DbStatement::with_params(
            &format!(
                "SELECT from_id FROM edge_grants \
                 WHERE env = ? AND {to_b} = ? AND grant_kind = 'manager' \
                   AND management_source_kind = 'team' AND management_source_id = ? \
                   AND status = 'revoked' AND {from_b} IN ({conjunction}) \
                 ORDER BY from_id",
                to_b = binary_identity(&self.flavor, "to_id"),
                from_b = binary_identity(&self.flavor, "from_id"),
            ),
            params,
        )
    }

    /// Shared per-row subject guard tail of the team lane: every affected
    /// row's subject must STILL be a live same-env Human and must NOT be
    /// the current owner. This is the in-transaction re-proof of the
    /// validated snapshot — drift fails the pin and rolls the whole
    /// attempt back. Binding order: `(env)`, then `(env, bot_id)`.
    /// Identity columns compare binary (see `binary_identity`).
    pub(super) fn sync_subject_guard_sql(&self) -> String {
        format!(
            " AND {} IN (SELECT th.bot_uuid FROM bcs_bots th \
                 WHERE th.env = ? AND th.actor_kind = 'human' \
                   AND COALESCE(th.is_deleted, 0) = 0) \
           AND {} NOT IN (SELECT {} FROM edge_grants so \
                 WHERE so.env = ? AND {} = ? AND so.grant_kind = 'owner' \
                   AND so.status = 'approved')",
            binary_identity(&self.flavor, "from_id"),
            binary_identity(&self.flavor, "from_id"),
            binary_identity(&self.flavor, "so.from_id"),
            binary_identity(&self.flavor, "so.to_id"),
        )
    }

    /// Bulk revoke of one lane chunk: only approved `team/<id>` rows of
    /// the affected subjects flip, pinned to the plan's exact count.
    /// Revokes deliberately carry NO subject-liveness / not-owner guards:
    /// REMOVING a member who has since died (or who became the owner
    /// through a transfer) is a cleanup, not a drift violation — grants
    /// and restores keep their subject guards, and the row-condition pins
    /// still own the optimistic window (any vanished row fails the pin
    /// and rolls the whole attempt back).
    pub(super) fn sync_lane_revoke_statement(
        &self,
        bot_id: &str,
        team_id: &str,
        chunk: &[String],
    ) -> DbStatement {
        let (conjunction, params) = in_list_params(
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
                DbValue::from(team_id),
            ],
            chunk,
        );
        DbStatement::with_params(
            &format!(
                "UPDATE edge_grants SET status = 'revoked', gmt_modified = {} \
                 WHERE env = ? AND {to_b} = ? AND grant_kind = 'manager' \
                   AND management_source_kind = 'team' AND management_source_id = ? \
                   AND status = 'approved' AND {from_b} IN ({conjunction})",
                self.flavor.now(),
                to_b = binary_identity(&self.flavor, "to_id"),
                from_b = binary_identity(&self.flavor, "from_id"),
            ),
            params,
        )
    }

    /// RESTORE of previously revoked team-slot rows: the SAME row ids
    /// return to approved — never a second slot, never INSERT IGNORE.
    pub(super) fn sync_lane_restore_statement(
        &self,
        bot_id: &str,
        team_id: &str,
        chunk: &[String],
    ) -> DbStatement {
        let (conjunction, params) = in_list_params(
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
                DbValue::from(team_id),
            ],
            chunk,
        );
        let mut params = params;
        params.extend([
            DbValue::from(self.env.as_str()),
            DbValue::from(self.env.as_str()),
            DbValue::from(bot_id),
        ]);
        DbStatement::with_params(
            &format!(
                "UPDATE edge_grants SET status = 'approved', gmt_modified = {} \
                 WHERE env = ? AND {} = ? AND grant_kind = 'manager' \
                   AND management_source_kind = 'team' AND management_source_id = ? \
                   AND status = 'revoked' AND {} IN ({conjunction}){}",
                self.flavor.now(),
                binary_identity(&self.flavor, "to_id"),
                binary_identity(&self.flavor, "from_id"),
                self.sync_subject_guard_sql(),
            ),
            params,
        )
    }

    /// Fresh multi-INSERT of a fresh chunk: each union arm is an
    /// independently guarded single-row INSERT SELECT (unique-slot
    /// NOT EXISTS + live-subject + not-current-owner), so a drifted
    /// subject or slot yields fewer rows and the pin rolls the whole
    /// transaction back.
    pub(super) fn sync_lane_fresh_insert_statement(
        &self,
        bot_id: &str,
        team_id: &str,
        chunk: &[String],
    ) -> DbStatement {
        let from_dual = match self.flavor {
            DbSqlFlavor::Sqlite => "",
            DbSqlFlavor::Mysql => " FROM DUAL",
        };
        let mut sql = String::from(
            "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
             status, originator_policy_type, originator_policy_data, management_source_kind, \
             management_source_id) ",
        );
        let mut params: Vec<DbValue> = Vec::with_capacity(TEAM_SYNC_INSERT_CHUNK * 13);
        for (arm, user_id) in chunk.iter().enumerate() {
            let from_id = human_actor_id(user_id);
            let env = self.env.as_str();
            params.extend([
                // SELECT list: env, from_id, to_id, source team
                DbValue::from(env),
                DbValue::from(from_id.clone()),
                DbValue::from(bot_id),
                DbValue::from(team_id),
                // unique slot NOT EXISTS (any status)
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(from_id.clone()),
                DbValue::from(team_id),
                // subject is a live same-env human
                DbValue::from(from_id.clone()),
                DbValue::from(env),
                // subject is not the current owner
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(from_id.clone()),
            ]);
            if arm > 0 {
                sql.push_str("\nUNION ALL ");
            }
            sql.push_str(&format!(
                "SELECT ?, ?, ?, 'manager', 0, NULL, 'approved', 'same_as_from', NULL, \
                 'team', ?{from_dual} \
                 WHERE NOT EXISTS (SELECT 1 FROM edge_grants t \
                       WHERE t.env = ? AND {t_to} = ? AND {t_from} = ? \
                         AND t.grant_kind = 'manager' \
                         AND t.management_source_kind = 'team' \
                         AND t.management_source_id = ?) \
                   AND EXISTS (SELECT 1 FROM bcs_bots th \
                       WHERE th.bot_uuid = ? AND th.env = ? AND th.actor_kind = 'human' \
                         AND COALESCE(th.is_deleted, 0) = 0) \
                   AND NOT EXISTS (SELECT 1 FROM edge_grants so \
                       WHERE so.env = ? AND {so_to} = ? AND {so_from} = ? \
                         AND so.grant_kind = 'owner' AND so.status = 'approved')",
                t_to = binary_identity(&self.flavor, "t.to_id"),
                t_from = binary_identity(&self.flavor, "t.from_id"),
                so_to = binary_identity(&self.flavor, "so.to_id"),
                so_from = binary_identity(&self.flavor, "so.from_id"),
            ));
        }
        DbStatement::with_params(sql, params)
    }

    /// Batched audit INSERT SELECT over one lane's changed chunk: the
    /// row conditions are the same POST-state predicates the changing
    /// statements just produced, `subject_user_id` is decoded in SQL from
    /// the shared `human_<uid>` actor shape, and the columns/audit-id
    /// rule are the Task 4 shared primitives. TEAM rows additionally carry
    /// the request's `idempotency_key` (the contract column that is NULL
    /// for non-team operations): the SELECT binds it AFTER `operation_id`.
    pub(super) fn sync_lane_audit_statement(
        &self,
        action: &'static str,
        bot_id: &str,
        team_id: &str,
        chunk: &[String],
        actor: &AuditActor,
        operation_id: &str,
        idempotency_key: &str,
    ) -> DbStatement {
        let subject_expr = match self.flavor {
            DbSqlFlavor::Sqlite => "substr(from_id, 7)",
            DbSqlFlavor::Mysql => "SUBSTRING(from_id, 7)",
        };
        let post_status = if action == "grant" {
            "approved"
        } else {
            "revoked"
        };
        let (conjunction, params) = in_list_params(
            vec![
                // SELECT-list literals after the audit_id expression:
                // actor_kind, actor_id, operation_id, idempotency_key;
                // then the WHERE literals: env, bot, team.
                DbValue::from(operation_id),
                DbValue::from(actor.kind_str()),
                DbValue::from(actor.actor_id()),
                DbValue::from(operation_id),
                DbValue::from(idempotency_key),
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
                DbValue::from(team_id),
            ],
            chunk,
        );
        DbStatement::with_params(
            &format!(
                "INSERT INTO bot_manager_changes ({team_columns}) \
                 SELECT {audit_id}, env, to_id, {subject}, id, management_source_kind, \
                   management_source_id, '{action}', ?, ?, ?, ? \
                 FROM edge_grants \
                 WHERE env = ? AND {to_b} = ? AND grant_kind = 'manager' \
                   AND management_source_kind = 'team' AND management_source_id = ? \
                   AND status = '{post_status}' AND {from_b} IN ({conjunction})",
                team_columns = team_manager_change_columns(),
                audit_id = audit_id_expr(&self.flavor),
                subject = subject_expr,
                action = action,
                post_status = post_status,
                to_b = binary_identity(&self.flavor, "to_id"),
                from_b = binary_identity(&self.flavor, "from_id"),
            ),
            params,
        )
    }

    /// The team binding upsert: `active` (even for an EMPTY snapshot —
    /// the binding may represent an active team with no members) or
    /// `stopped` (the move's old team), carrying the operation id.
    pub(super) fn sync_binding_upsert_statement(
        &self,
        bot_id: &str,
        team_id: &str,
        operation_id: &str,
        activate: bool,
    ) -> DbStatement {
        let status = if activate { "active" } else { "stopped" };
        let conflict_clause = match self.flavor {
            DbSqlFlavor::Sqlite => format!(
                "ON CONFLICT (env, bot_id, team_id) DO UPDATE SET \
                 status = '{status}', last_operation_id = excluded.last_operation_id, \
                 gmt_modified = CURRENT_TIMESTAMP"
            ),
            DbSqlFlavor::Mysql =>
                "ON DUPLICATE KEY UPDATE status = VALUES(status), \
                 last_operation_id = VALUES(last_operation_id), gmt_modified = NOW()"
                    .to_string(),
        };
        DbStatement::with_params(
            &format!(
                "INSERT INTO bot_team_manager_sources \
                   (env, bot_id, team_id, status, last_operation_id) \
                 VALUES (?, ?, ?, '{status}', ?) {conflict_clause}"
            ),
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
                DbValue::from(team_id),
                DbValue::from(operation_id),
            ],
        )
    }

    /// The guarded durable receipt: ONE row per
    /// `(env, service, bot, team, key)`, committed with the changes.
    /// The `NOT EXISTS` re-check of the idempotency slot plus the
    /// live-initialized-Bot / unique-owner-slot guards are the
    /// in-transaction re-read under the Bot lock: any drift yields ZERO
    /// rows, the pin rolls every edge change/audit/binding in this
    /// transaction back, and the retry replays the racer's receipt or
    /// surfaces the conflicting payload.
    pub(super) fn sync_receipt_insert_statement(
        &self,
        prepared: &PreparedSync,
        operation_id: &str,
        result_text: &str,
    ) -> DbStatement {
        let from_dual = match self.flavor {
            DbSqlFlavor::Sqlite => "",
            DbSqlFlavor::Mysql => " FROM DUAL",
        };
        let new_team_id = match &prepared.operation {
            TeamManagerOperation::Sync => DbValue::Null,
            TeamManagerOperation::Move { new_team_id } => DbValue::from(new_team_id.clone()),
        };
        let env = self.env.as_str();
        DbStatement::with_params(
            &format!(
                "INSERT INTO bot_manager_sync_operations \
                   (env, service_id, bot_id, team_id, operation, new_team_id, payload, \
                    idempotency_key, operation_id, result) \
                 SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?{from_dual} \
                 WHERE NOT EXISTS (SELECT 1 FROM bot_manager_sync_operations r \
                       WHERE r.env = ? AND r.service_id = ? AND r.bot_id = ? \
                         AND r.team_id = ? AND r.idempotency_key = ?) \
                   AND EXISTS (SELECT 1 FROM bcs_bots tb \
                       WHERE tb.bot_uuid = ? AND tb.env = ? AND tb.ownership_version > 0 \
                         AND COALESCE(tb.is_deleted, 0) = 0) \
                   AND EXISTS (SELECT 1 FROM edge_grants os \
                       WHERE os.env = ? AND {} = ? AND os.grant_kind = 'owner' \
                         AND os.status = 'approved')",
                binary_identity(&self.flavor, "os.to_id"),
            ),
            vec![
                // SELECT list
                DbValue::from(env),
                DbValue::from(prepared.service.service_id.clone()),
                DbValue::from(prepared.bot_id.clone()),
                DbValue::from(prepared.team_id.clone()),
                DbValue::from(prepared.operation.storage_str()),
                new_team_id,
                DbValue::from(prepared.payload.clone()),
                DbValue::from(prepared.idempotency_key.clone()),
                DbValue::from(operation_id),
                DbValue::from(result_text),
                // idempotency-slot re-check under the lock
                DbValue::from(env),
                DbValue::from(prepared.service.service_id.clone()),
                DbValue::from(prepared.bot_id.clone()),
                DbValue::from(prepared.team_id.clone()),
                DbValue::from(prepared.idempotency_key.clone()),
                // live, initialized Bot
                DbValue::from(prepared.bot_id.clone()),
                DbValue::from(env),
                // unique approved owner slot
                DbValue::from(env),
                DbValue::from(prepared.bot_id.clone()),
            ],
        )
    }
}

/// Append one `?` per chunk member to an IN-list conjunction,
/// binding the shared `human_<uid>` actor shape.
fn in_list_params(mut params: Vec<DbValue>, chunk: &[String]) -> (String, Vec<DbValue>) {
    let mut conjunction = String::new();
    for user_id in chunk {
        if !conjunction.is_empty() {
            conjunction.push_str(", ");
        }
        conjunction.push('?');
        params.push(DbValue::from(human_actor_id(user_id)));
    }
    (conjunction, params)
}
