//! Strict authority reads (`BotAuthorityRepoPort` impl).
//!
//! Every read is scoped by the store-bound env, compares
//! identities byte-exact, requires the unified approved
//! management-edge shape (`grant_ref_id = 0`, `rules` NULL,
//! `status = 'approved'`), and never degrades a corrupt owner
//! state or a storage failure into a deny/`None` (plan Task
//! 1.5; spec §3.2 / §7). Batch queries are bounded collections
//! of at most [`CHUNK`] ids per statement — never one query per
//! input item.
//!
//! Owner/manager edges are metadata only: these reads never
//! feed runtime permission decisions, and runtime grant edges
//! never satisfy these reads (spec §3.2.5 / A16).

use std::collections::{HashMap, HashSet};

use async_trait::async_trait;
use bcs_db_api::{DbRow, DbStatement, DbValue};
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{
    AuthorityActorKind, BotAccessRelation, BotOwnershipView, BotRoleQuery,
    ManagerEdit, ManagerListQuery, ManagerPage, ManagerSetChange,
    ManagerSync, TransferCommand, TransferDirection, TransferKey,
    TransferListQuery, TransferOutcome, TransferPage, TransferStatus,
    TransferView, check_page_bounds, human_user_id_of_actor,
};

use super::{CHUNK, DbBotAuthorityStore, not_initialized_error, owner_integrity_error};

/// Transfer projection columns (spec §4.3).
pub(super) const TRANSFER_COLUMNS: &str = "bot_id, target_owner_actor_id, from_owner_actor_id, \
     status, requested_at, expires_at, updated_at, decided_at, decided_by_kind, \
     decided_by_id, reason, result_ownership_version, bot_name_snapshot";

/// Slice ids into bounded collection-query chunks.
fn chunked<'a>(items: &[&'a str]) -> Vec<Vec<&'a str>> {
    // Borrowed from `items` for the statement batch only.
    items
        .chunks(CHUNK)
        .map(<[&str]>::to_vec)
        .collect()
}

#[async_trait]
impl BotAuthorityRepoPort for DbBotAuthorityStore {
    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>> {
        let Some((_, ownership_version)) = self.bot_ownership_state(bot_id).await? else {
            return Ok(None);
        };
        if ownership_version == 0 {
            // Migration-window shim: a version-0 Bot answers the
            // legacy created_by arm (posture-independent reads during
            // the window); a miss denies exactly as before.
            return self.legacy_created_by_role(bot_id, user_id).await;
        }
        // Owner state completeness is part of the bot's authority
        // state: a corrupt state must surface as an error for any
        // strict read, not as a silent deny for uninvolved
        // principals (spec §3.2.2).
        self.resolve_owner(bot_id).await?;
        // spec §3.1: only a LIVE Human actor (env-bound,
        // not tombstoned) may authorize through an edge — a
        // residual edge owned by a deleted or never-ensured
        // Human grants nothing. Lifecycle facts are an
        // ordinary deny, not an integrity error.
        if !self.live_human_actors(&[user_id]).await?.contains(user_id) {
            return Ok(None);
        }
        let edges = self.management_edges_for(bot_id, user_id).await?;
        if edges.is_empty() {
            return Ok(None);
        }
        let relation = if edges.contains(&BotAccessRelation::Owner) {
            BotAccessRelation::Owner
        } else {
            BotAccessRelation::Manager
        };
        Ok(Some(relation))
    }


