//! Controllable-Bot union read (plan Task 9, spec §7.2).
//!
//! `list_controllable` answers "which Bot rows does this Human currently
//! control, and by which relation". It is the owner ∪ manager union of
//! the PHYSICAL Bot rows, filtered through the strict Task 3 read
//! contract — corruption never degrades into a shorter successful list —
//! plus the caller's own Human self row as an explicit `Owner`-labeled
//! compatibility projection (spec §4.1: the label means self identity
//! only and cannot be expanded into authority over other Humans).
//!
//! Both twins return ONE row per Bot (owner wins when the User holds
//! both edges), keep the relation label from the SAME union read (no
//! per-Bot sub-queries; Provider hydration stays a batch operation on
//! the Core side), apply the unified `kind`/`name`/`status` filters and
//! order `created_at DESC, bot_id ASC`. Virtual reachability, `total`
//! and offset/limit pagination stay with the application (spec §7.2).

use bcs_db_api::{DbStatement, DbTransactionStep, DbTransactionStepResult};
use bcs_service_api::port::repo::BotControlPlaneRepoPort;
use bcs_service_api::port::repo::bot_authority::human_actor_id;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::{BotAccessRelation, UNINITIALIZED_OWNERSHIP_VERSION};
use bcs_service_api::{
    ActorKind, ActorStatus, BotControllableQuery, BotControlPlaneRecord, ControllableBotRecord,
    ServiceError, ServiceResult,
};

use super::{PersistentBotRepo, Value};
use crate::memory::memory_authority::corrupt;
use crate::memory::MemoryBotRepo;

/// Strict validity of one union member (the SQL branch carries the same
/// reasons as a column): the mapped typed error for each failure shape.
fn invalid_union_reason(
    bot_id: &str,
    env: &str,
    reason: &str,
) -> ServiceError {
    match reason {
        "uninitialized" => ServiceError::Authority(AuthorityError::OwnershipNotInitialized {
            bot_id: bot_id.to_string(),
            env: env.to_string(),
        }),
        other => corrupt(
            bot_id,
            env,
            match other {
                "dangling_edge" => "role edge targets a missing or soft-deleted Bot",
                "human_target" => "role edge targets a Human row other than the self row",
                "owner_slot" => "initialized bot does not carry exactly one approved owner edge",
                detail => detail,
            },
        ),
    }
}

// ---------------------------------------------------------------------------
// Memory twin
// ---------------------------------------------------------------------------

