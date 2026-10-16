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
//! Implementations are expected to be best-effort from the *caller's* point of
//! view: `DbConnectService` logs-and-ignores errors from this port because
//! `edge_grants` is already the source of truth and there is no cross-store
//! transaction.

use async_trait::async_trait;

use crate::ServiceResult;

/// Mirror an edge-permission friendship decision into the legacy store.
///
/// Only Bot↔Bot pairs are in scope; implementations may assume the pair has
/// been kind-checked by the caller but should stay idempotent.
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