    async fn roles_for(
        &self,
        queries: &[BotRoleQuery],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        if queries.is_empty() {
            return Ok(Vec::new());
        }

        // Distinct bots and distinct users, chunked; every
        // statement binds ≤ CHUNK ids (plan Task 1.5).
        let bot_order = distinct_in_order(queries.iter().map(|query| &query.bot_id));
        let user_order = distinct_in_order(queries.iter().map(|query| &query.user_id));

        let mut states: HashMap<String, u64> = HashMap::new();
        let mut owner_counts: HashMap<String, usize> = HashMap::new();
        for bot_chunk in chunked(&bot_order) {
            for (bot_id, version) in self.bot_states_in(&bot_chunk).await? {
                states.insert(bot_id, version);
            }
            for (bot_id, approved_owners) in self.owner_counts_in(&bot_chunk).await? {
                owner_counts.insert(bot_id, approved_owners);
            }
        }
        // Owner completeness for every live INITIALIZED bot the
        // batch touches: corrupt states fail the whole batch
        // instead of resolving any roles (spec §3.2.2). Version-0
        // bots short-circuit to deny exactly like the single
        // `role` read — integrity errors are for version > 0
        // with a missing or duplicated approved owner.
        let mut corrupted: Option<String> = None;
        for (bot_id, count) in &owner_counts {
            if *count > 1 && states.get(bot_id).copied().unwrap_or(0) > 0 {
                corrupted = Some(format!("bot {bot_id} has {count} approved owner edges"));
            }
        }
        if let Some(detail) = corrupted {
            return Err(owner_integrity_error(detail));
        }
        for (bot_id, version) in &states {
            if *version > 0 && !owner_counts.contains_key(bot_id) {
                return Err(owner_integrity_error(format!(
                    "bot {bot_id} has no approved owner edge"
                )));
            }
        }

        // Migration-window shim: the legacy creator facts for every
        // Bot in the batch (ONE chunked read, never per-bot),
        // consulted only for version-0 rows by the final mapping.
        let mut legacy_creators: HashMap<String, String> = HashMap::new();
        if self.legacy_created_by_fallback {
            for bot_chunk in chunked(&bot_order) {
                for (bot_id, creator) in self.legacy_creators_in(&bot_chunk).await? {
                    if let Some(creator) = creator {
                        legacy_creators.insert(bot_id, creator);
                    }
                }
            }
        }

        // matched[(user, bot)] = relations; ≤2 per bot per user.
        let mut matched: HashMap<(&str, String), Vec<BotAccessRelation>> = HashMap::new();
        for &user in &user_order {
            for bot_chunk in chunked(&bot_order) {
                let edges = self.management_edges_for_chunk(user, &bot_chunk).await?;
                for (bot_id, relation) in edges {
                    // Own the bot key: chunk row strings may not
                    // outlive the loop.
                    matched
                        .entry((user, bot_id.clone()))
                        .or_default()
                        .push(relation);
                }
            }
        }

        // spec §3.1: only LIVE Human actors resolve any relation —
        // one chunked registry read for the whole batch (never a
        // per-user query), tombstoned or never-ensured operators
        // deny per query without erroring the batch.
        let user_refs: Vec<&str> = user_order.to_vec();
        let live_humans = self.live_human_actors(&user_refs).await?;

        let mut results = Vec::with_capacity(queries.len());
        for query in queries {
            let Some(version) = states.get(query.bot_id.as_str()).copied() else {
                results.push(None); // missing / deleted bot denies
                continue;
            };
            let edges = matched
                .get(&(query.user_id.as_str(), query.bot_id.clone()))
                .cloned()
                .unwrap_or_default();
            if version == 0 {
                // Migration-window shim: a version-0 Bot answers the
                // legacy created_by arm; a miss denies as before.
                let legacy = if self.legacy_created_by_fallback {
                    // created_by is the raw legacy User ID; compare
                    // against the actor id's human suffix (see the
                    // single-role arm for the same rule).
                    query
                        .user_id
                        .strip_prefix("human_")
                        .and_then(|legacy_user| {
                            legacy_creators
                                .get(query.bot_id.as_str())
                                .filter(|creator| creator.as_str() == legacy_user)
                        })
                        .map(|_| BotAccessRelation::Owner)
                } else {
                    None
                };
                results.push(legacy);
                continue;
            }
            if edges.is_empty() || !live_humans.contains(query.user_id.as_str()) {
                results.push(None); // no edge / no live Human denies
                continue;
            }
            results.push(Some(if edges.contains(&BotAccessRelation::Owner) {
                BotAccessRelation::Owner
            } else {
                BotAccessRelation::Manager
            }));
        }
        Ok(results)
    }

