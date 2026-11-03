//! Bot-authority projection on [`MemoryBotRepo`].
//!
//! Implements `BotAuthorityRepoPort` against the SAME shared
//! `MemoryBotRepo` state (own `RwLock` facet, per spec §7: Memory 的
//! Bot/Human/authority/转交/审计记录 共享一个原子状态边界 — the
//! facet below folds into the unified atomic boundary as the
//! lifecycle write paths land).
//!
//! Semantics mirror the SQL authority Store in
//! `bcs-edge-permission-store/src/authority/reads.rs` exactly:
//!
//! - strict role/ownership reads, owner completeness enforced only
//!   for initialized (version > 0) bots;
//! - byte-exact (case-sensitive) id comparison, edges keyed by env;
//! - only approved owner/manager edges in the unified shape
//!   (`grant_ref_id = 0`) count; runtime kinds never grant authority;
//! - transfer rows carry the spec §4.3 fields; pending rows project
//!   to expired past their `expires_at` read-time only;
//! - write commands mirror the SQL authority store
//!   (`managers` and the Task-3 transfer state machine
//!   below) inside one write-guard span per command.
//!
//! Env: like every other Memory repo read, this facet resolves the
//! process env (`bcs_config::resolve_env_str`) per call; the seeded
//! state is env-keyed so tests/fixtures drive isolation explicitly.

use std::collections::{BTreeSet, HashMap, HashSet};

use async_trait::async_trait;

use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityActorKind, AuthorityConflictCode, BotAccessRelation,
    BotOwnershipView, BotRoleQuery, ManagerEdit, ManagerEditAction, ManagerListQuery,
    ManagerPage, ManagerSetChange, ManagerSync, OwnershipChange, OwnershipSync,
    TransferAction, TransferChange, TransferCommand, TransferDirection, TransferKey,
    TransferListQuery, TransferOutcome, TransferPage, TransferStatus, TransferView,
    check_page_bounds, human_actor_id, human_user_id_of_actor,
};
use bcs_service_api::{ServiceError, ServiceResult};

pub(super) use bcs_config::resolve_env_str as resolve_env;
use tokio::sync::RwLock;
use tracing::warn;

use super::MemoryBotRepo;

/// Shared clock for the read-time expiry projection (ms).
pub(crate) fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

/// Edge kinds tracked by the memory projection. Runtime kinds are
/// only written by the conformance fixtures (runtime edges do not
/// otherwise exist in this projection yet) so the reads can prove
/// they never satisfy authority reads (A16 read direction).
#[allow(dead_code, reason = "runtime kinds exist only for A16 fixtures")]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum MemoryAuthorityEdgeKind {
    Owner,
    Manager,
    PermissionProfile,
    Rules,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MemoryAuthorityEdgeStatus {
    Approved,
    Revoked,
}

/// One transfer row in the memory projection (spec §4.3 fields).
/// `expected_ownership_version` and the decision fields are written
/// by the Task-3 transfer path; the Task-1 read path projects the
/// rest.
#[allow(dead_code, reason = "written by the Task-3 transfer path")]
#[derive(Debug, Clone)]
pub struct MemoryTransferRow {
    pub bot_id: String,
    pub target_owner_actor_id: String,
    pub from_owner_actor_id: String,
    pub expected_ownership_version: u64,
    pub status: TransferStatus,
    pub requested_at: u64,
    pub expires_at: u64,
    pub updated_at: u64,
    pub decided_at: Option<u64>,
    pub decided_by_kind: Option<AuthorityActorKind>,
    pub decided_by_id: Option<String>,
    pub reason: Option<String>,
    pub result_ownership_version: Option<u64>,
    pub bot_name_snapshot: Option<String>,
}

/// One manager-command audit row (spec §4.4) mirrored in
/// the memory projection so the write scenarios can pin
/// "no-op leaves no audit" and rollback semantics.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemoryAuthorityAuditRow {
    pub env: String,
    pub bot_id: String,
    pub mutation_id: String,
    pub sequence: u64,
    pub action: String,
    pub actor_kind: String,
    pub actor_id: String,
    pub subject_id: String,
    pub before_state: Option<String>,
    pub after_state: String,
    pub created_ms: u64,
}

/// Shared authority state bucket (same `MemoryBotRepo`
/// Arc). This is the single atomic boundary of the memory
/// projection (spec §7): write paths validate against a
/// staged clone of it and swap once on success; the
/// conformance fixtures drive the SAME fields — the
/// accessor is the fixture surface, not an application
/// API.
///
/// Human existence lives on the shared bots registry
/// (`MemoryBotRepo::bots` Human actor rows), not here:
/// the guards read a registry snapshot before they enter
/// this write-guard span.
#[derive(Debug, Default, Clone)]
pub struct MemoryAuthorityState {
    /// `ownership_version` per `(env, bot_id)`; absent = 0.
    pub ownership_versions: HashMap<(String, String), u64>,
    /// `(env, bot_id, actor_id, kind)` → (status, grant_ref_id).
    /// Approved authority edges carry `grant_ref_id = 0`.
    pub edges: HashMap<
        (String, String, String, MemoryAuthorityEdgeKind),
        (MemoryAuthorityEdgeStatus, u64),
    >,
    /// `(env, bot_id, target_actor)` → transfer row.
    pub transfers:
        HashMap<(String, String, String), MemoryTransferRow>,
    /// Bots the Provider COMMANDS own (metadata or
    /// binding membership): the ordinary retirement
    /// refuses them (two commands, two contracts;
    /// NEVER chained, spec §7.1).
    pub provider_managed: HashSet<(String, String)>,
    /// Permission-scope audit rows (spec §4.4), append-only.
    pub audit: Vec<MemoryAuthorityAuditRow>,
}

#[async_trait]
impl BotAuthorityRepoPort for MemoryBotRepo {
    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>> {
        let env = resolve_env();
        if self.deleted_bot_ids.read().await.contains(bot_id)
            || !self.bots.read().await.get(bot_id).is_some_and(|bot| bot.actor_kind == bcs_service_api::ActorKind::Bot)
        {
            return Ok(None); // missing / deleted bot denies
        }
        let version = self
            .authority
            .read()
            .await
            .ownership_versions
            .get(&(env.clone(), bot_id.to_string()))
            .copied()
            .unwrap_or(0);
        if version == 0 {
            // Migration-window shim: a version-0 Bot answers the
            // legacy created_by arm; a miss denies as before. The
            // live-Human registry gate deliberately does not apply
            // here (unmigrated operators predate that registry);
            // version > 0 states are never shadowed.
            if self
                    .legacy_created_by_fallback
                    .load(std::sync::atomic::Ordering::SeqCst) {
                // created_by is the raw legacy User ID; compare
                // against the actor id's human suffix (arbitrary
                // non-prefixed actor strings never match).
                let creator = self
                    .bots
                    .read()
                    .await
                    .get(bot_id)
                    .and_then(|bot| bot.created_by.clone());
                if user_id.strip_prefix("human_").is_some_and(|user| creator.as_deref() == Some(user)) {
                    return Ok(Some(BotAccessRelation::Owner));
                }
            }
            return Ok(None); // uninitialized denies
        }
        let state = self.authority.read().await;
        // Owner completeness is part of the state: corrupt states
        // error out for uninvolved principals too (spec §3.2.2).
        let owner_count = approved_owner_count(&state, &env, bot_id);
        let owner = if owner_count != 1 {
            return Err(owner_integrity_error(bot_id, owner_count));
        } else {
            state
                .edges
                .iter()
                .find(|(key, (status, ref_id))| {
                    key.0 == env
                        && key.1 == bot_id
                        && key.3 == MemoryAuthorityEdgeKind::Owner
                        && *status == MemoryAuthorityEdgeStatus::Approved
                        && *ref_id == 0
                })
                .map(|(key, _)| key.2.clone())
                .expect("count promised one owner")
        };
        if human_user_id_of_actor(&owner).is_none() {
            return Err(owner_integrity_error_message(format!(
                "owner edge subject '{owner}' is not a Human actor id"
            )));
        }
        // spec §3.1: only a LIVE Human actor authorizes
        // through an edge — a deleted or never-ensured
        // operator denies; lifecycle is an ordinary deny,
        // not an integrity error.
        if !memory_human_alive(self, user_id).await {
            return Ok(None);
        }
        Ok(match approved_edge_kind(&state, &env, bot_id, user_id) {
            Some(MemoryAuthorityEdgeKind::Owner) => Some(BotAccessRelation::Owner),
            Some(MemoryAuthorityEdgeKind::Manager) => Some(BotAccessRelation::Manager),
            _ => None,
        })
    }

