//! A membership provider that is not there.
//!
//! The real provider is `bcs_dist_store::coordination` (executor team); the
//! composition injects it. This stub is what a Connector runs with when none
//! is injected.
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
/// fails with `need_contract_not_implemented`, so the claim cannot prove
/// another holder dead and answers `HeldByOther`, and the Connector runs
/// without a registration.
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
