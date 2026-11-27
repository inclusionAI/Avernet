//! Gateway-side store providers of the distributed executor plane, written only
//! against `bcs-store-api` and the shared pieces of `bcs-dist-store`.
//!
//! - [`StoreTaskReader`]: the `TaskReaderPort` provider (restore, resume check,
//!   follow, list). `follow` is served by one blocking read per session that has
//!   subscribers in this process, shared by those subscribers (decision 23).
//! - [`GatewaySessionStore`]: the read-only `SessionStorePort` of a Gateway
//!   process (`get_spec`); Worker operations are refused.
//!
//! Admission is the shared `bcs_dist_store::inbox::StoreInboxProducer`.

mod group_listing;
mod group_projection;
mod page;
mod position;
mod readers;
mod session;
mod task_reader;

pub use page::CURSOR_LIFETIME_MILLIS;
pub use position::LOG_GENERATION;
pub use session::{GatewaySessionStore, WORKER_ONLY};
pub use task_reader::{GatewayStoreConfig, StoreTaskReader, SUBSCRIBER_LAGGED};
