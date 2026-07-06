//! Shared service contract types.
//!
//! `types` is the low-level module that may be used by `application`, `core`,
//! and `port` contracts without creating reverse dependencies between those
//! layers.

pub mod error;

pub use bcs_domain::*;
pub use error::{ServiceError, ServiceResult};
