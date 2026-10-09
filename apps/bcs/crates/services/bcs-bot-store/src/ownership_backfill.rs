//! Historical ownership backfill reads and the batched migration lane
//! (plan Task 17, spec §16.1).
//!
//! Everything here is a CURRENT-FACTS read the migration Core re-derives
//! candidates and conflicts from — the candidate scan pages live physical
//! version-0 Bots of the store env by `bot_uuid` keyset, the per-Bot state
//! read re-proves version/owner/creator evidence for BOTH the dry-run and
//! the execution, and the batch ledger read recovers an interrupted
//! `initialize_batch`'s committed prefix by `batch_id`. The store never
//! decides governance: classification lives in the Core; these relations
//! only carry the plain facts. No method in this module writes; the single
//! write entry is [`PersistentBotRepo::initialize_existing_ownership_in_batch_impl`],
//! which is the Task 5 one-transaction lane with the batch tag on the
//! initialization ledger row.
//!
//! The memory twin mirrors the same scans over the exact
//! [`MemoryBotRepo`] critical section the Task 3/4/5 authority state shares.

use std::collections::BTreeMap;

use bcs_service_api::port::repo::bot_authority::human_actor_id;
use bcs_service_api::ServiceResult;
use bcs_service_api::types::bot_authority::{
    OwnershipBatchInitialization, OwnershipMigrationBotState,
};

use super::ownership_initialization::sanitized;
use super::memory::memory_authority::MemoryAuthorityState;
use super::memory::RegisteredBotInner;
use super::{MemoryBotRepo, PersistentBotRepo, Value, resolve_env};

/// Positional-parameter safety bound for the IN-list enrichment reads;
/// the migration's batch bound (100) keeps the common case at one query.
const IN_CHUNK: usize = 100;

/// `bcs_bots.created_by` normalization shared by the SQL and memory reads:
/// the migration's ONLY trusted creator source, trimmed, blank -> absent.
/// The migration itself NEVER writes the column — normalization is a
/// read-side fact, not a data change.
fn normalize_created_by(raw: Option<String>) -> Option<String> {
    let trimmed = raw.map(|value| value.trim().to_string());
    trimmed.filter(|value| !value.is_empty())
}

/// The candidate scan's keyset page over live physical version-0 Bots of
/// the process env (see the `BotRepoPort::list_migration_candidates` doc).
impl PersistentBotRepo {
    pub(super) async fn list_migration_candidates_impl(
        &self,
        after_bot_id: Option<&str>,
        limit: u32,
    ) -> ServiceResult<Vec<OwnershipMigrationBotState>> {
        if limit == 0 {
            return Err(sanitized("migration candidate scan requires a positive limit"));
        }
        let env = resolve_env();
        // Inline LIMIT: the value is a validated u32, never client text,
        // and the bound keeps dialect drivers away from LIMIT-?
        // positional quirks.
        let (sql, params) = match after_bot_id {
            None => (
                format!(
                    "SELECT bot_uuid, created_by FROM bcs_bots \
                     WHERE env = ? AND COALESCE(is_deleted, 0) = 0 \
                       AND COALESCE(actor_kind, 'bot') <> 'human' \
                       AND ownership_version = 0 \
                     ORDER BY bot_uuid LIMIT {limit}"
                ),
                vec![Value::from(env.as_str())],
            ),
            Some(after) => (
                format!(
                    "SELECT bot_uuid, created_by FROM bcs_bots \
                     WHERE env = ? AND COALESCE(is_deleted, 0) = 0 \
                       AND COALESCE(actor_kind, 'bot') <> 'human' \
                       AND ownership_version = 0 \
                       AND bot_uuid > ? \
                     ORDER BY bot_uuid LIMIT {limit}"
                ),
                vec![Value::from(env.as_str()), Value::from(after)],
            ),
        };
        let rows = self
            .db_query(&sql, params)
            .await
            .map_err(|_| sanitized("migration candidate scan failed"))?;
        let mut candidates = Vec::with_capacity(rows.len());
        for row in rows {
            let bot_id = row
                .get_string("bot_uuid")
                .ok()
                .flatten()
                .ok_or_else(|| sanitized("migration candidate row decode failed"))?;
            candidates.push(OwnershipMigrationBotState {
                bot_id,
                env: env.to_string(),
                created_by: normalize_created_by(row.get_string("created_by").ok().flatten()),
                ownership_version: 0,
                live: true,
                physical: true,
                owner_edge_claimants: Vec::new(),
                creator_human_live: false,
            });
        }
        self.enrich_sql_states(&env, &mut candidates).await?;
        Ok(candidates)
    }

