//! Centralized BCS contract test harnesses.
//!
//! Each public trait has a stable harness entry point. Concrete implementations
//! call these functions from `tests/conformance_*.rs`.

pub mod application;
pub mod core;
pub mod interceptor;
pub mod lifecycle;
pub mod plugin;
pub mod port;
pub mod repo;