impl MemoryBotRepo {
    /// Compute the controllable union for `query` inside the shared
    /// lifecycle critical section (lock order: deleted -> bots ->
    /// authority, the deletion lane's documented family). Returns the
    /// validated (bot_id, relation) pairs plus whether the caller's own
    /// Human self row exists in this env.
    async fn controllable_union(
        &self,
        query: &BotControllableQuery,
    ) -> ServiceResult<(Vec<(String, BotAccessRelation)>, bool)> {
        let env = query.env.as_str();
        let from_id = human_actor_id(&query.user_id);
        let deleted = self.deleted_bot_ids.read().await;
        let bots = self.bots.read().await;
        let authority = self.authority.read().await;

        // 1. The User's own human self row (explicit compatibility
        //    projection, labeled owner; NEVER taken from a role edge —
        //    Humans carry no transferrable authority).
        let resolved_env =
            |bot_env: Option<&str>| bot_env.map(str::to_string).unwrap_or_else(super::resolve_env);
        let self_row_present = !deleted.contains(from_id.as_str())
            && bots
                .get(&from_id)
                .is_some_and(|self_row| resolved_env(self_row.env.as_deref()) == env);

        // 2. Physical union: every approved owner/manager edge of this
        //    User in this env, merged per Bot with owner priority. Each
        //    matching row decodes STRICTLY (fail-closed), and every
        //    involved Bot is validated exactly once — same strictness
        //    batch semantics as the Task 3 `roles_for` read.
        let mut candidates: Vec<(String, BotAccessRelation)> = Vec::new();
        for row in &authority.role_rows {
            if row.env != env
                || row.from_id != from_id
                || row.status != "approved"
                || (row.grant_kind != "owner" && row.grant_kind != "manager")
            {
                continue;
            }
            let relation = authority
                .relation_for(env, &from_id, &row.to_id)?
                .ok_or_else(|| {
                    corrupt(
                        &row.to_id,
                        env,
                        "authority row matched without a decodable relation",
                    )
                })?;
            match candidates
                .iter_mut()
                .find(|(bot_id, _)| bot_id == &row.to_id)
            {
                Some(entry) => {
                    if relation == BotAccessRelation::Owner {
                        entry.1 = BotAccessRelation::Owner;
                    }
                }
                None => candidates.push((row.to_id.clone(), relation)),
            }
        }

        for (bot_id, _relation) in &candidates {
            if deleted.contains(bot_id.as_str()) {
                return Err(invalid_union_reason(bot_id, env, "dangling_edge"));
            }
            let Some(bot_row) = bots.get(bot_id) else {
                return Err(invalid_union_reason(bot_id, env, "dangling_edge"));
            };
            if resolved_env(bot_row.env.as_deref()) != env {
                return Err(invalid_union_reason(bot_id, env, "dangling_edge"));
            }
            if bot_row.actor_kind == ActorKind::Human {
                return Err(invalid_union_reason(bot_id, env, "human_target"));
            }
            let ownership_version = authority
                .ownership_versions
                .get(bot_id)
                .copied()
                .unwrap_or(UNINITIALIZED_OWNERSHIP_VERSION);
            if ownership_version == UNINITIALIZED_OWNERSHIP_VERSION {
                return Err(invalid_union_reason(bot_id, env, "uninitialized"));
            }
            let owner_row_count = authority
                .role_rows
                .iter()
                .filter(|row| {
                    row.env == env
                        && row.to_id == *bot_id
                        && row.status == "approved"
                        && row.grant_kind == "owner"
                })
                .count();
            if owner_row_count != 1 {
                return Err(invalid_union_reason(bot_id, env, "owner_slot"));
            }
        }
        Ok((candidates, self_row_present))
    }

    /// Project and filter one controllable union member. `None` means
    /// the row no longer projects (e.g. concurrently soft-deleted between
    /// the union read and the record projection) and is skipped.
    async fn controllable_record(
        &self,
        query: &BotControllableQuery,
        bot_id: &str,
        relation: BotAccessRelation,
    ) -> ServiceResult<Option<ControllableBotRecord>> {
        let Some(record) = self.get_control_plane(bot_id, &query.env).await? else {
            return Ok(None);
        };
        if !controllable_passes_filters(&record, query)? {
            return Ok(None);
        }
        Ok(Some(ControllableBotRecord {
            record,
            access_relation: relation,
        }))
    }
}

fn controllable_passes_filters(
    record: &BotControlPlaneRecord,
    query: &BotControllableQuery,
) -> ServiceResult<bool> {
    if query.kind.is_some_and(|kind| record.kind != kind) {
        return Ok(false);
    }
    if query.status.is_some_and(|status| record.status != status) {
        return Ok(false);
    }
    let name = query
        .name
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_lowercase);
    if name.is_some_and(|needle| !record.name.to_lowercase().contains(&needle)) {
        return Ok(false);
    }
    Ok(true)
}

impl MemoryBotRepo {
    /// `BotControlPlaneRepoPort::list_controllable` for the memory twin.
    /// The trait arm lives in the memory control-plane impl block; this
    /// inherent engine keeps the contract in one place next to its SQL
    /// twin.
    pub(super) async fn list_controllable_impl(
        &self,
        query: &BotControllableQuery,
    ) -> ServiceResult<Vec<ControllableBotRecord>> {
        let (candidates, self_row_present) = self.controllable_union(query).await?;
        let mut records = Vec::with_capacity(candidates.len() + usize::from(self_row_present));
        for (bot_id, relation) in candidates {
            if let Some(record) = self.controllable_record(query, &bot_id, relation).await? {
                records.push(record);
            }
        }
        // The caller's own Human self row: explicit compatibility
        // projection labeled owner (carries no transferrable edge).
        if self_row_present {
            let self_row_id = human_actor_id(&query.user_id);
            if let Some(record) = self
                .controllable_record(query, &self_row_id, BotAccessRelation::Owner)
                .await?
            {
                records.push(record);
            }
        }
        records.sort_by(|left, right| {
            right
                .record
                .created_at
                .cmp(&left.record.created_at)
                .then_with(|| left.record.bot_id.cmp(&right.record.bot_id))
        });
        Ok(records)
    }
}