    async fn ownership(&self, bot_id: &str) -> ServiceResult<BotOwnershipView> {
        let Some((_, ownership_version)) = self.bot_ownership_state(bot_id).await? else {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        };
        if ownership_version == 0 {
            return Err(not_initialized_error(bot_id));
        }
        let owner = self.resolve_owner(bot_id).await?;
        Ok(BotOwnershipView {
            bot_id: bot_id.to_string(),
            owner_actor_id: owner.clone(),
            owner_user_id: self.human_user_id(&owner)?,
        })
    }

    async fn managers(&self, query: ManagerListQuery) -> ServiceResult<ManagerPage> {
        check_page_bounds(query.offset, query.limit)?;
        let Some((_, ownership_version)) = self.bot_ownership_state(&query.bot_id).await? else {
            return Err(ServiceError::BotNotFound(query.bot_id.clone()));
        };
        if ownership_version == 0 {
            return Err(not_initialized_error(&query.bot_id));
        }
        let owner = self.resolve_owner(&query.bot_id).await?;
        let view = BotOwnershipView {
            bot_id: query.bot_id.clone(),
            owner_actor_id: owner.clone(),
            owner_user_id: self.human_user_id(&owner)?,
        };

        let total = self
            .query_u64(
                "authority_managers_count",
                &format!(
                    "SELECT COUNT(*) AS total FROM edge_grants \
                     WHERE env = ? AND grant_kind = 'manager' AND grant_ref_id = 0 \
                       AND rules IS NULL AND status = 'approved' AND {}",
                    self.to_id_eq()
                ),
                vec![self.env_param(), DbValue::from(query.bot_id.clone())],
            )
            .await?;
        let rows = self
            .query(
                "authority_managers_page",
                DbStatement::with_params(
                    &format!(
                        "SELECT from_id FROM edge_grants \
                         WHERE env = ? AND grant_kind = 'manager' AND grant_ref_id = 0 \
                           AND rules IS NULL AND status = 'approved' AND {} \
                         ORDER BY {} LIMIT ? OFFSET ?",
                        self.to_id_eq(),
                        self.from_id_order()
                    ),
                    vec![
                        self.env_param(),
                        DbValue::from(query.bot_id.clone()),
                        DbValue::from(query.limit),
                        DbValue::from(query.offset),
                    ],
                ),
            )
            .await?;
        let mut items = Vec::with_capacity(rows.len());
        for row in &rows {
            let actor = crate::required_string(row, "from_id")?;
            items.push(self.human_user_id(&actor)?);
        }
        Ok(ManagerPage { owner: view, items, total })
    }

    async fn transfer(&self, key: &TransferKey) -> ServiceResult<Option<TransferView>> {
        let rows = self
            .query(
                "authority_transfer_get",
                DbStatement::with_params(
                    format!(
                        "SELECT {TRANSFER_COLUMNS} FROM bot_ownership_transfers \
                         WHERE env = ? AND bot_id = ? AND target_owner_actor_id = ? LIMIT 1"
                    )
                    .as_str(),
                    vec![
                        self.env_param(),
                        DbValue::from(key.bot_id.clone()),
                        DbValue::from(key.target_owner_actor_id.clone()),
                    ],
                ),
            )
            .await?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        // One DB-time read per `transfer` call.
        let now = self.db_now_ms().await?;
        Ok(Some(self.transfer_view_from_row(&row, now).await?))
    }

