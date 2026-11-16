//! Vendor-neutral runtime storage capabilities. See the crate README for the
//! persistence, lifecycle and provider-conformance requirements.
//!
//! Business keys, serialization, ownership, retries and commit proofs belong to
//! service stores. There is no raw-command escape hatch, client-side CAS fallback
//! or cross-key transaction implied by these contracts.

mod common;
mod error;
pub mod json;
pub mod kv;
pub mod stream;
pub mod zset;

pub use common::*;
pub use error::*;
