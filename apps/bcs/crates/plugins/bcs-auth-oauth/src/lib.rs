//! Provider-agnostic OAuth session authentication.
//!
//! The issuing provider is recorded in the JWT (`claims.src`) at login time, so
//! a single [`OAuthSessionPlugin`] handles google, github, or any future
//! provider. Provider-specific code (token exchange, userinfo) lives in each
//! [`bcs_auth_api::OAuthProvider`] implementation, not here.
//!
//! The crate also publishes the strict [`OAuthSessionEngine`] (the V1 auth
//! vertical-slice session lifecycle core) for use by the Task 11/12 HTTP
//! wiring. The legacy [`verify_oauth_session`] / [`OAuthSessionPlugin`]
//! (which translate JWT-verify failures to `Ok(None)`) remain for backward
//! compatibility with the existing `bcs_session` cookie chain; the strict
//! engine's callers consume [`SessionAuthError`]-typed errors instead.

mod login_state;
mod plugin;
mod session_lifecycle;
mod strict;
mod verify;

pub use login_state::MemoryPendingOAuthLoginStore;
pub use plugin::OAuthSessionPlugin;
pub use strict::OAuthSessionEngine;
pub use verify::verify_oauth_session;
