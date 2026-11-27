//! Provider-independent Worker membership, ownership, recovery and graceful drain.
//! The embedding processor owns reducer execution and persisted-effect replay;
//! coordination never invents their completion from a successful owner update.
mod config;
mod control;
mod processor;
mod recovery;
mod runtime;
pub use config::{HrwIdentity, WorkerConfig, rank_candidates};
pub use runtime::{CheckpointInitializer, WorkerRuntime};

mod drain;

pub use processor::{ProcessorConfig, ProcessorObservations, ProcessorPorts, WorkerProcessor};

mod processor_runtime;

mod rebuilding_feed;
mod scan_resources;

pub mod supervisor;

mod admission;
mod node_history;
pub use admission::WorkerTaskAdmission;
mod output;
pub use output::WorkerOutputReader;