    async fn roles_for(
        &self,
        queries: &[BotRoleQuery],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        // In process the whole state is one snapshot: read it once.
        let env = resolve_env();
        let state = self.authority.read().await;
        let mut results = Vec::with_capacity(queries.len());
        for query in queries {
            if self.deleted_bot_ids.read().await.contains(&query.bot_id)
                || !self.bots.read().await.get(&query.bot_id).is_some_and(|bot| bot.actor_kind == bcs_service_api::ActorKind::Bot)
            {
                results.push(None);
                continue;
            }
            let version = state
                .ownership_versions
                .get(&(env.clone(), query.bot_id.clone()))
                .copied()
                .unwrap_or(0);
            if version == 0 {
                // Migration-window shim, same arm as the single role read.
                let legacy = if self
                    .legacy_created_by_fallback
                    .load(std::sync::atomic::Ordering::SeqCst) {
                    self.bots
                        .read()
                        .await
                        .get(&query.bot_id)
                        .and_then(|bot| bot.created_by.clone())
                        .zip(query.user_id.strip_prefix("human_"))
                        .filter(|(creator, legacy_user)| creator == legacy_user)
                        .map(|_| BotAccessRelation::Owner)
                } else {
                    None
                };
                results.push(legacy);
                continue;
            }
            let owner_count = approved_owner_count(&state, &env, &query.bot_id);
            // Integrity first (spec §3.2.2): a corrupt owner
            // state errors the batch for every principal.
            match owner_count {
                1 => {}
                0 => {
                    return Err(owner_integrity_error(&query.bot_id, 0));
                }
                count => {
                    return Err(owner_integrity_error(&query.bot_id, count));
                }
            }
            // spec §3.1: only live Human actors resolve any
            // relation; a tombstoned or never-ensured
            // operator denies per query.
            if !memory_human_alive(self, &query.user_id).await {
                results.push(None);
                continue;
            }
            let edges = approved_edges(&state, &env, &query.bot_id, &query.user_id);
            results.push(match edges.first().copied() {
                Some(MemoryAuthorityEdgeKind::Owner) => Some(BotAccessRelation::Owner),
                Some(MemoryAuthorityEdgeKind::Manager) => Some(BotAccessRelation::Manager),
                _ => None,
            });
        }
        Ok(results)
    }

    async fn ownership(&self, bot_id: &str) -> ServiceResult<BotOwnershipView> {
        let env = resolve_env();
        if self.deleted_bot_ids.read().await.contains(bot_id)
            || !self.bots.read().await.contains_key(bot_id)
        {
            return Err(ServiceError::BotNotFound(bot_id.to_string()));
        }
        let version = self
            .authority
            .read()
            .await
            .ownership_versions
            .get(&(env.clone(), bot_id.to_string()))
            .copied()
            .unwrap_or(0);
        if version == 0 {
            return Err(not_initialized_error(bot_id));
        }
        let state = self.authority.read().await;
        let owner_count = approved_owner_count(&state, &env, bot_id);
        if owner_count != 1 {
            return Err(owner_integrity_error(bot_id, owner_count));
        }
        let owner = state
            .edges
            .iter()
            .find(|(key, (status, ref_id))| {
                // Env-scoped like every other read: another
                // env's owner identity must never leak.
                key.0 == env
                    && key.1 == bot_id
                    && key.3 == MemoryAuthorityEdgeKind::Owner
                    && *status == MemoryAuthorityEdgeStatus::Approved
                    && *ref_id == 0
            })
            .map(|(key, _)| key.2.clone())
            .expect("count promised one owner");
        Ok(BotOwnershipView {
            bot_id: bot_id.to_string(),
            owner_user_id: human_user_id_of_actor(&owner).ok_or_else(|| {
                owner_integrity_error_message(format!(
                    "owner edge subject '{owner}' is not a Human actor id"
                ))
            })?,
            owner_actor_id: owner,
        })
    }

    async fn managers(&self, query: ManagerListQuery) -> ServiceResult<ManagerPage> {
        check_page_bounds(query.offset, query.limit)?;
        let env = resolve_env();
        if self.deleted_bot_ids.read().await.contains(&query.bot_id)
            || !self.bots.read().await.contains_key(&query.bot_id)
        {
            return Err(ServiceError::BotNotFound(query.bot_id.clone()));
        }
        let version = self
            .authority
            .read()
            .await
            .ownership_versions
            .get(&(env.clone(), query.bot_id.clone()))
            .copied()
            .unwrap_or(0);
        if version == 0 {
            return Err(not_initialized_error(&query.bot_id));
        }
        let state = self.authority.read().await;
        let owner_count = approved_owner_count(&state, &env, &query.bot_id);
        if owner_count != 1 {
            return Err(owner_integrity_error(&query.bot_id, owner_count));
        }
        let owner = state
            .edges
            .iter()
            .find(|(key, (status, ref_id))| {
                // Env-scoped like every other read: another
                // env's owner identity must never leak.
                key.0 == env
                    && key.1 == query.bot_id
                    && key.3 == MemoryAuthorityEdgeKind::Owner
                    && *status == MemoryAuthorityEdgeStatus::Approved
                    && *ref_id == 0
            })
            .map(|(key, _)| key.2.clone())
            .expect("count promised one owner");
        let mut managers: Vec<String> = state
            .edges
            .iter()
            .filter(|(key, (status, ref_id))| {
                key.0 == env
                    && key.1 == query.bot_id
                    && key.3 == MemoryAuthorityEdgeKind::Manager
                    && *status == MemoryAuthorityEdgeStatus::Approved
                    && *ref_id == 0
            })
            .map(|(key, _)| key.2.clone())
            .collect();
        managers.sort();
        managers.dedup();
        let owner_ref = owner.clone();
        let owner_label = human_user_id_of_actor(&owner_ref).ok_or_else(|| {
            owner_integrity_error_message(format!(
                "owner edge subject '{owner_ref}' is not a Human actor id"
            ))
        })?;
        let total = managers.len() as u64;
        let items = managers
            .into_iter()
            .skip(query.offset.min(total) as usize)
            .take(query.limit as usize)
            .map(|actor| {
                human_user_id_of_actor(&actor).ok_or_else(|| {
                    owner_integrity_error_message(format!(
                        "manager edge subject '{actor}' is not a Human actor id"
                    ))
                })
            })
            .collect::<ServiceResult<Vec<_>>>()?;
        Ok(ManagerPage {
            owner: BotOwnershipView {
                bot_id: query.bot_id,
                owner_user_id: owner_label,
                owner_actor_id: owner,
            },
            items,
            total,
        })
    }

    async fn transfer(&self, key: &TransferKey) -> ServiceResult<Option<TransferView>> {
        let state = self.authority.read().await;
        transfer_view(&state, key)
    }

    async fn transfers(&self, query: TransferListQuery) -> ServiceResult<TransferPage> {
        check_page_bounds(query.offset, query.limit)?;
        let env = resolve_env();
        let state = self.authority.read().await;
        let now = now_ms();
        let mut views: Vec<TransferView> = state
            .transfers
            .iter()
            .filter(|(key, row)| {
                if key.0 != env {
                    return false;
                }
                let hit = match query.direction {
                    TransferDirection::Received => {
                        row.target_owner_actor_id == query.user_id
                    }
                    TransferDirection::Sent => row.from_owner_actor_id == query.user_id,
                };
                if !hit {
                    return false;
                }
                if !query.statuses.is_empty() {
                    query.statuses.contains(&effective_status(row, now))
                } else {
                    true
                }
            })
            .map(|(_key, row)| view_from_row(row, now))
            .collect();
        views.sort_by(|left, right| {
            right
                .requested_at
                .cmp(&left.requested_at)
                .then_with(|| left.bot_id.cmp(&right.bot_id))
                .then_with(|| {
                    left.target_owner_actor_id
                        .cmp(&right.target_owner_actor_id)
                })
        });
        let total = views.len() as u64;
        let items = views
            .into_iter()
            .skip(query.offset.min(total) as usize)
            .take(query.limit as usize)
            .collect();
        Ok(TransferPage { items, total })
    }

    async fn edit_manager(&self, command: ManagerEdit) -> ServiceResult<ManagerSetChange> {
        manager_edit_impl(self, command).await
    }

    async fn sync_managers(&self, command: ManagerSync) -> ServiceResult<ManagerSetChange> {
        manager_sync_impl(self, command).await
    }

    async fn sync_ownership(&self, command: OwnershipSync) -> ServiceResult<OwnershipChange> {
        super::ownership_sync::ownership_sync_impl(self, command).await
    }

    async fn change_transfer(&self, command: TransferCommand) -> ServiceResult<TransferOutcome> {
        transfer_change_impl(self, command).await
    }
}

// ==================== Task-2 manager write paths ====================
//
// Both commands acquire the shared boundary's write
// guard exactly ONCE per command and stage/validate/
// mutate/swap inside that span: the whole command is
// pure in-memory computation, so no interleaved
// command can validate against a stale base and
// silently discard another writer's committed change.

/// Max deduped users per sync request (spec §5.2) — the
/// memory projection enforces the same input/stock caps as
/// the SQL store for harness parity.
const MANAGER_INPUT_CAP: usize = 1_000;
const MANAGER_STOCK_BOUND: u64 = 10_000;

pub(super) fn memory_human_unknown(detail: String) -> ServiceError {
    ServiceError::InvalidOperation {
        message: format!(
            "{}: {detail}",
            bcs_service_api::application::v1::bot_authority::ERROR_BOT_AUTHORITY_HUMAN_UNKNOWN
        ),
        request_id: None,
    }
}

pub(super) fn memory_operator_forbidden(detail: &str) -> ServiceError {
    ServiceError::Forbidden(format!(
        "{}: {detail}",
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN
    ))
}

fn memory_input_limit() -> ServiceError {
    ServiceError::InvalidOperation {
        message: format!(
            "{}: a synced manager set exceeds the 1,000 deduped-user input cap",
            bcs_service_api::application::v1::bot_authority::ERROR_BOT_MANAGER_INPUT_LIMIT
        ),
        request_id: None,
    }
}

fn memory_stock_limit(count: u64) -> ServiceError {
    ServiceError::Conflict(format!(
        "{}: bot's current manager set has {count} rows, beyond \
         the frozen 10,000 stock bound; the whole request is rejected",
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_MANAGER_STOCK_LIMIT
    ))
}

fn memory_owner_not_manager_target() -> ServiceError {
    ServiceError::Conflict(format!(
        "{}: the owner is not addressable as a manager target",
        bcs_service_api::application::v1::bot_authority::ERROR_OWNER_NOT_MANAGER
    ))
}

