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
//!   and the insert is idempotent, returning the existing id;
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
    ProfileStatus, RequestStatus,
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
        let existing = grants.iter().find(|existing| {
            existing.from_id == grant.from_id
                && existing.to_id == grant.to_id
                && existing.env == grant.env
                && existing.grant_kind == grant.grant_kind
                && existing.grant_ref_id == grant.grant_ref_id
        });
        if let Some(existing) = existing {
            return Ok(existing.edge_id);
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

    async fn list_inbox(
        &self,
        to_id: &str,
        env: &str,
        status: Option<RequestStatus>,
    ) -> Vec<PermissionRequest> {
        self.list(|request| request.to_id == to_id, env, status).await
    }

    async fn list_sent(
        &self,
        from_id: &str,
        env: &str,
        status: Option<RequestStatus>,
    ) -> Vec<PermissionRequest> {
        self.list(|request| request.from_id == from_id, env, status)
            .await
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
/// The in-memory registry is single-process and effectively single-env, so the
/// lookup ignores `env` (the registry is the whole world) and only uses it as
/// the projection fallback when a registration carries no env. The
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
            env: bot.env.unwrap_or_else(|| env.to_string()),
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
mod tests {
    use std::sync::Arc;

    use bcs_domain::edge_permission::{
        BotActorConfig, EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType, PermissionRequest,
        RequestKind, RequestStatus,
    };
    use bcs_service_api::port::repo::edge_grant::{FriendIdsPage, FriendListQuery};
    use bcs_service_api::port::repo::{
        BotActorConfigRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
        PermissionRequestRepoPort,
    };

    use super::*;

    const ENV: &str = "dev";

    fn request(id: &str, from: &str, to: &str) -> PermissionRequest {
        PermissionRequest {
            request_id: id.to_string(),
            edge_id: None,
            env: ENV.to_string(),
            from_id: from.to_string(),
            to_id: to.to_string(),
            request_kind: RequestKind::Connect,
            requested_ref_id: None,
            requested_rules: None,
            message: Some("hello".to_string()),
            status: RequestStatus::Pending,
            decision_reason: None,
            created_by: from.to_string(),
            decided_by: None,
            decided_at: None,
            created_at: 0,
            updated_at: 0,
        }
    }

    fn grant(
        from: &str,
        to: &str,
        grant_kind: GrantKind,
        grant_ref_id: u64,
    ) -> EdgeGrant {
        EdgeGrant {
            edge_id: 0,
            env: ENV.to_string(),
            from_id: from.to_string(),
            to_id: to.to_string(),
            grant_kind,
            grant_ref_id,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        }
    }

    // ---- PermissionRequestRepoPort -------------------------------------

    #[tokio::test]
    async fn memory_permission_request_store_insert_get_decide() {
        let store = MemoryPermissionRequestStore::new();

        store.insert(request("r1", "a", "b")).await.unwrap();
        store.insert(request("r2", "c", "b")).await.unwrap();

        let got = store.get("r1", ENV).await.expect("inserted request");
        assert_eq!(got.status, RequestStatus::Pending);
        assert_eq!(got.request_kind, RequestKind::Connect);
        assert!(got.created_at > 0, "insert stamps gmt_create");
        assert_eq!(got.created_at, got.updated_at, "insert stamps both gmt_*");
        assert!(got.decided_at.is_none());
        assert!(store.get("r1", "other-env").await.is_none(), "env scoped");
        assert!(store.get("missing", ENV).await.is_none());

        // Inbox is recipient-scoped, newest first.
        let inbox = store.list_inbox("b", ENV, None).await;
        assert_eq!(
            inbox.iter().map(|r| r.request_id.as_str()).collect::<Vec<_>>(),
            vec!["r2", "r1"],
            "inbox returns both rows, most recently updated first"
        );
        assert!(
            store.list_inbox("a", ENV, None).await.is_empty(),
            "inbox is keyed on to_id"
        );

        // Sent is sender-scoped and status-filterable.
        let sent = store.list_sent("a", ENV, Some(RequestStatus::Pending)).await;
        assert_eq!(sent.len(), 1);
        assert_eq!(sent[0].request_id, "r1");
        assert!(
            store
                .list_sent("a", ENV, Some(RequestStatus::Approved))
                .await
                .is_empty(),
            "status filter applies"
        );

        let before = store.get("r1", ENV).await.unwrap().updated_at;
        store
            .decide(
                "r1",
                ENV,
                RequestStatus::Approved,
                "owner",
                Some("ok"),
            )
            .await
            .unwrap();
        let decided = store.get("r1", ENV).await.unwrap();
        assert_eq!(decided.status, RequestStatus::Approved);
        assert_eq!(decided.decided_by.as_deref(), Some("owner"));
        assert_eq!(decided.decision_reason.as_deref(), Some("ok"));
        assert!(decided.decided_at.is_some(), "decide stamps decided_at");
        assert!(
            decided.updated_at > before,
            "decide advances gmt_modified ({} > {})",
            decided.updated_at,
            before
        );
        assert_eq!(decided.created_at, got.created_at, "gmt_create is stable");

        assert!(
            store
                .list_inbox("b", ENV, Some(RequestStatus::Pending))
                .await
                .iter()
                .all(|r| r.request_id == "r2"),
            "decided rows leave the pending inbox filter"
        );
        assert_eq!(
            store.list_inbox("b", ENV, None).await[0].request_id,
            "r1",
            "the just-decided row moved to the front"
        );

        store.backfill_edge_id("r1", ENV, 42).await.unwrap();
        let backfilled = store.get("r1", ENV).await.unwrap();
        assert_eq!(backfilled.edge_id, Some(42));
        assert!(backfilled.updated_at >= decided.updated_at);
    }

    // ---- EdgeGrantRepoPort ---------------------------------------------

    #[tokio::test]
    async fn memory_edge_grant_store_friend_edges() {
        let profiles = Arc::new(MemoryPermissionProfileStore::new());
        let store = MemoryEdgeGrantStore::new(profiles.clone());

        let target_default = profiles.ensure_default_profile("target", ENV).await.unwrap();
        let caller_default = profiles.ensure_default_profile("caller", ENV).await.unwrap();
        let human_default = profiles.ensure_default_profile("human_1", ENV).await.unwrap();

        // D12: a friend edge is a PermissionProfile edge whose ref is the
        // peer's active default profile id.
        let forward = grant("caller", "target", GrantKind::PermissionProfile, target_default);
        let forward_id = store.insert_grant(forward.clone()).await.unwrap();
        let reverse = grant("target", "caller", GrantKind::PermissionProfile, caller_default);
        let reverse_id = store.insert_grant(reverse.clone()).await.unwrap();

        assert!(store.has_friend_edge("caller", "target", ENV).await);
        assert!(store.has_friend_edge("target", "caller", ENV).await);
        assert_eq!(store.list_friends("caller", ENV).await, vec!["target".to_string()]);
        assert_eq!(store.list_friends("target", ENV).await, vec!["caller".to_string()]);
        assert_eq!(store.list_active_grants("caller", "target", ENV).await.len(), 1);
        assert!(store.is_authorized("caller", "target", ENV).await);

        // Idempotent insert on the unique key.
        let duplicate = store.insert_grant(forward.clone()).await.unwrap();
        assert_eq!(duplicate, forward_id, "duplicate insert returns the existing id");
        assert_eq!(store.list_active_grants("caller", "target", ENV).await.len(), 1);
        assert_eq!(
            store.list_friends("caller", ENV).await,
            vec!["target".to_string()],
            "duplicate edges do not duplicate friends"
        );

        // A same-pair, wrong-ref edge is authorized but is not a friend edge.
        store
            .insert_grant(grant(
                "caller",
                "target",
                GrantKind::PermissionProfile,
                target_default + 100,
            ))
            .await
            .unwrap();
        assert_eq!(
            store.list_active_grants("caller", "target", ENV).await.len(),
            2
        );
        assert_eq!(
            store.list_friends("caller", ENV).await,
            vec!["target".to_string()],
            "wrong-ref edge is not a friend edge"
        );

        // A Rules edge authorizes without being a friendship.
        store
            .insert_grant(grant("caller", "rules-bot", GrantKind::Rules, 3))
            .await
            .unwrap();
        assert!(store.is_authorized("caller", "rules-bot", ENV).await);
        assert!(!store.has_friend_edge("caller", "rules-bot", ENV).await);

        // Paginated friend list: dedupe, actor-kind filter, byte-order sort,
        // and total across the whole filtered set.
        profiles.ensure_default_profile("human_1", ENV).await.unwrap();
        store
            .insert_grant(grant(
                "caller",
                "human_1",
                GrantKind::PermissionProfile,
                human_default,
            ))
            .await
            .unwrap();

        let all = store
            .list_friends_paginated("caller", ENV, FriendListQuery { target_type: None, offset: 0, limit: 100 })
            .await
            .unwrap();
        assert_eq!(
            all,
            FriendIdsPage {
                items: vec!["human_1".to_string(), "target".to_string()],
                total: 2,
            }
        );
        let humans = store
            .list_friends_paginated(
                "caller",
                ENV,
                FriendListQuery { target_type: Some(ActorKind::Human), offset: 0, limit: 100 },
            )
            .await
            .unwrap();
        assert_eq!(humans.items, vec!["human_1".to_string()]);
        assert_eq!(humans.total, 1);
        let bots = store
            .list_friends_paginated(
                "caller",
                ENV,
                FriendListQuery { target_type: Some(ActorKind::Bot), offset: 0, limit: 100 },
            )
            .await
            .unwrap();
        assert_eq!(bots.items, vec!["target".to_string()]);
        assert_eq!(bots.total, 1);
        let second_page = store
            .list_friends_paginated(
                "caller",
                ENV,
                FriendListQuery { target_type: None, offset: 1, limit: 1 },
            )
            .await
            .unwrap();
        assert_eq!(
            second_page,
            FriendIdsPage { items: vec!["target".to_string()], total: 2 },
            "total counts before pagination"
        );
        assert!(store
            .list_friends_paginated(
                "caller",
                ENV,
                FriendListQuery { target_type: None, offset: 0, limit: 0 },
            )
            .await
            .is_err());

        // Revoking is per edge: one direction is enough to keep a friendship
        // alive (D12 is symmetric); the wrong-ref edge cannot substitute for it.
        store.revoke_grant(forward_id, ENV).await.unwrap();
        assert!(
            store.has_friend_edge("caller", "target", ENV).await,
            "the reverse friend edge still holds the friendship"
        );
        assert_eq!(
            store.list_friends("caller", ENV).await,
            vec!["human_1".to_string(), "target".to_string()],
            "the reverse edge still lists target as a friend"
        );
        store.revoke_grant(reverse_id, ENV).await.unwrap();
        assert!(!store.has_friend_edge("caller", "target", ENV).await);
        assert_eq!(
            store.list_friends("caller", ENV).await,
            vec!["human_1".to_string()]
        );
        assert_eq!(
            store.list_active_grants("caller", "target", ENV).await.len(),
            1,
            "wrong-ref edge stays active"
        );
        assert!(store.is_authorized("caller", "target", ENV).await);
    }

    // ---- BotActorConfigRepoPort ----------------------------------------

    #[tokio::test]
    async fn memory_bot_actor_config_projects_registry_bot() {
        let registry = bcs_bot::BotCore::memory();
        registry
            .register(
                "bot-private".to_string(),
                bcs_service_api::BotCapabilities {
                    name: Some("Private Bot".to_string()),
                    visibility: "private".to_string(),
                    ..Default::default()
                },
            )
            .await
            .expect("register bot");
        let registry: Arc<dyn BotRegistryCoreService> = Arc::new(registry);

        let store = MemoryBotActorConfigStore::new(registry);
        let config = store
            .get("bot-private", ENV)
            .await
            .expect("registry bot projects to an actor config");

        assert_eq!(
            config,
            BotActorConfig {
                bot_id: "bot-private".to_string(),
                env: ENV.to_string(),
                name: "Private Bot".to_string(),
                visibility: "private".to_string(),
                status: "online".to_string(),
                created_by: None,
                user_visibility: "protected".to_string(),
                friend_check_in_strategy: "APPROVAL".to_string(),
                friend_ext: serde_json::Map::new(),
            }
        );
        assert!(store.get("missing-bot", ENV).await.is_none());
    }
}