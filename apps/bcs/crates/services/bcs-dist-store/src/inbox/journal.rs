//! Single-document admission helpers.
//!
//! The provider uses one scoped admission document. It does not reserve a
//! shard-wide journal sequence or materialize separate intent/canonical keys.
use super::{StoreInboxProducer, state::*, storage::*};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::AdmitRequest};

impl StoreInboxProducer {
    /// Build the immutable request envelope used by the admission document.
    /// No storage write occurs here; until `create_if_absent` succeeds the
    /// caller makes no recovery promise and must retry the original InputId.
    pub(super) fn new_admission(
        &self,
        request: AdmitRequest,
        generation: u64,
        connector: Option<bcs_service_api::port::repo::distributed::inbox::ConnectorEventIdentity>,
    ) -> Result<AdmissionRecord, StoreError> {
        let record = AdmissionRecord {
            schema_version: 1,
            generation,
            connector,
            request,
            created_at: self.inner.clock.now(),
            position: None,
            decision: None,
            registered: false,
            append_attempts: 0,
            append_credits: 1,
            phase: Phase::Created,
        };
        self.validate_admission(&record)?;
        Ok(record)
    }

    /// Intermediate events use the same per-record size validation but no
    /// namespace-wide admission budget or root reservation.
    pub(super) async fn reserve_append(
        &self,
        _sid: &Sid,
        _bytes: u64,
        _normal: bool,
        _repair: bool,
        _op: &str,
    ) -> Result<(), StoreError> {
        Ok(())
    }

    /// Create the single admission record if absent. A racing creator is
    /// resolved by reading the existing record and comparing immutable content.
    pub(super) async fn create_if_absent(
        &self,
        record: &AdmissionRecord,
    ) -> Result<(), StoreError> {
        self.validate_admission(record)?;
        let key = self
            .inner
            .layout
            .admission(&record.request.scope, &record.request.input.input_id)?;
        if !self
            .save(&key, None, record, &record.request.input.input_id.0)
            .await?
        {
            let (_, stored) = self
                .admission_record(&key)
                .await?
                .ok_or_else(|| unknown(&record.request.input.input_id.0))?;
            if stored.request.input.business_digest != record.request.input.business_digest {
                return Err(invalid("admission_content_conflict"));
            }
        }
        Ok(())
    }
}
