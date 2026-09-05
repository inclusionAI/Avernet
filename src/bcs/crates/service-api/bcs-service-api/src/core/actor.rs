use async_trait::async_trait;

use super::ServiceResult;

pub use bcs_domain::{
    ActorKind, ActorStatus, EnsureHumanResult, EnsureOwnerEdgesResult, RelationEdge,
};

/// Service for managing the BCS social/relation graph (`bcs_actor_relations`).
///
/// See `docs/specs/bcs-human-actor/design.md` §4.3 for SQL templates and
/// rationale. V1 only uses `is_creator`; `kinds` / `allow` / `deny` are
/// always 0 in V1.
#[async_trait]
pub trait RelationCoreService: Send + Sync {
    /// Insert or update a relation edge.
    ///
    /// `is_creator=TRUE` MUST NOT be downgraded to `FALSE` by subsequent
    /// upserts (use `GREATEST(is_creator, VALUES(is_creator))`).
    async fn upsert_edge(&self, edge: RelationEdge) -> ServiceResult<()>;

    /// Delete a relation edge by `(from_id, to_id, env)` triple.
    /// Idempotent: deleting a non-existent edge returns Ok.
    async fn delete_edge(&self, from_id: &str, to_id: &str, env: &str) -> ServiceResult<()>;

    /// Get a relation edge by `(from_id, to_id, env)` triple.
    /// Returns `None` if no edge exists.
    async fn get_edge(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
    ) -> ServiceResult<Option<RelationEdge>>;

    /// Ensure the two owner edges for a creator/bot pair exist.
    ///
    /// Writes (idempotently):
    /// - `(from=human_id, to=bot_id, env, is_creator=TRUE)`
    /// - `(from=bot_id, to=human_id, env, is_creator=FALSE)`
    async fn ensure_owner_edges(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<()>;

    /// Ensure the two owner edges and return per-edge creation/upgrade counts.
    ///
    /// Same semantics as [`ensure_owner_edges`](Self::ensure_owner_edges) but
    /// returns an [`EnsureOwnerEdgesResult`] so the `/me/ensure-human` handler
    /// can report accurate `edges_created` / `edges_upgraded` counts.
    ///
    /// **No default impl is provided** — all `RelationCoreService` implementations
    /// MUST explicitly implement this to avoid silently returning zero counts.
    async fn ensure_owner_edges_counted(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<EnsureOwnerEdgesResult>;

    /// Add the two friend edges for a friendship pair (called from
    /// `FriendCoreService::add_friendship`).
    ///
    /// Writes (idempotently):
    /// - `(from=a, to=b, env, kinds=0, allow=0, deny=0, is_creator=FALSE)`
    /// - `(from=b, to=a, env, kinds=0, allow=0, deny=0, is_creator=FALSE)`
    async fn add_friend_edges(&self, a: &str, b: &str, env: &str) -> ServiceResult<()>;

    /// Remove the two friend edges (called from
    /// `FriendCoreService::remove_friendship`).
    ///
    /// Owner edges (`is_creator=TRUE`) MUST be preserved.
    async fn remove_friend_edges(&self, a: &str, b: &str, env: &str) -> ServiceResult<()>;

    /// Remove all friend edges where `actor_id` participates (called from
    /// `FriendCoreService::remove_all_friendships`).
    ///
    /// Owner edges (`is_creator=TRUE`) MUST be preserved.
    async fn remove_all_friend_edges(&self, actor_id: &str, env: &str) -> ServiceResult<()>;

    /// Add a single one-way relation edge `(caller -> target)`.
    ///
    /// Used by the "invite a public actor to collaborate" path (caller subscribes
    /// to / delegates to the target, but they do NOT become friends). Only the
    /// `caller -> target` edge is written; the reverse edge is NOT inserted.
    async fn add_relation_edge(&self, caller: &str, target: &str, env: &str) -> ServiceResult<()>;

    /// List "friends" of `actor_id` via the relation graph using the bidirectional
    /// half-join semantics:
    ///
    /// ```sql
    /// SELECT a.to_id FROM bcs_actor_relations a
    /// INNER JOIN bcs_actor_relations b
    ///   ON a.to_id = b.from_id AND a.from_id = b.to_id AND a.env = b.env
    /// WHERE a.from_id = ? AND a.env = ? AND a.is_creator=FALSE AND b.is_creator=FALSE
    /// ```
    ///
    /// One-way relation edges (e.g. invitation to public actor) and owner
    /// edges (`is_creator=TRUE`) MUST NOT appear in the result.
    async fn list_friends_via_relation(
        &self,
        actor_id: &str,
        env: &str,
    ) -> ServiceResult<Vec<String>>;
}

/// Synchronizes legacy friendship mutations into the edge-permission friend
/// edge model while both friendship links coexist.
#[async_trait]
pub trait EdgePermissionFriendSyncService: Send + Sync {
    /// Mirror an established legacy friendship into approved edge-permission
    /// friend edges. Implementations must be idempotent.
    async fn sync_add_friendship(&self, a: &str, b: &str) -> ServiceResult<()>;

    /// Mirror removal of one legacy friendship into edge-permission friend-edge
    /// revocation. Implementations must preserve unrelated non-friend grants.
    async fn sync_remove_friendship(&self, a: &str, b: &str) -> ServiceResult<()>;
}
