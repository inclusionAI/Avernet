use super::{
    StoreInboxProducer,
    state::{AdmissionRecord, Schedule},
};
use crate::{
    codec::{decode_json, encode_json},
    error::{from_read, from_write},
};
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::{Version, WriteOutcome};
use serde::{Serialize, de::DeserializeOwned};
use std::{
    future::Future,
    time::{Duration, Instant},
};

pub(super) fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}
pub(super) fn unknown(op: &str) -> StoreError {
    StoreError::OutcomeUnknown {
        operation_id: op.into(),
    }
}
pub(super) fn pressure() -> StoreError {
    StoreError::NotApplied {
        code: "admission_capacity".into(),
    }
}

/// One conditional write loop, including every read, IO timeout and conflict wait.
pub(super) struct EqBudget {
    start: Instant,
    attempts: u32,
}
impl EqBudget {
    pub fn new() -> Self {
        Self {
            start: Instant::now(),
            attempts: 0,
        }
    }
    pub async fn attempt(&mut self, op: &str) -> Result<Duration, StoreError> {
        if self.attempts >= 8 {
            return Err(unknown(op));
        }
        if self.attempts > 0 {
            let delay = Duration::from_millis((5_u64 << (self.attempts - 1)).min(200));
            if self.start.elapsed() + delay >= Duration::from_millis(500) {
                return Err(unknown(op));
            }
            tokio::time::sleep(delay).await;
        }
        self.attempts += 1;
        Duration::from_millis(500)
            .checked_sub(self.start.elapsed())
            .filter(|d| !d.is_zero())
            .ok_or_else(|| unknown(op))
    }
    pub async fn io<T>(
        &self,
        op: &str,
        f: impl Future<Output = Result<T, StoreError>>,
    ) -> Result<T, StoreError> {
        let remaining = Duration::from_millis(500)
            .checked_sub(self.start.elapsed())
            .ok_or_else(|| unknown(op))?;
        tokio::time::timeout(remaining, f)
            .await
            .map_err(|_| unknown(op))?
    }
}
impl StoreInboxProducer {
    pub(super) async fn io_permit(&self) -> Result<tokio::sync::SemaphorePermit<'_>, StoreError> {
        tokio::time::timeout(Duration::from_millis(100), self.inner.io.acquire())
            .await
            .map_err(|_| pressure())?
            .map_err(|_| invalid("io_budget_closed"))
    }

    pub(super) async fn load<T: DeserializeOwned>(
        &self,
        key: &str,
    ) -> Result<Option<(Version, T)>, StoreError> {
        let _permit = self.io_permit().await?;
        let read = tokio::time::timeout(
            Duration::from_millis(500),
            self.inner.documents.read_document(key),
        )
        .await
        .map_err(|_| StoreError::NotApplied {
            code: "read_timeout".into(),
        })?
        .map_err(|e| from_read(&e))?;
        read.map(|v| {
            encoded_size(&v.value, self.inner.config.max_document_bytes)?;
            Ok((v.version, decode_json(v.value)?))
        })
        .transpose()
    }
    pub(super) async fn save<T: Serialize>(
        &self,
        key: &str,
        version: Option<Version>,
        value: &T,
        op: &str,
    ) -> Result<bool, StoreError> {
        Ok(self.save_version(key, version, value, op).await?.is_some())
    }
    pub(super) async fn save_version<T: Serialize>(
        &self,
        key: &str,
        version: Option<Version>,
        value: &T,
        op: &str,
    ) -> Result<Option<Version>, StoreError> {
        let doc = encode_json(value)?;
        encoded_size(&doc, self.inner.config.max_document_bytes)?;
        let _permit = self.io_permit().await?;
        let write = async {
            match version {
                Some(v) => self.inner.documents.replace_document(key, v, &doc).await,
                None => self.inner.documents.create_document(key, &doc).await,
            }
        };
        let outcome = tokio::time::timeout(Duration::from_millis(500), write)
            .await
            .map_err(|_| unknown(op))?
            .map_err(|e| from_write(&e, op))?;
        Ok(match outcome {
            WriteOutcome::Applied { version } => Some(version),
            WriteOutcome::ConditionNotMet => None,
        })
    }
    pub(super) fn validate_admission(
        &self,
        record: &AdmissionRecord,
    ) -> Result<(), StoreError> {
        encoded_size(&record.request, self.inner.config.max_input_bytes)?;
        if record.schema_version != 1
            || record.generation == 0
            || record.request.input.business_digest
                != super::validation::request_digest(&record.request)?
        {
            return Err(invalid("stored_admission_digest"));
        }
        Ok(())
    }
    pub(super) async fn admission_record(
        &self,
        key: &str,
    ) -> Result<Option<(Version, AdmissionRecord)>, StoreError> {
        let value = self.load::<AdmissionRecord>(key).await?;
        if let Some((_, record)) = &value {
            self.validate_admission(record)?;
            if self.inner.layout.admission(
                &record.request.scope,
                &record.request.input.input_id,
            )? != key
            {
                return Err(invalid("stored_admission_identity"));
            }
        }
        Ok(value)
    }
    pub(super) async fn schedule(
        &self,
        sid: &bcs_dist_model::Sid,
    ) -> Result<(Option<Version>, Schedule), StoreError> {
        Ok(match self.load(&self.inner.layout.schedule(sid)?).await? {
            Some((v, s)) => (Some(v), s),
            None => (None, Schedule::default()),
        })
    }
}

/// Counts escaped UTF-8 JSON bytes while refusing to allocate a second full body.
pub(super) fn encoded_size<T: Serialize>(value: &T, limit: usize) -> Result<usize, StoreError> {
    struct Counter {
        bytes: usize,
        limit: usize,
    }
    impl std::io::Write for Counter {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.bytes = self
                .bytes
                .checked_add(bytes.len())
                .filter(|n| *n <= self.limit)
                .ok_or_else(|| std::io::Error::other("encoded limit"))?;
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let mut counter = Counter { bytes: 0, limit };
    serde_json::to_writer(&mut counter, value).map_err(|_| invalid("encoded_byte_budget"))?;
    Ok(counter.bytes)
}