fn memory_audit_conflict(mutation_id: &str, sequence: u64) -> ServiceError {
    ServiceError::InternalError(format!(
        "bot authority memory store: audit row already exists for \
         mutation {mutation_id}, sequence {sequence}"
    ))
}

impl MemoryBotRepo {
    /// The shared atomic authority state boundary (spec
    /// §7). Contract fixtures drive the SAME fields the
    /// production facets read and write — this accessor is
    /// the fixture surface, not an application API.
    pub fn authority_state(&self) -> &RwLock<MemoryAuthorityState> {
        &self.authority
    }

    /// Fixture surface (same role as [`Self::authority_state`]):
    /// seed one live Human ACTOR row on the shared bots
    /// registry, in the explicitly given env. The
    /// human-existence guards read THIS registry (spec
    /// §3.1: a Human exists iff its `human_<user_id>` actor
    /// row is live in the bound env), so the fixtures seed
    /// the same row shape the production
    /// `ensure_human_actor` path creates.
    pub async fn seed_human_actor_in(&self, env: &str, user_id: &str) {
        use bcs_service_api::{ActorStatus, BotCapabilities};

        let bot_id = human_actor_id(user_id);
        let mut bots = self.bots.write().await;
        if let Some(existing) = bots.get_mut(&bot_id) {
            existing.env = Some(env.to_string());
        } else {
            bots.insert(
                bot_id.clone(),
                super::RegisteredBotInner {
                    bot_id: bot_id.clone(),
                    last_heartbeat: std::time::Instant::now(),
                    capabilities: BotCapabilities {
                        name: Some(user_id.to_string()),
                        visibility: "protected".to_string(),
                        ..Default::default()
                    },
                    ws_connection: None,
                    // A persisted-session row so the registry's
                    // expiry sweep never reaps a fixture Human.
                    session_token: Some(format!(
                        "fixture-token-{env}-{bot_id}",
                    )),
                    env: Some(env.to_string()),
                    status: ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Human,
                    created_by: Some(user_id.to_string()),
                    protocol_version: 1,
                    user_visibility: Default::default(),
                    friend_ext: Default::default(),
                    friend_check_in_strategy: Default::default(),
                },
            );
        }
        // A re-seed RESTORES a previously tombstoned row (a
        // seeded Human is live, matching the insert-only
        // seeding semantics the identity-set fixture had).
        self.deleted_bot_ids.write().await.remove(&bot_id);
    }

    /// Fixture surface: mark one Human ACTOR row tombstoned
    /// (the departed-Human scenario; env participates only
    /// through the seeded row's env field, tombstones are
    /// id-keyed like the memory repo's own soft delete).
    pub async fn remove_human_actor_in(&self, user_id: &str) {
        self.deleted_bot_ids
            .write()
            .await
            .insert(human_actor_id(user_id));
    }
}

pub(super) struct MemoryWriteContext {
    pub env: String,
    pub bot_id: String,
}

/// Bot existence + tombstone check shared by both write
/// commands. Deliberately completed BEFORE the single
/// write-guard span so no other-lock await happens while
/// holding the shared atomic boundary.
pub(super) async fn memory_require_live_bot(
    repo: &MemoryBotRepo,
    bot_id: &str,
) -> ServiceResult<MemoryWriteContext> {
    let env = resolve_env();
    if repo.deleted_bot_ids.read().await.contains(bot_id)
        || !repo.bots.read().await.contains_key(bot_id)
    {
        return Err(ServiceError::BotNotFound(bot_id.to_string()));
    }
    Ok(MemoryWriteContext {
        env,
        bot_id: bot_id.to_string(),
    })
}

/// Soft live-bot classification for the lenient sync
/// (spec §5.2 ruling): `Ok(None)` means missing/tombstoned
/// and the caller returns success zeros with a warn log;
/// the single-item edit and the transfer paths keep the
/// strict erroring variant above.
pub(super) async fn memory_bot_ctx_opt(
    repo: &MemoryBotRepo,
    bot_id: &str,
) -> ServiceResult<Option<MemoryWriteContext>> {
    let env = resolve_env();
    if repo.deleted_bot_ids.read().await.contains(bot_id)
        || !repo.bots.read().await.contains_key(bot_id)
    {
        return Ok(None);
    }
    Ok(Some(MemoryWriteContext {
        env,
        bot_id: bot_id.to_string(),
    }))
}

/// Human ACTOR registry snapshot (user ids) read BEFORE
/// the command's write-guard span: the guards need the
/// shared bots map, and the documented lock order is
/// The LIVE Human registry projection (spec §3.1): the
/// shared bots map is the Human ACTOR registry —
/// `actor_kind == Human`, env-bound, not tombstoned.
///
/// Callers read it INSIDE their authority write-guard
/// span (lock order authority → bots → deleted IDs; no
/// writer ever takes authority while holding bots, so
/// awaiting the reads here cannot deadlock). Read-side
/// authorization (`role`/`roles_for`) uses the same
/// registry per principal through `memory_human_alive`.
pub(super) async fn memory_live_humans(
    repo: &MemoryBotRepo,
) -> HashSet<String> {
    let env = resolve_env();
    let tombstoned = repo.deleted_bot_ids.read().await;
    let bots = repo.bots.read().await;
    bots.iter()
        .filter(|(id, bot)| {
            bot.actor_kind == bcs_service_api::ActorKind::Human
                && bot.env.as_deref() == Some(env.as_str())
                && !tombstoned.contains(*id)
        })
        .filter_map(|(id, _)| human_user_id_of_actor(id))
        .collect()
}

/// One principal's live-Human fact from the actor
/// registry (same registry as [`memory_live_humans`]):
/// the actor id is Human-shaped, a live actor row exists
/// — Human kind, env-bound — and no tombstone covers it.
async fn memory_human_alive(repo: &MemoryBotRepo, actor_id: &str) -> bool {
    if human_user_id_of_actor(actor_id).is_none() {
        return false;
    }
    let env = resolve_env();
    if repo.deleted_bot_ids.read().await.contains(actor_id) {
        return false;
    }
    match repo.bots.read().await.get(actor_id) {
        Some(bot) => {
            bot.actor_kind == bcs_service_api::ActorKind::Human
                && bot.env.as_deref() == Some(env.as_str())
        }
        None => false,
    }
}

/// Version + owner-completeness validation, run against
/// the staged clone INSIDE the command's single
/// write-guard span.
pub(super) fn memory_require_initialized(
    staged: &MemoryAuthorityState,
    ctx: &MemoryWriteContext,
) -> ServiceResult<u64> {
    let version = staged
        .ownership_versions
        .get(&(ctx.env.clone(), ctx.bot_id.clone()))
        .copied()
        .unwrap_or(0);
    if version == 0 {
        return Err(not_initialized_error(&ctx.bot_id));
    }
    let owner_count = approved_owner_count(staged, &ctx.env, &ctx.bot_id);
    if owner_count != 1 {
        return Err(owner_integrity_error(&ctx.bot_id, owner_count));
    }
    Ok(version)
}

/// Owner actor id of the bot; integrity errors preserve
/// the strict-read semantics.
pub(super) fn memory_state_owner(state: &MemoryAuthorityState, ctx: &MemoryWriteContext) -> String {
    state
        .edges
        .iter()
        .find(|(key, (status, ref_id))| {
            key.0 == ctx.env
                && key.1 == ctx.bot_id
                && key.3 == MemoryAuthorityEdgeKind::Owner
                && *status == MemoryAuthorityEdgeStatus::Approved
                && *ref_id == 0
        })
        .map(|(key, _)| key.2.clone())
        .expect("count promised one owner")
}

/// Sorted approved manager set, owner excluded — the
/// same owner-exclusion predicate as the SQL
/// `pre_read_current_set` (Task-0 review carry-over).
fn memory_approved_managers(
    state: &MemoryAuthorityState,
    ctx: &MemoryWriteContext,
    owner: &str,
) -> Vec<String> {
    let mut members: Vec<String> = state
        .edges
        .iter()
        .filter(|(key, (status, ref_id))| {
            key.0 == ctx.env
                && key.1 == ctx.bot_id
                && key.2 != owner
                && key.3 == MemoryAuthorityEdgeKind::Manager
                && *status == MemoryAuthorityEdgeStatus::Approved
                && *ref_id == 0
        })
        .map(|(key, _)| key.2.clone())
        .collect();
    members.sort();
    members
}

/// Validate + normalize the sync input (spec §5.2, the
/// lenient-sync ruling; harness parity with the SQL
/// store): dedup FIRST over the WHOLE raw input and count
/// the 1,000 cap on that deduped original — including
/// members the filter below will skip — then split into
/// the valid (`human_*` actor shapes, sorted) and the
/// skipped members (unknown shapes never fail the sync).
fn memory_normalize_input(
    user_ids: &[String],
) -> ServiceResult<(Vec<String>, Vec<String>)> {
    let deduped: BTreeSet<String> = user_ids.iter().cloned().collect();
    if deduped.len() > MANAGER_INPUT_CAP {
        return Err(memory_input_limit());
    }
    let mut valid = Vec::with_capacity(deduped.len());
    let mut skipped = Vec::new();
    for user_id in deduped {
        if human_user_id_of_actor(&user_id).is_some() {
            valid.push(user_id);
        } else {
            skipped.push(user_id);
        }
    }
    Ok((valid, skipped))
}

