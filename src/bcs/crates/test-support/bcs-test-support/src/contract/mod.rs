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
pub mod provider_registration;
pub mod repo;
pub mod provider_registration_core;
pub mod bot_registration_create;

pub use provider_registration::provider_registration_repo_port_contract_tests;