// ---------------------------------------------------------------------------
// SQL twin (PersistentBotRepo, SQLite/MySQL shared dialects)
// ---------------------------------------------------------------------------

impl PersistentBotRepo {
    /// Probe query for INVALID union members (the strict Task 3 batch
    /// semantics: any invalid Bot fails the whole query). Returns
    /// `(bot_id, reason)` rows for every member that is dangling,
    /// targets a Human row, is uninitialized, or lacks the unique
    /// approved owner slot.
    fn controllable_invalid_union_statement(
        &self,
        query: &BotControllableQuery,
    ) -> DbStatement {
        DbStatement::with_params(
            "SELECT m.bot_uuid AS bot_id, \
                    CASE \
                      WHEN b.bot_uuid IS NULL OR COALESCE(b.is_deleted, 0) = 1 THEN 'dangling_edge' \
                      WHEN COALESCE(b.actor_kind, 'bot') = 'human' THEN 'human_target' \
                      WHEN COALESCE(b.ownership_version, 0) = 0 THEN 'uninitialized' \
                      ELSE 'owner_slot' \
                    END AS reason \
             FROM (SELECT e.to_id AS bot_uuid FROM edge_grants e \
                    WHERE e.env = ? AND e.from_id = ? AND e.status = 'approved' \
                      AND e.grant_kind IN ('owner', 'manager')) m \
             LEFT JOIN bcs_bots b ON b.bot_uuid = m.bot_uuid AND b.env = ? \
             WHERE b.bot_uuid IS NULL OR COALESCE(b.is_deleted, 0) = 1 \
                OR COALESCE(b.actor_kind, 'bot') = 'human' \
                OR COALESCE(b.ownership_version, 0) = 0 \
                OR (SELECT COUNT(*) FROM edge_grants o \
                     WHERE o.env = ? AND o.to_id = m.bot_uuid \
                       AND o.grant_kind = 'owner' AND o.status = 'approved') <> 1",
            vec![
                Value::from(query.env.as_str()),
                Value::from(human_actor_id(&query.user_id)),
                Value::from(query.env.as_str()),
                Value::from(query.env.as_str()),
            ],
        )
    }