async fn manager_edit_impl(
    repo: &MemoryBotRepo,
    command: ManagerEdit,
) -> ServiceResult<ManagerSetChange> {
    // Human-only command (spec §5.1); trusted
    // services use the dedicated sync command.
    let operator_actor_id = match &command.mutation.actor {
        AuthorityActor::Human { user_id } => human_actor_id(user_id),
        _ => {
            return Err(memory_operator_forbidden(
                "single-item manager edit requires a Human actor; \
                 trusted services must use the dedicated sync command",
            ));
        }
    };
    let target_actor = human_user_id_of_actor(&command.user_id)
        .map(|user| human_actor_id(&user))
        .ok_or_else(|| {
            memory_human_unknown(format!(
                "manager target '{}' is not a Human actor id",
                command.user_id
            ))
        })?;
    let ctx = memory_require_live_bot(repo, &command.bot_id).await?;
    // One write guard spans the whole command (staged
    // clone → validate → mutate → swap): a concurrent
    // command cannot interleave and lose an update.
    let mut state_guard = repo.authority.write().await;
    // The Human registry is read INSIDE the span (lock
    // order authority → bots → deleted IDs, mirrors the
    // SQL store's in-transaction guards): a tombstone
    // racing the guard can never leak an edge to a
    // deleted Human.
    let live_humans = memory_live_humans(repo).await;
    let mut staged = state_guard.clone();
    memory_require_initialized(&staged, &ctx)?;
    let current_owner = memory_state_owner(&staged, &ctx);
    if current_owner == target_actor {
        return Err(memory_owner_not_manager_target());
    }
    let operator_edge = staged.edges.get(&(
        ctx.env.clone(),
        ctx.bot_id.clone(),
        operator_actor_id.clone(),
        MemoryAuthorityEdgeKind::Owner,
    ));
    let operator_manager_edge = staged.edges.get(&(
        ctx.env.clone(),
        ctx.bot_id.clone(),
        operator_actor_id.clone(),
        MemoryAuthorityEdgeKind::Manager,
    ));
    let operator_current = matches!(
        operator_edge,
        Some((MemoryAuthorityEdgeStatus::Approved, 0))
    ) || matches!(
        operator_manager_edge,
        Some((MemoryAuthorityEdgeStatus::Approved, 0))
    );
    if !operator_current {
        return Err(memory_operator_forbidden(&format!(
            "operator '{operator_actor_id}' holds no current owner/manager edge"
        )));
    }
    // Operator-survival gate (spec §3.1): the edge alone
    // authorizes nothing once the operator's Human ACTOR
    // row is gone — a tombstoned operator holding a
    // residual edge and a valid credential must not
    // grant or revoke manager rights. The snapshot is
    // read INSIDE this write span (the SQL store's
    // in-transaction guard shape), so a deletion racing
    // the command can never leak an edge.
    let operator_user =
        human_user_id_of_actor(&operator_actor_id).expect("operator shape");
    if !live_humans.contains(&operator_user) {
        return Err(memory_operator_forbidden(&format!(
            "operator '{operator_actor_id}' is not a live Human actor"
        )));
    }

    let target_current =
        staged
            .edges
            .get(&(
                ctx.env.clone(),
                ctx.bot_id.clone(),
                target_actor.clone(),
                MemoryAuthorityEdgeKind::Manager,
            ))
            .copied();
    // audit row writes use the mutation metadata.
    let actor_kind = command.mutation.actor.kind().as_str().to_string();
    let actor_id = command.mutation.actor.actor_id();
    let audit = (command.mutation.mutation_id.clone(), actor_kind, actor_id);

    let (added, removed) = match (command.action, target_current) {
        (ManagerEditAction::Grant, Some((MemoryAuthorityEdgeStatus::Approved, _))) => {
            return Ok(ManagerSetChange {
                added: 0,
                removed: 0,
                total: memory_state_total(&staged, &ctx),
            });
        }
        (ManagerEditAction::Revoke, Some((MemoryAuthorityEdgeStatus::Revoked, _))) => {
            return Ok(ManagerSetChange {
                added: 0,
                removed: 0,
                total: memory_state_total(&staged, &ctx),
            });
        }
        (ManagerEditAction::Revoke, None) => {
            return Ok(ManagerSetChange {
                added: 0,
                removed: 0,
                total: memory_state_total(&staged, &ctx),
            });
        }
        (ManagerEditAction::Grant, Some((MemoryAuthorityEdgeStatus::Revoked, _))) => {
            let target_user = human_user_id_of_actor(&target_actor)
                .expect("validated actor shape");
            // Strict edit target (spec §5.1): a live Human
            // ACTOR row must exist in the registry snapshot.
            if !live_humans.contains(&target_user) {
                return Err(memory_human_unknown(format!(
                    "manager target '{target_user}' is not a valid Human"
                )));
            }
            staged.edges.insert(
                (
                    ctx.env.clone(),
                    ctx.bot_id.clone(),
                    target_actor.clone(),
                    MemoryAuthorityEdgeKind::Manager,
                ),
                (MemoryAuthorityEdgeStatus::Approved, 0),
            );
            memory_append_audit(
                &mut staged,
                &ctx,
                "manager_restore",
                &audit.0,
                &audit.1,
                &audit.2,
                &target_actor,
                Some("revoked"),
                "approved",
                0,
            )?;
            (1, 0)
        }
        (ManagerEditAction::Grant, None) => {
            let target_user = human_user_id_of_actor(&target_actor)
                .expect("validated actor shape");
            // Strict edit target (spec §5.1): a live Human
            // ACTOR row must exist in the registry snapshot.
            if !live_humans.contains(&target_user) {
                return Err(memory_human_unknown(format!(
                    "manager target '{target_user}' is not a valid Human"
                )));
            }
            staged.edges.insert(
                (
                    ctx.env.clone(),
                    ctx.bot_id.clone(),
                    target_actor.clone(),
                    MemoryAuthorityEdgeKind::Manager,
                ),
                (MemoryAuthorityEdgeStatus::Approved, 0),
            );
            memory_append_audit(
                &mut staged,
                &ctx,
                "manager_grant",
                &audit.0,
                &audit.1,
                &audit.2,
                &target_actor,
                None,
                "approved",
                0,
            )?;
            (1, 0)
        }
        (ManagerEditAction::Revoke, Some((MemoryAuthorityEdgeStatus::Approved, _))) => {
            staged.edges.insert(
                (
                    ctx.env.clone(),
                    ctx.bot_id.clone(),
                    target_actor.clone(),
                    MemoryAuthorityEdgeKind::Manager,
                ),
                (MemoryAuthorityEdgeStatus::Revoked, 0),
            );
            memory_append_audit(
                &mut staged,
                &ctx,
                "manager_revoke",
                &audit.0,
                &audit.1,
                &audit.2,
                &target_actor,
                Some("approved"),
                "revoked",
                0,
            )?;
            (0, 1)
        }
    };
    let total = memory_state_total(&staged, &ctx);
    // Single atomic swap inside the write-guard span —
    // nothing half-written can leak and no committed
    // change can be lost to an interleaved command.
    *state_guard = staged;
    Ok(ManagerSetChange { added, removed, total })
}

fn memory_state_total(state: &MemoryAuthorityState, ctx: &MemoryWriteContext) -> u64 {
    let owner = memory_state_owner(state, ctx);
    memory_approved_managers(state, ctx, &owner).len() as u64
}

/// Append one audit row; a duplicated
/// `(env, mutation_id, sequence)` row fails (the
/// memory counterpart of the UNIQUE constraint).
#[allow(clippy::too_many_arguments)]
pub(super) fn memory_append_audit(
    state: &mut MemoryAuthorityState,
    ctx: &MemoryWriteContext,
    action: &str,
    mutation_id: &str,
    actor_kind: &str,
    actor_id: &str,
    subject_id: &str,
    before_state: Option<&str>,
    after_state: &str,
    sequence: u64,
) -> ServiceResult<()> {
    if state.audit.iter().any(|row| {
        row.env == ctx.env
            && row.mutation_id == mutation_id
            && row.sequence == sequence
    }) {
        return Err(memory_audit_conflict(mutation_id, sequence));
    }
    state.audit.push(MemoryAuthorityAuditRow {
        env: ctx.env.clone(),
        bot_id: ctx.bot_id.clone(),
        mutation_id: mutation_id.to_string(),
        sequence,
        action: action.to_string(),
        actor_kind: actor_kind.to_string(),
        actor_id: actor_id.to_string(),
        subject_id: subject_id.to_string(),
        before_state: before_state.map(str::to_string),
        after_state: after_state.to_string(),
        created_ms: now_ms(),
    });
    Ok(())
}

