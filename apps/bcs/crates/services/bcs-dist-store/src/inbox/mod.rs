//! Durable Inbox admission, consumption and scheduling.
//! All ports share authoritative DocumentCAS state; wake/index writes are hints.
//! See `README.md` for retained data, budgets and Worker integration obligations.

mod admission;
mod closure;
pub use closure::StoreAdmissionControl;
mod capacity;
pub use capacity::RetentionCapacityObservation;
mod consumer;
mod health;
pub use health::MaintenanceObservation;
mod journal;
mod listener;
mod maintenance;
mod producer;
mod pressure;
mod progress;
mod scheduling;
mod state;
mod storage;
mod validation;

pub use listener::{ListenerState, read_listener};
pub use producer::{InboxProducerConfig, StoreInboxProducer};

use bcs_dist_model::TimestampMillis;

/// Wall clock for `active` scores; injected so tests are deterministic.
pub trait Clock: Send + Sync {
    fn now(&self) -> TimestampMillis;
}

/// The system clock.
#[derive(Debug, Default, Clone, Copy)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> TimestampMillis {
        let millis = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or_default();
        TimestampMillis(i64::try_from(millis).unwrap_or(i64::MAX))
    }
}
