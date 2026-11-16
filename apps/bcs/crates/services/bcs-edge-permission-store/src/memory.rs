//! In-process implementations of the four edge-permission repository ports.
//!
//! These back the **in-memory bootstrap path** (`BcsServer::new` /
//! `new_allowing_private_outbound_for_tests`) and unit tests, so the
//! single-process server runs the same edge-permission `ConnectService` the
//! DB-backed deployments run.
//!
//! Scope note: unlike `bcs-friend-store::MemoryFriendRepo`, these stores do
//! **not** persist to `bots_base_dir`. They are process-local by design: the
//! in-memory server is a dev/test convenience, and the durable
//! `edge_grants` / `permission_profiles` / `permission_requests` tables remain
//! the source of truth in every deployment profile.
//!
//! Semantics follow the SQL store in [`crate`] (`DbEdgeGrantStore`,
//! `DbPermissionProfileStore`, `DbPermissionRequestStore`):
//!
//! - grants are unique on `(from_id, to_id, env, grant_kind, grant_ref_id)`
//!   and the insert upserts: the conflicting row is refreshed in place and
//!   keeps its id (a re-add after a revoke reactivates the revoked edge);
//! - a *friend* edge is an approved `PermissionProfile` edge whose
//!   `grant_ref_id` is the peer's active default profile id (D12);
//! - request `gmt_create`/`gmt_modified` are store-managed timestamps.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_domain::edge_permission::{
    BotActorConfig, EdgeGrant, EdgeStatus, GrantKind, PermissionProfile, PermissionRequest,
    ProfileStatus, RequestKind, RequestStatus,
};
use bcs_service_api::port::repo::edge_grant::{FriendIdsPage, FriendListQuery};
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
    PermissionRequestRepoPort,
};
use bcs_service_api::{ActorKind, BotRegistryCoreService, ServiceError, ServiceResult};
use bcs_domain::ActorStatus;
use tokio::sync::RwLock;

fn now_epoch_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as u64)
        .unwrap_or_default()
}

/// Strictly increasing millisecond clock.
///
/// `gmt_modified` ordering is observable (list_inbox/list_sent are
/// `updated_at DESC`), and two writes inside the same wall-clock millisecond
/// would otherwise tie. Bumping by one keeps the in-process ordering
/// deterministic without changing the epoch scale.
#[derive(Default)]
struct MonotonicClock {
    last: AtomicU64,
}

impl MonotonicClock {
    fn next(&self) -> u64 {
        let now = now_epoch_ms();
        loop {
            let last = self.last.load(Ordering::Relaxed);
            let next = if now > last { now } else { last + 1 };
            if self
                .last
                .compare_exchange_weak(last, next, Ordering::Relaxed, Ordering::Relaxed)
                .is_ok()
            {
                return next;
            }
        }
    }
}

fn next_id(counter: &AtomicU64) -> u64 {
    counter.fetch_add(1, Ordering::Relaxed) + 1
}

// ---------------------------------------------------------------------------
// permission_profiles
// ---------------------------------------------------------------------------

/// In-process [`PermissionProfileRepoPort`].
#[derive(Default)]
pub struct MemoryPermissionProfileStore {
    profiles: RwLock<Vec<PermissionProfile>>,
    next_profile_id: AtomicU64,
}

impl MemoryPermissionProfileStore {
    pub fn new() -> Self {
        Self::default()
    }

    fn active_default<'a>(
        profiles: &'a [PermissionProfile],
        bot_id: &str,
        env: &str,
    ) -> Option<&'a PermissionProfile> {
        profiles.iter().find(|profile| {
            profile.bot_id == bot_id
                && profile.env == env
                && profile.is_default
                && profile.status == ProfileStatus::Active
        })
    }
}