async fn manager_sync_impl(
    repo: &MemoryBotRepo,
    command: ManagerSync,
) -> ServiceResult<ManagerSetChange> {
    // sync 可信应用专用 (spec §5.2).
    if command.mutation.actor.kind() != AuthorityActorKind::Service {
        return Err(memory_operator_forbidden(
            "full-set manager sync is trusted-service only; \
             Human operators use the single-item manager commands",
        ));
    }
    let (typed_input, malformed) = memory_normalize_input(&command.user_ids)?;
    let mutation_id = command.mutation.mutation_id.clone();
    if !malformed.is_empty() {
        warn!(
            env = %resolve_env(),
            bot_id = %command.bot_id,
            mutation_id = %mutation_id,
            skipped = ?malformed,
            "manager sync skipped non-Human-actor input members"
        );
    }
    // Lenient target classification (spec §5.2): a
    // missing/tombstoned bot is a zeros no-op with a warn
    // log; version 0 (inside the span below) still
    // hard-rejects.
    let Some(ctx) = memory_bot_ctx_opt(repo, &command.bot_id).await? else {
        warn!(
            env = %resolve_env(),
            bot_id = %command.bot_id,
            mutation_id = %mutation_id,
            input_size = command.user_ids.len(),
            "manager sync target bot is missing; returning zeros"
        );
        return Ok(ManagerSetChange {
            added: 0,
            removed: 0,
            total: 0,
        });
    };
    // One write guard spans the whole command (staged
    // clone → validate → mutate → swap).
    let mut state_guard = repo.authority.write().await;
    // Live-Human partition facts read INSIDE the span
    // (lock order authority → bots → deleted IDs, the
    // memory twin of the SQL in-transaction guards): a
    // tombstone racing the guard can never leak an edge
    // to a deleted Human.
    let live_humans = memory_live_humans(repo).await;
    let mut staged = state_guard.clone();
    memory_require_initialized(&staged, &ctx)?;
    let current_owner = memory_state_owner(&staged, &ctx);
    let input_owner_excluded: Vec<String> = typed_input
        .iter()
        .filter(|actor| *actor != &current_owner)
        .cloned()
        .collect();
    // Lenient partition (spec §5.2): members with no live
    // Human ACTOR row are filtered out of the applied
    // remainder with a warn log — the final set always
    // equals the valid remainder, so an unknown-only input
    // clears the set.
    let mut skipped_users: Vec<String> = Vec::new();
    let mut input: Vec<String> = Vec::new();
    for actor in &input_owner_excluded {
        let user = human_user_id_of_actor(actor)
            .expect("normalized actor shape");
        if live_humans.contains(&user) {
            input.push(actor.clone());
        } else {
            skipped_users.push(user);
        }
    }
    if !skipped_users.is_empty() {
        warn!(
            env = %resolve_env(),
            bot_id = %command.bot_id,
            mutation_id = %mutation_id,
            skipped = ?skipped_users,
            "manager sync skipped input members with no live \
             Human actor row"
        );
    }
    let current = memory_approved_managers(&staged, &ctx, &current_owner);
    if current.len() as u64 > MANAGER_STOCK_BOUND {
        return Err(memory_stock_limit(current.len() as u64));
    }
    let current_set: HashSet<&String> =
        current.iter().collect();
    let input_set: HashSet<&String> = input.iter().collect();
    let mut removed: Vec<String> = current
        .iter()
        .filter(|actor| !input_set.contains(*actor))
        .cloned()
        .collect();
    removed.sort();
    let mut added: Vec<String> = input
        .iter()
        .filter(|actor| !current_set.contains(*actor))
        .cloned()
        .collect();
    added.sort();
    let change = ManagerSetChange {
        added: added.len() as u64,
        removed: removed.len() as u64,
        total: input.len() as u64,
    };

    let actor_kind = command.mutation.actor.kind().as_str().to_string();
    let actor_id = command.mutation.actor.actor_id();
    let mutation_id = command.mutation.mutation_id.clone();
    // Sequences: removed (sorted) first, then added
    // (sorted) — same sorted-diff shape as the SQL
    // store.
    let mut sequence = 0u64;
    for member in &removed {
        staged.edges.insert(
            (
                ctx.env.clone(),
                ctx.bot_id.clone(),
                member.clone(),
                MemoryAuthorityEdgeKind::Manager,
            ),
            (MemoryAuthorityEdgeStatus::Revoked, 0),
        );
        memory_append_audit(
            &mut staged,
            &ctx,
            "manager_revoke",
            &mutation_id,
            &actor_kind,
            &actor_id,
            member,
            Some("approved"),
            "revoked",
            sequence,
        )?;
        sequence += 1;
    }
    for member in &added {
        // The adds are a subset of the valid (registry-filtered)
        // remainder — no second existence pass here.
        let edge = (
            ctx.env.clone(),
            ctx.bot_id.clone(),
            member.clone(),
            MemoryAuthorityEdgeKind::Manager,
        );
        let restoring = staged
            .edges
            .get(&edge)
            .copied()
            == Some((MemoryAuthorityEdgeStatus::Revoked, 0));
        staged.edges.insert(edge, (MemoryAuthorityEdgeStatus::Approved, 0));
        if restoring {
            memory_append_audit(
                &mut staged,
                &ctx,
                "manager_restore",
                &mutation_id,
                &actor_kind,
                &actor_id,
                member,
                Some("revoked"),
                "approved",
                sequence,
            )?;
        } else {
            memory_append_audit(
                &mut staged,
                &ctx,
                "manager_grant",
                &mutation_id,
                &actor_kind,
                &actor_id,
                member,
                None,
                "approved",
                sequence,
            )?;
        }
        sequence += 1;
    }

    // Single atomic swap inside the write-guard span —
    // a failure above discarded the staged clone, and no
    // interleaved command can lose this commit.
    *state_guard = staged;
    Ok(change)
}

// ==================== Task-3 transfer write path ====================
//
// One write-guard span per command (`clone →
// validate → mutate → swap`), mirroring the SQL
// transaction guards at the state-map level: same
// static actor gates, same §6.4 classification, same
// lazy closures committed BEFORE the conflict is
// returned, same audit actions and JSON state
// strings. No other lock or await is taken inside
// the span — the whole command is pure in-memory
// computation, so concurrent commands commit-
// serialize like the SQL Bot-row lock (spec §7).
//
// The single span also closes the pre-read-to-
// write window structurally: classification and
// mutation observe and mutate ONE cloned state
// under one exclusive guard, so a command racing
// a same-key recreation is ALWAYS classified
// against the winner's committed state (review
// Important#1 — the SQL_create typed per-row
// guard parity is not needed here by
// construction).

/// The audit state string shares its exact spelling
/// with the SQL store (harness-pinned).
pub(super) fn transfer_audit_state(
    status: &str,
    from_owner_actor_id: &str,
    version: u64,
) -> String {
    format!(
        "{{\"status\":\"{status}\",\"from_owner\":\"{from_owner_actor_id}\",\"version\":{version}}}"
    )
}

/// Decisions on a missing row / initiation targeting
/// the current owner: a clean state denial (Task 5
/// maps the pinned code to 409).
fn transfer_state_denial(detail: String) -> ServiceError {
    ServiceError::Conflict(format!(
        "{}: {detail}",
        AuthorityConflictCode::TransferStateConflict.as_str()
    ))
}

/// Fixed system identity for in-command lazy
/// closures (spec §4.4; mirrors the SQL store).
pub(super) const CLOSING_SYSTEM_ACTOR: &str = "system_transfer_close";

/// Refresh window (spec §4.3: expires_at = 本轮
/// requested_at + 七天).
const SEVEN_DAYS_MS: u64 = 7 * 24 * 3600 * 1_000;

/// Effective-now status: pending past expiry reads
/// expired WITHOUT being rewritten (the GET lens;
/// Task-1).
fn effective_transfer_status(
    row: &MemoryTransferRow,
    now: u64,
) -> TransferStatus {
    if row.status == TransferStatus::Pending && now >= row.expires_at {
        TransferStatus::Expired
    } else {
        row.status
    }
}

/// Lazy-closure kinds of a dead pending (spec §6.4).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum MemoryClosureKind {
    Expire,
    InvalidateOwnerChanged,
}

impl MemoryClosureKind {
    fn status_str(self) -> &'static str {
        match self {
            MemoryClosureKind::Expire => "expired",
            MemoryClosureKind::InvalidateOwnerChanged => "invalidated",
        }
    }

    fn row_status(self) -> TransferStatus {
        match self {
            MemoryClosureKind::Expire => TransferStatus::Expired,
            MemoryClosureKind::InvalidateOwnerChanged => {
                TransferStatus::Invalidated
            }
        }
    }

    fn audit_action(self) -> &'static str {
        match self {
            MemoryClosureKind::Expire => "transfer_expire",
            MemoryClosureKind::InvalidateOwnerChanged => {
                "transfer_invalidate"
            }
        }
    }

    fn reason(self) -> &'static str {
        match self {
            MemoryClosureKind::Expire => "transfer_expired",
            MemoryClosureKind::InvalidateOwnerChanged => "owner_changed",
        }
    }

}

/// Static actor gates per action (spec §6.1 dual
/// admission; mirrors the SQL Store's gates). `None`
/// actor id = the trusted-app decision (no Human
/// identity).
fn transfer_operator(
    command: &TransferCommand,
) -> ServiceResult<(Option<String>, AuthorityActorKind)> {
    let target = command.key.target_owner_actor_id.clone();
    human_user_id_of_actor(&target).ok_or_else(|| {
        memory_human_unknown(format!(
            "transfer target '{target}' is not a Human actor id"
        ))
    })?;
    let actor = &command.mutation.actor;
    match (actor, command.action) {
        (
            AuthorityActor::Human { user_id },
            TransferAction::RequestOrRefresh,
        ) => Ok((Some(human_actor_id(user_id)), AuthorityActorKind::Human)),
        (AuthorityActor::Human { user_id }, TransferAction::Accept)
        | (AuthorityActor::Human { user_id }, TransferAction::Reject) => {
            if human_actor_id(user_id) != target {
                return Err(memory_operator_forbidden(&format!(
                    "transfer decision by '{}' does not match the \
                     target '{target}'",
                    human_actor_id(user_id)
                )));
            }
            Ok((Some(human_actor_id(user_id)), AuthorityActorKind::Human))
        }
        (AuthorityActor::Service { .. }, TransferAction::Accept)
        | (AuthorityActor::Service { .. }, TransferAction::Reject) => {
            Ok((Some(actor.actor_id()), AuthorityActorKind::Service))
        }
        (AuthorityActor::Human { user_id }, TransferAction::Cancel) => {
            Ok((Some(human_actor_id(user_id)), AuthorityActorKind::Human))
        }
        _ => Err(memory_operator_forbidden(&format!(
            "{:?} actors may not perform transfer {:?} on bot {}",
            actor.kind(),
            command.action,
            command.key.bot_id
        ))),
    }
}