    /// Current migration facts of ONE arbitrary Bot row (any version, live
    /// or deleted, physical or Human): the execution-time re-verification
    /// substrate (see the `BotRepoPort::migration_bot_state` doc).
    pub(super) async fn migration_bot_state_impl(
        &self,
        bot_id: &str,
    ) -> ServiceResult<Option<OwnershipMigrationBotState>> {
        let env = resolve_env();
        let rows = self
            .db_query(
                "SELECT created_by, ownership_version, COALESCE(is_deleted, 0) AS deleted, \
                        COALESCE(actor_kind, 'bot') AS actor_kind \
                 FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
                vec![Value::from(bot_id), Value::from(env.as_str())],
            )
            .await
            .map_err(|_| sanitized("migration bot state read failed"))?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let deleted = row.get_i64("deleted").ok().flatten().unwrap_or(0);
        let actor_kind = row
            .get_string("actor_kind")
            .ok()
            .flatten()
            .unwrap_or_else(|| "bot".to_string());
        let version = row
            .get_i64("ownership_version")
            .ok()
            .flatten()
            .unwrap_or(0)
            .max(0) as u64;
        let mut state = OwnershipMigrationBotState {
            bot_id: bot_id.to_string(),
            env: env.to_string(),
            created_by: normalize_created_by(row.get_string("created_by").ok().flatten()),
            ownership_version: version,
            live: deleted == 0,
            physical: actor_kind != "human",
            owner_edge_claimants: Vec::new(),
            creator_human_live: false,
        };
        if state.live && state.physical {
            self.enrich_sql_states(&env, std::slice::from_mut(&mut state))
                .await?;
        }
        Ok(Some(state))
    }

    /// One governed migration batch's committed initialization rows (the
    /// replay/recovery read, see the `BotRepoPort::list_batch_initializations`
    /// doc). The ledger is append-only: reports are rebuilt from it, never
    /// altered.
    pub(super) async fn list_batch_initializations_impl(
        &self,
        batch_id: &str,
    ) -> ServiceResult<Vec<OwnershipBatchInitialization>> {
        let env = resolve_env();
        let rows = self
            .db_query(
                "SELECT bot_id, owner_user_id, operation_id FROM bot_ownership_initializations \
                 WHERE env = ? AND batch_id = ? ORDER BY bot_id",
                vec![Value::from(env.as_str()), Value::from(batch_id)],
            )
            .await
            .map_err(|_| sanitized("migration batch ledger read failed"))?;
        let mut committed = Vec::with_capacity(rows.len());
        for row in rows {
            let bot_id = row
                .get_string("bot_id")
                .ok()
                .flatten()
                .ok_or_else(|| sanitized("migration batch ledger decode failed"))?;
            let owner_user_id = row
                .get_string("owner_user_id")
                .ok()
                .flatten()
                .ok_or_else(|| sanitized("migration batch ledger decode failed"))?;
            let operation_id = row
                .get_string("operation_id")
                .ok()
                .flatten()
                .ok_or_else(|| sanitized("migration batch ledger decode failed"))?;
            committed.push(OwnershipBatchInitialization {
                bot_id,
                owner_user_id,
                operation_id,
            });
        }
        Ok(committed)
    }

