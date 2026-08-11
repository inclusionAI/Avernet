//! Shared HTTP header extraction helpers.
//!
//! `extract_bearer_token` now lives in `bcs-auth-api` (so auth plugins can
//! read a Bearer JWT without depending on a delivery adapter). This module
//! re-exports it for existing in-crate call sites.

pub use bcs_auth_api::extract_bearer_token;
