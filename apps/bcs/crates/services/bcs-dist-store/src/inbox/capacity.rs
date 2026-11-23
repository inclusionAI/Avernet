//! Per-request and per-document physical size limits.
//!
//! The MVP no longer keeps a shard-wide admission ledger.  Capacity observations
//! therefore describe only limits that are still enforced on an individual
//! Inbox record; historical admission counts are intentionally unavailable.
use super::StoreInboxProducer;
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::AdmitRequest};

/// Diagnostic shape kept for API compatibility.  The fields that represented a
/// shard-wide retained-history budget are no longer populated by the provider.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RetentionCapacityObservation {
    pub charged_history_entries: u64,
    pub history_entries_remaining: u64,
    pub history_bytes_remaining: u64,
    pub normal_entries_remaining: u64,
    pub normal_bytes_remaining: u64,
    pub session_reservations_remaining: u64,
    pub distinct_sessions_remaining: u64,
    pub minimum_inbox_entries_remaining: u64,
    pub minimum_inbox_bytes_remaining: u64,
    pub minimum_normal_inbox_entries_remaining: u64,
    pub minimum_normal_inbox_bytes_remaining: u64,
    pub near_limit: bool,
}

impl StoreInboxProducer {
    /// The encoded Inbox entry must fit the single-record physical budget.
    pub(super) fn reliable_bytes(&self, request: &AdmitRequest) -> Result<u64, StoreError> {
        let mut fields = crate::codec::encode_inbox_entry(&request.input)?;
        fields.insert(
            "admission".into(),
            self.inner
                .layout
                .admission(&request.scope, &request.input.input_id)?
                .into_bytes(),
        );
        Ok(fields.iter().map(|(k, v)| k.len() + v.len()).sum::<usize>() as u64 + 512)
    }
}