/// An other-target pending row that still blocks
/// the initiation (the `transfer_pending`
/// conflict source): LIVE means fresh at `now`
/// AND matching the current round.
fn other_valid_pending(
    row: &MemoryTransferRow,
    now: u64,
    owner: &str,
    version: u64,
) -> bool {
    row.status == TransferStatus::Pending
        && now < row.expires_at
        && row.from_owner_actor_id == owner
        && row.expected_ownership_version == version
}

/// One edge write on the staged state: set the edge
/// row to the wanted status (insert when it does not
/// exist yet — the memory projection has no separate
/// revoked-row reuse requirement beyond status).
pub(super) fn memory_set_edge(
    staged: &mut MemoryAuthorityState,
    ctx: &MemoryWriteContext,
    actor: &str,
    kind: MemoryAuthorityEdgeKind,
    status: MemoryAuthorityEdgeStatus,
) {
    staged.edges.insert(
        (
            ctx.env.clone(),
            ctx.bot_id.clone(),
            actor.to_string(),
            kind,
        ),
        (status, 0),
    );
}

/// The Task-3 transfer command on the memory
/// projection. Outcome contract identical to the SQL
/// store (harness parity; plan §3).
async fn transfer_change_impl(
    repo: &MemoryBotRepo,
    command: TransferCommand,
) -> ServiceResult<TransferOutcome> {
    let TransferCommand {
        key,
        action,
        mutation,
    } = command;
    let (operator_actor, operator_kind) = transfer_operator(&TransferCommand {
        key: key.clone(),
        action,
        mutation: mutation.clone(),
    })?;
    // Live-bot gate before the span (same shape as
    // the manager commands).
    let ctx = memory_require_live_bot(repo, &key.bot_id).await?;
    // One write guard spans the whole command (staged
    // clone → validate → mutate → swap).
    let mut state_guard = repo.authority.write().await;
    // The target-survival registry read lives INSIDE the
    // span (lock order authority → bots → deleted IDs),
    // matching the SQL in-transaction survival guards.
    let live_humans = memory_live_humans(repo).await;
    let now = now_ms();
    let mut staged = state_guard.clone();
    let version = memory_require_initialized(&staged, &ctx)?;
    let owner = memory_state_owner(&staged, &ctx);
    // The target must remain a valid surviving Human
    // (spec §6.2/§6.3): a live Human ACTOR row in the
    // registry snapshot.
    let target_user =
        human_user_id_of_actor(&key.target_owner_actor_id)
            .expect("validated target shape");
    if !live_humans.contains(&target_user) {
        return Err(memory_human_unknown(format!(
            "transfer target '{}' is not a valid Human",
            key.target_owner_actor_id
        )));
    }
    // A HUMAN operator must be a live Human as well (spec
    // §3.1, read against the SAME in-span snapshot): the
    // residual edge of a tombstoned operator grants no
    // initiation, cancellation, or decision.
    if let Some(operator) = operator_actor.as_deref() {
        if let Some(operator_user) = human_user_id_of_actor(operator) {
            if !live_humans.contains(&operator_user) {
                return Err(memory_operator_forbidden(&format!(
                    "transfer operator '{operator}' is not a live Human actor"
                )));
            }
        }
    }
    let mutation_id = mutation.mutation_id.clone();
    let actor_kind = operator_kind.as_str();
    let actor_id = operator_actor
        .clone()
        .unwrap_or_else(|| mutation.actor.actor_id());

    let row_key = (
        ctx.env.clone(),
        key.bot_id.clone(),
        key.target_owner_actor_id.clone(),
    );

    match action {
        TransferAction::RequestOrRefresh => {
            // Initiation policy: Human current-owner only
            // (spec §6.2).
            let Some(operator) = operator_actor.clone() else {
                return Err(memory_operator_forbidden(
                    "transfer initiation is a Human (current-owner) \
                     command",
                ));
            };
            if operator != owner {
                return Err(memory_operator_forbidden(&format!(
                    "transfer initiation requires the current owner; \
                     '{operator}' is not '{owner}'"
                )));
            }
            if key.target_owner_actor_id == owner {
                return Err(transfer_state_denial(format!(
                    "transfer target '{}' is the current owner of bot '{}'",
                    key.target_owner_actor_id, key.bot_id
                )));
            }
            let row = staged.transfers.get(&row_key).cloned();
            let effective =
                row.as_ref().map(|row| effective_transfer_status(row, now));
            let (over_row, closure, change) = match (&row, effective) {
                (None, _) => (false, None, TransferChange::Created),
                (Some(_), Some(TransferStatus::Pending)) => {
                    let row = row.as_ref().expect("pending row");
                    if row.from_owner_actor_id == owner
                        && row.expected_ownership_version == version
                    {
                        // Valid same-snapshot pending →
                        // refresh (spec §6.2).
                        let refreshed = MemoryTransferRow {
                            requested_at: now,
                            expires_at: now + SEVEN_DAYS_MS,
                            updated_at: now,
                            ..row.clone()
                        };
                        memory_append_audit(
                            &mut staged,
                            &ctx,
                            "transfer_refresh",
                            &mutation_id,
                            actor_kind,
                            &actor_id,
                            &key.target_owner_actor_id,
                            None,
                            &transfer_audit_state(
                                "pending", &owner, version,
                            ),
                            0,
                        )?;
                        staged.transfers.insert(row_key, refreshed);
                        // Single atomic swap; the staged
                        // state is the committed one.
                        *state_guard = staged;
                        let view = state_guard
                            .transfers
                            .get(&(
                                ctx.env.clone(),
                                key.bot_id.clone(),
                                key.target_owner_actor_id.clone(),
                            ))
                            .map(|row| view_from_row(row, now))
                            .expect("row just written");
                        return Ok(TransferOutcome::Applied {
                            transfer: view,
                            change: TransferChange::Updated,
                        });
                    }
                    // Valid pending with a mismatched
                    // snapshot: close invalidated first.
                    (true, Some(MemoryClosureKind::InvalidateOwnerChanged), TransferChange::Created)
                }
                (_, Some(TransferStatus::Expired))
                    if row
                        .as_ref()
                        .map(|row| {
                            row.status == TransferStatus::Pending
                        })
                        .unwrap_or(false) =>
                {
                    // Stored pending past expiry: close
                    // expired first.
                    (true, Some(MemoryClosureKind::Expire), TransferChange::Created)
                }
                (Some(_), _) => {
                    // Terminal: same-target
                    // re-initiation reuses the row.
                    (true, None, TransferChange::Created)
                }
            };
            // Every other-target pending classifies
            // (spec §6.2): a LIVE one (fresh AND matching
            // the current round) blocks the initiation —
            // a no-write conflict needs no commit. A DEAD
            // one (expired, or owner/version mismatched)
            // is closed BEFORE the new round is created,
            // mirroring the SQL store's in-transaction
            // closure: the per-bot pending slot must not
            // keep answering for a stale round.
            let mut live_other: Option<MemoryTransferRow> =
                None;
            let mut dead_others: Vec<(
                (String, String, String),
                MemoryClosureKind,
                MemoryTransferRow,
            )> = Vec::new();
            for (other_key, other_row) in staged.transfers.iter()
            {
                if other_key.0 != ctx.env
                    || other_key.1 != ctx.bot_id
                    || other_key.2 == key.target_owner_actor_id
                    || other_row.status
                        != TransferStatus::Pending
                {
                    continue;
                }
                if other_valid_pending(
                    other_row,
                    now,
                    &owner,
                    version,
                ) {
                    if live_other.is_none() {
                        live_other = Some(other_row.clone());
                    }
                    continue;
                }
                let kind = if now >= other_row.expires_at {
                    // Past expiry (the same kind
                    // precedence the same-target
                    // closure uses).
                    MemoryClosureKind::Expire
                } else {
                    // Fresh by time; the round no
                    // longer matches (ABA).
                    MemoryClosureKind::InvalidateOwnerChanged
                };
                dead_others
                    .push((other_key.clone(), kind, other_row.clone()));
            }
            // Deterministic audit order (the SQL
            // pre-read orders by target).
            dead_others.sort_by(|a, b| a.0.2.cmp(&b.0.2));
            if let Some(other) = live_other {
                let view = view_from_row(&other, now);
                return Ok(TransferOutcome::Conflict {
                    transfer: view,
                    code: AuthorityConflictCode::TransferPending,
                });
            }
            let mut sequence = 0u64;
            if let Some(kind) = closure {
                let row = row.as_ref().expect("closure needs the row");
                memory_append_audit(
                    &mut staged,
                    &ctx,
                    kind.audit_action(),
                    &mutation_id,
                    AuthorityActorKind::System.as_str(),
                    CLOSING_SYSTEM_ACTOR,
                    &key.target_owner_actor_id,
                    Some(
                        &transfer_audit_state(
                            "pending",
                            &row.from_owner_actor_id,
                            row.expected_ownership_version,
                        )
                        .clone(),
                    ),
                    &transfer_audit_state(
                        kind.status_str(),
                        &row.from_owner_actor_id,
                        row.expected_ownership_version,
                    ),
                    sequence,
                )?;
                staged.transfers.insert(
                    row_key.clone(),
                    MemoryTransferRow {
                        status: kind.row_status(),
                        decided_at: Some(now),
                        decided_by_kind: Some(AuthorityActorKind::System),
                        decided_by_id: Some(
                            CLOSING_SYSTEM_ACTOR.to_string(),
                        ),
                        reason: Some(kind.reason().to_string()),
                        updated_at: now,
                        ..row.clone()
                    },
                );
                sequence += 1;
            }
            for (dead_key, dead_kind, dead_row) in &dead_others {
                memory_append_audit(
                    &mut staged,
                    &ctx,
                    dead_kind.audit_action(),
                    &mutation_id,
                    AuthorityActorKind::System.as_str(),
                    CLOSING_SYSTEM_ACTOR,
                    &dead_key.2,
                    Some(
                        &transfer_audit_state(
                            "pending",
                            &dead_row.from_owner_actor_id,
                            dead_row.expected_ownership_version,
                        )
                        .clone(),
                    ),
                    &transfer_audit_state(
                        dead_kind.status_str(),
                        &dead_row.from_owner_actor_id,
                        dead_row.expected_ownership_version,
                    ),
                    sequence,
                )?;
                staged.transfers.insert(
                    dead_key.clone(),
                    MemoryTransferRow {
                        status: dead_kind.row_status(),
                        decided_at: Some(now),
                        decided_by_kind: Some(
                            AuthorityActorKind::System,
                        ),
                        decided_by_id: Some(
                            CLOSING_SYSTEM_ACTOR.to_string(),
                        ),
                        reason: Some(dead_kind.reason().to_string()),
                        updated_at: now,
                        ..dead_row.clone()
                    },
                );
                sequence += 1;
            }
            let next_row = match row.as_ref() {
                Some(row) => MemoryTransferRow {
                    from_owner_actor_id: owner.clone(),
                    expected_ownership_version: version,
                    status: TransferStatus::Pending,
                    requested_at: now,
                    expires_at: now + SEVEN_DAYS_MS,
                    updated_at: now,
                    decided_at: None,
                    decided_by_kind: None,
                    decided_by_id: None,
                    reason: None,
                    result_ownership_version: None,
                    bot_name_snapshot: None,
                    ..row.clone()
                },
                None => MemoryTransferRow {
                    bot_id: key.bot_id.clone(),
                    target_owner_actor_id: key
                        .target_owner_actor_id
                        .clone(),
                    from_owner_actor_id: owner.clone(),
                    expected_ownership_version: version,
                    status: TransferStatus::Pending,
                    requested_at: now,
                    expires_at: now + SEVEN_DAYS_MS,
                    updated_at: now,
                    decided_at: None,
                    decided_by_kind: None,
                    decided_by_id: None,
                    reason: None,
                    result_ownership_version: None,
                    bot_name_snapshot: None,
                },
            };
            memory_append_audit(
                &mut staged,
                &ctx,
                "transfer_request",
                &mutation_id,
                actor_kind,
                &actor_id,
                &key.target_owner_actor_id,
                None,
                &transfer_audit_state("pending", &owner, version),
                sequence,
            )?;
            staged.transfers.insert(row_key.clone(), next_row);
            // Single atomic swap inside the span.
            *state_guard = staged;
            let view = state_guard
                .transfers
                .get(&(
                    ctx.env.clone(),
                    key.bot_id.clone(),
                    key.target_owner_actor_id.clone(),
                ))
                .map(|row| view_from_row(row, now))
                .expect("row just written");
            let _ = over_row;
            Ok(TransferOutcome::Applied {
                transfer: view,
                change,
            })
        }
        TransferAction::Accept
        | TransferAction::Reject
        | TransferAction::Cancel => {
            transfer_decision_memory(
                &mut staged,
                &mut state_guard,
                &ctx,
                &row_key,
                action,
                operator_actor.as_deref(),
                operator_kind,
                owner.as_str(),
                version,
                &mutation_id,
                &actor_id,
                now,
            )
        }
    }
}