    async fn transfers(&self, query: TransferListQuery) -> ServiceResult<TransferPage> {
        check_page_bounds(query.offset, query.limit)?;
        // ONE captured DB time per list call: the effective-status
        // lens in count and page AND every projected item share it
        // (spec §6.4 同一有效状态口径) — a pending row expiring
        // mid-read can never count as pending yet page as expired.
        let now_rows = self
            .query(
                "authority_transfers_now",
                DbStatement::new(
                    format!("SELECT {} AS now_txt", self.db_now_utc()).as_str(),
                ),
            )
            .await?;
        let now_txt = now_rows
            .first()
            .map(|row| crate::required_string(row, "now_txt"))
            .transpose()?
            .ok_or_else(|| {
                ServiceError::InternalError(
                    "bot authority: db time read returned no row".into(),
                )
            })?;
        let effective = "(CASE WHEN status = 'pending' AND expires_at <= ? \
             THEN 'expired' ELSE status END)"
            .to_string();
        let status_filter = if query.statuses.is_empty() {
            "1 = 1".to_string()
        } else {
            let placeholders = std::iter::repeat("?")
                .take(query.statuses.len())
                .collect::<Vec<_>>()
                .join(", ");
            format!("{effective} IN ({placeholders})")
        };
        // Direction selection: `user_id` of the list query is the
        // Human actor id of the requesting party; received rows
        // carry it as the transfer target, sent rows as the
        // originating owner. Ids compare byte-exact.
        // Ids compare byte-exact per direction: received rows
        // match on the transfer target, sent rows on the
        // originating owner.
        let actor_eq = match (query.direction, self.flavor()) {
            (TransferDirection::Received, bcs_db_api::DbSqlFlavor::Mysql) => {
                "CAST(target_owner_actor_id AS BINARY) = ?"
            }
            (TransferDirection::Received, _) => "target_owner_actor_id = ?",
            (TransferDirection::Sent, bcs_db_api::DbSqlFlavor::Mysql) => {
                "CAST(from_owner_actor_id AS BINARY) = ?"
            }
            (TransferDirection::Sent, _) => "from_owner_actor_id = ?",
        };
        let predicate = format!(
            "WHERE env = ? AND {actor_eq} AND {status_filter}"
        );
        let mut params = vec![self.env_param(), DbValue::from(query.user_id.clone())];
        if !query.statuses.is_empty() {
            params.push(DbValue::from(now_txt.as_str()));
            for status in &query.statuses {
                params.push(DbValue::from(status.as_str()));
            }
        }
        let total = self
            .query_u64(
                "authority_transfers_count",
                &format!(
                    "SELECT COUNT(*) AS total FROM bot_ownership_transfers {predicate}"
                ),
                params.clone(),
            )
            .await?;
        let mut page_params = params;
        page_params.push(DbValue::from(query.limit));
        page_params.push(DbValue::from(query.offset));
        let rows = self
            .query(
                "authority_transfers_page",
                DbStatement::with_params(
                    format!(
                        "SELECT {TRANSFER_COLUMNS} FROM bot_ownership_transfers {predicate} \
                         ORDER BY requested_at DESC, {}, {} LIMIT ? OFFSET ?",
                        self.bot_id_order(),
                        self.target_id_order()
                    )
                    .as_str(),
                    page_params,
                ),
            )
            .await?;
        // The projection shares the SAME captured DB time as the
        // count/page lens above — never a third clock.
        let now = parse_required_epoch(&now_txt, "now")?;
        let mut items = Vec::with_capacity(rows.len());
        for row in &rows {
            items.push(self.transfer_view_from_row(row, now).await?);
        }
        Ok(TransferPage { items, total })
    }

    async fn edit_manager(&self, command: ManagerEdit) -> ServiceResult<ManagerSetChange> {
        self.edit_manager_impl(command).await
    }

    async fn sync_managers(&self, command: ManagerSync) -> ServiceResult<ManagerSetChange> {
        self.sync_managers_impl(command).await
    }

    async fn change_transfer(&self, command: TransferCommand) -> ServiceResult<TransferOutcome> {
        self.change_transfer_impl(command).await
    }
}

fn distinct_in_order<'a>(values: impl Iterator<Item = &'a String>) -> Vec<&'a str> {
    let mut order: Vec<&str> = Vec::new();
    let mut seen: HashMap<&str, ()> = HashMap::new();
    for value in values {
        if seen.insert(value.as_str(), ()).is_none() {
            order.push(value.as_str());
        }
    }
    order
}

impl DbBotAuthorityStore {
    /// Live bot row projection: `None` when absent or tombstoned
    /// (spec §3.2.7: deleted bots authorize nobody).
    pub(super) async fn bot_ownership_state(
        &self,
        bot_id: &str,
    ) -> ServiceResult<Option<(String, u64)>> {
        let rows = self
            .query(
                "authority_bot_state",
                // Byte-exact bot id (MySQL would case-merge
                // principals otherwise, contradicting the
                // edge-side comparisons).
                DbStatement::with_params(
                    &format!(
                        "SELECT bot_uuid, ownership_version FROM bcs_bots \
                         WHERE {} AND env = ? \
                           AND COALESCE(is_deleted, 0) = 0 LIMIT 1",
                        self.bot_id_eq()
                    ),
                    vec![DbValue::from(bot_id), self.env_param()],
                ),
            )
            .await?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        Ok(Some((
            crate::required_string(&row, "bot_uuid")?,
            crate::required_u64(&row, "ownership_version")?,
        )))
    }

