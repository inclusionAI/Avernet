//! Provider-independent Worker membership, ownership, recovery and graceful drain.
//! The embedding processor owns reducer execution and persisted-effect replay;
//! coordination never invents their completion from a successful owner update.
mod config;
mod control;
mod recovery;
mod runtime;
pub use config::{HrwIdentity, WorkerConfig, rank_candidates};
pub use runtime::{CheckpointInitializer, WorkerRuntime};

mod drain;
