//! The `InboxProducerPort` provider, shared by the Gateway (PA inputs) and the
//! Connector (agent events).

mod listener;
mod producer;

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