    /// The filtered union selection: one row per Bot with its relation
    /// label (owner priority; the Human self row is labeled owner).
    fn controllable_selection_statement(
        &self,
        query: &BotControllableQuery,
    ) -> ServiceResult<DbStatement> {
        let mut sql = format!(
            "SELECT b.bot_uuid, b.name, b.bot_info, b.visibility, b.status, \
                    b.actor_kind, b.env, b.created_by, b.agent_code, \
                    b.task_claim_mode, b.task_dream_mode, b.user_visibility, \
                    b.friend_ext, b.friend_check_in_strategy, \
                    ({}) * 1000 AS gmt_create_ms, \
                    ({}) * 1000 AS gmt_modified_ms, \
                    CASE WHEN COALESCE(b.actor_kind, 'bot') = 'human' THEN 'owner' \
                         WHEN EXISTS (SELECT 1 FROM edge_grants own \
                                       WHERE own.env = b.env AND own.to_id = b.bot_uuid \
                                         AND own.from_id = ? AND own.status = 'approved' \
                                         AND own.grant_kind = 'owner') THEN 'owner' \
                         ELSE 'manager' END AS access_relation \
             FROM bcs_bots b \
             WHERE b.env = ? AND COALESCE(b.is_deleted, 0) = 0 \
               AND ( \
                    (COALESCE(b.actor_kind, 'bot') = 'human' AND b.bot_uuid = ?) \
                    OR EXISTS (SELECT 1 FROM edge_grants e \
                                WHERE e.env = b.env AND e.to_id = b.bot_uuid \
                                  AND e.from_id = ? AND e.status = 'approved' \
                                  AND e.grant_kind IN ('owner', 'manager')) \
                   )",
            self.flavor.unix_ts("b.gmt_create"),
            self.flavor.unix_ts("b.gmt_modified"),
        );
        let mut params = vec![
            Value::from(human_actor_id(&query.user_id)),
            Value::from(query.env.as_str()),
            Value::from(human_actor_id(&query.user_id)),
            Value::from(human_actor_id(&query.user_id)),
        ];
        if let Some(kind) = query.kind {
            sql.push_str(" AND COALESCE(b.actor_kind, 'bot') = ?");
            params.push(Value::from(match kind {
                ActorKind::Bot => "bot",
                ActorKind::Human => "human",
            }));
        }
        if let Some(name) = query
            .name
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            sql.push_str(" AND INSTR(LOWER(b.name), ?) > 0");
            params.push(Value::from(name.to_lowercase()));
        }
        if let Some(status) = query.status {
            sql.push_str(" AND b.status = ?");
            params.push(Value::from(match status {
                ActorStatus::Online => "online",
                ActorStatus::Hidden => "hidden",
            }));
        }
        sql.push_str(" ORDER BY b.gmt_create DESC, b.bot_uuid ASC");
        Ok(DbStatement::with_params(&sql, params))
    }
}

impl PersistentBotRepo {
    /// `BotControlPlaneRepoPort::list_controllable` for the SQL twin. The
    /// trait arm lives with the other control-plane port methods; this
    /// inherent engine keeps the union contract next to its memory twin.
    pub(super) async fn list_controllable_impl(
        &self,
        query: &BotControllableQuery,
    ) -> ServiceResult<Vec<ControllableBotRecord>> {
        // One consistent snapshot for validation + selection (the same
        // strictness contract as the aggregate authority reads).
        let selection = self.controllable_selection_statement(query)?;
        let results = self
            .db
            .transaction(vec![
                DbTransactionStep::Query(self.controllable_invalid_union_statement(query)),
                DbTransactionStep::Query(selection),
            ])
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        let (invalid_rows, selection_rows) = match results.as_slice() {
            [DbTransactionStepResult::Rows(invalid), DbTransactionStepResult::Rows(selection)] => {
                (invalid, selection)
            }
            _ => {
                return Err(ServiceError::InternalError(
                    "controllable union transaction returned unexpected step results".to_string(),
                ))
            }
        };
        if let Some(invalid) = invalid_rows.first() {
            let bot_id: String = invalid
                .get_string("bot_id")
                .map_err(|error| ServiceError::InternalError(error.to_string()))?
                .unwrap_or_default();
            let reason: String = invalid
                .get_string("reason")
                .map_err(|error| ServiceError::InternalError(error.to_string()))?
                .unwrap_or_default();
            return Err(invalid_union_reason(&bot_id, &query.env, &reason));
        }

        let mut records = Vec::with_capacity(selection_rows.len());
        for row in selection_rows {
            let access_relation: String = row
                .get_string("access_relation")
                .map_err(|error| ServiceError::InternalError(error.to_string()))?
                .ok_or_else(|| {
                    ServiceError::InternalError(
                        "controllable selection row has no access_relation".to_string(),
                    )
                })?;
            let access_relation = match access_relation.as_str() {
                "owner" => BotAccessRelation::Owner,
                "manager" => BotAccessRelation::Manager,
                other => {
                    let record = super::control_plane_record_from_row(row)?;
                    return Err(corrupt(
                        &record.bot_id,
                        &query.env,
                        format!("undecodable access relation '{other}'"),
                    ));
                }
            };
            // The relation decodes FIRST — a corrupt label fails closed
            // before any record is surfaced.
            let record = super::control_plane_record_from_row(row)?;
            if !controllable_passes_filters(&record, query)? {
                continue;
            }
            records.push(ControllableBotRecord {
                record,
                access_relation,
            });
        }
        Ok(records)
    }
}
