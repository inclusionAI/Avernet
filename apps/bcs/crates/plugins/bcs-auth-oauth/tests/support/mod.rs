//! Shared test fixtures for the bcs-auth-oauth integration tests.
//!
//! The strict session engine runs against the Task 5 plugin-API
//! [`AuthSessionIdentityPort`]; this module provides the in-crate
//! `TestSessionIdentity` double so plugin tests never depend back on the
//! bootstrap composition crate or a persistence layer.

pub mod session_identity;
