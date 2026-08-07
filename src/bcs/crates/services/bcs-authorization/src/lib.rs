//! Authorization core service implementations.
//!
//! This crate implements policy-free runtime authz context construction for BCS
//! service flows. Delivery adapters should call application/message-flow layers;
//! they should not compute grants themselves.

pub mod core;

pub use core::{AuthzContextBuilderService, AuthzContextBuilderServiceConfig};
