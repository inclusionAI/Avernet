//! Shared ordinary-pool pressure gates only new business reservations.
use super::{StoreInboxProducer, storage::invalid};
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::StorePoolObservation;
use std::time::Duration;

pub(super) fn validate_observation(
    observation: Option<StorePoolObservation>,
    pool_size: usize,
) -> Result<Duration, StoreError> {
    match observation {
        Some(StorePoolObservation::Unpooled) => Ok(Duration::ZERO),
        Some(StorePoolObservation::Bounded {
            capacity,
            in_use,
            waiters,
            congested_for,
        }) if capacity == pool_size
            && capacity > 0
            && in_use <= capacity
            && (in_use == capacity || waiters > 0 || congested_for.is_zero()) =>
        {
            Ok(congested_for)
        }
        _ => Err(invalid("admission_pool_observation")),
    }
}

impl StoreInboxProducer {
    pub(super) fn check_pool_pressure(&self) -> Result<(), StoreError> {
        let age = validate_observation(
            self.inner.documents.pool_observation(),
            self.inner.config.connection_pool_size,
        );
        if age.is_ok_and(|age| age < self.inner.config.pool_pressure_after) {
            Ok(())
        } else {
            Err(StoreError::NotApplied {
                code: "admission_pool_pressure".into(),
            })
        }
    }
}
