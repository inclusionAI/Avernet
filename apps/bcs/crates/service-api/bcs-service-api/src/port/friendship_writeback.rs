//! Edge-permission → legacy friendship reverse-sync port (coexistence window).
//!
//! While the old friend system (`bcs_friendships`, gated by the collaboration
//! group check in `bcs-group`) and the new edge-permission model
//! (`edge_grants`, gated by the friend-connection admission check) run in
//! parallel, friendships approved through the edge-permission Connect flow
//! must be mirrored into the legacy store so the group gate sees them.
//!
//! Spec: `docs/superpowers/specs/2026-10-15-edge-connect-friendship-writeback-design.md`
//!
//! Bot↔Bot retains its best-effort coexistence policy. Human→Bot mirrors are
//! required by the legacy Human friend list: failures must propagate, and
//! retries must repair partial writes (including an already-removed repo row).
//! There is no cross-store transaction; edge grants remain the mutation owner.

use async_trait::async_trait;

use crate::ServiceResult;

/// Mirror an edge-permission friendship decision into the legacy store.
///
/// Bot↔Bot and Human→Bot pairs are in scope. The relation mirror must be
/// maintained for Human readers; both argument orders may occur on removal.
/// Implementations must be idempotent and must not touch ownership edges.
#[async_trait]
pub trait FriendshipWritebackPort: Send + Sync {
    /// Insert the friendship row (idempotent; re-approving an approved pair
    /// backfills legacy rows written before this sync existed).
    async fn add_friendship(&self, caller: &str, target: &str) -> ServiceResult<()>;

    /// Remove the friendship row. Returns `true` when a row was actually
    /// removed, `false` when the pair did not exist.
    async fn remove_friendship(&self, caller: &str, target: &str) -> ServiceResult<bool>;
}

/// No-op writeback port for tests and for profiles running without the
/// legacy store (memory/local bootstrap profiles).
pub struct NoopFriendshipWritebackPort;

#[async_trait]
impl FriendshipWritebackPort for NoopFriendshipWritebackPort {
    async fn add_friendship(&self, _caller: &str, _target: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_friendship(&self, _caller: &str, _target: &str) -> ServiceResult<bool> {
        Ok(false)
    }
}
