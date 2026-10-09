//! Compatibility entry for callers that still import `bcs_http::server`.
//!
//! The old inline bootstrap server shim has been removed from this crate. New
//! code should import `bcs_http::router` and `bcs_http::state` directly.

pub use crate::error::HttpAdapterError;
pub use crate::router::build_router;
pub use crate::state::HttpAppState;