#[async_trait]
impl PermissionProfileRepoPort for MemoryPermissionProfileStore {
    async fn ensure_default_profile(&self, bot_id: &str, env: &str) -> ServiceResult<u64> {
        let mut profiles = self.profiles.write().await;
        // D12 rule 2: never overwrite or bump an existing default.
        if let Some(existing) = Self::active_default(&profiles, bot_id, env) {
            return Ok(existing.permission_profile_id);
        }
        // The wildcard-allow seed is a compile-time constant, so its JSON
        // parse cannot fail; allow `expect_used` at the smallest scope (F6).
        #[allow(
            clippy::expect_used,
            reason = "static wildcard-allow seed is a compile-time constant"
        )]
        let profile = PermissionProfile {
            permission_profile_id: next_id(&self.next_profile_id),
            bot_id: bot_id.to_string(),
            env: env.to_string(),
            name: "default".to_string(),
            description: None,
            rules_template: serde_json::from_str(crate::WILDCARD_ALLOW)
                .expect("wildcard-allow seed is valid JSON"),
            revision: 1,
            digest: crate::sha256_hex(crate::WILDCARD_ALLOW),
            is_default: true,
            status: ProfileStatus::Active,
            created_by: "system".to_string(),
            updated_by: None,
        };
        let id = profile.permission_profile_id;
        profiles.push(profile);
        Ok(id)
    }

    async fn get_active_default(&self, bot_id: &str, env: &str) -> Option<PermissionProfile> {
        let profiles = self.profiles.read().await;
        Self::active_default(&profiles, bot_id, env).cloned()
    }

    async fn upsert_revision(&self, profile: PermissionProfile) -> ServiceResult<()> {
        let mut profiles = self.profiles.write().await;
        let Some(existing) = profiles
            .iter_mut()
            .find(|existing| existing.permission_profile_id == profile.permission_profile_id)
        else {
            return Ok(());
        };
        // Only rules_template / revision / digest / updated_by move (D12 rule 2).
        existing.rules_template = profile.rules_template;
        existing.revision = profile.revision;
        existing.digest = profile.digest;
        existing.updated_by = profile.updated_by;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// edge_grants
// ---------------------------------------------------------------------------

/// In-process [`EdgeGrantRepoPort`].
///
/// Holds [`MemoryPermissionProfileStore`] because the friend-edge predicate is
/// defined against the peer's default profile id — the same join the SQL
/// implementation performs.
pub struct MemoryEdgeGrantStore {
    profiles: Arc<MemoryPermissionProfileStore>,
    grants: RwLock<Vec<EdgeGrant>>,
    next_edge_id: AtomicU64,
}

impl MemoryEdgeGrantStore {
    pub fn new(profiles: Arc<MemoryPermissionProfileStore>) -> Self {
        Self {
            profiles,
            grants: RwLock::new(Vec::new()),
            next_edge_id: AtomicU64::new(0),
        }
    }

    async fn default_profile_id(&self, bot_id: &str, env: &str) -> Option<u64> {
        self.profiles
            .get_active_default(bot_id, env)
            .await
            .map(|profile| profile.permission_profile_id)
    }

    /// Approved default-profile edge `from → to` with `grant_ref_id == default_ref`.
    async fn has_default_edge(
        &self,
        from: &str,
        to: &str,
        env: &str,
        default_ref: Option<u64>,
    ) -> bool {
        let Some(default_ref) = default_ref else {
            return false;
        };
        self.grants.read().await.iter().any(|grant| {
            grant.from_id == from
                && grant.to_id == to
                && grant.env == env
                && grant.status == EdgeStatus::Approved
                && grant.grant_kind == GrantKind::PermissionProfile
                && grant.grant_ref_id == default_ref
        })
    }

    /// Per-direction `(peer_id, grant_ref_id)` candidates, read under one lock.
    async fn friend_edge_candidates(&self, actor: &str, env: &str) -> (Vec<(String, u64)>, Vec<(String, u64)>) {
        let grants = self.grants.read().await;
        let outbound = grants
            .iter()
            .filter(|grant| {
                grant.from_id == actor
                    && grant.env == env
                    && grant.status == EdgeStatus::Approved
                    && grant.grant_kind == GrantKind::PermissionProfile
            })
            .map(|grant| (grant.to_id.clone(), grant.grant_ref_id))
            .collect();
        let inbound = grants
            .iter()
            .filter(|grant| {
                grant.to_id == actor
                    && grant.env == env
                    && grant.status == EdgeStatus::Approved
                    && grant.grant_kind == GrantKind::PermissionProfile
            })
            .map(|grant| (grant.from_id.clone(), grant.grant_ref_id))
            .collect();
        (outbound, inbound)
    }

    /// Distinct friend actor ids (`list_friends` / paginated base set).
    async fn friend_ids(&self, actor: &str, env: &str) -> Vec<String> {
        let (outbound, inbound) = self.friend_edge_candidates(actor, env).await;
        let mut cache: HashMap<String, Option<u64>> = HashMap::new();
        let mut friends: HashSet<String> = HashSet::new();

        for (peer, grant_ref_id) in outbound {
            let default = match cache.get(&peer) {
                Some(default) => *default,
                None => {
                    let default = self.default_profile_id(&peer, env).await;
                    cache.insert(peer.clone(), default);
                    default
                }
            };
            if default == Some(grant_ref_id) {
                friends.insert(peer);
            }
        }

        // Inbound friend edges are keyed on the actor's own default profile:
        // a peer can only be a friend of a bot that has a default profile.
        if let Some(actor_default) = self.default_profile_id(actor, env).await {
            for (peer, grant_ref_id) in inbound {
                if grant_ref_id == actor_default {
                    friends.insert(peer);
                }
            }
        }

        let mut out: Vec<String> = friends.into_iter().collect();
        out.sort();
        out
    }
}

#[async_trait]
impl EdgeGrantRepoPort for MemoryEdgeGrantStore {
    async fn list_active_grants(&self, from: &str, to: &str, env: &str) -> Vec<EdgeGrant> {
        self.grants
            .read()
            .await
            .iter()
            .filter(|grant| {
                grant.from_id == from
                    && grant.to_id == to
                    && grant.env == env
                    && grant.status == EdgeStatus::Approved
                    // Runtime grants only: owner/manager metadata edges never
                    // surface as runtime authorization (spec §3.2.5 / A16).
                    && matches!(
                        grant.grant_kind,
                        GrantKind::PermissionProfile | GrantKind::Rules
                    )
            })
            .cloned()
            .collect()
    }

    async fn is_authorized(&self, from: &str, to: &str, env: &str) -> bool {
        !self.list_active_grants(from, to, env).await.is_empty()
    }

    async fn has_friend_edge(&self, x: &str, y: &str, env: &str) -> bool {
        // D12: either direction being a default-profile edge counts.
        let dy = self.default_profile_id(y, env).await;
        if self.has_default_edge(x, y, env, dy).await {
            return true;
        }
        let dx = self.default_profile_id(x, env).await;
        self.has_default_edge(y, x, env, dx).await
    }

    async fn list_friends(&self, actor: &str, env: &str) -> Vec<String> {
        self.friend_ids(actor, env).await
    }

    async fn list_friends_paginated(
        &self,
        actor: &str,
        env: &str,
        query: FriendListQuery,
    ) -> ServiceResult<FriendIdsPage> {
        if query.limit == 0 || query.limit > 100 || query.offset > i64::MAX as u64 {
            return Err(ServiceError::InvalidOperation {
                message: "invalid friend pagination bounds".into(),
                request_id: None,
            });
        }
        // Human IDs start with the exact, case-sensitive prefix `human_`.
        let filtered: Vec<String> = self
            .friend_ids(actor, env)
            .await
            .into_iter()
            .filter(|peer| match query.target_type {
                None => true,
                Some(ActorKind::Human) => peer.starts_with("human_"),
                Some(ActorKind::Bot) => !peer.starts_with("human_"),
            })
            .collect();
        let total = filtered.len() as u64;
        let start = query.offset as usize;
        let items = if start >= filtered.len() {
            Vec::new()
        } else {
            let end = start.saturating_add(query.limit as usize).min(filtered.len());
            filtered[start..end].to_vec()
        };
        Ok(FriendIdsPage { items, total })
    }

    async fn insert_grant(&self, grant: EdgeGrant) -> ServiceResult<u64> {
        let mut grants = self.grants.write().await;
        let existing = grants.iter_mut().find(|existing| {
            existing.from_id == grant.from_id
                && existing.to_id == grant.to_id
                && existing.env == grant.env
                && existing.grant_kind == grant.grant_kind
                && existing.grant_ref_id == grant.grant_ref_id
        });
        if let Some(existing) = existing {
            // Upsert parity with the SQL store: the conflicting row is
            // refreshed in place and keeps its id, so a re-add after a
            // revoke reactivates the revoked edge (unfriend → refriend →
            // active) instead of returning the stale revoked row id.
            let edge_id = existing.edge_id;
            existing.rules = grant.rules;
            existing.status = grant.status;
            existing.originator_policy_type = grant.originator_policy_type;
            existing.originator_policy_data = grant.originator_policy_data;
            return Ok(edge_id);
        }
        let edge_id = next_id(&self.next_edge_id);
        grants.push(EdgeGrant { edge_id, ..grant });
        Ok(edge_id)
    }

    async fn revoke_grant(&self, edge_id: u64, env: &str) -> ServiceResult<()> {
        let mut grants = self.grants.write().await;
        if let Some(grant) = grants
            .iter_mut()
            .find(|grant| grant.edge_id == edge_id && grant.env == env)
        {
            grant.status = EdgeStatus::Revoked;
        }
        Ok(())
    }

    async fn get_default_profile_id(&self, bot_id: &str, env: &str) -> Option<u64> {
        self.default_profile_id(bot_id, env).await
    }
}

// ---------------------------------------------------------------------------
// permission_requests
// ---------------------------------------------------------------------------

/// In-process [`PermissionRequestRepoPort`].
#[derive(Default)]
pub struct MemoryPermissionRequestStore {
    requests: RwLock<Vec<PermissionRequest>>,
    clock: MonotonicClock,
}

impl MemoryPermissionRequestStore {
    pub fn new() -> Self {
        Self::default()
    }

    async fn list(
        &self,
        actor_matches: impl Fn(&PermissionRequest) -> bool,
        env: &str,
        status: Option<RequestStatus>,
    ) -> Vec<PermissionRequest> {
        let mut requests: Vec<PermissionRequest> = self
            .requests
            .read()
            .await
            .iter()
            .filter(|request| {
                request.env == env
                    && actor_matches(request)
                    && status.is_none_or(|status| request.status == status)
            })
            .cloned()
            .collect();
        requests.sort_by(|left, right| {
            right
                .updated_at
                .cmp(&left.updated_at)
                .then_with(|| right.request_id.cmp(&left.request_id))
        });
        requests
    }
}

#[async_trait]
impl PermissionRequestRepoPort for MemoryPermissionRequestStore {
    async fn insert(&self, request: PermissionRequest) -> ServiceResult<()> {
        let mut requests = self.requests.write().await;
        // Idempotent on the request id, matching the store's insert contract.
        if requests
            .iter()
            .any(|existing| existing.request_id == request.request_id)
        {
            return Ok(());
        }
        let timestamp = self.clock.next();
        requests.push(PermissionRequest {
            // Store-managed timestamps override the caller placeholders.
            created_at: timestamp,
            updated_at: timestamp,
            ..request
        });
        Ok(())
    }

    async fn get(&self, request_id: &str, env: &str) -> Option<PermissionRequest> {
        self.requests
            .read()
            .await
            .iter()
            .find(|request| request.request_id == request_id && request.env == env)
            .cloned()
    }

    async fn find_request(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
        request_kind: RequestKind,
        status: RequestStatus,
        created_by: &str,
    ) -> Option<PermissionRequest> {
        // Same recency tie-break as `list` (updated_at DESC, request_id DESC).
        let mut matches: Vec<PermissionRequest> = self
            .requests
            .read()
            .await
            .iter()
            .filter(|request| {
                request.env == env
                    && request.from_id == from_id
                    && request.to_id == to_id
                    && request.request_kind == request_kind
                    && request.status == status
                    && request.created_by == created_by
            })
            .cloned()
            .collect();
        matches.sort_by(|left, right| {
            right
                .updated_at
                .cmp(&left.updated_at)
                .then_with(|| right.request_id.cmp(&left.request_id))
        });
        matches.into_iter().next()
    }

    async fn list_inbox(
        &self,
        to_id: &str,
        env: &str,
        status: Option<RequestStatus>,
    ) -> ServiceResult<Vec<PermissionRequest>> {
        Ok(self.list(|request| request.to_id == to_id, env, status).await)
    }

    async fn list_sent(
        &self,
        from_id: &str,
        env: &str,
        status: Option<RequestStatus>,
    ) -> ServiceResult<Vec<PermissionRequest>> {
        Ok(self.list(|request| request.from_id == from_id, env, status)
            .await)
    }

    async fn decide(
        &self,
        request_id: &str,
        env: &str,
        status: RequestStatus,
        decided_by: &str,
        decision_reason: Option<&str>,
    ) -> ServiceResult<()> {
        let timestamp = self.clock.next();
        let mut requests = self.requests.write().await;
        if let Some(request) = requests
            .iter_mut()
            .find(|request| request.request_id == request_id && request.env == env)
        {
            request.status = status;
            request.decided_by = Some(decided_by.to_string());
            request.decision_reason = decision_reason.map(str::to_string);
            request.decided_at = Some(timestamp);
            request.updated_at = timestamp;
        }
        Ok(())
    }

    async fn backfill_edge_id(&self, request_id: &str, env: &str, edge_id: u64) -> ServiceResult<()> {
        let timestamp = self.clock.next();
        let mut requests = self.requests.write().await;
        if let Some(request) = requests
            .iter_mut()
            .find(|request| request.request_id == request_id && request.env == env)
        {
            request.edge_id = Some(edge_id);
            request.updated_at = timestamp;
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// bot actor config
// ---------------------------------------------------------------------------

/// In-process [`BotActorConfigRepoPort`] projected from the in-memory bot
/// registry.
///
/// The in-memory registry is single-process and effectively single-env, so
/// the registry facts are projected at the queried `env` — the same
/// `(bot_id, env)` keying the DB-backed store uses — instead of trusting the
/// registration's process-stamped env, which follows `resolve_env_str()` and
/// differs across shells (dev/local/pre). The
/// bot-internal attributes (`user_visibility`, `friend_check_in_strategy`,
/// `friend_ext`) are not part of `RegisteredBot`; the in-memory path projects
/// the product defaults (`protected` / `APPROVAL` / empty map) instead of
/// inventing a second in-memory side table.
pub struct MemoryBotActorConfigStore {
    registry: Arc<dyn BotRegistryCoreService>,
}

impl MemoryBotActorConfigStore {
    pub fn new(registry: Arc<dyn BotRegistryCoreService>) -> Self {
        Self { registry }
    }
}

#[async_trait]
impl BotActorConfigRepoPort for MemoryBotActorConfigStore {
    async fn get(&self, bot_id: &str, env: &str) -> Option<BotActorConfig> {
        let bot = self.registry.get(bot_id).await?;
        Some(BotActorConfig {
            bot_id: bot.bot_uuid,
            env: env.to_string(),
            name: bot.capabilities.name.clone().unwrap_or_default(),
            visibility: bot.capabilities.visibility.clone(),
            status: match bot.status {
                ActorStatus::Online => "online".to_string(),
                ActorStatus::Hidden => "hidden".to_string(),
            },
            created_by: bot.created_by,
            user_visibility: "protected".to_string(),
            friend_check_in_strategy: "APPROVAL".to_string(),
            friend_ext: serde_json::Map::new(),
        })
    }
}

#[cfg(test)]
#[path = "memory_tests.rs"]
mod tests;