    /// Adequate-signal enrichment of a fine-grained page (bounded by the
    /// migration batch limit): the approved owner-edge claimants of the
    /// listed Bots and the live-Human evidence for their creators.
    async fn enrich_sql_states(
        &self,
        env: &str,
        states: &mut [OwnershipMigrationBotState],
    ) -> ServiceResult<()> {
        if states.is_empty() {
            return Ok(());
        }
        let bot_ids: Vec<String> = states.iter().map(|state| state.bot_id.clone()).collect();
        // Approved owner edges of the page (raw from_id claimants — a
        // corrupt non-human-prefixed edge stays visible to the classifier).
        let mut claims: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for chunk in bot_ids.chunks(IN_CHUNK) {
            let placeholders = std::iter::repeat("?")
                .take(chunk.len())
                .collect::<Vec<_>>()
                .join(", ");
            let sql = format!(
                "SELECT to_id, from_id FROM edge_grants \
                 WHERE env = ? AND grant_kind = 'owner' AND status = 'approved' \
                   AND to_id IN ({placeholders}) \
                 ORDER BY to_id, from_id"
            );
            let mut params = vec![Value::from(env)];
            for bot_id in chunk {
                params.push(Value::from(bot_id.as_str()));
            }
            let rows = self
                .db_query(&sql, params)
                .await
                .map_err(|_| sanitized("migration owner-edge read failed"))?;
            for row in rows {
                if let (Some(to_id), Some(from_id)) = (
                    row.get_string("to_id").ok().flatten(),
                    row.get_string("from_id").ok().flatten(),
                ) {
                    claims.entry(to_id).or_default().push(from_id);
                }
            }
        }
        for state in states.iter_mut() {
            if let Some(list) = claims.get(&state.bot_id) {
                state.owner_edge_claimants = list.clone();
            }
        }
        // Live Human actor rows for the creators the page carries.
        let creator_ids: Vec<String> = states
            .iter()
            .filter_map(|state| state.created_by.clone())
            .map(|creator| human_actor_id(&creator))
            .collect();
        let mut live_humans: Vec<String> = Vec::new();
        for chunk in creator_ids.chunks(IN_CHUNK) {
            let placeholders = std::iter::repeat("?")
                .take(chunk.len())
                .collect::<Vec<_>>()
                .join(", ");
            let sql = format!(
                "SELECT bot_uuid FROM bcs_bots \
                 WHERE env = ? AND actor_kind = 'human' AND COALESCE(is_deleted, 0) = 0 \
                   AND bot_uuid IN ({placeholders})",
            );
            let mut params = vec![Value::from(env)];
            for actor_id in chunk {
                params.push(Value::from(actor_id.as_str()));
            }
            let rows = self
                .db_query(&sql, params)
                .await
                .map_err(|_| sanitized("migration creator-human read failed"))?;
            for row in rows {
                if let Some(bot_uuid) = row.get_string("bot_uuid").ok().flatten() {
                    live_humans.push(bot_uuid);
                }
            }
        }
        for state in states.iter_mut() {
            if let Some(creator) = state.created_by.as_deref() {
                state.creator_human_live = live_humans.contains(&human_actor_id(creator));
            }
        }
        Ok(())
    }
}

impl MemoryBotRepo {
    /// Memory twin of [`PersistentBotRepo::list_migration_candidates_impl`]:
    /// BTreeMap keyset page over the live physical version-0 rows of the
    /// process env, enriched from the shared authority state.
    pub(crate) async fn list_migration_candidates_impl(
        &self,
        after_bot_id: Option<&str>,
        limit: u32,
    ) -> ServiceResult<Vec<OwnershipMigrationBotState>> {
        if limit == 0 {
            return Err(sanitized("migration candidate scan requires a positive limit"));
        }
        let env = resolve_env();
        let bots = self.bots.read().await;
        let deleted = self.deleted_bot_ids.read().await;
        let authority = self.authority.read().await;
        let mut states = Vec::new();
        for (bot_id, row) in bots.iter() {
            if states.len() >= limit as usize {
                break;
            }
            if row.env.as_deref() != Some(env.as_str()) {
                continue;
            }
            if let Some(after) = after_bot_id && bot_id.as_str() <= after {
                continue;
            }
            if deleted.contains(bot_id) || row.actor_kind == bcs_service_api::ActorKind::Human {
                continue;
            }
            let version = authority
                .ownership_versions
                .get(bot_id)
                .copied()
                .unwrap_or(0);
            if version != 0 {
                continue;
            }
            states.push(OwnershipMigrationBotState {
                bot_id: bot_id.clone(),
                env: env.to_string(),
                created_by: normalize_created_by(row.created_by.clone()),
                ownership_version: version,
                live: true,
                physical: true,
                owner_edge_claimants: Vec::new(),
                creator_human_live: false,
            });
        }
        memory_enrich_states(&bots, &deleted, &authority, &env, &mut states);
        Ok(states)
    }

