mod auth_cookies;
mod authentication;
mod composite_principal;
mod csrf;
mod envelope;
mod error;
mod identity_policy;
mod invite_code_gate;
mod principal;
mod request_id;
mod state;

pub use auth_cookies::{
    CHALLENGE_COOKIE_LOCAL, CHALLENGE_COOKIE_SECURE, CookieProtocol,
    SESSION_COOKIE_NAME, no_store, strict_unique_cookie_value, unique_cookie_value,
};
pub use authentication::{
    AuthenticationContext, CredentialKind, DisplayMetadata, VerifiedRequestIdentity,
    VerificationAttempt, GATEWAY_PRINCIPAL_SOURCE,
};
pub use composite_principal::CompositePrincipalVerifier;
pub use csrf::TrustedBrowserOrigins;
pub use envelope::{Envelope, ErrorData};
pub use error::{ErrorResponse, application_error_response, invalid_request};
pub use identity_policy::{IdentityPolicyMethodRouterExt, RouteIdentityPolicy};
pub use invite_code_gate::{InviteCodeGateState, enforce_invite_code_gate};
pub use principal::{PrincipalVerificationError, PrincipalVerifier, verify_principal};
pub use request_id::RequestId;
pub use state::{ApiState, ChainUserProjection, PrincipalVerificationState};
