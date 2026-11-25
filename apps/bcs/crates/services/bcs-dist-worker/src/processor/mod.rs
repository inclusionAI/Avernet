//! Worker execution policy. Storage and pure Runner boundaries stay injected.
mod capacity;
mod commit;
mod decision;
mod effects;
mod encoding;
mod lifecycle;
mod references;
mod terminal;
mod transitions;

#[cfg(test)]
mod capacity_tests;
#[cfg(test)]
#[path = "tests/reference_tests.rs"]
mod reference_tests;
#[cfg(test)]
mod tests;

mod acquisition;
mod admin;
mod control;
mod diagnostics;
mod drive;
mod engine;
mod observations;
mod prepare;
mod publication;
mod replay;
mod risk;
mod thinking;
pub use engine::{ProcessorConfig, ProcessorPorts, WorkerProcessor};
pub use observations::ProcessorObservations;
