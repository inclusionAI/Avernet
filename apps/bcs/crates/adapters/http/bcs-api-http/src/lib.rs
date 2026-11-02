//! BCN versioned HTTP delivery adapter.

pub mod v1;

pub use v1::common::{
    ApiState, AuthenticationContext, CompositePrincipalVerifier, CredentialKind,
    PrincipalVerificationError, PrincipalVerifier, TrustedBrowserOrigins, VerificationAttempt,
    VerifiedRequestIdentity,
};
pub use v1::group_session_connection_router;
pub use v1::router;
