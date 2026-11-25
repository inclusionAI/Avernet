//! `SessionAuthorizationCoreService` — the narrow authorization
//! snapshot Core (bot-authority plan §3, spec §8.3).
//!
//! Sessions have no baseline `SessionCoreService`; this new Core
//! exposes exactly one capability: the strict
//! `read_authorization_snapshot` read. It forwards the
//! `SessionRepoPort::read_authorization_snapshot` port contract and
//! never falls back to an ordinary display cache or an
//! infallible `get`.

use async_trait::async_trait;

use crate::ServiceResult;
use crate::types::authorization_snapshot::SessionAuthorizationSnapshot;

#[async_trait]
pub trait SessionAuthorizationCoreService: Send + Sync {
    /// Read the Session's authorization-fact snapshot.
    ///
    /// Only `Ok(None)` means Session not found; storage and
    /// decoding failures are `Err`.
    async fn read_authorization_snapshot(
        &self,
        session_id: &str,
    ) -> ServiceResult<Option<SessionAuthorizationSnapshot>>;
}
