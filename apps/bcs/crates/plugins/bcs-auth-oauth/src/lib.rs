//! Provider-agnostic OAuth session authentication.
//!
//! The issuing provider is recorded in the JWT (`claims.src`) at login time, so
//! a single [`OAuthSessionPlugin`] handles google, github, or any future
//! provider. Provider-specific code (token exchange, userinfo) lives in each
//! [`bcs_auth_api::OAuthProvider`] implementation, not here.
//!
//! [`OAuthSessionEngine`] (V1 API auth plugin chain, `strict.rs` +
//! `session_lifecycle.rs`) is the strict session lifecycle authority:
//! verify / install / refresh / revoke over the Task 5
//! [`bcs_auth_api::AuthSessionIdentityPort`], with compare-and-swap
//! issuance — compare-and-swap issuance semantics and the closed error
//! taxonomy live in `bcs_auth_api::session`.

mod plugin;
mod login_state;
mod session_lifecycle;
mod strict;
mod verify;

pub use login_state::MemoryPendingOAuthLoginStore;
pub use plugin::OAuthSessionPlugin;
pub use strict::OAuthSessionEngine;
pub use verify::verify_oauth_session;
