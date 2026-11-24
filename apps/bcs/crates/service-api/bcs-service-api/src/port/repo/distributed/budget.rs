//! Shared per-Worker scan resources, injected into every scanner and supervisor.
//! Reservations never wait while holding a processor slot. A permit is released
//! on drop, including cancellation. Work quotas count attempted authoritative IO.
use super::common::StoreError;
use std::num::NonZeroU32;
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScanClass {
    Repair,
    Discovery,
    Dispatch,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScanBudgetProfile {
    pub identity: String,
    pub pool_size: usize,
    pub io_slots: usize,
    pub logs_per_second: u32,
    pub sessions_per_second: u32,
    /// Aggregate burst across all classes, separately for each work dimension.
    pub burst: u32,
}
/// Opaque owned reservation; dropping it releases the provider resource.
pub type ScanIoPermit = Box<dyn Send + Sync>;
pub trait ScanBudgetPort: Send + Sync {
    fn io_in_use(&self) -> Option<usize> {
        None
    }
    fn profile(&self) -> ScanBudgetProfile;
    fn try_io(&self) -> Option<ScanIoPermit>;
    /// Atomically reserve both dimensions, or consume neither. Each of the three
    /// classes retains a quarter of the quota; only the fourth quarter is shared.
    fn claim(&self, class: ScanClass, logs: u32, sessions: u32) -> Result<bool, StoreError>;
    /// Atomically reserve up to `limit` index rows from the log quota. Session
    /// visits reserve their own quota. `None` means no row can be reserved now.
    fn claim_log_page(
        &self,
        class: ScanClass,
        limit: NonZeroU32,
    ) -> Result<Option<NonZeroU32>, StoreError>;
}