/// The decision half of the transfer command (spec
/// §6.3/§6.4). Runs inside the caller's single
/// write-guard span: `staged` is mutated and swapped
/// over `state_guard` only when a real change
/// commits.
#[allow(clippy::too_many_arguments)]
fn transfer_decision_memory(
    staged: &mut MemoryAuthorityState,
    state_guard: &mut MemoryAuthorityState,
    ctx: &MemoryWriteContext,
    row_key: &(String, String, String),
    action: TransferAction,
    operator_actor: Option<&str>,
    operator_kind: AuthorityActorKind,
    owner: &str,
    version: u64,
    mutation_id: &str,
    actor_id: &str,
    now: u64,
) -> ServiceResult<TransferOutcome> {
    let target = row_key.2.clone();
    let Some(row) = staged.transfers.get(row_key).cloned() else {
        return Err(transfer_state_denial(format!(
            "no transfer row exists for bot '{}' and target '{target}'",
            ctx.bot_id
        )));
    };
    if action == TransferAction::Cancel {
        // 重复取消只核验本行发起 Human；操作 pending 的
        // 取消还必须核验当前 owner (spec §6.4).
        let identity_ok = operator_kind == AuthorityActorKind::Human
            && operator_actor == Some(row.from_owner_actor_id.as_str());
        let still_owner = operator_kind == AuthorityActorKind::Human
            && operator_actor == Some(owner);
        let on_live_pending = row.status == TransferStatus::Pending
            && effective_transfer_status(&row, now)
                == TransferStatus::Pending;
        if !identity_ok || (on_live_pending && !still_owner) {
            return Err(memory_operator_forbidden(&format!(
                "transfer cancel by '{:?}' is not the row initiator",
                operator_actor
            )));
        }
    }
    let effective = effective_transfer_status(&row, now);
    match effective {
        TransferStatus::Pending => {
            if row.from_owner_actor_id != owner
                || row.expected_ownership_version != version
            {
                // Owner/version mismatch: close
                // invalidated, audit committed BEFORE the
                // conflict returns (spec §6.4).
                memory_append_audit(
                    staged,
                    ctx,
                    "transfer_invalidate",
                    mutation_id,
                    AuthorityActorKind::System.as_str(),
                    CLOSING_SYSTEM_ACTOR,
                    &target,
                    Some(transfer_audit_state(
                        "pending",
                        &row.from_owner_actor_id,
                        row.expected_ownership_version,
                    ).as_str()),
                    &transfer_audit_state(
                        "invalidated",
                        &row.from_owner_actor_id,
                        row.expected_ownership_version,
                    ),
                    0,
                )?;
                let closed = MemoryTransferRow {
                    status: TransferStatus::Invalidated,
                    decided_at: Some(now),
                    decided_by_kind: Some(AuthorityActorKind::System),
                    decided_by_id: Some(CLOSING_SYSTEM_ACTOR.to_string()),
                    reason: Some("owner_changed".to_string()),
                    updated_at: now,
                    ..row.clone()
                };
                staged.transfers.insert(row_key.clone(), closed);
                *state_guard = staged.clone();
                let view = state_guard
                    .transfers
                    .get(row_key)
                    .map(|row| view_from_row(row, now))
                    .expect("row just closed");
                return Ok(TransferOutcome::Conflict {
                    transfer: view,
                    code: AuthorityConflictCode::OwnerChanged,
                });
            }
            apply_decision_memory(
                staged,
                state_guard,
                ctx,
                row_key,
                action,
                operator_kind,
                owner,
                version,
                mutation_id,
                actor_id,
                &row,
                now,
            )?
        }
        TransferStatus::Expired if row.status == TransferStatus::Pending => {
            memory_append_audit(
                staged,
                ctx,
                "transfer_expire",
                mutation_id,
                AuthorityActorKind::System.as_str(),
                CLOSING_SYSTEM_ACTOR,
                &target,
                Some(transfer_audit_state(
                    "pending",
                    &row.from_owner_actor_id,
                    row.expected_ownership_version,
                ).as_str()),
                &transfer_audit_state(
                    "expired",
                    &row.from_owner_actor_id,
                    row.expected_ownership_version,
                ),
                0,
            )?;
            let closed = MemoryTransferRow {
                status: TransferStatus::Expired,
                decided_at: Some(now),
                decided_by_kind: Some(AuthorityActorKind::System),
                decided_by_id: Some(CLOSING_SYSTEM_ACTOR.to_string()),
                reason: Some("transfer_expired".to_string()),
                updated_at: now,
                ..row.clone()
            };
            staged.transfers.insert(row_key.clone(), closed);
            *state_guard = staged.clone();
            let view = state_guard
                .transfers
                .get(row_key)
                .map(|row| view_from_row(row, now))
                .expect("row just closed");
            return Ok(TransferOutcome::Conflict {
                transfer: view,
                code: AuthorityConflictCode::TransferExpired,
            });
        }
        TransferStatus::Expired => {
            return memory_terminal_conflict(
                state_guard,
                staged,
                row_key,
                &row,
                now,
                AuthorityConflictCode::TransferExpired,
            );
        }
        TransferStatus::Invalidated => {
            return memory_terminal_conflict(
                state_guard,
                staged,
                row_key,
                &row,
                now,
                AuthorityConflictCode::OwnerChanged,
            );
        }
        TransferStatus::Accepted
        | TransferStatus::Rejected
        | TransferStatus::Cancelled => {
            let repeat = matches!(
                (row.status, action),
                (TransferStatus::Accepted, TransferAction::Accept)
                    | (TransferStatus::Rejected, TransferAction::Reject)
                    | (TransferStatus::Cancelled, TransferAction::Cancel)
            );
            if repeat {
                // Same decision repeated by an eligible
                // caller: 200-style no-write Unchanged.
                return Ok(TransferOutcome::Applied {
                    transfer: view_from_row(&row, now),
                    change: TransferChange::Unchanged,
                });
            }
            let view = view_from_row(&row, now);
            return Ok(TransferOutcome::Conflict {
                transfer: view,
                code: AuthorityConflictCode::TransferStateConflict,
            });
        }
    };
    let view = state_guard
        .transfers
        .get(row_key)
        .map(|row| view_from_row(row, now))
        .expect("row just decided");
    Ok(TransferOutcome::Applied {
        transfer: view,
        change: TransferChange::Updated,
    })
}

