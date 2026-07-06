//! Provider-agnostic OAuth session authentication.
//!
//! The issuing provider is recorded in the JWT (`claims.src`) at login time, so
//! a single [`OAuthSessionPlugin`] handles google, github, or any future
//! provider. Provider-specific code (token exchange, userinfo) lives in each
//! [`bcs_auth_api::OAuthProvider`] implementation, not here.

mod plugin;
mod verify;

pub use plugin::OAuthSessionPlugin;
pub use verify::verify_oauth_session;
