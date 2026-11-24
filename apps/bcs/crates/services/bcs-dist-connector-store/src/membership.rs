//! A membership provider that is not there.
//!
//! Membership (`MembershipPort`) is the list of live processes: each one
//! registers, renews a lease, and others ask whether it is alive. The
//! Connector needs it for one decision: a dispatch held by another instance
//! may be taken over only on proof that the holder is dead (`Liveness::Dead`).
//! `Unknown` is never `Dead`.
//!
//! The real provider is `bcs_dist_store::coordination::CoordinationStore`,
//! which implements `MembershipPort`. Nothing wires it into a Connector yet,
//! so a Connector runs with this stub. Consequences while it does:
//! - no claim can take over a call held by another instance (every liveness
//!   check fails, so the answer is `HeldByOther`);
//! - the Connector runs without a registration (no lease to renew).
//!
//! CONFORMANCE_WAIVED: this is a stub of a provider another team builds; it
//! cannot pass a provider harness and must not pretend to.

use async_trait::async_trait;
use bcs_dist_model::Instance;
use bcs_dist_store::error::need_contract;
use bcs_service_api::port::repo::distributed::common::{Page, PageRequest, StoreError};
use bcs_service_api::port::repo::distributed::ownership::{
    Liveness, MemberRecord, MembershipLease, MembershipPort, Renewal, RuntimeRole,
};
use std::time::Duration;

/// No membership provider is wired.
///
/// Heartbeats and liveness (decision 15) are not built here. Every method
/// fails with `need_contract_not_implemented` (a `StoreError::NotApplied`),
/// so the claim cannot prove another holder dead and answers `HeldByOther`,
/// and the Connector runs without a registration.
///
/// `register` takes a `control_address`: the URL where other processes reach
/// this process's control endpoint, stored in the member record. The real
/// provider refuses an empty or non-http(s) one.
#[derive(Debug, Default, Clone, Copy)]
pub struct PendingMembership;

#[async_trait]
impl MembershipPort for PendingMembership {
    /// No membership provider is wired.
    async fn register(
        &self,
        _instance: Instance,
        _role: RuntimeRole,
        _control_address: String,
        _ttl: Duration,
    ) -> Result<MembershipLease, StoreError> {
        Err(need_contract())
    }
    /// No membership provider is wired.
    async fn renew(&self, _lease: &MembershipLease, _ttl: Duration) -> Result<Renewal, StoreError> {
        Err(need_contract())
    }
    /// No membership provider is wired.
    async fn set_draining(&self, _lease: &MembershipLease) -> Result<Renewal, StoreError> {
        Err(need_contract())
    }
    /// No membership provider is wired.
    async fn retire(&self, _lease: &MembershipLease) -> Result<(), StoreError> {
        Err(need_contract())
    }
    /// No membership provider is wired.
    async fn liveness(&self, _instance: &Instance) -> Result<Liveness, StoreError> {
        Err(need_contract())
    }
    /// No membership provider is wired.
    async fn members(
        &self,
        _role: RuntimeRole,
        _page: PageRequest,
    ) -> Result<Page<MemberRecord>, StoreError> {
        Err(need_contract())
    }
}
