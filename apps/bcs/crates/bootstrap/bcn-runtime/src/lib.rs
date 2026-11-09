//! Explicit composition root; memory is available only for local/test profiles.
mod config;
pub use config::*;
mod builder;
pub use builder::*;
pub mod connectors;
