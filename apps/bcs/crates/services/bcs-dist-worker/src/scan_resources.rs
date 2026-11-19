//! Acquisition uses the same pool and Session quota as durable discovery.
use crate::runtime::{WorkerRuntime, err, store_error};
use bcs_service_api::{port::repo::distributed::budget::*, types::dist_runtime::RuntimeError};
use std::sync::Arc;

pub(crate) struct ScanVisit {
    _permit: Option<ScanIoPermit>,
}
impl WorkerRuntime {
    pub(crate) fn install_scan_budget(
        &self,
        budget: Arc<dyn ScanBudgetPort>,
    ) -> Result<(), RuntimeError> {
        let reader = self
            .inbox
            .reader_resources()
            .ok_or_else(|| err("processor_reader_resources_unknown"))?;
        if reader.max_document_bytes < 960 * 1024
            || reader.max_readers == 0
            || reader.max_readers
                < self
                    .config
                    .shards
                    .len()
                    .min(self.config.max_active_sessions)
            || self.config.max_active_sessions > reader.max_watches
            || self.config.max_active_sessions > reader.max_receipts
        {
            return Err(err("processor_reader_capacity_mismatch"));
        }
        let profile = budget.profile();
        if self.inbox.scan_budget_profile().as_ref() != Some(&profile)
            || self.scheduling.scan_budget_profile().as_ref() != Some(&profile)
            || profile.identity.is_empty()
            || profile.identity.len() > 256
            || profile.io_slots == 0
            || profile.io_slots > 8
            || profile.io_slots > profile.pool_size / 4
            || !(4..=1000).contains(&profile.logs_per_second)
            || !profile.logs_per_second.is_multiple_of(4)
            || !(4..=200).contains(&profile.sessions_per_second)
            || !profile.sessions_per_second.is_multiple_of(4)
            || !(4..=128).contains(&profile.burst)
            || !profile.burst.is_multiple_of(4)
        {
            return Err(err("processor_scan_budget_mismatch"));
        }
        let mut installed = self
            .scan_budget
            .lock()
            .map_err(|_| err("scan_budget_poisoned"))?;
        if installed.as_ref().is_some_and(|b| b.profile() != profile) {
            return Err(err("processor_scan_budget_mismatch"));
        }
        *installed = Some(budget);
        Ok(())
    }
    pub(crate) fn begin_session_scan(&self) -> Result<Option<ScanVisit>, RuntimeError> {
        let budget = self
            .scan_budget
            .lock()
            .map_err(|_| err("scan_budget_poisoned"))?
            .clone();
        // Legacy coordination-only hosts retain their existing API. A complete
        // phase-one Processor cannot be constructed without installing a budget.
        let Some(budget) = budget else {
            return Ok(Some(ScanVisit { _permit: None }));
        };
        let Some(permit) = budget.try_io() else {
            return Ok(None);
        };
        if !budget
            .claim(ScanClass::Discovery, 0, 1)
            .map_err(store_error)?
        {
            return Ok(None);
        }
        Ok(Some(ScanVisit {
            _permit: Some(permit),
        }))
    }
}
