//! Shared ordinary-pool pressure gates only new business reservations.
use super::storage::invalid;
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
