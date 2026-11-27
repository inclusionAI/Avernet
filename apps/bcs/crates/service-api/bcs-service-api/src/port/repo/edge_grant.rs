//! `EdgeGrantRepoPort` — persistence port for `edge_grants`.
use std::collections::HashSet;

use async_trait::async_trait;
use bcs_domain::edge_permission::EdgeGrant;
use bcs_domain::ActorKind;

use crate::core::error::{ServiceError, ServiceResult};

/// Transport-neutral friend query. None includes both actor kinds.
/// Callers supply limit in 1..=100 and offset <= i64::MAX.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FriendListQuery {
    pub target_type: Option<ActorKind>,
    pub offset: u64,
    pub limit: u32,
}

/// Distinct internal actor IDs, ordered by case-sensitive UTF-8 byte order.
/// total counts filtered peers before pagination, including for empty pages.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FriendIdsPage {
    pub items: Vec<String>,
    pub total: u64,
}

#[async_trait]
pub trait EdgeGrantRepoPort: Send + Sync {
    /// Active profile/rules/agent_invoke grants `from -> to` in `env`.
    /// Legacy best-effort read; security consumers use the checked read below.
    async fn list_active_grants(&self, from: &str, to: &str, env: &str) -> Vec<EdgeGrant>;

    /// Checked, byte-exact runtime grants (profile, rules, agent_invoke).
    /// Query/decoding/shape errors fail the entire read; no partial success.
    /// The default fails closed so downstream implementations must explicitly
    /// opt into this contract, never delegate to an infallible read.
    async fn list_runtime_grants_checked(
        &self, _from: &str, _to: &str, _env: &str,
    ) -> ServiceResult<Vec<EdgeGrant>> {
        Err(ServiceError::InternalError("Checked runtime grant lookup is unavailable".into()))
    }

    /// True iff an active approved profile/rules/agent_invoke edge exists in
    /// this direction and environment. Owner/manager do not grant runtime access.
    /// A new type does not imply friendship or a target default-profile ref.
    async fn is_authorized(&self, from: &str, to: &str, env: &str) -> bool;

    /// `are_friends(x, y)` = any approved default-profile edge x→y OR y→x (D12).
    async fn has_friend_edge(&self, x: &str, y: &str, env: &str) -> bool;

    /// Friends of `actor` (any direction, default-profile edge) — actor ids only.
    async fn list_friends(&self, actor: &str, env: &str) -> Vec<String>;

    /// Store-owned filtering, deduplication, counting and pagination of approved
    /// active-default-profile edges in either direction. Human IDs start with
    /// the exact, case-sensitive prefix `human_`; all other IDs are Bots.
    /// Read failures are errors, never successful empty/partial pages.
    async fn list_friends_paginated(
        &self,
        actor: &str,
        env: &str,
        query: FriendListQuery,
    ) -> ServiceResult<FriendIdsPage>;

    /// Candidate-bound Human → Bot friendship read: approved edges on each
    /// requested Bot's active default permission profile in `env`.
    /// Storage errors must fail the read, not become missing or allowed edges.
    async fn find_human_friend_bots(
        &self,
        _human_id: &str,
        _env: &str,
        _bot_ids: &[String],
    ) -> ServiceResult<HashSet<String>> {
        Err(ServiceError::InternalError("Human friend lookup is unavailable".into()))
    }

    async fn insert_grant(&self, grant: EdgeGrant) -> ServiceResult<u64>;

    async fn revoke_grant(&self, edge_id: u64, env: &str) -> ServiceResult<()>;

    /// Atomically revoke the edge only when it is still active/approved,
    /// returning `true` when this call performed the transition. The default
    /// keeps existing implementors compiling; the durable store overrides it
    /// with a conditional update (`… WHERE edge_id=? AND status='approved'`)
    /// so concurrent removals transition exactly once and notify exactly once.
    async fn revoke_grant_if_active(&self, edge_id: u64, env: &str) -> ServiceResult<bool> {
        self.revoke_grant(edge_id, env).await?;
        Ok(true)
    }

    /// Revoke one friendship **relation** atomically. The `primary` edge is
    /// the relation-level winner token: the durable store performs it as a
    /// conditional update inside a transaction with stop-on-no-rows, so at
    /// most one concurrent caller transitions the relation and may emit a
    /// notice. `others` (the opposite direction, if any) are revoked in the
    /// same transaction after `primary` transitioned.
    ///
    /// Returns the edges THIS call transitioned. The default keeps external
    /// implementors compiling; it is not atomic across the two directions.
    async fn revoke_relation_if_active(
        &self,
        primary: u64,
        others: &[u64],
        env: &str,
    ) -> ServiceResult<Vec<u64>> {
        let mut revoked = Vec::new();
        if self.revoke_grant_if_active(primary, env).await? {
            revoked.push(primary);
            for edge_id in others {
                if self.revoke_grant_if_active(*edge_id, env).await? {
                    revoked.push(*edge_id);
                }
            }
        }
        Ok(revoked)
    }

    /// Cached source for friend-edge discrimination: `(bot_id, env) -> default profile_id`.
    async fn get_default_profile_id(&self, bot_id: &str, env: &str) -> Option<u64>;
}
