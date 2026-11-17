//! Connector engine of the distributed executor plane.
//!
//! The Connector makes the downstream A2A calls (one call = one dispatch) and
//! reports agent events to the session's inbox. This crate holds the rules and
//! the engine; it reaches storage and agents only through ports.
//!
//! - [`rules`]: the pure rules (`DistConnectorCoreService`).
//! - [`identity`]: stable request, input and interaction ids.

pub mod identity;
pub mod rules;
