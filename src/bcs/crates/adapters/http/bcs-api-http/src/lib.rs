//! BCN versioned HTTP delivery adapter.

pub mod v1;

pub use v1::common::{ApiState, PrincipalVerificationError, PrincipalVerifier};
pub use v1::router;
