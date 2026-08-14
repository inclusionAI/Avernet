//! Session file workspace application service.
//!
//! `SessionFileServiceImpl` owns capability routing, mutate authz, the three-stage
//! upload pipeline, delete routing, share-token mint/consume, and the Pending sweep
//! for the BCS session shared file workspace.

pub mod authz;
pub mod noop;
pub mod service;

pub use noop::NoopSessionFileService;
pub use service::{SessionFileServiceConfig, SessionFileServiceImpl};