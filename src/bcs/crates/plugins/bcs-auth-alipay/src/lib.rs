//! Alipay OAuth provider for BCS.
//!
//! Implements the Alipay-specific [`OAuthProvider`](bcs_auth_api::OAuthProvider)
//! (token exchange + userinfo). Alipay uses RSA2 (SHA256WithRSA) request
//! signing, implemented in the [`sign`] module.
//!
//! Session verification is provider-agnostic and handled by
//! `bcs_auth_api::OAuthSessionPlugin`, so this crate carries no `AuthPlugin`.

pub mod config;
pub mod provider;
pub mod sign;

pub use config::AlipayConfig;
pub use provider::AlipayOAuthProvider;