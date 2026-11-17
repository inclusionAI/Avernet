//! Connector engine of the distributed executor plane.
//!
//! The Connector makes the downstream A2A calls (one call = one dispatch) and
//! reports agent events to the session's inbox. This crate holds the rules and
//! the engine; it reaches storage and agents only through ports.
//!
//! - [`rules`]: the pure rules (`DistConnectorCoreService`).
//! - [`identity`]: stable request, input and interaction ids.
//! - [`engine`]: the main loop (receive → claim → ack), drain and snapshot.
//! - [`config`]: settings and ports.
//! - [`delayed`]: explicit stubs of contracts that are not final.
//!
//! The per-call actor lives in the private modules `call`, `send`, `control`
//! and `report`.

mod call;
mod control;
mod report;
mod send;

pub mod config;
pub mod delayed;
pub mod engine;
pub mod identity;
pub mod rules;

pub use config::{ConnectorConfig, ConnectorPorts};
pub use engine::ConnectorEngine;
