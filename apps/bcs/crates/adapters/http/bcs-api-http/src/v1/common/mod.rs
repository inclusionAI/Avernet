mod authentication;
mod auth_cookies;
mod composite_principal;
mod csrf;
mod envelope;
mod error;
mod identity_policy;
mod principal;
mod invite_code_gate;
mod request_id;
mod state;

pub use authentication::{
    AuthenticationContext, CredentialKind, VerificationAttempt, VerifiedRequestIdentity,
};
pub use auth_cookies::{challenge_cookie_name, encode_cookie_change};
pub use composite_principal::CompositePrincipalVerifier;
pub use csrf::TrustedBrowserOrigins;
pub use envelope::{Envelope, ErrorData};
pub use error::{ErrorResponse, application_error_response, auth_dependency_unavailable, invalid_request};
pub use identity_policy::{IdentityPolicyMethodRouterExt, RouteIdentityPolicy};
pub use invite_code_gate::{InviteCodeGateState, enforce_invite_code_gate};
pub use principal::{PrincipalVerificationError, PrincipalVerifier, verify_principal};
pub use request_id::RequestId;
pub use state::{ApiState, PrincipalVerificationState};
