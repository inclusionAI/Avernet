//! Runtime binding barrier for Human participant message-scope mutations.
//!
//! Group mutation services acquire a lease before changing a persisted scope.
//! The adapter must reject new bindings for the same Group/Human and invalidate
//! existing bindings before returning the lease.

use async_trait::async_trait;
use std::sync::atomic::{AtomicU64, Ordering};

use crate::core::ServiceResult;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParticipantViewScopeChangeLease {
    pub scope_id: String,
    pub human_actor_id: String,
    pub lease_id: u64,
}

#[async_trait]
pub trait ParticipantViewBindingPort: Send + Sync {
    async fn begin_scope_change(
        &self,
        scope_id: &str,
        human_actor_id: &str,
    ) -> ServiceResult<ParticipantViewScopeChangeLease>;

    async fn finish_scope_change(
        &self,
        lease: ParticipantViewScopeChangeLease,
    ) -> ServiceResult<()>;
}

#[derive(Debug, Default)]
pub struct NoopParticipantViewBindingPort;

static NEXT_NOOP_LEASE_ID: AtomicU64 = AtomicU64::new(1);

#[async_trait]
impl ParticipantViewBindingPort for NoopParticipantViewBindingPort {
    async fn begin_scope_change(
        &self,
        scope_id: &str,
        human_actor_id: &str,
    ) -> ServiceResult<ParticipantViewScopeChangeLease> {
        Ok(ParticipantViewScopeChangeLease {
            scope_id: scope_id.to_string(),
            human_actor_id: human_actor_id.to_string(),
            lease_id: NEXT_NOOP_LEASE_ID.fetch_add(1, Ordering::Relaxed),
        })
    }

    async fn finish_scope_change(
        &self,
        _lease: ParticipantViewScopeChangeLease,
    ) -> ServiceResult<()> {
        Ok(())
    }
}