    /// Chunked `(bot_id → ownership_version)`; absent rows mean
    /// missing/deleted bots.
    async fn bot_states_in(&self, bots: &[&str]) -> ServiceResult<Vec<(String, u64)>> {
        let mut params = vec![self.env_param()];
        for bot in bots {
            params.push(DbValue::from(*bot));
        }
        let rows = self
            .query(
                "authority_bot_states_chunk",
                DbStatement::with_params(
                    format!(
                        "SELECT bot_uuid, ownership_version FROM bcs_bots \
                         WHERE env = ? AND COALESCE(is_deleted, 0) = 0 \
                           AND {}",
                        self.bot_id_in(bots.len())
                    )
                    .as_str(),
                    params,
                ),
            )
            .await?;
        rows.iter()
            .map(|row| {
                Ok((
                    crate::required_string(row, "bot_uuid")?,
                    crate::required_u64(row, "ownership_version")?,
                ))
            })
            .collect()
    }

    /// Legacy creator facts for ONE chunk of Bots (migration-window
    /// shim read): pairs of (bot_uuid, created_by); only version-0
    /// rows become fallback candidates at the final mapping, so the
    /// read does not pre-filter on ownership_version (the same live-row
    /// scoping `bot_states_in` uses).
    async fn legacy_creators_in(
        &self,
        bots: &[&str],
    ) -> ServiceResult<Vec<(String, Option<String>)>> {
        let mut params = vec![self.env_param()];
        for bot in bots {
            params.push(DbValue::from(*bot));
        }
        let rows = self
            .query(
                "authority_legacy_creators_chunk",
                DbStatement::with_params(
                    format!(
                        "SELECT bot_uuid, created_by FROM bcs_bots \
                         WHERE env = ? AND COALESCE(is_deleted, 0) = 0 \
                           AND {}",
                        self.bot_id_in(bots.len())
                    )
                    .as_str(),
                    params,
                ),
            )
            .await?;
        rows.iter()
            .map(|row| {
                Ok((
                    crate::required_string(row, "bot_uuid")?,
                    crate::optional_string(row, "created_by")?,
                ))
            })
            .collect()
    }