/// A no-write terminal conflict re-read from the
/// committed state (nothing swapped).
fn memory_terminal_conflict(
    state_guard: &MemoryAuthorityState,
    _staged: &MemoryAuthorityState,
    row_key: &(String, String, String),
    row: &MemoryTransferRow,
    now: u64,
    code: AuthorityConflictCode,
) -> ServiceResult<TransferOutcome> {
    let view = state_guard
        .transfers
        .get(row_key)
        .map(|committed| view_from_row(committed, now))
        .unwrap_or_else(|| view_from_row(row, now));
    Ok(TransferOutcome::Conflict { transfer: view, code })
}

/// Apply an accept/reject/cancel decision to the
/// staged state (spec §6.3 for accept: old owner edge
/// revoked, target owner edge created/restored,
/// version +1, the new owner's manager edge removed,
/// the old owner added as a normal manager, row
/// accepted, audit). Other managers are untouched.
#[allow(clippy::too_many_arguments)]
fn apply_decision_memory(
    staged: &mut MemoryAuthorityState,
    state_guard: &mut MemoryAuthorityState,
    ctx: &MemoryWriteContext,
    row_key: &(String, String, String),
    action: TransferAction,
    operator_kind: AuthorityActorKind,
    owner: &str,
    version: u64,
    mutation_id: &str,
    actor_id: &str,
    row: &MemoryTransferRow,
    now: u64,
) -> ServiceResult<TransferStatus> {
    let target = row_key.2.clone();
    let (status, audit_action) = match action {
        TransferAction::Accept => {
            // Edge-state classification on the staged
            // clone (mirrors the SQL pre-read + guards:
            // the staged state IS the guard).
            let target_owner_edge = staged.edges.get(&(
                ctx.env.clone(),
                ctx.bot_id.clone(),
                target.clone(),
                MemoryAuthorityEdgeKind::Owner,
            ));
            match target_owner_edge {
                Some((MemoryAuthorityEdgeStatus::Approved, _)) => {
                    return Err(owner_integrity_error_message(format!(
                        "bot {} target '{target}' already holds an \
                         approved owner edge",
                        ctx.bot_id
                    )));
                }
                Some((MemoryAuthorityEdgeStatus::Revoked, _)) => {
                    // Restore the revoked owner row.
                    memory_set_edge(
                        staged,
                        ctx,
                        &target,
                        MemoryAuthorityEdgeKind::Owner,
                        MemoryAuthorityEdgeStatus::Approved,
                    );
                }
                None => {
                    memory_set_edge(
                        staged,
                        ctx,
                        &target,
                        MemoryAuthorityEdgeKind::Owner,
                        MemoryAuthorityEdgeStatus::Approved,
                    );
                }
            }
            // Old owner: revoke the owner edge.
            memory_set_edge(
                staged,
                ctx,
                owner,
                MemoryAuthorityEdgeKind::Owner,
                MemoryAuthorityEdgeStatus::Revoked,
            );
            // Old owner becomes a normal manager
            // (approved edges stay, revoked rows restore,
            // absent rows insert).
            memory_set_edge(
                staged,
                ctx,
                owner,
                MemoryAuthorityEdgeKind::Manager,
                MemoryAuthorityEdgeStatus::Approved,
            );
            // The new owner's manager edge is removed
            // (spec §6.3 step 3) so no stale
            // owner+manager edge can resurface.
            memory_set_edge(
                staged,
                ctx,
                &target,
                MemoryAuthorityEdgeKind::Manager,
                MemoryAuthorityEdgeStatus::Revoked,
            );
            // Version +1 — the only command that grows
            // the internal version (spec §4.2).
            staged
                .ownership_versions
                .insert(
                    (ctx.env.clone(), ctx.bot_id.clone()),
                    version + 1,
                );
            ("accepted", "transfer_accept")
        }
        TransferAction::Reject => ("rejected", "transfer_reject"),
        TransferAction::Cancel => ("cancelled", "transfer_cancel"),
        TransferAction::RequestOrRefresh => {
            unreachable!("apply covers decisions only")
        }
    };
    let decided_status = if action == TransferAction::Accept {
        TransferStatus::Accepted
    } else if action == TransferAction::Reject {
        TransferStatus::Rejected
    } else {
        TransferStatus::Cancelled
    };
    let decided = MemoryTransferRow {
        status: decided_status,
        decided_at: Some(now),
        decided_by_kind: Some(operator_kind),
        decided_by_id: Some(actor_id.to_string()),
        result_ownership_version: if action == TransferAction::Accept {
            Some(version + 1)
        } else {
            None
        },
        updated_at: now,
        ..row.clone()
    };
    memory_append_audit(
        staged,
        ctx,
        audit_action,
        mutation_id,
        operator_kind.as_str(),
        actor_id,
        &target,
        Some(transfer_audit_state(
            "pending",
            &row.from_owner_actor_id,
            row.expected_ownership_version,
        ).as_str()),
        &transfer_audit_state(
            status,
            if action == TransferAction::Accept {
                &target
            } else {
                &row.from_owner_actor_id
            },
            if action == TransferAction::Accept {
                version + 1
            } else {
                version
            },
        ),
        0,
    )?;
    staged
        .transfers
        .insert(row_key.clone(), decided);
    // Single atomic swap inside the span.
    *state_guard = staged.clone();
    Ok(decided_status)
}

// Helpers shared with the facet's write paths to come.
fn approved_owner_count(
    state: &MemoryAuthorityState,
    env: &str,
    bot_id: &str,
) -> usize {
    state
        .edges
        .iter()
        .filter(|(key, (status, ref_id))| {
            key.0 == env
                && key.1 == bot_id
                && key.3 == MemoryAuthorityEdgeKind::Owner
                && *status == MemoryAuthorityEdgeStatus::Approved
                && *ref_id == 0
        })
        .count()
}

fn approved_edges(
    state: &MemoryAuthorityState,
    env: &str,
    bot_id: &str,
    actor_id: &str,
) -> Vec<MemoryAuthorityEdgeKind> {
    let mut kinds: Vec<MemoryAuthorityEdgeKind> = state
        .edges
        .iter()
        .filter(|(key, (status, ref_id))| {
            key.0 == env
                && key.1 == bot_id
                && key.2 == actor_id
                && *status == MemoryAuthorityEdgeStatus::Approved
                && *ref_id == 0
                && matches!(
                    key.3,
                    MemoryAuthorityEdgeKind::Owner | MemoryAuthorityEdgeKind::Manager
                )
        })
        .map(|(key, _)| key.3)
        .collect();
    // Owner first, matching the SQL reader's union order.
    kinds.sort_by_key(|kind| match kind {
        MemoryAuthorityEdgeKind::Owner => 0,
        MemoryAuthorityEdgeKind::Manager => 1,
        _ => 2,
    });
    kinds
}

fn approved_edge_kind(
    state: &MemoryAuthorityState,
    env: &str,
    bot_id: &str,
    actor_id: &str,
) -> Option<MemoryAuthorityEdgeKind> {
    approved_edges(state, env, bot_id, actor_id)
        .into_iter()
        .next()
}

fn effective_status(row: &MemoryTransferRow, now_ms: u64) -> TransferStatus {
    if row.status == TransferStatus::Pending && now_ms >= row.expires_at {
        TransferStatus::Expired
    } else {
        row.status
    }
}

fn view_from_row(row: &MemoryTransferRow, now_ms: u64) -> TransferView {
    TransferView {
        bot_id: row.bot_id.clone(),
        target_owner_actor_id: row.target_owner_actor_id.clone(),
        from_owner_actor_id: row.from_owner_actor_id.clone(),
        status: effective_status(row, now_ms),
        requested_at: row.requested_at,
        expires_at: row.expires_at,
        updated_at: row.updated_at,
        decided_at: row.decided_at,
        decided_by_kind: row.decided_by_kind,
        decided_by_id: row.decided_by_id.clone(),
        reason: row.reason.clone(),
        result_ownership_version: row.result_ownership_version,
        bot_name_snapshot: row.bot_name_snapshot.clone(),
    }
}

fn transfer_view(
    state: &MemoryAuthorityState,
    key: &TransferKey,
) -> ServiceResult<Option<TransferView>> {
    let env = resolve_env();
    Ok(state
        .transfers
        .get(&(env, key.bot_id.clone(), key.target_owner_actor_id.clone()))
        .map(|row| view_from_row(row, now_ms())))
}

pub(super) fn owner_integrity_error(bot_id: &str, owner_count: usize) -> ServiceError {
    owner_integrity_error_message(if owner_count == 0 {
        format!("bot {bot_id} has no approved owner edge")
    } else {
        format!("bot {bot_id} has {owner_count} approved owner edges")
    })
}

pub(super) fn owner_integrity_error_message(detail: String) -> ServiceError {
    ServiceError::InternalError(format!(
        "{}: {}",
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_OWNER_INTEGRITY,
        detail
    ))
}

pub(super) fn not_initialized_error(bot_id: &str) -> ServiceError {
    ServiceError::Conflict(format!(
        "{}: bot {} not initialized in this environment",
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_AUTHORITY_NOT_INITIALIZED,
        bot_id
    ))
}