    /// Memory twin of [`PersistentBotRepo::migration_bot_state_impl`].
    pub(crate) async fn migration_bot_state_impl(
        &self,
        bot_id: &str,
    ) -> ServiceResult<Option<OwnershipMigrationBotState>> {
        let env = resolve_env();
        let bots = self.bots.read().await;
        let deleted = self.deleted_bot_ids.read().await;
        let authority = self.authority.read().await;
        if !bots.contains_key(bot_id) {
            if deleted.contains(bot_id) {
                // Removed tombstone: present but not live.
                return Ok(Some(OwnershipMigrationBotState {
                    bot_id: bot_id.to_string(),
                    env: env.to_string(),
                    created_by: None,
                    ownership_version: authority
                        .ownership_versions
                        .get(bot_id)
                        .copied()
                        .unwrap_or(0),
                    live: false,
                    physical: true,
                    owner_edge_claimants: Vec::new(),
                    creator_human_live: false,
                }));
            }
            return Ok(None);
        }
        let row = &bots[bot_id];
        let mut state = OwnershipMigrationBotState {
            bot_id: bot_id.to_string(),
            env: env.to_string(),
            created_by: normalize_created_by(row.created_by.clone()),
            ownership_version: authority
                .ownership_versions
                .get(bot_id)
                .copied()
                .unwrap_or(0),
            live: !deleted.contains(bot_id),
            physical: row.actor_kind != bcs_service_api::ActorKind::Human,
            owner_edge_claimants: Vec::new(),
            creator_human_live: false,
        };
        if state.live && state.physical {
            memory_enrich_states(&bots, &deleted, &authority, &env, std::slice::from_mut(&mut state));
        }
        Ok(Some(state))
    }

    /// Memory twin of [`PersistentBotRepo::list_batch_initializations_impl`].
    pub(crate) async fn list_batch_initializations_impl(
        &self,
        batch_id: &str,
    ) -> ServiceResult<Vec<OwnershipBatchInitialization>> {
        let env = resolve_env();
        let authority = self.authority.read().await;
        let mut committed: Vec<OwnershipBatchInitialization> = authority
            .initialization_records
            .iter()
            .filter(|record| {
                record.env == env && record.batch_id.as_deref() == Some(batch_id)
            })
            .map(|record| OwnershipBatchInitialization {
                bot_id: record.bot_id.clone(),
                owner_user_id: record.owner_user_id.clone(),
                operation_id: record.operation_id.clone(),
            })
            .collect();
        committed.sort_by(|a, b| a.bot_id.cmp(&b.bot_id));
        Ok(committed)
    }
}

/// Enrich memory states from the shared critical-section state: approved
/// owner-edge claimants per Bot and the live-Human evidence per creator.
fn memory_enrich_states(
    bots: &BTreeMap<String, RegisteredBotInner>,
    deleted: &std::collections::HashSet<String>,
    authority: &MemoryAuthorityState,
    env: &str,
    states: &mut [OwnershipMigrationBotState],
) {
    for state in states.iter_mut() {
        state.owner_edge_claimants = authority
            .role_rows
            .iter()
            .filter(|row| {
                row.env == env
                    && row.to_id == state.bot_id
                    && row.grant_kind == "owner"
                    && row.status == "approved"
            })
            .map(|row| row.from_id.clone())
            .collect();
        if let Some(creator) = state.created_by.clone() {
            let human_id = human_actor_id(&creator);
            state.creator_human_live = bots
                .get(&human_id)
                .is_some_and(|row| row.actor_kind == bcs_service_api::ActorKind::Human)
                && !deleted.contains(&human_id);
        }
    }
}