//! OAuth HTTP routes: /auth/url, /auth/callback/{provider}, /auth/logout.
//!
//! These routes are shared across all OAuth providers. Provider-specific
//! logic is delegated to the `OAuthProvider` trait implementations injected
//! into `OAuthRouteState`.
//!
//! This file is the module root / facade. The route handlers and
//! `OAuthRouteState` impl live in [`legacy_facade`]; the CSRF state store lives
//! in [`state`]. Behavior-preserving split only — see Task 1 of the V1 API
//! auth plugin chain plan.

pub mod cookies;

mod legacy_facade;

pub use cookies::{
    CHALLENGE_COOKIE_LOCAL, CHALLENGE_COOKIE_SECURE, CookieProtocol, no_store, origin_matches,
    unique_cookie_value,
};
pub use legacy_facade::{
    AuthUrlResponse, CallbackParams, OAuthRouteState, ProviderUrl, UserInfoResponse,
    auth_url_handler, callback_handler, current_user_handler, get_user_handler,
    identity_routes, logout_handler, refresh_handler, routes,
};

// Child test modules (`tests`, `diagnostics_test`) historically resolved their
// shared dependencies through `use super::*;` against this module's original
// top-level imports. After the split the imports live alongside the impl in
// `legacy_facade`, so we re-stub them here as test-only re-exports to keep the
// behavior-preserving split from forcing edits inside every test file.
#[cfg(test)]
#[allow(unused_imports)]
pub(crate) use {
    legacy_facade::*,
    axum::{
        extract::{Path, State},
        http::{HeaderMap, HeaderName, HeaderValue, StatusCode},
        response::IntoResponse,
        routing::{get, post},
        Json, Router,
    },
    serde::{Deserialize, Serialize},
    tracing::warn,
    std::{collections::HashMap, sync::Arc},
    bcs_auth_api::{OAuthConfig, OAuthProvider, UserIdentityPort, BCS_SESSION_COOKIE},
    bcs_jwt::{Claims, JwtService},
    bcs_test_support::MockOAuthProvider,
    bcs_service_api::application::v1::{
        ApplicationError, AuthProviderUrl as V1AuthProviderUrl, AuthService,
        CompleteOAuthLogin,
    },
};

#[cfg(test)]
mod tests;
#[cfg(test)]
mod diagnostics_test;