    /// Legacy `created_by` fallback role for ONE Bot (migration
    /// window shim `bot_authority.legacy_created_by_fallback`):
    /// answers the strict `role()`/`roles_for()` version-0 deny ONLY
    /// when the live Bot never initialized ownership AND its legacy
    /// creator fact names this Human — reproducing the pre-authority
    /// `created_by` read semantics so an environment can ship the
    /// code first and migrate ownership on demand. The live-Human
    /// actor registry gate deliberately does NOT apply here
    /// (unmigrated operators predate that registry); version > 0
    /// bot_ownership_state and owner-edge integrity are never
    /// shadowed. Sunset together with the config flag.
    async fn legacy_created_by_role(
        &self,
        bot_id: &str,
        user_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>> {
        if !self.legacy_created_by_fallback {
            return Ok(None);
        }
        let rows = self
            .query(
                "authority_legacy_created_by",
                DbStatement::with_params(
                    &format!(
                        "SELECT created_by FROM bcs_bots \
                         WHERE {} AND env = ? \
                           AND COALESCE(is_deleted, 0) = 0 \
                           AND ownership_version = 0 LIMIT 1",
                        self.bot_id_eq()
                    ),
                    vec![DbValue::from(bot_id), self.env_param()],
                ),
            )
            .await?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        // created_by is the raw legacy User ID; the authority read
        // takes the Human ACTOR id, so the comparison strips the
        // prefix (arbitrary non-prefixed actor strings never match).
        let Some(legacy_user) = user_id.strip_prefix("human_") else {
            return Ok(None);
        };
        let created_by: Option<String> = crate::optional_string(&row, "created_by")?;
        Ok(match created_by.as_deref() {
            Some(creator) if creator == legacy_user => Some(BotAccessRelation::Owner),
            _ => None,
        })
    }

    /// Approved-owner rows for one bot, bounded at 2 so missing,
    /// present, and duplicated states are distinguishable without
    /// an unbounded read.
    pub(super) async fn approved_owner_rows(&self, bot_id: &str) -> ServiceResult<Vec<String>> {
        let rows = self
            .query(
                "authority_owner_rows",
                DbStatement::with_params(
                    format!(
                        "SELECT from_id FROM edge_grants \
                         WHERE env = ? AND grant_kind = 'owner' AND grant_ref_id = 0 \
                           AND rules IS NULL AND status = 'approved' \
                           AND {} LIMIT 2",
                        self.to_id_eq()
                    )
                    .as_str(),
                    vec![self.env_param(), DbValue::from(bot_id)],
                ),
            )
            .await?;
        rows.iter()
            .map(|row| crate::required_string(row, "from_id"))
            .collect()
    }

    /// The single approved owner of an initialized bot.
    ///
    /// Missing or duplicated owners are integrity errors — a
    /// corrupt state must never surface as a deny (spec §3.2.2).
    async fn resolve_owner(&self, bot_id: &str) -> ServiceResult<String> {
        let owners = self.approved_owner_rows(bot_id).await?;
        match owners.len() {
            1 => Ok(owners.into_iter().next().expect("exactly 1 row")),
            0 => Err(owner_integrity_error(format!(
                "bot {bot_id} has no approved owner edge"
            ))),
            count => Err(owner_integrity_error(format!(
                "bot {bot_id} has {count} approved owner edges"
            ))),
        }
    }

    /// Approved owner counts for a chunk of bots. Bounded by the
    /// approved-owner uniqueness (≤1/bot) except for corrupt data,
    /// which the caller must reject as count ≠ 1.
    async fn owner_counts_in(&self, bots: &[&str]) -> ServiceResult<Vec<(String, usize)>> {
        let mut params = vec![self.env_param()];
        for bot in bots {
            params.push(DbValue::from(*bot));
        }
        let rows = self
            .query(
                "authority_owner_counts_chunk",
                DbStatement::with_params(
                    format!(
                        "SELECT to_id, from_id FROM edge_grants \
                         WHERE env = ? AND grant_kind = 'owner' AND grant_ref_id = 0 \
                           AND rules IS NULL AND status = 'approved' AND {}",
                        self.to_id_in(bots.len())
                    )
                    .as_str(),
                    params,
                ),
            )
            .await?;
        let mut counts: HashMap<String, usize> = HashMap::new();
        for row in &rows {
            let bot_id = crate::required_string(row, "to_id")?;
            *counts.entry(bot_id).or_insert(0) += 1;
        }
        Ok(counts.into_iter().collect())
    }

    /// Approved owner/manager edges of one actor on one bot
    /// (≤2 rows: unique key caps at one per kind).
    pub(super) async fn management_edges_for(
        &self,
        bot_id: &str,
        actor_id: &str,
    ) -> ServiceResult<Vec<BotAccessRelation>> {
        let rows = self
            .query(
                "authority_edges_for",
                DbStatement::with_params(
                    format!(
                        "SELECT grant_kind FROM edge_grants \
                         WHERE env = ? AND {} AND {} AND {} LIMIT 2",
                        Self::MANAGEMENT_EDGE_PREDICATE,
                        self.to_id_eq(),
                        self.from_id_eq()
                    )
                    .as_str(),
                    vec![
                        self.env_param(),
                        DbValue::from(bot_id),
                        DbValue::from(actor_id),
                    ],
                ),
            )
            .await?;
        edges_from_rows(&rows)
    }

    /// Per-actor edges for a chunk of bots (≤2 rows per (bot,
    /// user): the (from,to,env,kind,ref) unique key bounds this).
    async fn management_edges_for_chunk(
        &self,
        actor_id: &str,
        bots: &[&str],
    ) -> ServiceResult<Vec<(String, BotAccessRelation)>> {
        let mut params = vec![self.env_param()];
        for bot in bots {
            params.push(DbValue::from(*bot));
        }
        params.push(DbValue::from(actor_id));
        let rows = self
            .query(
                "authority_edges_chunk",
                DbStatement::with_params(
                    format!(
                        "SELECT to_id, grant_kind FROM edge_grants \
                         WHERE env = ? AND {} AND {} AND {}",
                        Self::MANAGEMENT_EDGE_PREDICATE,
                        self.to_id_in(bots.len()),
                        self.from_id_eq()
                    )
                    .as_str(),
                    params,
                ),
            )
            .await?;
        let mut edges = Vec::with_capacity(rows.len());
        for row in &rows {
            let bot_id = crate::required_string(row, "to_id")?;
            let kind = parse_management_kind(&crate::required_string(row, "grant_kind")?)?;
            edges.push((bot_id, kind));
        }
        Ok(edges)
    }

    /// Project one transfer row with the read-time effective
    /// status (pending past `expires_at` reads as expired; no
    /// write happens on GET, spec §6.4). `db_now_ms` is the
    /// shared read clock: the caller captures it ONCE per read
    /// (statement count stays bounded and filter/count/
    /// projection share one DB-time value, so a row at the
    /// expiry boundary cannot filter as expired but project
    /// as pending).
    /// Publish the row projection for the transfer
    /// write paths: their command outcomes reuse the
    /// exact same effective-status lens (one captured
    /// DB time per command; Task-1 fix).
    pub(super) async fn transfer_view_from_row(
        &self,
        row: &DbRow,
        db_now_ms: u64,
    ) -> ServiceResult<TransferView> {
        let stored_status = parse_transfer_status(&crate::required_string(row, "status")?)?;
        let requested_at = db_time_ms(row, "requested_at")?;
        let expires_at = db_time_ms(row, "expires_at")?;
        let updated_at = db_time_ms(row, "updated_at")?;
        let status = if stored_status == TransferStatus::Pending
            && db_now_ms >= expires_at
        {
            TransferStatus::Expired
        } else {
            stored_status
        };
        let decided_by_kind = match crate::optional_string(row, "decided_by_kind")? {
            Some(value) => Some(parse_actor_kind(&value)?),
            None => None,
        };
        Ok(TransferView {
            bot_id: crate::required_string(row, "bot_id")?,
            target_owner_actor_id: crate::required_string(row, "target_owner_actor_id")?,
            from_owner_actor_id: crate::required_string(row, "from_owner_actor_id")?,
            status,
            requested_at,
            expires_at,
            updated_at,
            decided_at: crate::optional_timestamp_text(row, "decided_at")?
                .map(|value| parse_required_epoch(&value, "decided_at"))
                .transpose()?,
            decided_by_kind,
            decided_by_id: crate::optional_string(row, "decided_by_id")?,
            reason: crate::optional_string(row, "reason")?,
            result_ownership_version: crate::optional_u64(row, "result_ownership_version")?,
            bot_name_snapshot: crate::optional_string(row, "bot_name_snapshot")?,
        })
    }

    /// The LIVE Human actor ids out of `actor_ids` (spec §3.1:
    /// the Human registry is the `bcs_bots` actor registry —
    /// `actor_kind='human'`, env-bound, not tombstoned; ids are
    /// actor ids byte-exact). Chunked to the collection-query
    /// bound so batch readers never degrade to per-user queries.
    async fn live_human_actors(
        &self,
        actor_ids: &[&str],
    ) -> ServiceResult<HashSet<String>> {
        let mut live = HashSet::with_capacity(actor_ids.len());
        for ids in actor_ids.chunks(CHUNK) {
            let params: Vec<DbValue> = ids
                .iter()
                .map(|actor| DbValue::from(*actor))
                .chain(std::iter::once(self.env_param()))
                .collect();
            let rows = self
                .query(
                    "authority_humans_alive",
                    DbStatement::with_params(
                        format!(
                            "SELECT bot_uuid FROM bcs_bots \
                             WHERE {} \
                               AND actor_kind = 'human' \
                               AND env = ? \
                               AND COALESCE(is_deleted, 0) = 0",
                            self.bot_id_in(ids.len())
                        )
                        .as_str(),
                        params,
                    ),
                )
                .await?;
            for row in &rows {
                live.insert(crate::required_string(row, "bot_uuid")?);
            }
        }
        Ok(live)
    }

    /// DB-time epoch-ms for the read-time expiry projection
    /// (shared time definition = 事务数据库时间).
    pub(super) async fn db_now_ms(&self) -> ServiceResult<u64> {
        let rows = self
            .query(
                "authority_db_now",
                DbStatement::new(format!("SELECT {} AS now_txt", self.db_now_utc()).as_str()),
            )
            .await?;
        let value = rows
            .first()
            .map(|row| crate::required_string(row, "now_txt"))
            .transpose()?
            .ok_or_else(|| {
                ServiceError::InternalError(
                    "bot authority: db time read returned no row".into(),
                )
            })?;
        parse_required_epoch(&value, "now")
    }

    /// Owner edges must point at Human actor ids; anything else is
    /// corrupt data (edges are Human → Bot by spec §3.2.4).
    fn to_id_in(&self, count: usize) -> String {
        let placeholders = self.id_placeholders(count);
        match self.flavor() {
            bcs_db_api::DbSqlFlavor::Mysql => {
                format!("CAST(to_id AS BINARY) IN ({placeholders})")
            }
            bcs_db_api::DbSqlFlavor::Sqlite => format!("to_id IN ({placeholders})"),
        }
    }

    /// Owner edges must point at Human actor ids; anything else is
    /// corrupt data (edges are Human → Bot by spec §3.2.4).
    fn human_user_id(&self, owner_actor_id: &str) -> ServiceResult<String> {
        human_user_id_of_actor(owner_actor_id).ok_or_else(|| {
            owner_integrity_error(format!(
                "owner edge subject '{owner_actor_id}' is not a Human actor id"
            ))
        })
    }

}

fn edges_from_rows(rows: &[DbRow]) -> ServiceResult<Vec<BotAccessRelation>> {
    let mut edges = Vec::with_capacity(rows.len());
    for row in rows {
        edges.push(parse_management_kind(&crate::required_string(row, "grant_kind")?)?);
    }
    Ok(edges)
}

pub(super) fn parse_required_epoch(value: &str, column: &'static str) -> ServiceResult<u64> {
    crate::parse_timestamp_epoch_ms(value).ok_or_else(|| {
        ServiceError::InternalError(format!(
            "bot authority: unparseable {column} timestamp '{value}'"
        ))
    })
}

fn db_time_ms(row: &DbRow, column: &'static str) -> ServiceResult<u64> {
    let value = crate::required_string(row, column)?;
    parse_required_epoch(&value, column)
}

fn parse_transfer_status(value: &str) -> ServiceResult<TransferStatus> {
    match value {
        "pending" => Ok(TransferStatus::Pending),
        "accepted" => Ok(TransferStatus::Accepted),
        "rejected" => Ok(TransferStatus::Rejected),
        "cancelled" => Ok(TransferStatus::Cancelled),
        "expired" => Ok(TransferStatus::Expired),
        "invalidated" => Ok(TransferStatus::Invalidated),
        other => Err(ServiceError::InternalError(format!(
            "unknown transfer status: {other}"
        ))),
    }
}

fn parse_actor_kind(value: &str) -> ServiceResult<AuthorityActorKind> {
    match value {
        "human" => Ok(AuthorityActorKind::Human),
        "service" => Ok(AuthorityActorKind::Service),
        "provider" => Ok(AuthorityActorKind::Provider),
        "system" => Ok(AuthorityActorKind::System),
        other => Err(ServiceError::InternalError(format!(
            "unknown authority actor kind: {other}"
        ))),
    }
}

fn parse_management_kind(value: &str) -> ServiceResult<BotAccessRelation> {
    match value {
        "owner" => Ok(BotAccessRelation::Owner),
        "manager" => Ok(BotAccessRelation::Manager),
        other => Err(ServiceError::InternalError(format!(
            "unknown management edge kind: {other}"
        ))),
    }
}
